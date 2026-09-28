"""采集业务库资源占用与表结构库存。"""

from __future__ import annotations

import hashlib
import logging
import re
from difflib import unified_diff
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.mysql_client import MySQLClientError, list_business_databases, mysql_cursor
from apps.databases.models import (
    ColumnInventory,
    DatabaseInventory,
    IndexInventory,
    SchemaChangeEvent,
    TableInventory,
)

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def normalize_create_sql(sql: str) -> str:
    text = (sql or "").strip()
    text = _WHITESPACE_RE.sub(" ", text)
    return text


def structure_hash(sql: str) -> str:
    normalized = normalize_create_sql(sql)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def make_unified_diff(before: str, after: str, table: str) -> str:
    before_lines = (before or "").splitlines()
    after_lines = (after or "").splitlines()
    return "\n".join(
        unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{table}.sql",
            tofile=f"b/{table}.sql",
            lineterm="",
        )
    )


def _processlist_weights() -> dict[str, dict[str, int]]:
    """返回 {db: {connections, running, sleeping}}。"""
    weights: dict[str, dict[str, int]] = {}
    try:
        with mysql_cursor() as cur:
            cur.execute(
                """
                SELECT
                    DB,
                    COUNT(*) AS connections,
                    SUM(CASE WHEN COMMAND != 'Sleep' THEN 1 ELSE 0 END) AS running,
                    SUM(CASE WHEN COMMAND = 'Sleep' THEN 1 ELSE 0 END) AS sleeping
                FROM information_schema.processlist
                WHERE DB IS NOT NULL AND DB != ''
                GROUP BY DB
                """
            )
            for db_name, connections, running, sleeping in cur.fetchall():
                weights[db_name] = {
                    "connections": int(connections or 0),
                    "running": int(running or 0),
                    "sleeping": int(sleeping or 0),
                }
    except Exception as exc:
        logger.warning("processlist weights failed: %s", exc)
    return weights


def _buffer_pool_size() -> int:
    try:
        with mysql_cursor() as cur:
            cur.execute("SHOW GLOBAL VARIABLES LIKE 'innodb_buffer_pool_size'")
            row = cur.fetchone()
            return int(row[1]) if row else 0
    except Exception:
        return 0


def collect_table_sizes(database: str | None = None) -> list[dict]:
    system_dbs = tuple(settings.SYSTEM_DB_NAMES)
    sql = """
        SELECT
            TABLE_SCHEMA,
            TABLE_NAME,
            COALESCE(ENGINE, 'Unknown') AS engine,
            COALESCE(TABLE_ROWS, 0) AS row_count,
            COALESCE(DATA_LENGTH, 0) AS data_bytes,
            COALESCE(INDEX_LENGTH, 0) AS index_bytes,
            COALESCE(DATA_LENGTH + INDEX_LENGTH, 0) AS size_bytes,
            COALESCE(TABLE_COMMENT, '') AS table_comment
        FROM information_schema.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
          AND TABLE_SCHEMA NOT IN (%s, %s, %s, %s)
    """
    params: list[Any] = list(system_dbs)
    if database:
        sql += " AND TABLE_SCHEMA = %s"
        params.append(database)
    sql += " ORDER BY size_bytes DESC, TABLE_SCHEMA, TABLE_NAME"
    with mysql_cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "database": r[0],
            "name": r[1],
            "engine": r[2] or "-",
            "row_count": int(r[3] or 0),
            "data_bytes": int(r[4] or 0),
            "index_bytes": int(r[5] or 0),
            "size_bytes": int(r[6] or 0),
            "comment": r[7] or "",
        }
        for r in rows
    ]


