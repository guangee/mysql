from apps.backups.naming import is_uploadable_backup_filename
from apps.storages.services import get_boto3_client


def upload_backup_file(storage, upload, backup_type: str) -> dict:
    filename = (getattr(upload, "name", "") or "").rsplit("/", 1)[-1]
    if not is_uploadable_backup_filename(filename):
        raise ValueError(
            "文件名须为 YYYYMMDD_HHMMSS_full|incr_v版本.tar.gz 或旧格式 backup_YYYYMMDD_HHMMSS.tar.gz"
        )
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
