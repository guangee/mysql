"""把本机 MySQL 全量初始化后，按 binlog 近实时写到远程 MySQL。"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterator

import MySQLdb
from django.conf import settings
from django.utils import timezone

from apps.core.mysql_client import MySQLClientError, mysql_cursor
from apps.dts.events import clip_sql, event_kind, format_event_time, record_sql_events
from apps.dts.models import DtsTask

_POS_RE = re.compile(
    r"(?:SOURCE_LOG_FILE|MASTER_LOG_FILE)='([^']+)'.*?(?:SOURCE_LOG_POS|MASTER_LOG_POS)=(\d+)",
    re.IGNORECASE,
)
_DEFINER_RE = re.compile(
    r"DEFINER\s*=\s*(?:`[^`]*`@`[^`]*`|'[^']*'@'[^']*')\s*",
    re.IGNORECASE,
)
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_STRUCT_RE = re.compile(r"Table structure for table `([^`]+)`")
_DATA_RE = re.compile(r"Dumping data for table `([^`]+)`")
_LOCK_RE = re.compile(r"^(LOCK\s+TABLES|UNLOCK\s+TABLES)\b", re.IGNORECASE)
_SKIP_QUERY = re.compile(r"^(BEGIN|COMMIT|ROLLBACK|SAVEPOINT|XA\s)", re.IGNORECASE)
_PROGRESS_FIELDS = [
    "table_total",
    "table_done",
    "rows_total",
    "bytes_total",
    "bytes_done",
    "bytes_streamed",
    "progress_percent",
    "current_table",
    "full_phase",
    "structure_done",
    "updated_at",
]


class DtsError(Exception):
    pass


def validate_database_name(name: str) -> str:
    text = (name or "").strip()
    if text in settings.SYSTEM_DB_NAMES or not _DB_NAME_RE.match(text):
        raise DtsError(f"不能同步数据库: {name}")
    return text


def list_source_databases() -> list[str]:
    with mysql_cursor() as cur:
        cur.execute("SHOW DATABASES")
        names = [row[0] for row in cur.fetchall()]
    return [name for name in names if name not in settings.SYSTEM_DB_NAMES]


def inspect_source_database(name: str) -> dict:
    schema = validate_database_name(name)
    with mysql_cursor() as cur:
        cur.execute(
            """
            SELECT TABLE_NAME, TABLE_TYPE,
                   IFNULL(DATA_LENGTH, 0) + IFNULL(INDEX_LENGTH, 0),
                   IFNULL(TABLE_ROWS, 0)
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s
            """,
            (schema,),
        )
        rows = cur.fetchall()
    tables = {}
    table_total = 0
    bytes_total = 0
    rows_total = 0
    for table_name, table_type, size, row_count in rows:
        if _as_text(table_type) != "BASE TABLE":
            continue
        table_total += 1
        nbytes = int(size or 0)
        nrows = max(0, int(row_count or 0))
        tables[_as_text(table_name)] = {"bytes": nbytes, "rows": nrows}
        bytes_total += nbytes
        rows_total += nrows
    return {
        "table_total": table_total,
        "bytes_total": bytes_total,
        "rows_total": rows_total,
        "tables": tables,
    }


def assign_inventory(task, stats: dict, reset_progress: bool = True) -> None:
    task.table_total = int(stats["table_total"])
    task.bytes_total = int(stats["bytes_total"])
    task.rows_total = int(stats["rows_total"])
    if reset_progress:
        task.table_done = 0
        task.bytes_done = 0
        task.bytes_streamed = 0
        task.progress_percent = 0
        task.current_table = ""
        task.full_phase = ""
        task.structure_done = 0


def replace_table_rows(task, stats: dict) -> None:
    from apps.dts.models import DtsTableSync

    DtsTableSync.objects.filter(task=task).delete()
    rows = []
    for index, name in enumerate(sorted(stats["tables"])):
        info = stats["tables"][name]
        rows.append(
            DtsTableSync(
                task=task,
                name=name,
                rows_total=int(info["rows"]),
                bytes_total=int(info["bytes"]),
                phase="pending",
                sort_order=index,
            )
        )
    if rows:
        DtsTableSync.objects.bulk_create(rows, batch_size=500)


def overall_percent(phase: str, structure_done: int, table_total: int, bytes_done: int, bytes_total: int, table_done: int, finished: bool = False) -> int:
    if finished:
        return 100
    total = max(int(table_total), 1)
    if phase == "export":
        if bytes_total <= 0:
            return 1
        return min(8, max(1, int(min(1.0, bytes_done / bytes_total) * 8)))
    structure_ratio = min(1.0, structure_done / total)
    if phase == "structure":
        return min(20, 8 + int(structure_ratio * 12))
    if bytes_total > 0:
        data_ratio = min(1.0, max(0, bytes_done) / bytes_total)
    else:
        data_ratio = min(1.0, table_done / total)
    return min(99, 20 + int(data_ratio * 80))


def progress_percent(table_done: int, table_total: int, bytes_done: int, bytes_total: int, streamed: int, finished: bool = False) -> int:
    if finished:
        return 100
    parts = []
    if table_total > 0:
        parts.append(table_done / table_total)
    if bytes_total > 0:
        moved = max(bytes_done, min(streamed, bytes_total))
        parts.append(moved / bytes_total)
    if not parts:
        return 0
    return min(99, int(max(parts) * 100))


def format_bytes(size: int) -> str:
    value = float(max(0, int(size)))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
        value /= 1024
    return f"{int(size)} B"


def _source_status() -> dict:
    with mysql_cursor() as cur:
        row = None
        try:
            cur.execute("SHOW BINARY LOG STATUS")
            row = cur.fetchone()
        except MySQLdb.Error:
            row = None
        if not row:
            cur.execute("SHOW MASTER STATUS")
            row = cur.fetchone()
        cur.execute(
            "SHOW VARIABLES WHERE Variable_name IN "
            "('log_bin','binlog_format','binlog_row_image','binlog_row_metadata','server_id')"
        )
        variables = {name: value for name, value in cur.fetchall()}
    if not row:
        raise DtsError("读不到源库 binlog 位点")
    return {
        "file": row[0],
        "position": int(row[1]),
        "log_bin": str(variables.get("log_bin", "")).upper(),
        "binlog_format": str(variables.get("binlog_format", "")).upper(),
        "binlog_row_image": str(variables.get("binlog_row_image", "")).upper(),
        "binlog_row_metadata": str(variables.get("binlog_row_metadata", "")).upper(),
        "server_id": str(variables.get("server_id", "")),
    }


def ensure_source_ready() -> dict:
    status = _source_status()
    if status["log_bin"] not in {"ON", "1"}:
        raise DtsError("源库未开启 binlog，无法做增量同步")
    if status["binlog_format"] not in {"ROW", ""}:
        raise DtsError(f"源库 binlog_format={status['binlog_format']}，需要 ROW")
    return status


def test_target_connection(host: str, port: int, user: str, password: str) -> dict:
    try:
        conn = _connect_target(host, port, user, password)
    except MySQLdb.Error as exc:
        raise DtsError(f"连接目标库失败: {exc}") from exc
    try:
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]
        cur.execute("SELECT CURRENT_USER()")
        current = cur.fetchone()[0]
        databases = _list_databases(cur)
    finally:
        conn.close()
    return {"ok": True, "version": version, "current_user": current, "databases": databases}


def _list_databases(cur) -> list[str]:
    cur.execute("SHOW DATABASES")
    names = []
    for row in cur.fetchall():
        name = _as_text(row[0])
        if name in settings.SYSTEM_DB_NAMES or not _DB_NAME_RE.match(name):
            continue
        names.append(name)
    return names


def inspect_target_databases(host: str, port: int, user: str, password: str) -> list[dict]:
    try:
        conn = _connect_target(host, port, user, password)
    except MySQLdb.Error as exc:
        raise DtsError(f"连接目标库失败: {exc}") from exc
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.SCHEMA_NAME,
                   s.DEFAULT_CHARACTER_SET_NAME,
                   COUNT(t.TABLE_NAME),
                   COALESCE(SUM(t.DATA_LENGTH + t.INDEX_LENGTH), 0)
            FROM information_schema.SCHEMATA s
            LEFT JOIN information_schema.TABLES t
              ON t.TABLE_SCHEMA = s.SCHEMA_NAME AND t.TABLE_TYPE = 'BASE TABLE'
            WHERE s.SCHEMA_NAME NOT IN ('information_schema','performance_schema','mysql','sys')
            GROUP BY s.SCHEMA_NAME, s.DEFAULT_CHARACTER_SET_NAME
            ORDER BY s.SCHEMA_NAME
            """
        )
        items = []
        for name, charset, tables, size in cur.fetchall():
            schema = _as_text(name)
            if not _DB_NAME_RE.match(schema) or schema in settings.SYSTEM_DB_NAMES:
                continue
            items.append(
                {
                    "name": schema,
                    "charset": _as_text(charset) or "utf8mb4",
                    "table_count": int(tables or 0),
                    "size_bytes": int(size or 0),
                }
            )
        return items
    finally:
        conn.close()