def collect_schema_data_free() -> dict[str, int]:
    system_dbs = tuple(settings.SYSTEM_DB_NAMES)
    try:
        with mysql_cursor() as cur:
            cur.execute(
                """
                SELECT TABLE_SCHEMA, COALESCE(SUM(DATA_FREE), 0)
                FROM information_schema.TABLES
                WHERE TABLE_TYPE = 'BASE TABLE'
                  AND TABLE_SCHEMA NOT IN (%s, %s, %s, %s)
                GROUP BY TABLE_SCHEMA
                """,
                system_dbs,
            )
            return {name: int(free or 0) for name, free in cur.fetchall()}
    except Exception as exc:
        logger.warning("collect DATA_FREE failed: %s", exc)
        return {}


def collect_schema_statement_totals() -> dict[str, dict]:
    """按 schema 汇总 performance_schema 语句累计值。不可用时返回空。"""
    system_dbs = tuple(settings.SYSTEM_DB_NAMES)
    try:
        with mysql_cursor() as cur:
            cur.execute(
                """
                SELECT
                    SCHEMA_NAME,
                    COALESCE(SUM(COUNT_STAR), 0),
                    COALESCE(SUM(SUM_TIMER_WAIT), 0),
                    COALESCE(SUM(SUM_ROWS_EXAMINED), 0),
                    COALESCE(SUM(SUM_ROWS_SENT), 0),
                    COALESCE(SUM(SUM_ERRORS), 0),
                    COALESCE(SUM(SUM_WARNINGS), 0),
                    COALESCE(SUM(SUM_NO_INDEX_USED), 0),
                    COALESCE(SUM(SUM_NO_GOOD_INDEX_USED), 0)
                FROM performance_schema.events_statements_summary_by_digest
                WHERE SCHEMA_NAME IS NOT NULL
                  AND SCHEMA_NAME NOT IN (%s, %s, %s, %s)
                GROUP BY SCHEMA_NAME
                """,
                system_dbs,
            )
            result = {}
            for row in cur.fetchall():
                result[row[0]] = {
                    "count_star": int(row[1] or 0),
                    "sum_timer_wait_ps": int(row[2] or 0),
                    "sum_rows_examined": int(row[3] or 0),
                    "sum_rows_sent": int(row[4] or 0),
                    "sum_errors": int(row[5] or 0),
                    "sum_warnings": int(row[6] or 0),
                    "sum_no_index_used": int(row[7] or 0),
                    "sum_no_good_index_used": int(row[8] or 0),
                }
            return result
    except Exception as exc:
        logger.warning("performance_schema statement totals unavailable: %s", exc)
        return {}


def apply_statement_rates(databases: list[dict], now_ts: int | None = None) -> list[dict]:
    """用 Redis 中上次累计值计算 QPS / 扫描行速率等。"""
    import time

    from apps.core.metrics import load_database_statement_totals, save_database_statement_totals

    now_ts = now_ts or int(time.time())
    totals = collect_schema_statement_totals()
    enriched = []
    for db in databases:
        item = dict(db)
        name = item["name"]
        cur = totals.get(name) or {
            "count_star": 0,
            "sum_timer_wait_ps": 0,
            "sum_rows_examined": 0,
            "sum_rows_sent": 0,
            "sum_errors": 0,
            "sum_warnings": 0,
            "sum_no_index_used": 0,
            "sum_no_good_index_used": 0,
        }
        prev = load_database_statement_totals(name) or {}
        prev_ts = int(prev.get("ts") or 0)
        dt = max(now_ts - prev_ts, 1) if prev_ts else 0

        def _rate(key: str, current=cur, previous=prev, delta=dt) -> float:
            if not delta:
                return 0.0
            return max(0.0, (int(current.get(key) or 0) - int(previous.get(key) or 0)) / delta)

        count = int(cur.get("count_star") or 0)
        timer_ps = int(cur.get("sum_timer_wait_ps") or 0)
        item["qps"] = round(_rate("count_star"), 2)
        item["rows_examined_per_sec"] = round(_rate("sum_rows_examined"), 2)
        item["rows_sent_per_sec"] = round(_rate("sum_rows_sent"), 2)
        item["errors_per_sec"] = round(_rate("sum_errors"), 2)
        item["no_index_used_per_sec"] = round(_rate("sum_no_index_used"), 2)
        item["statements_total"] = count
        item["avg_latency_ms"] = round((timer_ps / count) / 1_000_000_000, 3) if count else 0.0
        item["statement_errors"] = int(cur.get("sum_errors") or 0)
        item["statement_warnings"] = int(cur.get("sum_warnings") or 0)
        save_database_statement_totals(name, {**cur, "ts": now_ts})
        enriched.append(item)
    return enriched


