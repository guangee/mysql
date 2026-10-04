"""运行时模式：allinone（单容器本机）与 split（控制台 docker exec MySQL）。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from django.conf import settings


class RuntimeError_(Exception):
    """运行时适配错误（避免遮盖内置 RuntimeError）。"""


def runtime_mode() -> str:
    mode = (os.environ.get("RUNTIME_MODE") or getattr(settings, "RUNTIME_MODE", "allinone") or "allinone")
    mode = str(mode).strip().lower()
    return mode if mode in {"allinone", "split"} else "allinone"


def is_allinone() -> bool:
    return runtime_mode() == "allinone"


def mysql_data_dir() -> Path:
    return Path(getattr(settings, "MYSQL_DATA_DIR", "/var/lib/mysql"))


def mysql_config_dir() -> Path:
    return Path(getattr(settings, "MYSQL_CONFIG_DIR", "/etc/mysql/conf.d"))


def backup_base_dir() -> Path:
    return Path(getattr(settings, "BACKUP_BASE_DIR", "/backups"))


def run_host_cmd(
    command: list[str],
    *,
    timeout: int = 3600,
    check: bool = True,
    env: dict | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=merged,
            input=input_text,
        )
    except FileNotFoundError as exc:
        raise RuntimeError_(f"命令不可用: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError_(f"命令执行超时 ({timeout}s)") from exc
    if check and result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError_(stderr or f"命令失败，退出码 {result.returncode}")
    return result


def run_mysql_cmd(
    command: list[str],
    *,
    timeout: int = 3600,
    check: bool = True,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """在 MySQL 运行环境中执行命令（allinone=本机，split=docker exec）。"""
    if is_allinone():
        return run_host_cmd(command, timeout=timeout, check=check, env=env)

    from django.conf import settings as dj_settings

    container = dj_settings.DOCKER_MYSQL_CONTAINER
    full_cmd = ["docker", "exec", container] + list(command)
    if env:
        # docker exec -e KEY=VAL ...
        prefixed: list[str] = ["docker", "exec"]
        for key, value in env.items():
            prefixed.extend(["-e", f"{key}={value}"])
        prefixed.append(container)
        prefixed.extend(command)
        full_cmd = prefixed
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError_("docker 命令不可用，请挂载 /var/run/docker.sock") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError_(f"命令执行超时 ({timeout}s)") from exc
    if check and result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError_(stderr or f"命令失败，退出码 {result.returncode}")
    return result


def read_mysql_file(path: str, timeout: int = 30) -> str:
    if is_allinone():
        file_path = Path(path)
        if not file_path.exists():
            return ""
        try:
            return file_path.read_text(encoding="utf-8")
        except OSError:
            return ""
    result = run_mysql_cmd(["cat", path], timeout=timeout, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def write_mysql_file(path: str, content: str, timeout: int = 30) -> None:
    if is_allinone():
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return

    import shlex

    from django.conf import settings as dj_settings

    container = dj_settings.DOCKER_MYSQL_CONTAINER
    parent = str(Path(path).parent)
    run_mysql_cmd(["mkdir", "-p", parent], timeout=timeout, check=True)
    quoted = shlex.quote(path)
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", container, "bash", "-c", f"cat > {quoted}"],
            input=content,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError_("docker 命令不可用，请挂载 /var/run/docker.sock") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError_(f"写入 {path} 超时 ({timeout}s)") from exc
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError_(stderr or f"写入 {path} 失败")


def mysqldump_command(database: str) -> list[str]:
    """返回可直接 Popen/run 的 mysqldump 命令（含环境）。"""
    password = settings.MYSQL_ROOT_PASSWORD
    dump = [
        "mysqldump",
        "-h127.0.0.1",
        "-uroot",
        f"-p{password}",
        "--single-transaction",
        "--source-data=2",
        "--set-gtid-purged=OFF",
        "--hex-blob",
        "--routines",
        "--triggers",
        "--events",
        "--default-character-set=utf8mb4",
        "--column-statistics=0",
        "--net-buffer-length=1048576",
        "--max-allowed-packet=67108864",
        "--databases",
        database,
    ]
    if is_allinone():
        return dump
    return [
        "docker",
        "exec",
        "-e",
        f"MYSQL_PWD={password}",
        settings.DOCKER_MYSQL_CONTAINER,
        "mysqldump",
        "-h127.0.0.1",
        "-uroot",
        "--single-transaction",
        "--source-data=2",
        "--set-gtid-purged=OFF",
        "--hex-blob",
        "--routines",
        "--triggers",
        "--events",
        "--default-character-set=utf8mb4",
        "--column-statistics=0",
        "--net-buffer-length=1048576",
        "--max-allowed-packet=67108864",
        "--databases",
        database,
    ]
