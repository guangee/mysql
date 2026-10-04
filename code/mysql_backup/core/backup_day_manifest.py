"""按日备份目录的 day.xml 说明文件。"""

from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional
from xml.dom import minidom

from mysql_backup.core.backup_naming import (
    DAY_MANIFEST_NAME,
    extract_backup_timestamp,
    list_archives_in_dir,
    parse_backup_name,
    timestamp_to_display,
)

SYSTEM_DBS = {"information_schema", "performance_schema", "mysql", "sys"}


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _prettify(elem: ET.Element) -> str:
    rough = ET.tostring(elem, encoding="utf-8")
    parsed = minidom.parseString(rough)
    return parsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


def _normalize_databases(databases) -> list[dict]:
    """统一为 [{name, size_bytes, table_count, row_count}, ...]。"""
    result: list[dict] = []
    if not databases:
        return result
    if isinstance(databases, str):
        for name in databases.split(","):
            name = name.strip()
            if name:
                result.append(
                    {"name": name, "size_bytes": 0, "table_count": 0, "row_count": 0}
                )
        return result
    for item in databases:
        if isinstance(item, str):
            name = item.strip()
            if name:
                result.append(
                    {"name": name, "size_bytes": 0, "table_count": 0, "row_count": 0}
                )
            continue
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        result.append(
            {
                "name": name,
                "size_bytes": int(item.get("size_bytes") or 0),
                "table_count": int(item.get("table_count") or 0),
                "row_count": int(item.get("row_count") or 0),
            }
        )
    return result


def _file_elem_to_dict(node: ET.Element) -> dict:
    databases = []
    for db_node in node.findall("database"):
        databases.append(
            {
                "name": db_node.get("name", ""),
                "size_bytes": int(db_node.get("sizeBytes") or 0),
                "table_count": int(db_node.get("tableCount") or 0),
                "row_count": int(db_node.get("rowCount") or 0),
            }
        )
    # 兼容旧版 attributes
    if not databases and node.get("databases"):
        databases = _normalize_databases(node.get("databases"))
    return {
        "name": node.get("name", ""),
        "timestamp": node.get("timestamp", ""),
        "kind": node.get("kind", ""),
        "version": node.get("version", ""),
        "size_bytes": int(node.get("sizeBytes") or 0),
        "recoverable_to": node.get("recoverableTo", ""),
        "databases": databases,
    }