def enrich_databases_with_resource_shares(
    databases: list[dict],
    host_cpu_percent: float | None = None,
    host_memory_percent: float | None = None,
) -> list[dict]:
    """按数据体积分摊 buffer pool，按连接/活跃会话分摊算力；附带存储碎片与会话明细。"""
    import time

    buffer_pool = _buffer_pool_size()
    weights = _processlist_weights()
    free_map = collect_schema_data_free()
    total_data = sum(int(db.get("data_bytes") or 0) for db in databases) or 0
    total_conn = sum(weights.get(db["name"], {}).get("connections", 0) for db in databases) or 0
    total_running = sum(weights.get(db["name"], {}).get("running", 0) for db in databases) or 0
    cpu = float(host_cpu_percent or 0)
    mem_host = float(host_memory_percent or 0)

    enriched = []
    for db in databases:
        item = dict(db)
        name = item["name"]
        data_bytes = int(item.get("data_bytes") or 0)
        index_bytes = int(item.get("index_bytes") or 0)
        size_bytes = int(item.get("size_bytes") or 0)
        conn = weights.get(name, {}).get("connections", int(item.get("connection_count") or 0))
        running = weights.get(name, {}).get("running", 0)
        sleeping = weights.get(name, {}).get("sleeping", max(conn - running, 0))
        item["connection_count"] = conn
        item["running_sessions"] = running
        item["sleeping_sessions"] = sleeping

        memory_share = int(buffer_pool * (data_bytes / total_data)) if total_data and buffer_pool else 0
        conn_share = (conn / total_conn) if total_conn else 0.0
        running_share = (running / total_running) if total_running else 0.0
        if total_conn or total_running:
            compute_share = 0.5 * conn_share + 0.5 * running_share
        else:
            compute_share = 0.0
        item["memory_share_bytes"] = memory_share
        item["memory_share_display"] = _format_size(memory_share)
        item["compute_share_percent"] = round(compute_share * 100, 2)
        item["cpu_share_percent"] = round(compute_share * cpu, 2)
        if mem_host:
            item["memory_share_percent"] = round(compute_share * mem_host, 2)
        else:
            item["memory_share_percent"] = round((memory_share / buffer_pool * 100) if buffer_pool else 0, 2)
        item["buffer_pool_total_bytes"] = buffer_pool
        item["data_free_bytes"] = int(free_map.get(name) or 0)
        item["data_free_display"] = _format_size(item["data_free_bytes"])
        item["index_ratio_percent"] = round(index_bytes / size_bytes * 100, 1) if size_bytes else 0.0
        item["data_ratio_percent"] = round(data_bytes / size_bytes * 100, 1) if size_bytes else 0.0
        enriched.append(item)

    return apply_statement_rates(enriched, now_ts=int(time.time()))


