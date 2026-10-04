"""读库存与结构变更，供 API 使用。"""

from __future__ import annotations

from apps.databases.models import DatabaseInventory, SchemaChangeEvent, TableInventory


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def serialize_database(db: DatabaseInventory) -> dict:
    return {
        "name": db.name,
        "charset": db.charset,
        "collation": db.collation,
        "table_count": db.table_count,
        "row_count": db.row_count_est,
        "data_bytes": db.data_bytes,
        "index_bytes": db.index_bytes,
        "size_bytes": db.size_bytes,
        "connection_count": db.connection_count,
        "memory_share_bytes": db.memory_share_bytes,
        "memory_share_display": _format_size(db.memory_share_bytes),
        "compute_share_percent": db.compute_share_percent,
        "cpu_share_percent": db.cpu_share_percent,
        "primary_engine": db.primary_engine,
        "engines": db.engines_json or [],
        "users": db.users_json or [],
        "size_display": _format_size(db.size_bytes),
        "data_size_display": _format_size(db.data_bytes),
        "index_size_display": _format_size(db.index_bytes),
        "last_seen_at": db.last_seen_at.isoformat() if db.last_seen_at else None,
        "schema_synced_at": db.schema_synced_at.isoformat() if db.schema_synced_at else None,
    }


def serialize_table(table: TableInventory, db_size_bytes: int = 0) -> dict:
    size_bytes = table.size_bytes
    total = db_size_bytes or 1
    return {
        "name": table.name,
        "engine": table.engine,
        "row_count": table.row_count_est,
        "data_bytes": table.data_bytes,
        "index_bytes": table.index_bytes,
        "size_bytes": size_bytes,
        "comment": table.comment,
        "size_display": _format_size(size_bytes),
        "data_size_display": _format_size(table.data_bytes),
        "index_size_display": _format_size(table.index_bytes),
        "size_percent": round(size_bytes / total * 100, 2) if total else 0,
        "data_percent": round(table.data_bytes / size_bytes * 100, 1) if size_bytes else 0,
        "index_percent": round(table.index_bytes / size_bytes * 100, 1) if size_bytes else 0,
        "structure_hash": table.structure_hash,
    }


def get_inventory_database_detail(name: str) -> dict | None:
    try:
        db = DatabaseInventory.objects.get(name=name)
    except DatabaseInventory.DoesNotExist:
        return None
    tables = [
        serialize_table(table, db.size_bytes)
        for table in db.tables.all().order_by("-size_bytes", "name")
    ]
    return {
        "database": serialize_database(db),
        "tables": tables,
        "table_total": len(tables),
        "collected_at": db.last_seen_at.isoformat() if db.last_seen_at else None,
        "resource_notes": [
            "内存按各库数据体积占业务数据总量的比例，近似分摊 InnoDB Buffer Pool。",
            "算力按连接数与非 Sleep 活跃会话各占一半权重近似分摊，再乘容器 CPU%。",
        ],
        "from_inventory": True,
    }


def get_inventory_table_structure(database: str, table: str) -> dict | None:
    try:
        table_obj = TableInventory.objects.select_related("database").get(
            database__name=database,
            name=table,
        )
    except TableInventory.DoesNotExist:
        return None
    columns = [
        {
            "name": col.name,
            "type": col.column_type,
            "nullable": col.nullable,
            "key": col.column_key,
            "default": col.column_default,
            "extra": col.extra,
            "comment": col.comment,
        }
        for col in table_obj.columns.all()
    ]
    indexes = [
        {
            "name": idx.name,
            "unique": idx.unique,
            "type": idx.index_type,
            "columns": idx.columns_json or [],
        }
        for idx in table_obj.indexes.all()
    ]
    recent_changes = [
        serialize_schema_change(event)
        for event in SchemaChangeEvent.objects.filter(
            database_name=database,
            table_name=table,
        )[:10]
    ]
    return {
        "database": database,
        "table": table,
        "columns": columns,
        "indexes": indexes,
        "create_sql": table_obj.create_sql,
        "structure_hash": table_obj.structure_hash,
        "recent_changes": recent_changes,
        "from_inventory": True,
    }


def serialize_schema_change(event: SchemaChangeEvent) -> dict:
    return {
        "id": event.id,
        "database_name": event.database_name,
        "table_name": event.table_name,
        "change_type": event.change_type,
        "change_type_display": event.get_change_type_display(),
        "structure_hash_before": event.structure_hash_before,
        "structure_hash_after": event.structure_hash_after,
        "create_sql_before": event.create_sql_before,
        "create_sql_after": event.create_sql_after,
        "unified_diff": event.unified_diff,
        "detected_at": event.detected_at.isoformat() if event.detected_at else None,
    }


def list_schema_changes(database: str, table: str | None = None, limit: int = 50) -> list[dict]:
    qs = SchemaChangeEvent.objects.filter(database_name=database)
    if table:
        qs = qs.filter(table_name=table)
    return [serialize_schema_change(event) for event in qs[:limit]]