def create_target_database(
    host: str,
    port: int,
    user: str,
    password: str,
    name: str,
    charset: str = "utf8mb4",
) -> dict:
    schema = validate_database_name(name)
    charset = (charset or "utf8mb4").strip().lower()
    collation_map = {
        "utf8mb4": "utf8mb4_unicode_ci",
        "utf8": "utf8_general_ci",
        "latin1": "latin1_swedish_ci",
    }
    if charset not in collation_map:
        raise DtsError("不支持的字符集")
    collation = collation_map[charset]
    try:
        conn = _connect_target(host, port, user, password)
    except MySQLdb.Error as exc:
        raise DtsError(f"连接目标库失败: {exc}") from exc
    try:
        cur = conn.cursor()
        cur.execute("SHOW DATABASES")
        existing = {_as_text(row[0]) for row in cur.fetchall()}
        if schema in existing:
            raise DtsError(f"远程库 {schema} 已存在")
        cur.execute(
            f"CREATE DATABASE `{schema}` CHARACTER SET %s COLLATE %s",
            (charset, collation),
        )
    except MySQLdb.Error as exc:
        raise DtsError(f"创建远程库失败: {exc}") from exc
    finally:
        conn.close()
    return {"name": schema, "charset": charset, "collation": collation}


def _rewrite_database(sql: str, source: str, target: str) -> str:
    if not source or source == target:
        return sql
    return sql.replace(f"`{source}`", f"`{target}`")


