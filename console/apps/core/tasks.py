from __future__ import annotations

import logging
import time

from celery import shared_task
from django.db import close_old_connections
from django.utils import timezone

from apps.core.ops_runner import get_mysql_host_stats
from apps.core.metrics import (
    record_database_metrics,
    record_host_metrics,
    save_dashboard_snapshot,
    save_database_detail_snapshot,
    save_database_list_snapshot,
)
from apps.core.mysql_client import (
    MySQLClientError,
    get_mysql_system_stats,
    list_business_databases,
    ping_mysql,
)
from apps.databases.collector import (
    build_database_detail_payload,
    collect_table_sizes,
    enrich_databases_with_resource_shares,
    sync_schema_inventory,
    upsert_database_inventory_sizes,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def collect_host_metrics_task(self):
    collect_dashboard_snapshot_task()


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def build_database_list_snapshot(
    databases: list,
    mysql_stats: dict | None,
    host_stats: dict | None,
    collected_at: str,
    mysql_error: str = "",
) -> dict:
    items = []
    total_size = 0
    total_connections = 0
    total_memory_share = 0
    for db in databases:
        item = dict(db)
        size_bytes = int(item.get("size_bytes") or 0)
        data_bytes = int(item.get("data_bytes") or 0)
        index_bytes = int(item.get("index_bytes") or 0)
        memory_share = int(item.get("memory_share_bytes") or 0)
        item["size_display"] = _format_size(size_bytes)
        item["data_size_display"] = _format_size(data_bytes)
        item["index_size_display"] = _format_size(index_bytes)
        item["memory_share_display"] = item.get("memory_share_display") or _format_size(memory_share)
        item["compute_share_percent"] = float(item.get("compute_share_percent") or 0)
        item["cpu_share_percent"] = float(item.get("cpu_share_percent") or 0)
        items.append(item)
        total_size += size_bytes
        total_connections += int(item.get("connection_count") or 0)
        total_memory_share += memory_share
    available_host = host_stats if host_stats and host_stats.get("available") else None
    return {
        "items": items,
        "summary": {
            "database_count": len(items),
            "total_size_bytes": total_size,
            "total_size_display": _format_size(total_size),
            "total_connections": total_connections,
            "total_memory_share_bytes": total_memory_share,
            "total_memory_share_display": _format_size(total_memory_share),
            "mysql_connections": (mysql_stats or {}).get("connections"),
            "mysql_memory": (mysql_stats or {}).get("memory"),
            "host_stats": available_host,
            "resource_notes": [
                "内存按各库数据体积占业务数据总量的比例，近似分摊 InnoDB Buffer Pool。",
                "算力按连接数与非 Sleep 活跃会话各占一半权重近似分摊，再乘容器 CPU%。",
            ],
        },
        "collected_at": collected_at,
        "mysql_error": mysql_error,
    }


@shared_task(bind=True, ignore_result=True)
def collect_dashboard_snapshot_task(self):
    """后台采样概览与库资源画像。页面只读快照。"""
    close_old_connections()
    payload = {
        "mysql_status": {"connected": False, "version": "-", "uptime_seconds": 0},
        "mysql_stats": None,
        "host_stats": None,
        "mysql_error": "",
        "db_count": 0,
    }
    databases: list[dict] = []
    try:
        payload["mysql_status"] = ping_mysql()
        payload["mysql_status"]["connected"] = True
        stats = get_mysql_system_stats()
        payload["mysql_stats"] = stats
        payload["mysql_status"]["uptime_seconds"] = stats["uptime_seconds"]
        payload["mysql_status"]["uptime_display"] = stats["uptime_display"]
        databases = list_business_databases()
        payload["db_count"] = len(databases)
    except MySQLClientError as exc:
        payload["mysql_error"] = str(exc)

    try:
        host_stats = get_mysql_host_stats()
        payload["host_stats"] = host_stats
        record_host_metrics(host_stats)
    except Exception as exc:
        host_stats = {"available": False, "error": str(exc)[:200]}
        payload["host_stats"] = host_stats

    cpu_percent = None
    memory_percent = None
    if host_stats and host_stats.get("available"):
        try:
            cpu_percent = float(host_stats.get("cpu_percent") or 0)
        except (TypeError, ValueError):
            cpu_percent = 0
        try:
            memory_percent = float(
                host_stats.get("memory_usage_percent")
                or host_stats.get("host_memory_usage_percent")
                or 0
            )
        except (TypeError, ValueError):
            memory_percent = 0

    if databases:
        databases = enrich_databases_with_resource_shares(
            databases,
            host_cpu_percent=cpu_percent,
            host_memory_percent=memory_percent,
        )
        try:
            upsert_database_inventory_sizes(databases)
        except Exception:
            logger.exception("upsert database inventory sizes failed")

        table_sizes: list[dict] = []
        try:
            table_sizes = collect_table_sizes()
        except Exception:
            logger.exception("collect table sizes failed")

        collected_at = timezone.localtime(timezone.now()).isoformat()
        ts = int(time.time())
        for db in databases:
            try:
                detail = build_database_detail_payload(
                    db["name"],
                    databases,
                    table_sizes,
                    collected_at,
                    mysql_stats=payload.get("mysql_stats"),
                    host_stats=payload.get("host_stats"),
                )
                if detail:
                    save_database_detail_snapshot(db["name"], detail)
                record_database_metrics(
                    db["name"],
                    {
                        "ts": ts,
                        "size_bytes": int(db.get("size_bytes") or 0),
                        "data_bytes": int(db.get("data_bytes") or 0),
                        "index_bytes": int(db.get("index_bytes") or 0),
                        "data_free_bytes": int(db.get("data_free_bytes") or 0),
                        "connection_count": int(db.get("connection_count") or 0),
                        "running_sessions": int(db.get("running_sessions") or 0),
                        "sleeping_sessions": int(db.get("sleeping_sessions") or 0),
                        "memory_share_bytes": int(db.get("memory_share_bytes") or 0),
                        "memory_share_percent": float(db.get("memory_share_percent") or 0),
                        "compute_share_percent": float(db.get("compute_share_percent") or 0),
                        "cpu_share_percent": float(db.get("cpu_share_percent") or 0),
                        "qps": float(db.get("qps") or 0),
                        "rows_examined_per_sec": float(db.get("rows_examined_per_sec") or 0),
                        "rows_sent_per_sec": float(db.get("rows_sent_per_sec") or 0),
                        "avg_latency_ms": float(db.get("avg_latency_ms") or 0),
                        "errors_per_sec": float(db.get("errors_per_sec") or 0),
                        "no_index_used_per_sec": float(db.get("no_index_used_per_sec") or 0),
                        "index_ratio_percent": float(db.get("index_ratio_percent") or 0),
                    },
                )
            except Exception:
                logger.exception("save database detail/metrics for %s failed", db.get("name"))

    payload["collected_at"] = timezone.localtime(timezone.now()).isoformat()
    save_dashboard_snapshot(payload)
    save_database_list_snapshot(
        build_database_list_snapshot(
            databases,
            payload.get("mysql_stats"),
            payload.get("host_stats"),
            payload["collected_at"],
            payload.get("mysql_error") or "",
        )
    )


@shared_task(bind=True, ignore_result=True)
def sync_schema_inventory_task(self):
    """低频全量同步表结构库存与 DDL 变更历史。"""
    close_old_connections()
    try:
        result = sync_schema_inventory()
        logger.info("schema inventory sync done: %s", result)
        return result
    except MySQLClientError as exc:
        logger.warning("schema inventory sync skipped: %s", exc)
        return {"error": str(exc)}
    except Exception:
        logger.exception("schema inventory sync failed")
        raise
