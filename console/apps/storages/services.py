import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from django.conf import settings
from django.http import StreamingHttpResponse

from apps.core.crypto import decrypt_value
from apps.storages.models import StorageBackend

_INTERNAL_HOST_RE = re.compile(
    r"^(localhost|127\.0\.0\.1|mysql|redis|console|.*\.local)$",
    re.I,
)


def storage_to_dict(storage: StorageBackend) -> dict:
    return {
        "id": storage.id,
        "name": storage.name,
        "alias": storage.alias,
        "endpoint": storage.endpoint,
        "access_key": storage.access_key,
        "secret_key": storage.secret_key,
        "bucket": storage.bucket,
        "region": storage.region,
        "use_ssl": storage.use_ssl,
        "force_path_style": storage.force_path_style,
        "enabled": storage.enabled,
    }


def export_storages_config() -> int:
    storages = StorageBackend.objects.filter(enabled=True).order_by("id")
    payload = [storage_to_dict(s) for s in storages]
    target = Path(settings.SHARED_STORAGES_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(payload)


def get_boto3_client(storage: StorageBackend):
    scheme = "https" if storage.use_ssl else "http"
    endpoint_url = f"{scheme}://{storage.endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=storage.access_key,
        aws_secret_access_key=storage.secret_key,
        region_name=storage.region or "us-east-1",
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path" if storage.force_path_style else "auto"},
        ),
    )


def test_storage_connection(storage: StorageBackend) -> tuple[bool, str]:
    client = get_boto3_client(storage)
    test_key = f".console_test/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        client.head_bucket(Bucket=storage.bucket)
        client.put_object(
            Bucket=storage.bucket,
            Key=test_key,
            Body=b"mysql-console connectivity test",
        )
        client.delete_object(Bucket=storage.bucket, Key=test_key)
        return True, "连接成功"
    except ClientError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def list_backup_objects(storage: StorageBackend, prefix: str = "") -> list[dict]:
    client = get_boto3_client(storage)
    objects = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=storage.bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.endswith(".tar.gz"):
                continue
            backup_type = "full" if "/full/" in key or key.startswith("full/") else "incremental"
            objects.append(
                {
                    "key": key,
                    "size_bytes": item["Size"],
                    "last_modified": item["LastModified"],
                    "backup_type": backup_type,
                    "storage_id": storage.id,
                    "storage_name": storage.name,
                }
            )
    return objects


def generate_presigned_download_url(storage: StorageBackend, key: str, expires: int = 3600) -> str:
    client = get_boto3_client(storage)
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": storage.bucket, "Key": key},
        ExpiresIn=expires,
    )
    return _apply_public_endpoint(url, storage)


def _apply_public_endpoint(url: str, storage: StorageBackend) -> str:
    public = (getattr(settings, "S3_PUBLIC_ENDPOINT", None) or os.environ.get("S3_PUBLIC_ENDPOINT", "")).strip()
    if not public:
        return url
    parsed = urlparse(url)
    if "://" in public:
        pub = urlparse(public)
    else:
        scheme = "https" if storage.use_ssl else "http"
        pub = urlparse(f"{scheme}://{public}")
    return urlunparse(parsed._replace(scheme=pub.scheme, netloc=pub.netloc))


def is_direct_download_likely_accessible(storage: StorageBackend) -> bool:
    if (getattr(settings, "S3_PUBLIC_ENDPOINT", None) or os.environ.get("S3_PUBLIC_ENDPOINT", "")).strip():
        return True
    host = storage.endpoint.split(":")[0].strip()
    if _INTERNAL_HOST_RE.match(host):
        return False
    if "." not in host and not host.isdigit():
        return False
    return True


def validate_backup_object_key(key: str) -> str:
    key = (key or "").strip().lstrip("/")
    if not key or ".." in key or not key.endswith(".tar.gz"):
        raise ValueError("无效的备份对象 Key")
    return key


def build_backup_download_options(storage: StorageBackend, key: str, expires: int = 3600) -> dict:
    key = validate_backup_object_key(key)
    filename = key.split("/")[-1]
    direct_url = generate_presigned_download_url(storage, key, expires=expires)
    direct_accessible = is_direct_download_likely_accessible(storage)
    return {
        "filename": filename,
        "storage_id": storage.id,
        "storage_name": storage.name,
        "key": key,
        "proxy_url": f"/api/backups/download/proxy/?storage_id={storage.id}&key={quote(key, safe='/')}",
        "direct_url": direct_url,
        "direct_accessible": direct_accessible,
        "direct_hint": (
            "当前存储 Endpoint 为内网地址，浏览器可能无法直连；请使用 API 代理下载。"
            if not direct_accessible
            else "在浏览器中打开预签名链接，从对象存储直连下载（不经过控制台转发）。"
        ),
        "expires_in": expires,
    }


def stream_storage_object(storage: StorageBackend, key: str) -> StreamingHttpResponse:
    key = validate_backup_object_key(key)
    client = get_boto3_client(storage)
    try:
        obj = client.get_object(Bucket=storage.bucket, Key=key)
    except ClientError as exc:
        raise ValueError(str(exc)) from exc

    filename = key.split("/")[-1]
    body = obj["Body"]
    response = StreamingHttpResponse(
        body.iter_chunks(chunk_size=1024 * 1024),
        content_type=obj.get("ContentType") or "application/gzip",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    content_length = obj.get("ContentLength")
    if content_length is not None:
        response["Content-Length"] = str(content_length)
    return response


def import_from_env() -> StorageBackend | None:
    import os

    if StorageBackend.objects.exists():
        return None

    if os.environ.get("S3_BACKUP_ENABLED", "false").lower() != "true":
        return None

    endpoint = os.environ.get("S3_ENDPOINT", "")
    access_key = os.environ.get("S3_ACCESS_KEY", "")
    secret_key = os.environ.get("S3_SECRET_KEY", "")
    if not endpoint or not access_key:
        return None

    storage = StorageBackend(
        name="默认对象存储",
        alias=os.environ.get("S3_ALIAS", "s3"),
        endpoint=endpoint,
        bucket=os.environ.get("S3_BUCKET", "mysql"),
        region=os.environ.get("S3_REGION", "us-east-1"),
        use_ssl=os.environ.get("S3_USE_SSL", "false").lower() == "true",
        force_path_style=os.environ.get("S3_FORCE_PATH_STYLE", "true").lower() == "true",
        enabled=True,
    )
    storage.set_access_key(access_key)
    storage.set_secret_key(secret_key)
    storage.save()
    export_storages_config()
    return storage
