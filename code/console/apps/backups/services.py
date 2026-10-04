import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from apps.backups.models import BackupRetentionPolicy

_CRON_FIELD = re.compile(r"^(\*|[0-9]{1,2}|\*/[0-9]{1,2}|[0-9]{1,2}-[0-9]{1,2}|[0-9]{1,2}(,[0-9]{1,2})+)$")
_TS_RE = re.compile(r"^(\d{8})_(\d{6})$")


def _zone():
    name = os.environ.get("TZ") or settings.TIME_ZONE or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def parse_backup_timestamp(timestamp: str):
    match = _TS_RE.match(timestamp or "")
    if not match:
        return None
    naive = datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
    return timezone.make_aware(naive, _zone())


def validate_cron_schedule(expr: str) -> str:
    parts = (expr or "").strip().split()
    if len(parts) != 5 or not all(_CRON_FIELD.match(part) for part in parts):
        raise ValueError(f"无效的 cron 表达式: {expr}")
    return " ".join(parts)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def get_retention_policy() -> BackupRetentionPolicy:
    policy = BackupRetentionPolicy.objects.order_by("id").first()
    if policy:
        return policy
    return BackupRetentionPolicy.objects.create(
        full_backup_schedule=os.environ.get("FULL_BACKUP_SCHEDULE", "0 2 * * 0"),
        incremental_backup_schedule=os.environ.get("INCREMENTAL_BACKUP_SCHEDULE", "0 3 * * *"),
        full_retention_days=_env_int("FULL_BACKUP_RETENTION_DAYS", _env_int("BACKUP_RETENTION_DAYS", 30)),
        incremental_retention_days=_env_int("INCREMENTAL_BACKUP_RETENTION_DAYS", _env_int("BACKUP_RETENTION_DAYS", 14)),
        cleanup_local_schedule=os.environ.get("CLEANUP_LOCAL_SCHEDULE", "0 * * * *"),
        cleanup_s3_schedule=os.environ.get("CLEANUP_S3_SCHEDULE", "0 4 * * *"),
    )


def retention_to_dict(policy: BackupRetentionPolicy | None = None) -> dict:
    policy = policy or get_retention_policy()
    return {
        "full_backup_schedule": policy.full_backup_schedule,
        "incremental_backup_schedule": policy.incremental_backup_schedule,
        "full_backup_enabled": policy.full_backup_enabled,
        "incremental_backup_enabled": policy.incremental_backup_enabled,
        "full_retention_days": policy.full_retention_days,
        "incremental_retention_days": policy.incremental_retention_days,
        "cleanup_local_schedule": policy.cleanup_local_schedule,
        "cleanup_s3_schedule": policy.cleanup_s3_schedule,
        "cleanup_local_enabled": policy.cleanup_local_enabled,
        "cleanup_s3_enabled": policy.cleanup_s3_enabled,
        "updated_at": policy.updated_at,
    }


def export_retention_policy(policy: BackupRetentionPolicy | None = None) -> None:
    policy = policy or get_retention_policy()
    payload = retention_to_dict(policy)
    updated = payload.get("updated_at")
    if hasattr(updated, "isoformat"):
        payload["updated_at"] = updated.isoformat()
    target = Path(settings.SHARED_BACKUP_POLICY_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def init_retention_policy_from_env() -> BackupRetentionPolicy:
    policy = get_retention_policy()
    export_retention_policy(policy)
    return policy


def annotate_backup_expiry(items: list[dict], retention_days: int) -> None:
    now = timezone.now()
    for item in items:
        backed_up_at = parse_backup_timestamp(item.get("timestamp") or "")
        item["retention_days"] = retention_days
        if not backed_up_at:
            item["expires_at"] = None
            item["expiry_status"] = "ok"
            continue
        expires_at = backed_up_at + timedelta(days=retention_days)
        item["expires_at"] = expires_at.isoformat()
        if now >= expires_at:
            item["expiry_status"] = "expired"
        elif expires_at - now <= timedelta(days=2):
            item["expiry_status"] = "warning"
        else:
            item["expiry_status"] = "ok"
