from __future__ import annotations

import os
import re
from pathlib import Path

from django.conf import settings

from apps.core.models import SystemCredential


ENV_KEY_MAP = {
    "root": "MYSQL_ROOT_PASSWORD",
    "backup": "MYSQL_BACKUP_PASSWORD",
    "default_user": "MYSQL_PASSWORD",
}


def sync_credential_to_env(role: str, password: str) -> None:
    """更新 backup.env 中的 MySQL 密码变量"""
    env_key = ENV_KEY_MAP.get(role)
    if not env_key:
        return

    backup_env = Path(settings.BACKUP_ENV_FILE)
    lines: list[str] = []
    if backup_env.exists():
        lines = backup_env.read_text(encoding="utf-8").splitlines()

    safe = (
        str(password)
        .replace("\\", "\\\\")
        .replace("'", "'\"'\"'")
        .replace("\n", " ")
        .strip()
    )
    new_line = f"export {env_key}='{safe}'"
    pattern = re.compile(rf"^export {env_key}=")
    replaced = False
    updated = []
    for line in lines:
        if pattern.match(line):
            updated.append(new_line)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(new_line)
    backup_env.parent.mkdir(parents=True, exist_ok=True)
    backup_env.write_text("\n".join(updated) + "\n", encoding="utf-8")


def upsert_system_credential(role: str, username: str, password: str) -> SystemCredential:
    cred, _ = SystemCredential.objects.get_or_create(
        role=role,
        defaults={"username": username},
    )
    cred.username = username
    cred.set_password(password)
    cred.save()
    sync_credential_to_env(role, password)
    return cred


def get_effective_password(role: str) -> str:
    try:
        cred = SystemCredential.objects.get(role=role)
        if cred.password:
            return cred.password
    except SystemCredential.DoesNotExist:
        pass
    env_key = ENV_KEY_MAP.get(role, "")
    return os.environ.get(env_key, "")
