import re

from apps.storages.services import get_boto3_client

_NAME_RE = re.compile(r"^backup_(\d{8}_\d{6})\.tar\.gz$")


def upload_backup_file(storage, upload, backup_type: str) -> dict:
    filename = (getattr(upload, "name", "") or "").rsplit("/", 1)[-1]
    if not _NAME_RE.match(filename):
        raise ValueError("文件名须为 backup_YYYYMMDD_HHMMSS.tar.gz")
    if backup_type not in {"full", "incremental"}:
        raise ValueError("backup_type 须为 full 或 incremental")

    key = f"{backup_type}/{filename}"
    client = get_boto3_client(storage)
    client.upload_fileobj(upload, storage.bucket, key)
    return {
        "filename": filename,
        "key": key,
        "backup_type": backup_type,
        "storage_id": storage.id,
        "storage_name": storage.name,
        "size_bytes": getattr(upload, "size", None),
    }
