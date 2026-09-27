"""业务库浏览：表结构、只读 SQL、Excel 导出"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from django.conf import settings
from django.http import HttpResponse

from apps.core.mysql_client import MySQLClientError, _validate_db_name, mysql_cursor

_FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|GRANT|REVOKE|"
    r"CALL|LOAD|OUTFILE|INFILE|LOAD_FILE|SLEEP|BENCHMARK|SET\s+GLOBAL)\b",
    re.I,
)
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,64}$")


def _validate_table_name(name: str) -> None:
    if not name or not _TABLE_NAME_RE.match(name):
        raise ValueError("表名无效")


def _ensure_business_database(name: str) -> None:
    _validate_db_name(name)
    if not database_exists(name):
        raise ValueError(f"数据库 {name} 不存在")


def database_exists(name: str) -> bool:
    from apps.core.mysql_client import database_exists as _exists

    return _exists(name)


def get_database_overview(name: str) -> dict:
    _ensure_business_database(name)
    from apps.core.mysql_client import list_business_databases

    for db in list_business_databases():
        if db["name"] == name:
            return db
    raise ValueError(f"数据库 {name} 不存在")


def list_database_tables(database: str) -> list[dict]:
    _ensure_business_database(database)
    sql = """
        SELECT
            TABLE_NAME,
            ENGINE,
            TABLE_ROWS,
            DATA_LENGTH,
            INDEX_LENGTH,
            (DATA_LENGTH + INDEX_LENGTH) AS size_bytes,
            TABLE_COMMENT
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
    """
    with mysql_cursor(database=database) as cur:
        cur.execute(sql, (database,))
        rows = cur.fetchall()
    return [
        {
            "name": r[0],
            "engine": r[1] or "-",
            "row_count": int(r[2] or 0),
            "data_bytes": int(r[3] or 0),
            "index_bytes": int(r[4] or 0),
            "size_bytes": int(r[5] or 0),
            "comment": r[6] or "",
        }
        for r in rows
    ]


def get_table_structure(database: str, table: str) -> dict:
    _ensure_business_database(database)
    _validate_table_name(table)
    safe_table = table.replace("`", "``")

    columns_sql = """
        SELECT
            COLUMN_NAME,
            COLUMN_TYPE,
            IS_NULLABLE,
            COLUMN_KEY,
            COLUMN_DEFAULT,
            EXTRA,
            COLUMN_COMMENT
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
    """
    with mysql_cursor(database=database) as cur:
        cur.execute(columns_sql, (database, table))
        columns = [
            {
                "name": r[0],
                "type": r[1],
                "nullable": r[2] == "YES",
                "key": r[3] or "",
                "default": r[4],
                "extra": r[5] or "",
                "comment": r[6] or "",
            }
            for r in cur.fetchall()
        ]
        if not columns:
            raise ValueError(f"表 {table} 不存在")

        cur.execute(f"SHOW INDEX FROM `{safe_table}`")
        index_rows = cur.fetchall()
        indexes: dict[str, dict] = {}
        for row in index_rows:
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

        create_sql = ""
        try:
            cur.execute(f"SHOW CREATE TABLE `{safe_table}`")
            create_row = cur.fetchone()
            if create_row:
                create_sql = create_row[1]
        except MySQLClientError:
            pass

    return {
        "database": database,
        "table": table,
        "columns": columns,
        "indexes": list(indexes.values()),
        "create_sql": create_sql,
    }


def validate_readonly_sql(sql: str) -> str:
    text = (sql or "").strip()
    if not text:
        raise ValueError("SQL 不能为空")
    if ";" in text.rstrip(";"):
        raise ValueError("不支持多条 SQL 语句")
    text = text.rstrip(";").strip()
    if not re.match(r"^(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN)\b", text, re.I):
        raise ValueError("仅允许 SELECT、SHOW、DESCRIBE、EXPLAIN 语句")
    if _FORBIDDEN_SQL.search(text):
        raise ValueError("SQL 含不允许的关键字")
    return text


def execute_readonly_query(database: str, sql: str, limit: int = 500) -> dict:
    _ensure_business_database(database)
    safe_sql = validate_readonly_sql(sql)
    limit = max(1, min(int(limit), 5000))

    wrapped = safe_sql
    if re.match(r"^SELECT\b", safe_sql, re.I) and not re.search(r"\bLIMIT\b", safe_sql, re.I):
        wrapped = f"{safe_sql} LIMIT {limit + 1}"

    with mysql_cursor(database=database) as cur:
        cur.execute(wrapped)
        if cur.description:
            columns = [col[0] for col in cur.description]
            raw_rows = cur.fetchmany(limit + 1)
            truncated = len(raw_rows) > limit
            if truncated:
                raw_rows = raw_rows[:limit]
            rows = [[_serialize_cell(v) for v in row] for row in raw_rows]
            return {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "limit": limit,
            }
        return {"columns": [], "rows": [], "row_count": 0, "truncated": False, "limit": limit}


def build_table_select_sql(database: str, table: str, limit: int = 5000) -> str:
    _ensure_business_database(database)
    _validate_table_name(table)
    safe_table = table.replace("`", "``")
    limit = max(1, min(int(limit), 5000))
    return f"SELECT * FROM `{safe_table}` LIMIT {limit}"


def _serialize_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, bool, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def query_result_to_excel(columns: list[str], rows: list[list[Any]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(columns)
    for row in rows:
        ws.append([_excel_cell(v) for v in row])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _excel_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (int, float, bool, str)):
        return value
    return str(value)


def export_query_to_excel_response(
    database: str,
    sql: str,
    limit: int = 5000,
    filename: str = "export.xlsx",
) -> HttpResponse:
    result = execute_readonly_query(database, sql, limit=limit)
    if not result["columns"]:
        raise ValueError("查询无结果列，无法导出")
    content = query_result_to_excel(result["columns"], result["rows"])
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