def _connect_target(host: str, port: int, user: str, password: str):
    return MySQLdb.connect(
        host=host,
        port=int(port),
        user=user,
        passwd=password,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=120,
        write_timeout=120,
        autocommit=True,
    )


def _task_running(task_id: int) -> bool:
    status = DtsTask.objects.filter(pk=task_id).values_list("status", flat=True).first()
    return status == "full_sync"


def _mysqldump_cmd(database: str) -> list[str]:
    from apps.core.runtime import mysqldump_command

    return mysqldump_command(database)


def _spawn_dump(cmd: list[str]):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    stderr_chunks: list[str] = []

    def _read_err() -> None:
        if proc.stderr:
            stderr_chunks.append(proc.stderr.read())

    threading.Thread(target=_read_err, daemon=True).start()
    return proc, stderr_chunks


def _dump_snapshot(task, source_db: str, dump_path: str) -> tuple[tuple[str, int], int]:
    proc, stderr_chunks = _spawn_dump(_mysqldump_cmd(source_db))
    position: tuple[str, int] | None = None
    streamed = 0
    last = 0.0
    try:
        assert proc.stdout is not None
        with open(dump_path, "w", encoding="utf-8") as handle:
            for line in proc.stdout:
                if not _task_running(task.id):
                    proc.kill()
                    raise DtsError("全量同步已停止")
                handle.write(line)
                streamed += len(line.encode("utf-8", errors="replace"))
                found = _POS_RE.search(line)
                if found and position is None:
                    position = (found.group(1), int(found.group(2)))
                now = time.monotonic()
                if now - last >= 1:
                    last = now
                    task.full_phase = "export"
                    task.bytes_streamed = streamed
                    task.progress_percent = overall_percent(
                        "export", 0, task.table_total, streamed, task.bytes_total, 0
                    )
                    task.save(update_fields=["full_phase", "bytes_streamed", "progress_percent", "updated_at"])
        code = proc.wait(timeout=60)
        if code != 0:
            err = "".join(stderr_chunks).strip()
            raise DtsError(err or f"mysqldump 退出码 {code}")
        if position is None:
            raise DtsError("全量导出里没有 binlog 位点，无法接上增量")
        return position, streamed
    finally:
        if proc.poll() is None:
            proc.kill()


