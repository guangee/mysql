from __future__ import annotations

from celery import shared_task
from django.db import close_old_connections
from django.utils import timezone

from apps.core.docker_client import get_mysql_host_stats
from apps.core.metrics import (
    record_host_metrics,
    save_dashboard_snapshot,
    save_database_list_snapshot,
)
from apps.core.mysql_client import (
    MySQLClientError,
    get_mysql_system_stats,
    list_business_databases,
    ping_mysql,
)


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


def build_database_list_snapshot(databases: list, mysql_stats: dict | None, host_stats: dict | None, collected_at: str, mysql_error: str = "") -> dict:
    items = []
    total_size = 0
    total_connections = 0
    for db in databases:
        item = dict(db)
        size_bytes = int(item.get("size_bytes") or 0)
        data_bytes = int(item.get("data_bytes") or 0)
        index_bytes = int(item.get("index_bytes") or 0)
        item["size_display"] = _format_size(size_bytes)
        item["data_size_display"] = _format_size(data_bytes)
        item["index_size_display"] = _format_size(index_bytes)
        items.append(item)
        total_size += size_bytes
        total_connections += int(item.get("connection_count") or 0)
    available_host = host_stats if host_stats and host_stats.get("available") else None
    return {
        "items": items,
        "summary": {
            "database_count": len(items),
            "total_size_bytes": total_size,
            "total_size_display": _format_size(total_size),
            "total_connections": total_connections,
            "mysql_connections": (mysql_stats or {}).get("connections"),
            "mysql_memory": (mysql_stats or {}).get("memory"),
            "host_stats": available_host,
        },
        "collected_at": collected_at,
        "mysql_error": mysql_error,
    }


@shared_task(bind=True, ignore_result=True)
def collect_dashboard_snapshot_task(self):
    """后台采样概览数据。页面只读这份快照，不再在打开时现查 MySQL 和 docker stats。"""
    close_old_connections()
    payload = {
        "mysql_status": {"connected": False, "version": "-", "uptime_seconds": 0},
        "mysql_stats": None,
        "host_stats": None,
        "mysql_error": "",
        "db_count": 0,
    }
    databases = []
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
        payload["host_stats"] = {"available": False, "error": str(exc)[:200]}

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
