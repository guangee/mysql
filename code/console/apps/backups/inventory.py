from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.backups.models import BackupFileIndex
from apps.backups.naming import extract_backup_timestamp, find_backup_archive, parse_backup_name
from apps.storages.models import StorageBackend
from apps.storages.services import list_backup_objects

_STALE_AFTER = timedelta(minutes=10)
_DATE_RE = __import__("re").compile(r"^\d{8}$")


def _entry(backup_type: str, timestamp: str, filename: str, storage: dict, version: str = "", date: str = "") -> dict:
    return {
        "filename": filename,
        "timestamp": timestamp,
        "date": date or (timestamp[:8] if timestamp else ""),
        "backup_type": backup_type,
        "version": version,
        "storages": [storage],
    }


def _merge(files: dict, backup_type: str, timestamp: str, filename: str, storage: dict, version: str = "", date: str = "") -> None:
    key = (backup_type, timestamp)
    current = files.get(key)
    if current is None:
        files[key] = _entry(backup_type, timestamp, filename, storage, version=version, date=date)
        return
    if filename and (not current.get("filename") or current["filename"].startswith("backup_")):
        if "_v" in filename or not current["filename"]:
            current["filename"] = filename
    if version and not current.get("version"):
        current["version"] = version
    if date and not current.get("date"):
        current["date"] = date
    if not any(
        item.get("storage_id") == storage.get("storage_id") and item.get("key") == storage.get("key")
        for item in current["storages"]
    ):
        current["storages"].append(storage)


def _scan_object_storage() -> dict:
    files = {}
    for storage in StorageBackend.objects.filter(enabled=True):
        try:
            objects = list_backup_objects(storage)
        except Exception:
            continue
        for obj in objects:
            if obj["key"].endswith("/day.xml"):
                continue
            parsed = parse_backup_name(obj["key"])
            ts = extract_backup_timestamp(obj["key"])
            if not ts:
                continue
            filename = obj["key"].rsplit("/", 1)[-1]
            version = getattr(parsed, "version", "") or ""
            parts = obj["key"].strip("/").split("/")
            date = parts[1] if len(parts) >= 3 and _DATE_RE.match(parts[1]) else ts[:8]
            _merge(
                files,
                obj["backup_type"],
                ts,
                filename,
                {
                    "storage_id": obj["storage_id"],
                    "storage_name": obj["storage_name"],
                    "key": obj["key"],
                    "size_bytes": obj["size_bytes"],
                    "last_modified": obj["last_modified"],
                },
                version=version,
                date=date,
            )
    return files


def _scan_local(files: dict) -> None:
    base = Path(settings.BACKUP_BASE_DIR)
    for backup_type, dirname in (("full", "full"), ("incremental", "incremental")):
        root = base / dirname
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            # 日期目录：扫描全部压缩包
            if _DATE_RE.match(child.name):
                for archive in sorted(child.glob("*.tar.gz")):
                    if not archive.is_file():
                        continue
                    ts = extract_backup_timestamp(archive.name)
                    if not ts:
                        continue
                    parsed = parse_backup_name(archive.name)
                    version = getattr(parsed, "version", "") or ""
                    _merge(
                        files,
                        backup_type,
                        ts,
                        archive.name,
                        {
                            "storage_id": None,
                            "storage_name": "本地",
                            "key": f"{dirname}/{child.name}/{archive.name}",
                            "size_bytes": archive.stat().st_size,
                            "last_modified": None,
                        },
                        version=version,
                        date=child.name,
                    )
                continue

            archive = find_backup_archive(child)
            ts = extract_backup_timestamp(child.name) or (extract_backup_timestamp(archive.name) if archive else None)
            if not archive or not ts:
                continue
            parsed = parse_backup_name(child.name) or parse_backup_name(archive.name)
            version = getattr(parsed, "version", "") or ""
            _merge(
                files,
                backup_type,
                ts,
                archive.name,
                {
                    "storage_id": None,
                    "storage_name": "本地",
                    "key": f"{dirname}/{child.name}/{archive.name}",
                    "size_bytes": archive.stat().st_size,
                    "last_modified": None,
                },
                version=version,
                date=ts[:8],
            )


def list_backup_files() -> list[dict]:
    files = _scan_object_storage()
    _scan_local(files)
    items = list(files.values())
    for item in items:
        remote = [storage for storage in item["storages"] if storage.get("storage_id")]
        if remote:
            item["storages"] = remote
    items.sort(key=lambda item: item["timestamp"], reverse=True)
    return items


def get_backup_file_catalog() -> dict:
    row = BackupFileIndex.objects.order_by("-updated_at").first()
    if not row:
        return {
            "last_synced_at": None,
            "sync_status": "idle",
            "file_count": 0,
            "syncing": False,
        }
    return {
        "last_synced_at": row.updated_at,
        "sync_status": row.sync_status,
        "file_count": row.file_count,
        "syncing": row.sync_status == "running",
    }


def catalog_to_dict(catalog: dict | None = None) -> dict:
    catalog = catalog or get_backup_file_catalog()
    synced = catalog.get("last_synced_at")
    if hasattr(synced, "isoformat"):
        catalog = {**catalog, "last_synced_at": synced.isoformat()}
    return catalog


def is_index_stale(catalog: dict | None = None) -> bool:
    catalog = catalog or get_backup_file_catalog()
    synced = catalog.get("last_synced_at")
    if not synced:
        return True
    if catalog.get("syncing"):
        return False
    if timezone.is_naive(synced):
        synced = timezone.make_aware(synced, timezone.get_current_timezone())
    return timezone.now() - synced > _STALE_AFTER


def sync_backup_file_index_with_retry(force: bool = False) -> dict:
    row = BackupFileIndex.objects.order_by("-updated_at").first()
    if row and not force and not is_index_stale():
        return get_backup_file_catalog()
    if row is None:
        row = BackupFileIndex.objects.create(sync_status="running")
    else:
        row.sync_status = "running"
        row.save(update_fields=["sync_status", "updated_at"])
    try:
        items = list_backup_files()
        row.payload = {"files": _json_safe(items)}
        row.file_count = len(items)
        row.sync_status = "ok"
        row.save()
    except Exception:
        row.sync_status = "failed"
        row.save(update_fields=["sync_status", "updated_at"])
        raise
    return get_backup_file_catalog()


def _json_safe(items: list[dict]) -> list[dict]:
    safe = []
    for item in items:
        storages = []
        for storage in item.get("storages", []):
            copied = dict(storage)
            modified = copied.get("last_modified")
            if hasattr(modified, "isoformat"):
                copied["last_modified"] = modified.isoformat()
            storages.append(copied)
        safe.append({**item, "storages": storages})
    return safe