def _walk_dump(path: str, task_id: int, want_section: str):
    splitter = SqlSplitter()
    section = "structure"
    table = ""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not _task_running(task_id):
                raise DtsError("全量同步已停止")
            structure = _STRUCT_RE.search(line)
            data = _DATA_RE.search(line)
            if structure or data:
                for statement in splitter.finish():
                    if section == want_section:
                        yield table, statement, 0
                splitter.reset()
                section = "structure" if structure else "data"
                table = (structure or data).group(1)
                if section == want_section:
                    yield table, None, 0
                continue
            if section != want_section:
                continue
            nbytes = len(line.encode("utf-8", errors="replace"))
            if want_section == "data" and nbytes:
                yield table, None, nbytes
            for statement in splitter.feed(line):
                yield table, statement, 0
        for statement in splitter.finish():
            if section == want_section:
                yield table, statement, 0


def _save_table(row, phase: str, **extra) -> None:
    row.phase = phase
    fields = ["phase"]
    for key, value in extra.items():
        setattr(row, key, value)
        fields.append(key)
    row.save(update_fields=fields)


def _apply_structure(task, cur, dump_path: str, source_db: str, target_db: str, records: dict) -> None:
    task.full_phase = "structure"
    task.append_log(f"开始同步表结构，共 {task.table_total} 张表")
    task.save(update_fields=["full_phase", "log_text", "updated_at"])
    last_name = ""
    done = 0

    def mark_structure(name: str) -> None:
        nonlocal done
        row = records.get(name)
        if not row or row.phase != "pending":
            return
        _save_table(row, "structure")
        done += 1

    try:
        for table, statement, _nbytes in _walk_dump(dump_path, task.id, "structure"):
            if statement is None:
                if last_name:
                    mark_structure(last_name)
                last_name = table
                task.current_table = table
                task.structure_done = done
                task.progress_percent = overall_percent(
                    "structure", done, task.table_total, 0, task.bytes_total, 0
                )
                task.save(update_fields=["current_table", "structure_done", "progress_percent", "full_phase", "updated_at"])
                continue
            if _LOCK_RE.match(statement.strip()):
                continue
            _execute_statement(cur, _rewrite_database(statement, source_db, target_db))
    except Exception as exc:
        row = records.get(last_name)
        if row and row.phase == "pending":
            _save_table(row, "error", error_message=str(exc)[:255])
        raise
    if last_name:
        mark_structure(last_name)
    task.structure_done = done
    task.current_table = ""
    task.progress_percent = overall_percent("structure", done, task.table_total, 0, task.bytes_total, 0)
    task.append_log(f"表结构同步完成，{done}/{task.table_total} 张表")
    task.save(update_fields=["structure_done", "current_table", "progress_percent", "log_text", "full_phase", "updated_at"])


