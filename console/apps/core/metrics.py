from __future__ import annotations

import json
import time
from typing import Any

from django.conf import settings
from redis import Redis


def _redis() -> Redis:
    return Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


def _metrics_key() -> str:
    return getattr(settings, "HOST_METRICS_REDIS_KEY", "mysql_console:host_metrics")


def _last_collect_key() -> str:
    return getattr(settings, "HOST_METRICS_LAST_COLLECT_KEY", "mysql_console:host_metrics:last_collect")


def _snapshot_key() -> str:
    return getattr(settings, "DASHBOARD_SNAPSHOT_REDIS_KEY", "mysql_console:dashboard_snapshot")


def save_dashboard_snapshot(payload: dict) -> None:
    _redis().set(_snapshot_key(), json.dumps(payload, ensure_ascii=False))


def load_dashboard_snapshot() -> dict | None:
    return _load_json(_snapshot_key())


def _database_list_key() -> str:
    return getattr(settings, "DATABASE_LIST_SNAPSHOT_REDIS_KEY", "mysql_console:database_list_snapshot")


def save_database_list_snapshot(payload: dict) -> None:
    _redis().set(_database_list_key(), json.dumps(payload, ensure_ascii=False))


def load_database_list_snapshot() -> dict | None:
    return _load_json(_database_list_key())


def _database_detail_key(name: str) -> str:
    prefix = getattr(settings, "DATABASE_DETAIL_SNAPSHOT_REDIS_KEY", "mysql_console:database_detail")
    return f"{prefix}:{name}"


def save_database_detail_snapshot(name: str, payload: dict) -> None:
    _redis().set(_database_detail_key(name), json.dumps(payload, ensure_ascii=False))


def load_database_detail_snapshot(name: str) -> dict | None:
    return _load_json(_database_detail_key(name))


def _database_metrics_key(name: str) -> str:
    prefix = getattr(settings, "DATABASE_METRICS_REDIS_KEY", "mysql_console:database_metrics")
    return f"{prefix}:{name}"


def record_database_metrics(name: str, point: dict) -> None:
    if not name or not point.get("ts"):
        return
    client = _redis()
    key = _database_metrics_key(name)
    ts_ms = int(point["ts"]) * 1000
    retention_ms = int(getattr(settings, "HOST_METRICS_RETENTION_SECONDS", 86400)) * 1000
    max_points = int(getattr(settings, "HOST_METRICS_MAX_POINTS", 1440))
    payload = json.dumps(point, separators=(",", ":"))
    client.zadd(key, {payload: ts_ms})
    client.zremrangebyscore(key, 0, ts_ms - retention_ms)
    count = client.zcard(key)
    if count > max_points:
        client.zpopmin(key, count - max_points)


def get_database_metrics_history(name: str, hours: float | None = None) -> dict[str, Any]:
    hours = hours or float(getattr(settings, "HOST_METRICS_DEFAULT_HOURS", 6))
    hours = max(0.5, min(hours, 168))
    since_ms = int((time.time() - hours * 3600) * 1000)
    client = _redis()
    rows = client.zrangebyscore(_database_metrics_key(name), since_ms, "+inf")
    points: list[dict] = []
    for raw in rows:
        try:
            item = json.loads(raw)
            if isinstance(item, dict) and item.get("ts"):
                points.append(item)
        except (TypeError, json.JSONDecodeError):
            continue
    points.sort(key=lambda x: x["ts"])
    instance = get_host_metrics_history(hours)
    detail = load_database_detail_snapshot(name) or {}
    return {
        "database": name,
        "interval_seconds": int(getattr(settings, "HOST_METRICS_COLLECT_INTERVAL", 3)),
        "retention_hours": round(
            int(getattr(settings, "HOST_METRICS_RETENTION_SECONDS", 86400)) / 3600, 1
        ),
        "hours": hours,
        "points": points,
        "instance_points": instance.get("points") or [],
        "monitors": detail.get("monitors"),
        "instance_context": detail.get("instance_context"),
        "resource_notes": detail.get("resource_notes") or [],
        "collected_at": detail.get("collected_at"),
    }


def _database_statement_key(name: str) -> str:
    prefix = getattr(settings, "DATABASE_STATEMENT_REDIS_KEY", "mysql_console:database_statements")
    return f"{prefix}:{name}"


def save_database_statement_totals(name: str, payload: dict) -> None:
    if not name:
        return
    _redis().set(_database_statement_key(name), json.dumps(payload, separators=(",", ":")))


def load_database_statement_totals(name: str) -> dict | None:
    return _load_json(_database_statement_key(name))


def _load_json(key: str) -> dict | None:
    raw = _redis().get(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def extract_metric_point(stats: dict) -> dict | None:
    if not stats or not stats.get("available"):
        return None
    ts = int(time.time())
    return {
        "ts": ts,
        "load_1m": _float_or_none(stats.get("load_1m")),
        "cpu_percent": _float_or_none(stats.get("cpu_percent")),
        "memory_usage_percent": _float_or_none(stats.get("memory_usage_percent")),
        "host_memory_usage_percent": _float_or_none(stats.get("host_memory_usage_percent")),
    }


def record_host_metrics(stats: dict) -> dict | None:
    point = extract_metric_point(stats)
    if not point:
        return None

    client = _redis()
    ts_ms = point["ts"] * 1000
    retention_ms = int(getattr(settings, "HOST_METRICS_RETENTION_SECONDS", 86400)) * 1000
    max_points = int(getattr(settings, "HOST_METRICS_MAX_POINTS", 1440))

    payload = json.dumps(point, separators=(",", ":"))
    client.zadd(_metrics_key(), {payload: ts_ms})
    client.zremrangebyscore(_metrics_key(), 0, ts_ms - retention_ms)
    count = client.zcard(_metrics_key())
    if count > max_points:
        client.zpopmin(_metrics_key(), count - max_points)
    client.set(_last_collect_key(), str(point["ts"]))
    return point


def maybe_collect_host_metrics(stats: dict) -> None:
    """Dashboard 等请求路径上的兜底采样（Beat 未运行时仍能积累数据）"""
    interval = int(getattr(settings, "HOST_METRICS_COLLECT_INTERVAL", 3))
    client = _redis()
    last = client.get(_last_collect_key())
    now = time.time()
    if last and now - float(last) < interval:
        return
    record_host_metrics(stats)


def get_host_metrics_history(hours: float | None = None) -> dict[str, Any]:
    hours = hours or float(getattr(settings, "HOST_METRICS_DEFAULT_HOURS", 6))
    hours = max(0.5, min(hours, 168))
    since_ms = int((time.time() - hours * 3600) * 1000)

    client = _redis()
    rows = client.zrangebyscore(_metrics_key(), since_ms, "+inf")
    points: list[dict] = []
    for raw in rows:
        try:
            item = json.loads(raw)
            if isinstance(item, dict) and item.get("ts"):
                points.append(item)
        except (TypeError, json.JSONDecodeError):
            continue

    points.sort(key=lambda x: x["ts"])
    return {
        "interval_seconds": int(getattr(settings, "HOST_METRICS_COLLECT_INTERVAL", 3)),
        "retention_hours": round(
            int(getattr(settings, "HOST_METRICS_RETENTION_SECONDS", 86400)) / 3600, 1
        ),
        "hours": hours,
        "points": points,
    }


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
