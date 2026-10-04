"""控制台侧备份命名解析（与 mysql_backup.core.backup_naming 对齐）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LEGACY_BACKUP_FILE = "backup.tar.gz"
TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}$")
BACKUP_ID_RE = re.compile(
    r"^(?P<timestamp>\d{8}_\d{6})_(?P<kind>full|incr|incremental)_v(?P<version>[0-9A-Za-z][0-9A-Za-z._+-]*)$"
)
BACKUP_FILENAME_RE = re.compile(
    r"(?:^|/)"
    r"(?:"
    r"(?P<timestamp>\d{8}_\d{6})_(?P<kind>full|incr|incremental)_v(?P<version>[0-9A-Za-z][0-9A-Za-z._+-]*)"
    r"|backup_(?P<legacy_ts>\d{8}_\d{6})"
    r")\.tar\.gz$"
)

try:
    from mysql_backup.core.backup_naming import (  # type: ignore
        extract_backup_timestamp as _mb_extract,
        find_backup_archive as _mb_find_archive,
        parse_backup_name as _mb_parse,
    )

    def extract_backup_timestamp(name: str) -> Optional[str]:
        return _mb_extract(name)

    def parse_backup_name(name: str):
        return _mb_parse(name)

    def find_backup_archive(backup_dir: Path) -> Optional[Path]:
        return _mb_find_archive(backup_dir)

except Exception:  # pragma: no cover - 开发环境未挂载 mysql_backup 时回退

    @dataclass(frozen=True)
    class _Parsed:
        timestamp: str
        kind: str
        version: str
        backup_id: str
        filename: str

    def extract_backup_timestamp(name: str) -> Optional[str]:
        text = (name or "").strip().rstrip("/")
        base = text.rsplit("/", 1)[-1]
        if TIMESTAMP_RE.match(base):
            return base
        m = BACKUP_ID_RE.match(base)
        if m:
            return m.group("timestamp")
        m = BACKUP_FILENAME_RE.search(base)
        if not m:
            return None
        return m.group("timestamp") or m.group("legacy_ts")

    def parse_backup_name(name: str):
        text = (name or "").strip().rstrip("/")
        base = text.rsplit("/", 1)[-1]
        if base.endswith(".tar.gz"):
            m = BACKUP_FILENAME_RE.search(base)
            if not m:
                return None
            if m.group("legacy_ts"):
                ts = m.group("legacy_ts")
                return _Parsed(ts, "", "", f"backup_{ts}", base)
            kind = "incr" if m.group("kind") in {"incr", "incremental"} else "full"
            ts = m.group("timestamp")
            ver = m.group("version")
            backup_id = f"{ts}_{kind}_v{ver}"
            return _Parsed(ts, kind, ver, backup_id, f"{backup_id}.tar.gz")
        if TIMESTAMP_RE.match(base):
            return _Parsed(base, "", "", base, LEGACY_BACKUP_FILE)
        m = BACKUP_ID_RE.match(base)
        if not m:
            return None
        kind = "incr" if m.group("kind") in {"incr", "incremental"} else "full"
        ts = m.group("timestamp")
        ver = m.group("version")
        backup_id = f"{ts}_{kind}_v{ver}"
        return _Parsed(ts, kind, ver, backup_id, f"{backup_id}.tar.gz")

    def find_backup_archive(backup_dir: Path) -> Optional[Path]:
        if not backup_dir.is_dir():
            return None
        preferred = backup_dir / f"{backup_dir.name}.tar.gz"
        if preferred.is_file() and preferred.stat().st_size > 0:
            return preferred
        legacy = backup_dir / LEGACY_BACKUP_FILE
        if legacy.is_file() and legacy.stat().st_size > 0:
            return legacy
        for path in sorted(backup_dir.glob("*.tar.gz")):
            if path.is_file() and path.stat().st_size > 0:
                return path
        return None


UPLOAD_NAME_RE = re.compile(
    r"^("
    r"\d{8}_\d{6}_(full|incr|incremental)_v[0-9A-Za-z][0-9A-Za-z._+-]*"
    r"|backup_\d{8}_\d{6}"
    r")\.tar\.gz$"
)


def is_uploadable_backup_filename(filename: str) -> bool:
    return bool(UPLOAD_NAME_RE.match((filename or "").rsplit("/", 1)[-1]))