def _apply_data(task, cur, dump_path: str, source_db: str, target_db: str, records: dict) -> None:
    task.full_phase = "data"
    task.table_done = 0
    task.bytes_done = 0
    task.current_table = ""
    task.append_log("表结构已完成，开始逐表全量同步")
    task.save(update_fields=["full_phase", "table_done", "bytes_done", "current_table", "log_text", "updated_at"])
    last_name = ""
    done_bytes = 0
    done_tables = 0
    current_bytes = 0
    last_save = 0.0

    def finish_table(name: str) -> None:
        nonlocal done_bytes, done_tables, current_bytes
        row = records.get(name)
        if not row or row.phase == "done":
            current_bytes = 0
            return
        _save_table(row, "done", rows_copied=row.rows_total, bytes_copied=row.bytes_total)
        done_tables += 1
        done_bytes += int(row.bytes_total)
        current_bytes = 0
        task.table_done = done_tables
        task.bytes_done = done_bytes

    def publish(force: bool = False) -> None:
        nonlocal last_save
        now = time.monotonic()
        if not force and now - last_save < 1:
            return
        last_save = now
        row = records.get(last_name)
        partial = min(current_bytes, int(row.bytes_total)) if row and row.bytes_total else current_bytes
        if row and row.phase == "copying":
            row.bytes_copied = current_bytes
            row.save(update_fields=["bytes_copied"])
        task.bytes_done = done_bytes + partial
        task.table_done = done_tables
        task.current_table = last_name
        task.progress_percent = overall_percent(
            "data", task.structure_done, task.table_total, task.bytes_done, task.bytes_total, done_tables
        )
        task.save(update_fields=["bytes_done", "table_done", "current_table", "progress_percent", "full_phase", "updated_at"])

    try:
        for table, statement, nbytes in _walk_dump(dump_path, task.id, "data"):
            if statement is None and nbytes == 0:
                if last_name:
                    finish_table(last_name)
                last_name = table
                current_bytes = 0
                row = records.get(table)
                if row and row.phase != "done":
                    _save_table(row, "copying", bytes_copied=0)
                publish(force=True)
                continue
            if statement is None:
                current_bytes += nbytes
                publish()
                continue
            if _LOCK_RE.match(statement.strip()):
                continue
            _execute_statement(cur, _rewrite_database(statement, source_db, target_db))
    except Exception as exc:
        row = records.get(last_name)
        if row and row.phase == "copying":
            _save_table(row, "error", error_message=str(exc)[:255])
        raise
    if last_name:
        finish_table(last_name)
    leftovers = [row for row in records.values() if row.phase in {"pending", "structure"}]
    for row in leftovers:
        row.phase = "done"
        row.rows_copied = row.rows_total
        row.bytes_copied = row.bytes_total
    if leftovers:
        from apps.dts.models import DtsTableSync

        DtsTableSync.objects.bulk_update(leftovers, ["phase", "rows_copied", "bytes_copied"])
    task.table_done = task.table_total
    task.bytes_done = task.bytes_total
    task.structure_done = task.table_total
    task.current_table = ""
    task.progress_percent = overall_percent(
        "data", task.table_total, task.table_total, task.bytes_total, task.bytes_total, task.table_total
    )
    task.append_log(f"逐表全量完成，{task.table_done}/{task.table_total} 张表，{format_bytes(task.bytes_done)}")
    task.save(update_fields=["table_done", "bytes_done", "structure_done", "current_table", "progress_percent", "log_text", "full_phase", "updated_at"])


def run_full_sync(task_id: int) -> None:
    task = DtsTask.objects.get(pk=task_id)
    databases = [validate_database_name(name) for name in task.allowed_databases()]
    if len(databases) != 1:
        raise DtsError("请选择一个本地库")
    source_db = databases[0]
    target_db = validate_database_name(task.target_database)
    source = ensure_source_ready()
    if source["binlog_row_image"] not in {"FULL", ""}:
        task.append_log(f"提示: binlog_row_image={source['binlog_row_image']}，建议设为 FULL，否则更新行的旧值可能不完整")
    if source.get("binlog_row_metadata") not in {"FULL", ""}:
        task.append_log(
            f"提示: binlog_row_metadata={source.get('binlog_row_metadata') or 'MINIMAL'}，"
            "增量已启用列名缓存；建议源库设为 FULL"
        )

    stats = inspect_source_database(source_db)
    assign_inventory(task, stats, reset_progress=True)
    host, port, user, password = task.target_endpoint()
    task.append_log(
        f"开始全量同步: {source_db} -> {host}:{port}/{target_db}，"
        f"{task.table_total} 张表，约 {task.rows_total} 行，{format_bytes(task.bytes_total)}"
    )
    task.full_phase = "export"
    task.append_log("正在导出一致性快照，随后先同步全部表结构，再逐表导入数据")
    task.save(update_fields=["log_text", "error_message", *_PROGRESS_FIELDS])
    replace_table_rows(task, stats)
    records = {row.name: row for row in task.table_syncs.all()}
    dump_path = f"/tmp/dts-{task.id}.sql"
    position: tuple[str, int] | None = None
    streamed = 0
    conn = None
    try:
        position, streamed = _dump_snapshot(task, source_db, dump_path)
        conn = _connect_target(*task.target_endpoint())
        cur = conn.cursor()
        _prepare_session(cur)
        _apply_structure(task, cur, dump_path, source_db, target_db, records)
        _apply_data(task, cur, dump_path, source_db, target_db, records)
    finally:
        if conn is not None:
            conn.close()
        if os.path.exists(dump_path):
            os.remove(dump_path)

    task.refresh_from_db()
    if task.status != "full_sync":
        task.append_log("全量已中断，未切入增量")
        task.save(update_fields=["log_text", "updated_at"])
        return
    task.binlog_file, task.binlog_pos = position
    task.status = "incremental"
    task.error_message = ""
    task.lag_seconds = 0
    task.last_sync_at = timezone.now()
    task.full_phase = "done"
    task.table_done = task.table_total
    task.structure_done = task.table_total
    task.bytes_done = task.bytes_total
    task.bytes_streamed = max(int(task.bytes_streamed or 0), streamed)
    task.progress_percent = 100
    task.current_table = ""
    task.append_log(
        f"全量完成，{task.table_done}/{task.table_total} 张表，{format_bytes(task.bytes_done)}，"
        f"增量从 {position[0]}:{position[1]} 开始"
    )
    task.save()

