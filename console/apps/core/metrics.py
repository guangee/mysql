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
    interval = int(getattr(settings, "HOST_METRICS_COLLECT_INTERVAL", 60))
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
        "interval_seconds": int(getattr(settings, "HOST_METRICS_COLLECT_INTERVAL", 60)),
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