def build_database_detail_payload(
    database_name: str,
    databases: list[dict],
    tables: list[dict],
    collected_at: str,
    top_n: int | None = None,
    mysql_stats: dict | None = None,
    host_stats: dict | None = None,
) -> dict | None:
    overview = next((db for db in databases if db["name"] == database_name), None)
    if not overview:
        return None
    limit = top_n or int(getattr(settings, "DATABASE_TABLE_TOP_N", 50))
    db_tables = [t for t in tables if t["database"] == database_name]
    db_tables.sort(key=lambda t: (-int(t["size_bytes"]), t["name"]))
    total_size = int(overview.get("size_bytes") or 0) or 1
    formatted_tables = []
    for table in db_tables[:limit]:
        size_bytes = int(table["size_bytes"])
        data_bytes = int(table["data_bytes"])
        index_bytes = int(table["index_bytes"])
        formatted_tables.append(
            {
                **table,
                "size_display": _format_size(size_bytes),
                "data_size_display": _format_size(data_bytes),
                "index_size_display": _format_size(index_bytes),
                "size_percent": round(size_bytes / total_size * 100, 2) if total_size else 0,
                "data_percent": round(data_bytes / size_bytes * 100, 1) if size_bytes else 0,
                "index_percent": round(index_bytes / size_bytes * 100, 1) if size_bytes else 0,
            }
        )
    overview_out = dict(overview)
    overview_out["size_display"] = _format_size(int(overview.get("size_bytes") or 0))
    overview_out["data_size_display"] = _format_size(int(overview.get("data_bytes") or 0))
    overview_out["index_size_display"] = _format_size(int(overview.get("index_bytes") or 0))
    overview_out["memory_share_display"] = overview.get("memory_share_display") or _format_size(
        int(overview.get("memory_share_bytes") or 0)
    )
    overview_out["data_free_display"] = overview.get("data_free_display") or _format_size(
        int(overview.get("data_free_bytes") or 0)
    )
    available_host = host_stats if host_stats and host_stats.get("available") else None
    return {
        "database": overview_out,
        "tables": formatted_tables,
        "table_total": len(db_tables),
        "collected_at": collected_at,
        "monitors": {
            "storage": {
                "data_bytes": int(overview.get("data_bytes") or 0),
                "index_bytes": int(overview.get("index_bytes") or 0),
                "size_bytes": int(overview.get("size_bytes") or 0),
                "data_free_bytes": int(overview.get("data_free_bytes") or 0),
                "data_ratio_percent": float(overview.get("data_ratio_percent") or 0),
                "index_ratio_percent": float(overview.get("index_ratio_percent") or 0),
            },
            "sessions": {
                "connections": int(overview.get("connection_count") or 0),
                "running": int(overview.get("running_sessions") or 0),
                "sleeping": int(overview.get("sleeping_sessions") or 0),
            },
            "compute": {
                "cpu_share_percent": float(overview.get("cpu_share_percent") or 0),
                "compute_share_percent": float(overview.get("compute_share_percent") or 0),
                "memory_share_bytes": int(overview.get("memory_share_bytes") or 0),
                "memory_share_percent": float(overview.get("memory_share_percent") or 0),
                "qps": float(overview.get("qps") or 0),
                "rows_examined_per_sec": float(overview.get("rows_examined_per_sec") or 0),
                "rows_sent_per_sec": float(overview.get("rows_sent_per_sec") or 0),
                "avg_latency_ms": float(overview.get("avg_latency_ms") or 0),
                "errors_per_sec": float(overview.get("errors_per_sec") or 0),
                "no_index_used_per_sec": float(overview.get("no_index_used_per_sec") or 0),
            },
        },
        "instance_context": {
            "mysql_connections": (mysql_stats or {}).get("connections"),
            "mysql_memory": (mysql_stats or {}).get("memory"),
            "mysql_storage": (mysql_stats or {}).get("storage"),
            "mysql_performance": (mysql_stats or {}).get("performance"),
            "host_stats": available_host,
        },
        "resource_notes": [
            "内存按各库数据体积占业务数据总量的比例，近似分摊 InnoDB Buffer Pool。",
            "算力按连接数与非 Sleep 活跃会话各占一半权重近似分摊，再乘容器 CPU%。",
            "QPS / 扫描行速率来自 performance_schema 累计值的采样差分；首次采样可能为 0。",
        ],
    }