def advance_incremental(task_id: int, max_events: int = 2000) -> int:
    task = DtsTask.objects.get(pk=task_id)
    if task.status != "incremental":
        return 0
    if not task.binlog_file:
        raise DtsError("缺少全量完成后的 binlog 位点")
    source = _source_status()
    if task.binlog_file == source["file"] and int(task.binlog_pos) >= int(source["position"]):
        task.lag_seconds = 0
        task.last_sync_at = timezone.now()
        task.save(update_fields=["lag_seconds", "last_sync_at", "updated_at"])
        return 0

    from pymysqlreplication import BinLogStreamReader
    from pymysqlreplication.event import QueryEvent, RotateEvent
    from pymysqlreplication.row_event import DeleteRowsEvent, UpdateRowsEvent, WriteRowsEvent

    source_db = validate_database_name(task.source_database_name())
    target_db = validate_database_name(task.target_database)
    allowed = {source_db}
    stream = None
    conn = None
    applied = 0
    last_event_at = task.last_event_at
    recent: list[dict] = []
    try:
        # MySQL 默认 binlog_row_metadata=MINIMAL 时 TableMap 不含列名；
        # use_column_name_cache 会从 information_schema 补齐，避免 UNKNOWN_COLn。
        stream = BinLogStreamReader(
            connection_settings={
                "host": settings.MYSQL_HOST,
                "port": settings.MYSQL_PORT,
                "user": "root",
                "passwd": settings.MYSQL_ROOT_PASSWORD,
            },
            server_id=880000 + (task.id % 100000),
            resume_stream=True,
            blocking=False,
            log_file=task.binlog_file,
            log_pos=int(task.binlog_pos),
            only_schemas=list(allowed),
            freeze_schema=False,
            use_column_name_cache=True,
            enable_logging=False,
        )
        conn = _connect_target(*task.target_endpoint())
        cur = conn.cursor()
        _prepare_session(cur)
        for event in stream:
            task.refresh_from_db(fields=["status"])
            if task.status != "incremental":
                break
            file_name = stream.log_file or task.binlog_file
            next_pos = int(getattr(getattr(event, "packet", None), "log_pos", 0) or 0)
            if isinstance(event, (WriteRowsEvent, UpdateRowsEvent, DeleteRowsEvent)):
                if _as_text(event.schema) in allowed:
                    statements = list(_row_statements(conn, event, target_db))
                    for sql in statements:
                        item = _sql_event_record(
                            event,
                            sql,
                            file_name,
                            next_pos,
                            ok=True,
                            table=_as_text(event.table),
                        )
                        recent.append(item)
                        cur.execute(sql)
                    applied += 1
            elif isinstance(event, QueryEvent):
                query = _as_text(getattr(event, "query", ""))
                schema = _as_text(getattr(event, "schema", ""))
                if _should_apply_query(schema, query, allowed):
                    sql = _DEFINER_RE.sub("", _rewrite_database(query, source_db, target_db))
                    item = _sql_event_record(event, sql, file_name, next_pos, ok=True, table="")
                    recent.append(item)
                    cur.execute(f"USE {qident(target_db)}")
                    cur.execute(sql)
                    applied += 1
            elif not isinstance(event, RotateEvent):
                pass
            if next_pos:
                task.binlog_file = file_name
                task.binlog_pos = next_pos
            ts = getattr(event, "timestamp", None)
            if ts:
                last_event_at = datetime.fromtimestamp(int(ts), tz=timezone.get_current_timezone())
            if applied >= max_events:
                break
        task.events_applied = int(task.events_applied or 0) + applied
        task.last_event_at = last_event_at
        task.last_sync_at = timezone.now()
        if last_event_at:
            task.lag_seconds = max(0, int((timezone.now() - last_event_at).total_seconds()))
        if task.binlog_file == source["file"] and int(task.binlog_pos) >= int(source["position"]):
            task.lag_seconds = 0
        task.error_message = ""
        if applied:
            task.append_log(f"增量应用 {applied} 个事件，位点 {task.binlog_file}:{task.binlog_pos}")
        task.save()
        record_sql_events(task.id, recent)
    except Exception as exc:
        task.refresh_from_db()
        if recent:
            recent[-1]["ok"] = False
            recent[-1]["error"] = str(exc)[:500]
            record_sql_events(task.id, recent)
        else:
            record_sql_events(
                task.id,
                [
                    {
                        "at": format_event_time(timezone.now()),
                        "kind": "ERROR",
                        "table": "",
                        "sql": clip_sql(str(exc)),
                        "file": task.binlog_file,
                        "pos": task.binlog_pos,
                        "ok": False,
                        "error": str(exc)[:500],
                    }
                ],
            )
        if task.status == "incremental":
            task.status = "error"
            task.error_message = str(exc)[:2000]
            task.append_log(f"增量失败: {task.error_message}")
            task.save()
        raise DtsError(task.error_message or str(exc)) from exc
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if conn is not None:
            conn.close()
    return applied


