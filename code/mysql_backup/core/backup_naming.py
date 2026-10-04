"""
备份命名约定。

目录（按日）:
  /backups/{full|incremental}/YYYYMMDD/
    YYYYMMDD_HHMMSS_{full|incr}_v{version}.tar.gz
    day.xml

压缩包:
  {YYYYMMDD}_{HHMMSS}_{kind}_v{version}.tar.gz

兼容旧格式:
  - 目录 YYYYMMDD_HHMMSS[_full_vX] + 单文件压缩包
  - 文件 backup_YYYYMMDD_HHMMSS.tar.gz
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from mysql_backup import __version__ as _PKG_VERSION
except Exception:  # pragma: no cover
    _PKG_VERSION = "0.1.0"

KIND_FULL = "full"
KIND_INCR = "incr"
KIND_ALIASES = {
    "full": KIND_FULL,
    "incr": KIND_INCR,
    "incremental": KIND_INCR,
}

DATE_RE = re.compile(r"^\d{8}$")
TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}$")
BACKUP_ID_RE = re.compile(
    r"^(?P<timestamp>\d{8}_\d{6})_(?P<kind>full|incr|incremental)_v(?P<version>[0-9A-Za-z][0-9A-Za-z._+-]*)$"
)
BACKUP_FILENAME_RE = re.compile(
    r"(?:^|/)"
    r"(?P<stem>"
    r"(?P<timestamp>\d{8}_\d{6})_(?P<kind>full|incr|incremental)_v(?P<version>[0-9A-Za-z][0-9A-Za-z._+-]*)"
    r"|backup_(?P<legacy_ts>\d{8}_\d{6})"
    r")\.tar\.gz$"
)
LEGACY_BACKUP_FILE = "backup.tar.gz"
DAY_MANIFEST_NAME = "day.xml"


@dataclass(frozen=True)
class BackupName:
    timestamp: str
    kind: str
    version: str
    backup_id: str
    filename: str

    @property
    def kind_label(self) -> str:
        return "全量" if self.kind == KIND_FULL else "增量"

    @property
    def date(self) -> str:
        return self.timestamp[:8]


def get_backup_app_version() -> str:
    raw = (
        os.environ.get("BACKUP_APP_VERSION")
        or os.environ.get("APP_VERSION")
        or _PKG_VERSION
        or "0.1.0"
    ).strip()
    cleaned = re.sub(r"[^0-9A-Za-z._+-]+", ".", raw).strip(".")
    return cleaned or "0.1.0"


def normalize_kind(kind: str) -> str:
    key = (kind or "").strip().lower()
    if key not in KIND_ALIASES:
        raise ValueError(f"无效备份类型: {kind}")
    return KIND_ALIASES[key]


def make_backup_id(timestamp: str, kind: str, version: str | None = None) -> str:
    if not TIMESTAMP_RE.match(timestamp or ""):
        raise ValueError(f"无效时间戳: {timestamp}")
    kind_norm = normalize_kind(kind)
    ver = version or get_backup_app_version()
    return f"{timestamp}_{kind_norm}_v{ver}"


def make_backup_filename(timestamp: str, kind: str, version: str | None = None) -> str:
    return f"{make_backup_id(timestamp, kind, version)}.tar.gz"


def build_backup_name(timestamp: str, kind: str, version: str | None = None) -> BackupName:
    kind_norm = normalize_kind(kind)
    ver = version or get_backup_app_version()
    backup_id = make_backup_id(timestamp, kind_norm, ver)
    return BackupName(
        timestamp=timestamp,
        kind=kind_norm,
        version=ver,
        backup_id=backup_id,
        filename=f"{backup_id}.tar.gz",
    )


def backup_date_from_timestamp(timestamp: str) -> str:
    ts = extract_backup_timestamp(timestamp) or timestamp
    if TIMESTAMP_RE.match(ts or ""):
        return ts[:8]
    if DATE_RE.match(ts or ""):
        return ts
    raise ValueError(f"无法从时间戳提取日期: {timestamp}")


def day_dir_for(type_root: Path, timestamp: str) -> Path:
    return type_root / backup_date_from_timestamp(timestamp)


def parse_backup_name(name: str) -> Optional[BackupName]:
    text = (name or "").strip().rstrip("/")
    if not text:
        return None
    base = text.rsplit("/", 1)[-1]

    if base.endswith(".tar.gz"):
        match = BACKUP_FILENAME_RE.search(base)
        if not match:
            return None
        if match.group("legacy_ts"):
            ts = match.group("legacy_ts")
            return BackupName(
                timestamp=ts,
                kind="",
                version="",
                backup_id=f"backup_{ts}",
                filename=base,
            )
        kind = normalize_kind(match.group("kind"))
        ver = match.group("version")
        ts = match.group("timestamp")
        backup_id = make_backup_id(ts, kind, ver)
        return BackupName(timestamp=ts, kind=kind, version=ver, backup_id=backup_id, filename=f"{backup_id}.tar.gz")

    if DATE_RE.match(base):
        return None

    if TIMESTAMP_RE.match(base):
        return BackupName(
            timestamp=base,
            kind="",
            version="",
            backup_id=base,
            filename=LEGACY_BACKUP_FILE,
        )

    match = BACKUP_ID_RE.match(base)
    if not match:
        return None
    kind = normalize_kind(match.group("kind"))
    ver = match.group("version")
    ts = match.group("timestamp")
    backup_id = make_backup_id(ts, kind, ver)
    return BackupName(
        timestamp=ts,
        kind=kind,
        version=ver,
        backup_id=backup_id,
        filename=f"{backup_id}.tar.gz",
    )


def extract_backup_timestamp(name: str) -> Optional[str]:
    parsed = parse_backup_name(name)
    return parsed.timestamp if parsed else None


def is_date_dir_name(name: str) -> bool:
    return bool(DATE_RE.match(name or ""))


def is_backup_dir_name(name: str) -> bool:
    """兼容：日期目录、旧时间戳目录、旧 backup_id 目录。"""
    if is_date_dir_name(name):
        return True
    parsed = parse_backup_name(name)
    if not parsed:
        return False
    return bool(TIMESTAMP_RE.match(name) or BACKUP_ID_RE.match(name))


def find_backup_archive(backup_dir: Path) -> Optional[Path]:
    """
    在目录中定位压缩包。
    日期目录可能有多个包，返回按文件名排序的最新一个；
    单备份目录则返回其中唯一/首选压缩包。
    """
    if not backup_dir.is_dir():
        return None
    preferred = backup_dir / f"{backup_dir.name}.tar.gz"
    if preferred.is_file() and preferred.stat().st_size > 0:
        return preferred
    legacy = backup_dir / LEGACY_BACKUP_FILE
    if legacy.is_file() and legacy.stat().st_size > 0:
        return legacy
    archives = sorted(
        [p for p in backup_dir.glob("*.tar.gz") if p.is_file() and p.stat().st_size > 0],
        key=lambda p: p.name,
    )
    return archives[-1] if archives else None


def list_archives_in_dir(backup_dir: Path) -> list[Path]:
    if not backup_dir.is_dir():
        return []
    return sorted(
        [p for p in backup_dir.glob("*.tar.gz") if p.is_file() and p.stat().st_size > 0],
        key=lambda p: p.name,
    )


def resolve_backup_archive(type_root: Path, timestamp_or_id: str) -> Optional[Path]:
    """按时间戳/backup_id/文件名定位具体压缩包。"""
    token = (timestamp_or_id or "").strip()
    if not token or not type_root.is_dir():
        return None

    if token.endswith(".tar.gz"):
        # 直接文件名或相对路径
        direct = type_root / Path(token).name
        if direct.is_file():
            return direct
        for path in type_root.rglob(Path(token).name):
            if path.is_file():
                return path

    ts = extract_backup_timestamp(token) or (token if TIMESTAMP_RE.match(token) else None)
    if not ts:
        return None

    # 1) 日期目录下的命名压缩包
    day = type_root / ts[:8]
    if day.is_dir():
        for archive in list_archives_in_dir(day):
            if extract_backup_timestamp(archive.name) == ts:
                return archive

    # 2) 旧：backup_id / timestamp 单目录
    for candidate in (
        type_root / token,
        type_root / ts,
        type_root / make_backup_id(ts, "full"),
        type_root / make_backup_id(ts, "incr"),
    ):
        if candidate.is_dir():
            archive = find_backup_archive(candidate)
            if archive and (extract_backup_timestamp(archive.name) in {None, ts} or archive.name == LEGACY_BACKUP_FILE):
                if extract_backup_timestamp(archive.name) == ts or archive.name == LEGACY_BACKUP_FILE:
                    return archive
            # 目录内任意匹配时间戳的包
            for archive in list_archives_in_dir(candidate):
                if extract_backup_timestamp(archive.name) == ts:
                    return archive

    # 3) 遍历全部子目录
    for child in type_root.iterdir():
        if not child.is_dir():
            continue
        for archive in list_archives_in_dir(child):
            if extract_backup_timestamp(archive.name) == ts:
                return archive
    return None


def resolve_backup_dir(type_root: Path, timestamp_or_id: str) -> Optional[Path]:
    """
    定位备份所在目录（日期目录或旧单备份目录）。
    新布局下同一日期目录含多个压缩包，请优先用 resolve_backup_archive。
    """
    archive = resolve_backup_archive(type_root, timestamp_or_id)
    if archive is not None:
        return archive.parent

    token = (timestamp_or_id or "").strip()
    if not token or not type_root.is_dir():
        return None
    exact = type_root / token
    if exact.is_dir() and (list_archives_in_dir(exact) or (exact / "xtrabackup_checkpoints").is_file()):
        return exact
    ts = extract_backup_timestamp(token) or (token if TIMESTAMP_RE.match(token) else None)
    if not ts:
        return None
    day = type_root / ts[:8]
    if day.is_dir() and list_archives_in_dir(day):
        return day
    return None


def list_backup_dirs(type_root: Path) -> list[tuple[str, Path]]:
    """兼容旧接口：返回 (timestamp, 目录)。新布局下每个压缩包对应其日期目录。"""
    items: list[tuple[str, Path]] = []
    seen = set()
    for ts, archive in list_backup_archives(type_root):
        key = (ts, str(archive.parent))
        if key in seen:
            continue
        seen.add(key)
        items.append((ts, archive.parent))
    items.sort(key=lambda x: x[0])
    return items


def list_backup_archives(type_root: Path) -> list[tuple[str, Path]]:
    """列出 (timestamp, archive_path)，按时间戳排序（旧→新）。"""
    items: list[tuple[str, Path]] = []
    if not type_root.is_dir():
        return items
    for child in type_root.iterdir():
        if not child.is_dir():
            continue
        if is_date_dir_name(child.name) or is_backup_dir_name(child.name):
            for archive in list_archives_in_dir(child):
                ts = extract_backup_timestamp(archive.name)
                if not ts and archive.name == LEGACY_BACKUP_FILE and TIMESTAMP_RE.match(child.name):
                    ts = child.name
                if not ts:
                    ts = extract_backup_timestamp(child.name)
                if ts:
                    items.append((ts, archive))
            # 未打包的旧目录
            if not list_archives_in_dir(child) and (child / "xtrabackup_checkpoints").is_file():
                ts = extract_backup_timestamp(child.name)
                if ts:
                    items.append((ts, child))
    items.sort(key=lambda x: (x[0], x[1].name))
    return items


def s3_backup_object_name(timestamp: str, kind: str, version: str | None = None) -> str:
    return make_backup_filename(timestamp, kind, version)


def s3_backup_object_names_for_lookup(timestamp: str, kind: str, version: str | None = None) -> list[str]:
    names = [make_backup_filename(timestamp, kind, version)]
    legacy = f"backup_{timestamp}.tar.gz"
    if legacy not in names:
        names.append(legacy)
    return names


def s3_backup_key(kind_dir: str, timestamp: str, filename: str) -> str:
    """对象存储 key：{full|incremental}/YYYYMMDD/filename"""
    date = backup_date_from_timestamp(timestamp)
    return f"{kind_dir}/{date}/{filename}"


def parse_backup_filename_timestamp(filename: str) -> Optional[datetime]:
    ts = extract_backup_timestamp(filename or "")
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def timestamp_to_display(timestamp: str) -> str:
    """YYYYMMDD_HHMMSS -> YYYY-MM-DD HH:MM:SS"""
    ts = extract_backup_timestamp(timestamp) or timestamp
    try:
        return datetime.strptime(ts, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return timestamp