def collect_database_metadata(
    *,
    mysql_host: str = "localhost",
    mysql_port: int = 3306,
    mysql_user: str = "root",
    mysql_password: str = "",
) -> list[dict]:
    """
    查询用户库元数据：名称、数据大小、表数量、近似行数。
    """
    sql = (
        "SELECT t.TABLE_SCHEMA, "
        "COUNT(*), "
        "COALESCE(SUM(t.DATA_LENGTH + t.INDEX_LENGTH), 0), "
        "COALESCE(SUM(t.TABLE_ROWS), 0) "
        "FROM information_schema.TABLES t "
        "WHERE t.TABLE_SCHEMA NOT IN "
        "('information_schema','performance_schema','mysql','sys') "
        "GROUP BY t.TABLE_SCHEMA "
        "ORDER BY t.TABLE_SCHEMA;"
    )
    cmd = ["mysql", "-N", "-e", sql]
    if mysql_host in ("localhost", "127.0.0.1"):
        cmd.insert(1, "--socket=/var/run/mysqld/mysqld.sock")
    else:
        cmd[1:1] = [f"-h{mysql_host}", f"-P{mysql_port}"]
    cmd.extend([f"-u{mysql_user}", f"-p{mysql_password}"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception:
        return []
    if result.returncode != 0 or not result.stdout.strip():
        # 退化：只列库名
        return _list_schema_names(mysql_host, mysql_port, mysql_user, mysql_password)

    items: list[dict] = []
    seen = set()
    for line in result.stdout.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name = parts[0].strip()
        if not name or name in SYSTEM_DBS:
            continue
        seen.add(name)
        items.append(
            {
                "name": name,
                "table_count": int(float(parts[1] or 0)),
                "size_bytes": int(float(parts[2] or 0)),
                "row_count": int(float(parts[3] or 0)),
            }
        )

    # 无表的空库也要出现
    for name in _list_schema_names(mysql_host, mysql_port, mysql_user, mysql_password):
        if name not in seen:
            items.append(
                {"name": name, "table_count": 0, "size_bytes": 0, "row_count": 0}
            )
    items.sort(key=lambda item: item["name"])
    return items


def _list_schema_names(
    mysql_host: str, mysql_port: int, mysql_user: str, mysql_password: str
) -> list[str]:
    sql = (
        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
        "WHERE SCHEMA_NAME NOT IN "
        "('information_schema','performance_schema','mysql','sys') "
        "ORDER BY SCHEMA_NAME;"
    )
    cmd = ["mysql", "-N", "-e", sql]
    if mysql_host in ("localhost", "127.0.0.1"):
        cmd.insert(1, "--socket=/var/run/mysqld/mysqld.sock")
    else:
        cmd[1:1] = [f"-h{mysql_host}", f"-P{mysql_port}"]
    cmd.extend([f"-u{mysql_user}", f"-p{mysql_password}"])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception:
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    names = []
    for line in result.stdout.strip().split("\n"):
        name = line.strip()
        if name and name not in SYSTEM_DBS:
            names.append(name)
    return names


def read_day_manifest(day_dir: Path) -> dict:
    path = day_dir / DAY_MANIFEST_NAME
    if not path.is_file():
        return {
            "date": day_dir.name if day_dir.name.isdigit() else "",
            "kind": "",
            "count": 0,
            "updated_at": "",
            "files": [],
        }
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return {
            "date": day_dir.name,
            "kind": "",
            "count": 0,
            "updated_at": "",
            "files": [],
        }
    files = [_file_elem_to_dict(node) for node in root.findall("file")]
    return {
        "date": root.get("date") or day_dir.name,
        "kind": root.get("kind") or "",
        "count": int(root.get("count") or len(files)),
        "updated_at": root.get("updatedAt") or "",
        "files": files,
    }


def write_day_manifest(
    day_dir: Path,
    *,
    kind: str,
    files: Iterable[dict],
    log: Optional[Callable[[str], None]] = None,
) -> Path:
    day_dir.mkdir(parents=True, exist_ok=True)
    file_list = sorted(list(files), key=lambda item: item.get("timestamp") or item.get("name") or "")
    date_label = day_dir.name
    if len(date_label) == 8 and date_label.isdigit():
        date_attr = f"{date_label[:4]}-{date_label[4:6]}-{date_label[6:8]}"
    else:
        date_attr = date_label

    root = ET.Element(
        "backupDay",
        {
            "date": date_attr,
            "kind": kind,
            "count": str(len(file_list)),
            "updatedAt": _now_str(),
        },
    )
    for item in file_list:
        file_node = ET.SubElement(
            root,
            "file",
            {
                "name": item.get("name") or "",
                "timestamp": item.get("timestamp") or "",
                "kind": item.get("kind") or kind,
                "version": item.get("version") or "",
                "sizeBytes": str(int(item.get("size_bytes") or 0)),
                "recoverableTo": item.get("recoverable_to") or "",
                "databaseCount": str(len(_normalize_databases(item.get("databases")))),
            },
        )
        for db in _normalize_databases(item.get("databases")):
            ET.SubElement(
                file_node,
                "database",
                {
                    "name": db["name"],
                    "sizeBytes": str(int(db.get("size_bytes") or 0)),
                    "tableCount": str(int(db.get("table_count") or 0)),
                    "rowCount": str(int(db.get("row_count") or 0)),
                },
            )

    path = day_dir / DAY_MANIFEST_NAME
    path.write_text(_prettify(root), encoding="utf-8")
    if log:
        log(f"已更新每日说明: {path}（共 {len(file_list)} 个备份）")
    return path


def upsert_day_manifest_entry(
    day_dir: Path,
    *,
    kind: str,
    filename: str,
    timestamp: str,
    version: str,
    size_bytes: int,
    databases,
    recoverable_to: str | None = None,
    log: Optional[Callable[[str], None]] = None,
) -> Path:
    manifest = read_day_manifest(day_dir)
    files = {item["name"]: item for item in manifest.get("files", []) if item.get("name")}
    files[filename] = {
        "name": filename,
        "timestamp": timestamp,
        "kind": kind,
        "version": version,
        "size_bytes": size_bytes,
        "recoverable_to": recoverable_to or timestamp_to_display(timestamp),
        "databases": _normalize_databases(databases),
    }

    existing_names = {p.name for p in list_archives_in_dir(day_dir)}
    files = {name: item for name, item in files.items() if name in existing_names}
    for archive in list_archives_in_dir(day_dir):
        if archive.name in files:
            continue
        parsed = parse_backup_name(archive.name)
        ts = (parsed.timestamp if parsed else extract_backup_timestamp(archive.name)) or ""
        files[archive.name] = {
            "name": archive.name,
            "timestamp": ts,
            "kind": (parsed.kind if parsed and parsed.kind else kind),
            "version": (parsed.version if parsed else ""),
            "size_bytes": archive.stat().st_size,
            "recoverable_to": timestamp_to_display(ts) if ts else "",
            "databases": [],
        }

    return write_day_manifest(day_dir, kind=kind, files=files.values(), log=log)


def rebuild_day_manifest(
    day_dir: Path,
    *,
    kind: str,
    log: Optional[Callable[[str], None]] = None,
) -> Path:
    """根据目录内压缩包重建 day.xml（保留原 XML 中仍有效的 databases 等信息）。"""
    old = {item["name"]: item for item in read_day_manifest(day_dir).get("files", [])}
    files = []
    for archive in list_archives_in_dir(day_dir):
        parsed = parse_backup_name(archive.name)
        ts = (parsed.timestamp if parsed else extract_backup_timestamp(archive.name)) or ""
        prev = old.get(archive.name, {})
        files.append(
            {
                "name": archive.name,
                "timestamp": ts,
                "kind": (parsed.kind if parsed and parsed.kind else kind),
                "version": (parsed.version if parsed else prev.get("version", "")),
                "size_bytes": archive.stat().st_size,
                "recoverable_to": prev.get("recoverable_to")
                or (timestamp_to_display(ts) if ts else ""),
                "databases": _normalize_databases(prev.get("databases")),
            }
        )
    return write_day_manifest(day_dir, kind=kind, files=files, log=log)
