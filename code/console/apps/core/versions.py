"""采集一体环境各组件版本。"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from django.conf import settings


def collect_versions() -> dict:
    return {
        "app": _app_version(),
        "os": _os_pretty(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "django": _django_version(),
        "celery": _pkg_version("celery"),
        "mysql": _mysql_version(),
        "redis": _redis_version(),
        "xtrabackup": _cmd_version(["xtrabackup", "--version"]),
        "nginx": _cmd_version(["nginx", "-v"]),
    }


def _app_version() -> str:
    for key in ("APP_VERSION", "BACKUP_APP_VERSION"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw
    for path in (Path("/app/VERSION"), Path("/opt/mysql-backup/VERSION")):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("version="):
                return line.split("=", 1)[1].strip() or "0.1.0"
    try:
        from mysql_backup import __version__
        return str(__version__)
    except Exception:
        return "0.1.0"


def _os_pretty() -> str:
    path = Path("/etc/os-release")
    if path.is_file():
        data = {}
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                data[key] = val.strip().strip('"')
        return data.get("PRETTY_NAME") or data.get("NAME") or platform.platform()
    return platform.platform()


def _django_version() -> str:
    try:
        import django

        return django.get_version()
    except Exception:
        return ""


def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return ""


def _mysql_version() -> str:
    try:
        import MySQLdb

        conn = MySQLdb.connect(
            host=getattr(settings, "MYSQL_HOST", "127.0.0.1"),
            port=int(getattr(settings, "MYSQL_PORT", 3306) or 3306),
            user="root",
            passwd=getattr(settings, "MYSQL_ROOT_PASSWORD", "") or "",
            connect_timeout=2,
        )
        try:
            cur = conn.cursor()
            cur.execute("SELECT VERSION()")
            row = cur.fetchone()
            return str(row[0]) if row else ""
        finally:
            conn.close()
    except Exception:
        return ""


def _redis_version() -> str:
    try:
        from redis import Redis

        url = getattr(settings, "CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
        client = Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        info = client.info("server")
        return str(info.get("redis_version") or "")
    except Exception:
        return ""


def _cmd_version(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=False)
    except Exception:
        return ""
    text = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
    if cmd and "xtrabackup" in cmd[0]:
        for line in text.splitlines():
            if "xtrabackup version" in line.lower():
                return line.strip()[:160]
        import re

        match = re.search(r"xtrabackup version [0-9][^\s,]*", text, re.IGNORECASE)
        if match:
            return match.group(0)
    for line in text.splitlines():
        line = line.strip()
        if line and "recognized server arguments" not in line.lower():
            return line[:160]
    return ""