def _sql_event_record(event, sql: str, file_name: str, pos: int, ok: bool, table: str) -> dict:
    ts = getattr(event, "timestamp", None)
    at = (
        datetime.fromtimestamp(int(ts), tz=timezone.get_current_timezone())
        if ts
        else timezone.now()
    )
    return {
        "at": format_event_time(at),
        "kind": event_kind(event),
        "table": table,
        "sql": clip_sql(sql),
        "file": file_name,
        "pos": pos,
        "ok": ok,
        "error": "",
    }


def _prepare_session(cur) -> None:
    cur.execute("SET SESSION foreign_key_checks=0")
    cur.execute("SET SESSION unique_checks=0")
    cur.execute("SET SESSION sql_mode='NO_ENGINE_SUBSTITUTION'")
    try:
        cur.execute("SET SESSION sql_log_bin=0")
    except MySQLdb.Error:
        pass


def _execute_statement(cur, statement: str) -> None:
    sql = _DEFINER_RE.sub("", statement).strip()
    if not sql or sql == ";":
        return
    cur.execute(sql)


def _should_apply_query(schema: str, query: str, allowed: set[str]) -> bool:
    text = query.strip()
    if not text or _SKIP_QUERY.match(text):
        return False
    if schema in allowed:
        return True
    if schema in settings.SYSTEM_DB_NAMES:
        return False
    for name in allowed:
        if f"`{name}`" in text:
            return True
    return False


def _row_statements(conn, event, schema_name: str | None = None) -> Iterator[str]:
    from pymysqlreplication.row_event import DeleteRowsEvent, UpdateRowsEvent, WriteRowsEvent

    schema = qident(schema_name or _as_text(event.schema))
    table = qident(_as_text(event.table))
    if isinstance(event, WriteRowsEvent):
        rows = [{_as_text(key): val for key, val in row["values"].items()} for row in event.rows]
        if not rows:
            return
        _ensure_known_columns(rows[0], event)
        columns = list(rows[0].keys())
        col_sql = ", ".join(qident(col) for col in columns)
        values_sql = ", ".join(
            "(" + ", ".join(sql_literal(conn, row.get(col)) for col in columns) + ")"
            for row in rows
        )
        yield f"REPLACE INTO {schema}.{table} ({col_sql}) VALUES {values_sql}"
        return
    if isinstance(event, DeleteRowsEvent):
        for row in event.rows:
            values = {_as_text(key): val for key, val in (row.get("values") or {}).items()}
            _ensure_known_columns(values, event)
            where = _where_sql(conn, values)
            yield f"DELETE FROM {schema}.{table} WHERE {where}"
        return
    if isinstance(event, UpdateRowsEvent):
        for row in event.rows:
            before = {_as_text(key): val for key, val in (row.get("before_values") or {}).items()}
            after = {_as_text(key): val for key, val in (row.get("after_values") or {}).items()}
            _ensure_known_columns(before or after, event)
            assign = ", ".join(f"{qident(col)}={sql_literal(conn, val)}" for col, val in after.items())
            where = _where_sql(conn, before or after)
            yield f"UPDATE {schema}.{table} SET {assign} WHERE {where}"