def upsert_database_inventory_sizes(databases: list[dict], seen_at=None) -> None:
    seen_at = seen_at or timezone.now()
    for db in databases:
        DatabaseInventory.objects.update_or_create(
            name=db["name"],
            defaults={
                "charset": db.get("charset") or "",
                "collation": db.get("collation") or "",
                "table_count": int(db.get("table_count") or 0),
                "row_count_est": int(db.get("row_count") or 0),
                "data_bytes": int(db.get("data_bytes") or 0),
                "index_bytes": int(db.get("index_bytes") or 0),
                "size_bytes": int(db.get("size_bytes") or 0),
                "connection_count": int(db.get("connection_count") or 0),
                "memory_share_bytes": int(db.get("memory_share_bytes") or 0),
                "compute_share_percent": float(db.get("compute_share_percent") or 0),
                "cpu_share_percent": float(db.get("cpu_share_percent") or 0),
                "primary_engine": db.get("primary_engine") or "-",
                "engines_json": db.get("engines") or [],
                "users_json": db.get("users") or [],
                "last_seen_at": seen_at,
            },
        )


def _fetch_create_sql(cur, table: str) -> str:
    safe = table.replace("`", "``")
    cur.execute(f"SHOW CREATE TABLE `{safe}`")
    row = cur.fetchone()
    return row[1] if row else ""


def _fetch_columns(cur, database: str, table: str) -> list[dict]:
    cur.execute(
        """
        SELECT
            COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY,
            COLUMN_DEFAULT, EXTRA, COLUMN_COMMENT, ORDINAL_POSITION
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
        """,
        (database, table),
    )
    return [
        {
            "name": r[0],
            "type": r[1],
            "nullable": r[2] == "YES",
            "key": r[3] or "",
            "default": r[4],
            "extra": r[5] or "",
            "comment": r[6] or "",
            "ordinal_position": int(r[7] or 0),
        }
        for r in cur.fetchall()
    ]


def _fetch_indexes(cur, table: str) -> list[dict]:
    safe = table.replace("`", "``")
    cur.execute(f"SHOW INDEX FROM `{safe}`")
    indexes: dict[str, dict] = {}
    for row in cur.fetchall():
        key_name = row[2]
        entry = indexes.setdefault(
            key_name,
            {
                "name": key_name,
                "unique": not bool(row[1]),
                "type": row[10] or "BTREE",
                "columns": [],
            },
        )
        entry["columns"].append(row[4])
    return list(indexes.values())


