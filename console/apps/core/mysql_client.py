from __future__ import annotations

import re
import time
from contextlib import contextmanager
from typing import Any, Generator, Optional

import MySQLdb
from django.conf import settings


class MySQLClientError(Exception):
    pass


def _connect(
    user: str = "root",
    password: Optional[str] = None,
    database: Optional[str] = None,
    max_retries: int = 10,
    retry_delay: float = 2.0,
) -> MySQLdb.Connection:
    password = password if password is not None else settings.MYSQL_ROOT_PASSWORD
    last_exc: Exception | None = None
    # 2002/2003: MySQL 尚未启动或网络未就绪；2013: 握手阶段连接中断
    retry_errnos = {2002, 2003, 2013}

    for attempt in range(max_retries):
        try:
            return MySQLdb.connect(
                host=settings.MYSQL_HOST,
                port=settings.MYSQL_PORT,
                user=user,
                passwd=password,
                db=database or "",
                charset="utf8mb4",
                connect_timeout=10,
            )
        except MySQLdb.Error as exc:
            last_exc = exc
            errno = exc.args[0] if exc.args else None
            if errno in retry_errnos and attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            raise MySQLClientError(str(exc)) from exc

    raise MySQLClientError(str(last_exc)) from last_exc


@contextmanager
def mysql_cursor(
    user: str = "root",
    password: Optional[str] = None,
    database: Optional[str] = None,
) -> Generator[Any, None, None]:
    conn = _connect(user=user, password=password, database=database)
    try:
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping_mysql() -> dict:
    with mysql_cursor() as cur:
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]
        cur.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
        uptime = cur.fetchone()[1]
    return {"version": version, "uptime_seconds": int(uptime), "connected": True}