def _ensure_known_columns(values: dict, event) -> None:
    unknown = [name for name in values if str(name).startswith("UNKNOWN_COL")]
    if not unknown:
        return
    table = f"{_as_text(getattr(event, 'schema', ''))}.{_as_text(getattr(event, 'table', ''))}"
    raise DtsError(
        f"行事件缺少列名（{table}），请确认源库 binlog_row_metadata=FULL "
        f"或增量读取已启用列名缓存。样例列: {', '.join(unknown[:5])}"
    )


def _where_sql(conn, values: dict) -> str:
    if not values:
        raise DtsError("行事件缺少列值，无法在目标库定位行")
    return " AND ".join(f"{qident(str(col))}<=>{sql_literal(conn, val)}" for col, val in values.items())


def qident(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def sql_literal(conn, value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "NULL" if value != value else repr(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        value = value.strftime("%Y-%m-%d %H:%M:%S.%f")
    elif isinstance(value, date):
        value = value.isoformat()
    elif isinstance(value, timedelta):
        value = str(value)
    elif isinstance(value, (bytes, bytearray)):
        return "X'" + bytes(value).hex() + "'"
    elif isinstance(value, (dict, list, set, tuple)):
        import json
        value = json.dumps(value, ensure_ascii=False, default=str)
    raw = conn.literal(str(value))
    return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


class SqlSplitter:
    """按 mysqldump 的 DELIMITER 切出可执行语句。"""

    def __init__(self) -> None:
        self.delimiter = ";"
        self.buf: list[str] = []
        self.in_sq = False
        self.in_dq = False
        self.in_bt = False
        self.escape = False

    def feed(self, text: str) -> list[str]:
        if (
            not self.buf
            and not self.in_sq
            and not self.in_dq
            and not self.in_bt
        ):
            match = re.match(r"^DELIMITER\s+(\S+)\s*$", text.strip(), re.IGNORECASE)
            if match:
                self.delimiter = match.group(1)
                return []
            if text.startswith("--") or not text.strip():
                return []
        statements: list[str] = []
        delim = self.delimiter
        dlen = len(delim)
        for char in text:
            self.buf.append(char)
            if self.escape:
                self.escape = False
                continue
            if char == "\\" and (self.in_sq or self.in_dq):
                self.escape = True
                continue
            if char == "'" and not self.in_dq and not self.in_bt:
                self.in_sq = not self.in_sq
                continue
            if char == '"' and not self.in_sq and not self.in_bt:
                self.in_dq = not self.in_dq
                continue
            if char == "`" and not self.in_sq and not self.in_dq:
                self.in_bt = not self.in_bt
                continue
            if self.in_sq or self.in_dq or self.in_bt or len(self.buf) < dlen:
                continue
            if dlen == 1:
                matched = self.buf[-1] == delim
            else:
                matched = "".join(self.buf[-dlen:]) == delim
            if not matched:
                continue
            statement = "".join(self.buf[:-dlen]).strip()
            self.buf = []
            if statement and not statement.upper().startswith("DELIMITER "):
                statements.append(statement)
        return statements

    def reset(self) -> None:
        self.delimiter = ";"
        self.buf = []
        self.in_sq = False
        self.in_dq = False
        self.in_bt = False
        self.escape = False

    def finish(self) -> list[str]:
        tail = "".join(self.buf).strip()
        self.buf = []
        if tail and not tail.upper().startswith("DELIMITER "):
            return [tail]
        return []