def sync_schema_inventory() -> dict:
    """全量同步结构库存并记录 DDL 变更。"""
    now = timezone.now()
    databases = list_business_databases()
    table_sizes = collect_table_sizes()
    sizes_by_db: dict[str, list[dict]] = {}
    for table in table_sizes:
        sizes_by_db.setdefault(table["database"], []).append(table)

    created = altered = dropped = 0
    synced_tables = 0

    live_db_names = {db["name"] for db in databases}
    for stale in DatabaseInventory.objects.exclude(name__in=live_db_names):
        for table in stale.tables.all():
            SchemaChangeEvent.objects.create(
                database_name=stale.name,
                table_name=table.name,
                change_type=SchemaChangeEvent.CHANGE_DROPPED,
                structure_hash_before=table.structure_hash,
                create_sql_before=table.create_sql,
                unified_diff=make_unified_diff(table.create_sql, "", table.name),
            )
            dropped += 1
        stale.delete()

    for db in databases:
        db_obj, _ = DatabaseInventory.objects.update_or_create(
            name=db["name"],
            defaults={
                "charset": db.get("charset") or "",
                "collation": db.get("collation") or "",
                "table_count": int(db.get("table_count") or 0),
                "row_count_est": int(db.get("row_count") or 0),
                "data_bytes": int(db.get("data_bytes") or 0),
                "index_bytes": int(db.get("index_bytes") or 0),
                "size_bytes": int(db.get("size_bytes") or 0),
                "connection_count": int(db.get("connection_count") or 0),
                "primary_engine": db.get("primary_engine") or "-",
                "engines_json": db.get("engines") or [],
                "users_json": db.get("users") or [],
                "last_seen_at": now,
                "schema_synced_at": now,
            },
        )
        live_tables = {t["name"]: t for t in sizes_by_db.get(db["name"], [])}
        existing = {t.name: t for t in db_obj.tables.all()}

        for missing_name, old in existing.items():
            if missing_name in live_tables:
                continue
            SchemaChangeEvent.objects.create(
                database_name=db["name"],
                table_name=missing_name,
                change_type=SchemaChangeEvent.CHANGE_DROPPED,
                structure_hash_before=old.structure_hash,
                create_sql_before=old.create_sql,
                unified_diff=make_unified_diff(old.create_sql, "", missing_name),
            )
            old.delete()
            dropped += 1

        try:
            with mysql_cursor(database=db["name"]) as cur:
                for table_name, size_meta in live_tables.items():
                    try:
                        create_sql = _fetch_create_sql(cur, table_name)
                    except Exception as exc:
                        logger.warning("SHOW CREATE TABLE %s.%s failed: %s", db["name"], table_name, exc)
                        create_sql = ""
                    hash_value = structure_hash(create_sql)
                    old = existing.get(table_name)
                    change_type = None
                    before_sql = ""
                    before_hash = ""
                    if old is None:
                        change_type = SchemaChangeEvent.CHANGE_CREATED
                    elif old.structure_hash and hash_value and old.structure_hash != hash_value:
                        change_type = SchemaChangeEvent.CHANGE_ALTERED
                        before_sql = old.create_sql
                        before_hash = old.structure_hash
                    elif old is not None and not old.structure_hash and hash_value:
                        # 首次补齐哈希，不记变更
                        pass

                    if change_type:
                        SchemaChangeEvent.objects.create(
                            database_name=db["name"],
                            table_name=table_name,
                            change_type=change_type,
                            structure_hash_before=before_hash,
                            structure_hash_after=hash_value,
                            create_sql_before=before_sql,
                            create_sql_after=create_sql,
                            unified_diff=make_unified_diff(before_sql, create_sql, table_name),
                        )
                        if change_type == SchemaChangeEvent.CHANGE_CREATED:
                            created += 1
                        else:
                            altered += 1

                    table_obj, _ = TableInventory.objects.update_or_create(
                        database=db_obj,
                        name=table_name,
                        defaults={
                            "engine": size_meta.get("engine") or "-",
                            "row_count_est": int(size_meta.get("row_count") or 0),
                            "data_bytes": int(size_meta.get("data_bytes") or 0),
                            "index_bytes": int(size_meta.get("index_bytes") or 0),
                            "size_bytes": int(size_meta.get("size_bytes") or 0),
                            "comment": size_meta.get("comment") or "",
                            "structure_hash": hash_value,
                            "create_sql": create_sql,
                            "last_seen_at": now,
                        },
                    )

                    columns = _fetch_columns(cur, db["name"], table_name)
                    indexes = _fetch_indexes(cur, table_name)
                    with transaction.atomic():
                        table_obj.columns.all().delete()
                        ColumnInventory.objects.bulk_create(
                            [
                                ColumnInventory(
                                    table=table_obj,
                                    name=col["name"],
                                    column_type=col["type"],
                                    nullable=col["nullable"],
                                    column_key=col["key"],
                                    column_default=col["default"],
                                    extra=col["extra"],
                                    comment=col["comment"],
                                    ordinal_position=col["ordinal_position"],
                                )
                                for col in columns
                            ]
                        )
                        table_obj.indexes.all().delete()
                        IndexInventory.objects.bulk_create(
                            [
                                IndexInventory(
                                    table=table_obj,
                                    name=idx["name"],
                                    unique=idx["unique"],
                                    index_type=idx["type"],
                                    columns_json=idx["columns"],
                                )
                                for idx in indexes
                            ]
                        )
                    synced_tables += 1
        except MySQLClientError as exc:
            logger.warning("schema sync for %s failed: %s", db["name"], exc)

    return {
        "databases": len(databases),
        "tables": synced_tables,
        "created": created,
        "altered": altered,
        "dropped": dropped,
        "synced_at": timezone.localtime(now).isoformat(),
    }
