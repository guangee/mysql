"""
备份 crontab 配置（供 start_backup / update_crontab 共用）
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

FULL_BACKUP_SCHEDULE = os.environ.get("FULL_BACKUP_SCHEDULE", "0 2 * * 0")
INCREMENTAL_BACKUP_SCHEDULE = os.environ.get("INCREMENTAL_BACKUP_SCHEDULE", "0 3 * * *")
LOCAL_BACKUP_RETENTION_HOURS = int(os.environ.get("LOCAL_BACKUP_RETENTION_HOURS", "0"))
BACKUP_BASE_DIR = Path(os.environ.get("BACKUP_BASE_DIR", "/backups"))
BACKUP_POLICY_FILE = Path(os.environ.get("BACKUP_POLICY_FILE", "/shared/backup_policy.json"))

CRON_ENV_VARS = [
    "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET", "S3_BACKUP_ENABLED",
    "S3_REGION", "S3_USE_SSL", "S3_FORCE_PATH_STYLE", "S3_ALIAS",
    "STORAGES_CONFIG_FILE", "BACKUP_POLICY_FILE",
    "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD",
    "MYSQL_BACKUP_USER", "MYSQL_BACKUP_PASSWORD",
    "BACKUP_BASE_DIR", "LOCAL_BACKUP_RETENTION_HOURS", "BACKUP_RETENTION_DAYS",
    "FULL_BACKUP_RETENTION_DAYS", "INCREMENTAL_BACKUP_RETENTION_DAYS",
    "FULL_BACKUP_SCHEDULE", "INCREMENTAL_BACKUP_SCHEDULE",
    "CLEANUP_LOCAL_SCHEDULE", "CLEANUP_S3_SCHEDULE",
    "TZ", "PYTHONPATH",
]


def _default_local_schedule() -> str:
    return os.environ.get(
        "CLEANUP_LOCAL_SCHEDULE",
        "* * * * *" if LOCAL_BACKUP_RETENTION_HOURS == 0 else "0 * * * *",
    )


def _default_s3_schedule() -> str:
    return os.environ.get("CLEANUP_S3_SCHEDULE", "0 4 * * *")


def load_policy_overrides() -> dict:
    if not BACKUP_POLICY_FILE.exists():
        return {}
    try:
        data = json.loads(BACKUP_POLICY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_cleanup_schedules() -> dict:
    policy = load_policy_overrides()
    return {
        "cleanup_local_schedule": policy.get("cleanup_local_schedule") or _default_local_schedule(),
        "cleanup_s3_schedule": policy.get("cleanup_s3_schedule") or _default_s3_schedule(),
        "cleanup_local_enabled": policy.get("cleanup_local_enabled", True),
        "cleanup_s3_enabled": policy.get("cleanup_s3_enabled", True),
    }


def get_backup_schedules() -> dict:
    policy = load_policy_overrides()
    return {
        "full_backup_schedule": policy.get("full_backup_schedule") or FULL_BACKUP_SCHEDULE,
        "incremental_backup_schedule": policy.get("incremental_backup_schedule") or INCREMENTAL_BACKUP_SCHEDULE,
        "full_backup_enabled": policy.get("full_backup_enabled", True),
        "incremental_backup_enabled": policy.get("incremental_backup_enabled", True),
    }


def log_default(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def apply_backup_crontab(log: Callable[[str], None] | None = None) -> bool:
    log = log or log_default
    backup_schedules = get_backup_schedules()
    full_backup_schedule = backup_schedules["full_backup_schedule"]
    incremental_backup_schedule = backup_schedules["incremental_backup_schedule"]
    full_backup_enabled = backup_schedules["full_backup_enabled"]
    incremental_backup_enabled = backup_schedules["incremental_backup_enabled"]
    schedules = get_cleanup_schedules()
    cleanup_local_schedule = schedules["cleanup_local_schedule"]
    cleanup_s3_schedule = schedules["cleanup_s3_schedule"]
    cleanup_local_enabled = schedules["cleanup_local_enabled"]
    cleanup_s3_enabled = schedules["cleanup_s3_enabled"]

    log("配置备份计划任务...")

    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
        existing_crontab = result.stdout if result.returncode == 0 else ""
    except Exception:
        existing_crontab = ""

    new_crontab_lines = []
    managed_tokens = (
        "full_backup",
        "incremental_backup",
        "cleanup_old_backups",
        "mysql_backup backup",
        "mysql_backup schedule",
    )
    for line in existing_crontab.split("\n"):
        if line.strip() and not any(token in line for token in managed_tokens):
            new_crontab_lines.append(line)

    env_file = BACKUP_BASE_DIR / "backup.env"
    env_count = 0
    try:
        os.environ["CLEANUP_LOCAL_SCHEDULE"] = cleanup_local_schedule
        os.environ["CLEANUP_S3_SCHEDULE"] = cleanup_s3_schedule
        os.environ["FULL_BACKUP_SCHEDULE"] = full_backup_schedule
        os.environ["INCREMENTAL_BACKUP_SCHEDULE"] = incremental_backup_schedule
        os.environ.setdefault("PYTHONPATH", "/opt/mysql-backup/src")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("# 备份任务环境变量，由 crontab 配置脚本生成\n")
            for var in CRON_ENV_VARS:
                val = os.environ.get(var)
                if val is not None and str(val).strip() != "":
                    safe = str(val).replace("\\", "\\\\").replace("'", "'\"'\"'").replace("\n", " ").strip()
                    f.write(f"export {var}='{safe}'\n")
                    env_count += 1
        log(f"[调度] 已写入 {env_count} 个环境变量到 {env_file}")
    except Exception as exc:
        log(f"警告: 写入 {env_file} 失败: {exc}")
        return False

    source_cmd = f". {env_file} &&"
    if full_backup_enabled:
        new_crontab_lines.append(
            f"{full_backup_schedule} {source_cmd} python3 -m mysql_backup backup full >> {BACKUP_BASE_DIR}/backup.log 2>&1"
        )
    if incremental_backup_enabled:
        new_crontab_lines.append(
            f"{incremental_backup_schedule} {source_cmd} python3 -m mysql_backup backup incremental >> {BACKUP_BASE_DIR}/backup.log 2>&1"
        )
    if cleanup_local_enabled:
        new_crontab_lines.append(
            f"{cleanup_local_schedule} {source_cmd} python3 -m mysql_backup backup cleanup --local-only >> {BACKUP_BASE_DIR}/backup.log 2>&1"
        )
    if cleanup_s3_enabled:
        new_crontab_lines.append(
            f"{cleanup_s3_schedule} {source_cmd} python3 -m mysql_backup backup cleanup --s3-only >> {BACKUP_BASE_DIR}/backup.log 2>&1"
        )

    new_crontab = "\n".join(new_crontab_lines) + "\n"
    try:
        process = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
        process.communicate(input=new_crontab)
        if process.returncode != 0:
            log(f"警告: 配置 crontab 失败，退出码: {process.returncode}")
            return False
    except Exception as exc:
        log(f"警告: 配置 crontab 失败: {exc}")
        return False

    log("备份计划任务已配置:")
    if full_backup_enabled:
        log(f"  全量备份: {full_backup_schedule}")
    else:
        log("  全量备份: 已禁用")
    if incremental_backup_enabled:
        log(f"  增量备份: {incremental_backup_schedule}")
    else:
        log("  增量备份: 已禁用")
    if cleanup_local_enabled:
        log(f"  本地过期清理: {cleanup_local_schedule}")
    else:
        log("  本地过期清理: 已禁用")
    if cleanup_s3_enabled:
        log(f"  对象存储过期清理: {cleanup_s3_schedule}")
    else:
        log("  对象存储过期清理: 已禁用")
    return True