def _int_val(value: str | None, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _format_uptime(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}秒"
    days, rem = divmod(max(seconds, 0), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes or not parts:
        parts.append(f"{minutes}分钟")
    return "".join(parts)


def get_mysql_system_stats() -> dict:
    """采集 MySQL 连接、内存、存储与性能指标"""
    status_keys = [
        "Threads_connected",
        "Threads_running",
        "Max_used_connections",
        "Uptime",
        "Queries",
        "Slow_queries",
        "Innodb_buffer_pool_bytes_data",
        "Innodb_buffer_pool_bytes_dirty",
        "Bytes_received",
        "Bytes_sent",
        "Open_tables",
        "Aborted_connects",
    ]
    variable_keys = [
        "max_connections",
        "innodb_buffer_pool_size",
        "datadir",
        "version",
    ]

    with mysql_cursor() as cur:
        cur.execute(
            "SHOW GLOBAL STATUS WHERE Variable_name IN ({})".format(
                ",".join(["%s"] * len(status_keys))
            ),
            status_keys,
        )
        status = {name: val for name, val in cur.fetchall()}

        cur.execute(
            "SHOW GLOBAL VARIABLES WHERE Variable_name IN ({})".format(
                ",".join(["%s"] * len(variable_keys))
            ),
            variable_keys,
        )
        variables = {name: val for name, val in cur.fetchall()}

        cur.execute(
            """
            SELECT
                COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0) AS total_bytes,
                COALESCE(SUM(
                    CASE WHEN TABLE_SCHEMA IN ('mysql', 'information_schema', 'performance_schema', 'sys')
                    THEN 0 ELSE DATA_LENGTH + INDEX_LENGTH END
                ), 0) AS business_bytes
            FROM information_schema.TABLES
            """
        )
        total_bytes, business_bytes = cur.fetchone()

        binlog_bytes = 0
        try:
            cur.execute("SHOW BINARY LOGS")
            binlog_bytes = sum(_int_val(row[1]) for row in cur.fetchall())
        except MySQLdb.Error:
            pass

    uptime = _int_val(status.get("Uptime"))
    threads_connected = _int_val(status.get("Threads_connected"))
    max_connections = _int_val(variables.get("max_connections"), 151)
    buffer_pool_size = _int_val(variables.get("innodb_buffer_pool_size"))
    buffer_pool_used = _int_val(status.get("Innodb_buffer_pool_bytes_data")) + _int_val(
        status.get("Innodb_buffer_pool_bytes_dirty")
    )

    return {
        "uptime_seconds": uptime,
        "uptime_display": _format_uptime(uptime),
        "connections": {
            "current": threads_connected,
            "running": _int_val(status.get("Threads_running")),
            "max_used": _int_val(status.get("Max_used_connections")),
            "max_limit": max_connections,
            "usage_percent": round(threads_connected / max_connections * 100, 1)
            if max_connections
            else 0,
            "aborted": _int_val(status.get("Aborted_connects")),
        },
        "memory": {
            "innodb_buffer_pool_size_bytes": buffer_pool_size,
            "innodb_buffer_pool_used_bytes": min(buffer_pool_used, buffer_pool_size or buffer_pool_used),
            "innodb_buffer_pool_usage_percent": round(buffer_pool_used / buffer_pool_size * 100, 1)
            if buffer_pool_size
            else 0,
        },
        "storage": {
            "data_total_bytes": int(total_bytes or 0),
            "data_business_bytes": int(business_bytes or 0),
            "binlog_bytes": binlog_bytes,
            "datadir": variables.get("datadir", ""),
        },
        "performance": {
            "queries_total": _int_val(status.get("Queries")),
            "slow_queries": _int_val(status.get("Slow_queries")),
            "bytes_received": _int_val(status.get("Bytes_received")),
            "bytes_sent": _int_val(status.get("Bytes_sent")),
            "open_tables": _int_val(status.get("Open_tables")),
        },
    }


def list_business_databases() -> list[dict]:
    system_dbs = ("information_schema", "performance_schema", "mysql", "sys")

    schema_sql = """
        SELECT
            s.SCHEMA_NAME,
            s.DEFAULT_CHARACTER_SET_NAME,
            s.DEFAULT_COLLATION_NAME
        FROM information_schema.SCHEMATA s
        WHERE s.SCHEMA_NAME NOT IN (%s, %s, %s, %s)
        ORDER BY s.SCHEMA_NAME
    """
    table_stats_sql = """
        SELECT
            TABLE_SCHEMA,
            COALESCE(ENGINE, 'Unknown') AS engine,
            COUNT(*) AS table_count,
            COALESCE(SUM(TABLE_ROWS), 0) AS row_count,
            COALESCE(SUM(DATA_LENGTH), 0) AS data_bytes,
            COALESCE(SUM(INDEX_LENGTH), 0) AS index_bytes,
            COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0) AS size_bytes
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA NOT IN (%s, %s, %s, %s)
          AND TABLE_TYPE = 'BASE TABLE'
        GROUP BY TABLE_SCHEMA, ENGINE
        ORDER BY TABLE_SCHEMA, size_bytes DESC
    """
    conn_sql = """
        SELECT DB AS db_name, COUNT(*) AS connection_count
        FROM information_schema.processlist
        WHERE DB IS NOT NULL AND DB != ''
        GROUP BY DB
    """
    users_sql = """
        SELECT Db, GROUP_CONCAT(DISTINCT CONCAT(`User`, '@', `Host`) ORDER BY `User`, `Host`) AS users
        FROM mysql.db
        GROUP BY Db
    """

    with mysql_cursor() as cur:
        cur.execute(schema_sql, system_dbs)
        schemas = {
            name: {"charset": charset, "collation": collation}
            for name, charset, collation in cur.fetchall()
        }

        cur.execute(table_stats_sql, system_dbs)
        table_rows = cur.fetchall()

        conn_map: dict[str, int] = {}
        try:
            cur.execute(conn_sql)
            conn_map = {name: int(cnt) for name, cnt in cur.fetchall()}
        except MySQLdb.Error:
            pass

        users_map: dict[str, list[str]] = {}
        try:
            cur.execute(users_sql)
            for db_name, users in cur.fetchall():
                users_map[db_name] = users.split(",") if users else []
        except MySQLdb.Error:
            pass

    stats_by_db: dict[str, dict] = {}
    for schema, engine, tcount, rows, data_b, index_b, size_b in table_rows:
        entry = stats_by_db.setdefault(
            schema,
            {
                "table_count": 0,
                "row_count": 0,
                "data_bytes": 0,
                "index_bytes": 0,
                "size_bytes": 0,
                "engines": [],
            },
        )
        entry["table_count"] += int(tcount or 0)
        entry["row_count"] += int(rows or 0)
        entry["data_bytes"] += int(data_b or 0)
        entry["index_bytes"] += int(index_b or 0)
        entry["size_bytes"] += int(size_b or 0)
        entry["engines"].append(
            {
                "engine": engine,
                "table_count": int(tcount or 0),
                "row_count": int(rows or 0),
                "size_bytes": int(size_b or 0),
            }
        )

    databases = []
    for name in sorted(schemas.keys()):
        meta = schemas[name]
        stats = stats_by_db.get(
            name,
            {
                "table_count": 0,
                "row_count": 0,
                "data_bytes": 0,
                "index_bytes": 0,
                "size_bytes": 0,
                "engines": [],
            },
        )
        engines = stats["engines"]
        primary_engine = engines[0]["engine"] if engines else "-"
        databases.append(
            {
                "name": name,
                "charset": meta["charset"],
                "collation": meta["collation"],
                "table_count": stats["table_count"],
                "row_count": stats["row_count"],
                "data_bytes": stats["data_bytes"],
                "index_bytes": stats["index_bytes"],
                "size_bytes": stats["size_bytes"],
                "primary_engine": primary_engine,
                "engines": engines,
                "connection_count": conn_map.get(name, 0),
                "users": users_map.get(name, []),
            }
        )
    return databases


def get_database_users(database: str) -> list[str]:
    sql = """
        SELECT CONCAT(`User`, '@', `Host`) AS user_host
        FROM mysql.db
        WHERE Db = %s
        GROUP BY `User`, `Host`
        ORDER BY `User`, `Host`
    """
    with mysql_cursor() as cur:
        cur.execute(sql, (database,))
        return [row[0] for row in cur.fetchall()]


def list_mysql_users(include_system: bool = False) -> list[dict]:
    sql = """
        SELECT User, Host, account_locked, password_expired
        FROM mysql.user
        ORDER BY User, Host
    """
    system_users = {"mysql.infoschema", "mysql.session", "mysql.sys", "debian-sys-maint"}
    with mysql_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    users = []
    for user, host, locked, expired in rows:
        if not include_system and user in system_users:
            continue
        grants = get_user_grants(user, host)
        grant_tree = build_grant_tree(grants)
        users.append(
            {
                "user": user,
                "host": host,
                "locked": bool(locked),
                "expired": bool(expired),
                "grants_summary": summarize_grants(grants),
                "grants_raw": grants,
                "grant_tree": grant_tree,
                "database_count": sum(1 for node in grant_tree if node.get("scope") == "database"),
                "is_system": user in {"root"} or user.startswith("mysql."),
            }
        )
    return users


def get_user_grants(user: str, host: str) -> list[str]:
    with mysql_cursor() as cur:
        cur.execute(f"SHOW GRANTS FOR {_mysql_account(user, host)}")
        return [row[0] for row in cur.fetchall()]


def summarize_grants(grants: list[str]) -> str:
    if not grants:
        return "无权限"
    if any("ALL PRIVILEGES" in g and " ON *.*" in g for g in grants):
        return "全局 ALL PRIVILEGES"
    if any("ALL PRIVILEGES" in g for g in grants):
        dbs = _extract_grant_databases(grants)
        if dbs:
            return f"ALL · {', '.join(dbs[:5])}"
        return "ALL PRIVILEGES"
    dbs = _extract_grant_databases(grants)
    return ", ".join(dbs[:5]) or "见详情"


def _extract_grant_databases(grants: list[str]) -> list[str]:
    dbs = set()
    for g in grants:
        parsed = _parse_grant_statement(g)
        if not parsed:
            continue
        if parsed["scope"] == "global":
            dbs.add("*.*")
        elif parsed["database"]:
            dbs.add(parsed["database"])
    return sorted(dbs)


def _parse_grant_statement(statement: str) -> dict | None:
    """解析 SHOW GRANTS 单行，返回 scope/database/table/privileges。"""
    text = (statement or "").strip()
    if not text.upper().startswith("GRANT "):
        return None
    # GRANT ... ON <target> TO ...
    match = re.search(
        r"^GRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s+",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    priv_part = re.sub(r"\s+", " ", match.group(1)).strip()
    target = match.group(2).strip()
    with_grant = "WITH GRANT OPTION" in text.upper()

    scope = "database"
    database = ""
    table = ""
    if target == "*.*":
        scope = "global"
        database = "*"
        table = "*"
    else:
        # `db`.* | `db`.`tbl` | db.* | db.tbl
        target_match = re.match(
            r"^(?:`([^`]+)`|([A-Za-z0-9$_]+))\.(?:\*|`([^`]+)`|([A-Za-z0-9$_]+))$",
            target,
        )
        if not target_match:
            return {
                "scope": "other",
                "database": "",
                "table": "",
                "privileges": priv_part,
                "privilege_list": [p.strip() for p in priv_part.split(",") if p.strip()],
                "with_grant_option": with_grant,
                "raw": text,
                "level_label": _privilege_level_label(priv_part),
            }
        database = target_match.group(1) or target_match.group(2) or ""
        table_name = target_match.group(3) or target_match.group(4)
        if table_name:
            scope = "table"
            table = table_name
        else:
            scope = "database"
            table = "*"

    return {
        "scope": scope,
        "database": database,
        "table": table,
        "privileges": priv_part,
        "privilege_list": [p.strip() for p in priv_part.split(",") if p.strip()],
        "with_grant_option": with_grant,
        "raw": text,
        "level_label": _privilege_level_label(priv_part),
    }


def _privilege_level_label(priv_part: str) -> str:
    upper = priv_part.upper()
    if "ALL PRIVILEGES" in upper or upper.strip() == "ALL":
        return "全部权限"
    tokens = {p.strip().upper() for p in priv_part.split(",") if p.strip()}
    read_only = {"SELECT"}
    read_write = {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "DROP",
        "ALTER",
        "INDEX",
        "TRIGGER",
        "REFERENCES",
    }
    if tokens and tokens <= read_only:
        return "只读"
    if tokens and tokens <= read_write and ("INSERT" in tokens or "UPDATE" in tokens or "DELETE" in tokens):
        return "读写"
    if tokens == {"USAGE"}:
        return "USAGE"
    return "自定义"


def build_grant_tree(grants: list[str]) -> list[dict]:
    """把 grants 归并为前端树：全局节点 + 各数据库节点（含表级子节点）。"""
    global_nodes: list[dict] = []
    db_map: dict[str, dict] = {}

    for statement in grants:
        parsed = _parse_grant_statement(statement)
        if not parsed:
            continue
        if parsed["scope"] == "global":
            global_nodes.append(
                {
                    "id": f"global:{parsed['privileges']}",
                    "label": f"全局 *.* · {parsed['level_label']}",
                    "scope": "global",
                    "database": "*",
                    "table": "*",
                    "privileges": parsed["privileges"],
                    "privilege_list": parsed["privilege_list"],
                    "level_label": parsed["level_label"],
                    "with_grant_option": parsed["with_grant_option"],
                    "raw": parsed["raw"],
                    "children": [],
                }
            )
            continue

        db_name = parsed["database"] or "(unknown)"
        node = db_map.setdefault(
            db_name,
            {
                "id": f"db:{db_name}",
                "label": db_name,
                "scope": "database",
                "database": db_name,
                "table": "",
                "privileges": "",
                "privilege_list": [],
                "level_label": "",
                "with_grant_option": False,
                "raw": "",
                "children": [],
            },
        )
        if parsed["scope"] == "database":
            node["privileges"] = parsed["privileges"]
            node["privilege_list"] = parsed["privilege_list"]
            node["level_label"] = parsed["level_label"]
            node["with_grant_option"] = parsed["with_grant_option"]
            node["raw"] = parsed["raw"]
            node["label"] = f"{db_name} · {parsed['level_label']}"
        else:
            node["children"].append(
                {
                    "id": f"table:{db_name}.{parsed['table']}:{parsed['privileges']}",
                    "label": f"表 {parsed['table']} · {parsed['level_label']}",
                    "scope": "table",
                    "database": db_name,
                    "table": parsed["table"],
                    "privileges": parsed["privileges"],
                    "privilege_list": parsed["privilege_list"],
                    "level_label": parsed["level_label"],
                    "with_grant_option": parsed["with_grant_option"],
                    "raw": parsed["raw"],
                    "children": [],
                }
            )

    for node in db_map.values():
        if not node["level_label"] and node["children"]:
            node["label"] = f"{node['database']} · 仅表级授权"
            node["level_label"] = "仅表级"
        node["children"].sort(key=lambda item: item.get("table") or item.get("label") or "")

    result = global_nodes + [db_map[name] for name in sorted(db_map.keys())]
    return result


def change_user_password(user: str, host: str, new_password: str) -> None:
    with mysql_cursor() as cur:
        cur.execute(
            f"ALTER USER {_mysql_account(user, host)} IDENTIFIED BY {_sql_password_literal(cur, new_password)}"
        )
        cur.execute("FLUSH PRIVILEGES")


PRIVILEGE_LEVELS = {
    "all": "ALL PRIVILEGES",
    "read_write": "SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, TRIGGER, REFERENCES",
    "read_only": "SELECT",
}


def _validate_db_name(name: str) -> None:
    if not name or len(name) > 64:
        raise ValueError("数据库名长度须为 1–64")
    if name in settings.SYSTEM_DB_NAMES:
        raise ValueError("不能使用系统保留库名")
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
        raise ValueError("数据库名只能包含字母、数字和下划线")


def _validate_mysql_user(name: str) -> None:
    if not name or len(name) > 32:
        raise ValueError("用户名长度须为 1–32")
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
        raise ValueError("用户名只能包含字母、数字和下划线")


def _validate_host(host: str) -> None:
    if not host or len(host) > 255:
        raise ValueError("Host 无效")
    if not re.match(r"^[%a-zA-Z0-9._-]+$", host):
        raise ValueError("Host 含非法字符")


def _quote_ident(value: str) -> str:
    return value.replace("`", "``").replace("'", "''")


def _mysql_account(user: str, host: str) -> str:
    safe_user = user.replace("'", "''")
    safe_host = host.replace("'", "''")
    return f"'{safe_user}'@'{safe_host}'"


def _sql_password_literal(cur, password: str) -> str:
    literal = cur.connection.literal(password)
    return literal.decode() if isinstance(literal, bytes) else literal


def _resolve_privileges(level: str) -> str:
    if level not in PRIVILEGE_LEVELS:
        raise ValueError(f"未知权限级别: {level}")
    return PRIVILEGE_LEVELS[level]


def database_exists(name: str) -> bool:
    with mysql_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
            (name,),
        )
        return cur.fetchone() is not None


def mysql_user_exists(user: str, host: str) -> bool:
    with mysql_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM mysql.user WHERE User = %s AND Host = %s",
            (user, host),
        )
        return cur.fetchone() is not None


def grant_database_privileges(user: str, host: str, database: str, privilege_level: str = "all") -> None:
    _validate_mysql_user(user)
    _validate_host(host)
    _validate_db_name(database)
    if not database_exists(database):
        raise ValueError(f"数据库 {database} 不存在")
    if not mysql_user_exists(user, host):
        raise ValueError(f"用户 {user}@{host} 不存在")

    privileges = _resolve_privileges(privilege_level)
    safe_db = _quote_ident(database)
    with mysql_cursor() as cur:
        cur.execute(
            f"GRANT {privileges} ON `{safe_db}`.* TO {_mysql_account(user, host)}"
        )
        cur.execute("FLUSH PRIVILEGES")


def create_database(
    name: str,
    charset: str = "utf8mb4",
    collation: str | None = None,
    grant_user: str | None = None,
    grant_host: str | None = None,
    privilege_level: str = "all",
) -> dict:
    _validate_db_name(name)
    if database_exists(name):
        raise ValueError(f"数据库 {name} 已存在")

    if charset not in {"utf8mb4", "utf8", "latin1"}:
        raise ValueError("不支持的字符集")
    if not collation:
        collation = {
            "utf8mb4": "utf8mb4_unicode_ci",
            "utf8": "utf8_general_ci",
            "latin1": "latin1_swedish_ci",
        }[charset]

    safe_name = _quote_ident(name)
    with mysql_cursor() as cur:
        cur.execute(
            f"CREATE DATABASE `{safe_name}` CHARACTER SET %s COLLATE %s",
            (charset, collation),
        )

    granted = False
    if grant_user and grant_host:
        grant_database_privileges(grant_user, grant_host, name, privilege_level)
        granted = True

    return {
        "name": name,
        "charset": charset,
        "collation": collation,
        "granted_to": f"{grant_user}@{grant_host}" if granted else None,
        "privilege_level": privilege_level if granted else None,
    }


def create_business_user(
    user: str,
    host: str,
    password: str,
    database: str | None = None,
    privilege_level: str = "all",
) -> dict:
    _validate_mysql_user(user)
    _validate_host(host)
    if mysql_user_exists(user, host):
        raise ValueError(f"用户 {user}@{host} 已存在")

    if database:
        _validate_db_name(database)
        if not database_exists(database):
            raise ValueError(f"数据库 {database} 不存在")

    with mysql_cursor() as cur:
        cur.execute(
            f"CREATE USER {_mysql_account(user, host)} IDENTIFIED BY {_sql_password_literal(cur, password)}"
        )
        if database:
            privileges = _resolve_privileges(privilege_level)
            safe_db = _quote_ident(database)
            cur.execute(
                f"GRANT {privileges} ON `{safe_db}`.* TO {_mysql_account(user, host)}"
            )
        cur.execute("FLUSH PRIVILEGES")

    return {
        "user": user,
        "host": host,
        "database": database,
        "privilege_level": privilege_level if database else None,
        "granted": bool(database),
    }


def list_system_accounts() -> list[dict]:
    accounts = []
    backup_user = settings.MYSQL_BACKUP_USER or settings.MYSQL_USER
    backup_password_source = "env"

    for role, user, host in [
        ("root", "root", "localhost"),
        ("root", "root", "%"),
        ("backup", backup_user, "%"),
        ("default_user", settings.MYSQL_USER, "%"),
    ]:
        accounts.append(
            {
                "role": role,
                "user": user,
                "host": host,
                "password_source": backup_password_source if role == "backup" else "env",
            }
        )
    return accounts
