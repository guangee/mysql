from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from django.conf import settings


class DockerClientError(Exception):
    pass


_MOUNT_SOURCE_CACHE: dict[str, str] | None = None


def _load_mysql_container_mount_sources() -> dict[str, str]:
    """从生产 MySQL 容器 inspect 解析 bind mount 的主机路径（供 docker run -v 使用）"""
    global _MOUNT_SOURCE_CACHE
    if _MOUNT_SOURCE_CACHE is not None:
        return _MOUNT_SOURCE_CACHE

    container = settings.DOCKER_MYSQL_CONTAINER
    result = subprocess.run(
        ["docker", "inspect", container, "--format", "{{json .Mounts}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise DockerClientError(
            (result.stderr or result.stdout or "无法 inspect MySQL 容器").strip()
        )

    mapping: dict[str, str] = {}
    for mount in json.loads(result.stdout or "[]"):
        dest = mount.get("Destination")
        source = mount.get("Source")
        if dest and source:
            mapping[dest] = source

    if not mapping:
        raise DockerClientError(f"容器 {container} 未找到 bind mount 信息")

    _MOUNT_SOURCE_CACHE = mapping
    return mapping


def _host_bind_path(mount_dest: str) -> str:
    """从 MySQL 容器 inspect 解析 bind mount 的主机源路径（供 docker run -v 使用）"""
    mounts = _load_mysql_container_mount_sources()
    host_source = mounts.get(mount_dest)
    if not host_source:
        raise DockerClientError(f"MySQL 容器未挂载 {mount_dest}")
    return host_source


def read_file_in_mysql_container(container_path: str, timeout: int = 30) -> str:
    result = exec_in_mysql_container(["cat", container_path], timeout=timeout, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def write_file_in_mysql_container(container_path: str, content: str, timeout: int = 30) -> None:
    import shlex

    container = settings.DOCKER_MYSQL_CONTAINER
    parent = str(Path(container_path).parent)
    exec_in_mysql_container(["mkdir", "-p", parent], timeout=timeout)
    quoted = shlex.quote(container_path)
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
        raise DockerClientError("docker 命令不可用，请挂载 /var/run/docker.sock") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerClientError(f"写入 {container_path} 超时 ({timeout}s)") from exc
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise DockerClientError(stderr or f"写入 {container_path} 失败")


def exec_in_mysql_container(
    command: list[str],
    timeout: int = 3600,
    check: bool = True,
) -> subprocess.CompletedProcess:
    container = settings.DOCKER_MYSQL_CONTAINER
    full_cmd = ["docker", "exec", container] + command
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DockerClientError("docker 命令不可用，请挂载 /var/run/docker.sock") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerClientError(f"命令执行超时 ({timeout}s)") from exc

    if check and result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise DockerClientError(stderr or f"命令失败，退出码 {result.returncode}")
    return result


def run_backup_command(backup_type: str) -> subprocess.CompletedProcess:
    if backup_type not in {"full", "incremental"}:
        raise ValueError(f"未知备份类型: {backup_type}")
    return exec_in_mysql_container(
        ["python3", "-u", "-m", "mysql_backup", "backup", backup_type],
        timeout=3600,
        check=False,
    )


def run_backup_command_streaming(backup_type: str, on_line, timeout: int = 3600) -> subprocess.CompletedProcess:
    """边执行备份边回调每一行输出，供任务页实时展示进度。"""
    import select
    import time

    if backup_type not in {"full", "incremental"}:
        raise ValueError(f"未知备份类型: {backup_type}")
    container = settings.DOCKER_MYSQL_CONTAINER
    cmd = [
        "docker", "exec",
        "-e", "PYTHONUNBUFFERED=1",
        container,
        "python3", "-u", "-m", "mysql_backup", "backup", backup_type,
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise DockerClientError("docker 命令不可用，请挂载 /var/run/docker.sock") from exc

    lines: list[str] = []
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    try:
        while True:
            if time.monotonic() > deadline:
                proc.kill()
                raise DockerClientError(f"命令执行超时 ({timeout}s)")
            ready, _, _ = select.select([proc.stdout], [], [], 1)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            raw = proc.stdout.readline()
            if not raw:
                break
            line = raw.rstrip("\n")
            lines.append(line)
            if on_line:
                on_line(line)
        code = proc.wait(timeout=30)
    except DockerClientError:
        raise
    except Exception as exc:
        proc.kill()
        raise DockerClientError(str(exc)) from exc
    return subprocess.CompletedProcess(cmd, code, stdout="\n".join(lines), stderr="")


def run_cleanup_command(scope: str) -> subprocess.CompletedProcess:
    if scope not in {"local", "s3", "all"}:
        raise ValueError(f"未知清理范围: {scope}")
    if scope == "local":
        cmd = ["python3", "-m", "mysql_backup", "backup", "cleanup", "--local-only"]
    elif scope == "s3":
        cmd = ["python3", "-m", "mysql_backup", "backup", "cleanup", "--s3-only"]
    else:
        cmd = ["python3", "-m", "mysql_backup", "backup", "cleanup"]
    return exec_in_mysql_container(cmd, timeout=1800, check=False)


def apply_backup_crontab() -> subprocess.CompletedProcess:
    return exec_in_mysql_container(
        ["python3", "-m", "mysql_backup", "schedule", "update"],
        timeout=60,
        check=False,
    )


def run_pitr_restore(target_time: str, full_backup_timestamp: str = "") -> subprocess.CompletedProcess:
    """停止 MySQL 容器 → 一次性容器执行 PITR → 重新启动 MySQL"""
    from django.conf import settings

    container = settings.DOCKER_MYSQL_CONTAINER
    image = settings.DOCKER_MYSQL_IMAGE
    if not image:
        raise DockerClientError("未配置 MYSQL_IMAGE，无法执行时间点恢复")

    logs: list[str] = []

    def _run(cmd: list[str], timeout: int = 120, check: bool = False) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)
        except FileNotFoundError as exc:
            raise DockerClientError("docker 命令不可用") from exc
        except subprocess.TimeoutExpired as exc:
            raise DockerClientError(f"命令执行超时 ({timeout}s): {' '.join(cmd)}") from exc

    stop = _run(["docker", "stop", container], timeout=180)
    logs.append(f"[停止容器] exit={stop.returncode}\n{(stop.stdout or '') + (stop.stderr or '')}".strip())

    pitr_cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "mysql_backup-network",
    ]
    passthrough_keys = [
        "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET", "S3_BACKUP_ENABLED",
        "S3_REGION", "S3_USE_SSL", "S3_FORCE_PATH_STYLE", "S3_ALIAS",
        "STORAGES_CONFIG_FILE", "BACKUP_POLICY_FILE", "BACKUP_BASE_DIR", "BACKUP_TIMEZONE",
    ]
    for key in passthrough_keys:
        val = os.environ.get(key)
        if val is not None and str(val).strip() != "":
            pitr_cmd.extend(["-e", f"{key}={val}"])
    pitr_cmd.extend([
        "-e",
        f"RESTORE_TZ={settings.RESTORE_TIMEZONE}",
        "-e",
        "AUTO_STOP_MYSQL=true",
        "-e",
        f"MYSQL_ROOT_PASSWORD={settings.MYSQL_ROOT_PASSWORD}",
        "-v",
        f"{_host_bind_path('/var/lib/mysql')}:/var/lib/mysql",
        "-v",
        f"{_host_bind_path('/etc/mysql/conf.d')}:/etc/mysql/conf.d",
        "-v",
        f"{_host_bind_path('/backups')}:/backups",
        "-v",
        f"{_host_bind_path('/shared')}:/shared",
        "-e",
        "TZ=Asia/Shanghai",
        image,
        "python3", "-m", "mysql_backup", "restore", "pitr",
        target_time,
    ])
    if full_backup_timestamp:
        pitr_cmd.append(full_backup_timestamp)

    pitr = _run(pitr_cmd, timeout=7200)
    logs.append(f"[PITR 脚本] exit={pitr.returncode}\n{(pitr.stdout or '') + (pitr.stderr or '')}".strip())

    start = _run(["docker", "start", container], timeout=120)
    logs.append(f"[启动容器] exit={start.returncode}\n{(start.stdout or '') + (start.stderr or '')}".strip())

    combined = subprocess.CompletedProcess(
        args=pitr_cmd,
        returncode=pitr.returncode if pitr.returncode != 0 else start.returncode,
        stdout="\n\n".join(logs),
        stderr="",
    )
    return combined


def run_full_backup_restore(full_backup_timestamp: str) -> subprocess.CompletedProcess:
    """停止 MySQL → 从指定全量备份恢复 → 重新启动 MySQL"""
    container = settings.DOCKER_MYSQL_CONTAINER
    image = settings.DOCKER_MYSQL_IMAGE
    if not image:
        raise DockerClientError("未配置 MYSQL_IMAGE，无法执行全量恢复")

    ts = (full_backup_timestamp or "").strip()
    if not ts:
        raise DockerClientError("未指定全量备份时间戳")

    logs: list[str] = []

    stop = _docker_run(["docker", "stop", container], timeout=180)
    logs.append(f"[停止容器] exit={stop.returncode}\n{(stop.stdout or '') + (stop.stderr or '')}".strip())

    restore_shell = (
        "set -e; "
        f"python3 -m mysql_backup restore backup {ts}; "
        'RESTORE_DIR=$(dirname "$(find /backups/restore -name backup-my.cnf | head -1)"); '
        '[ -f "$RESTORE_DIR/backup-my.cnf" ] || RESTORE_DIR=/backups/restore; '
        'python3 -m mysql_backup restore apply "$RESTORE_DIR"'
    )

    restore_cmd = [
        "docker", "run", "--rm", "--network", DOCKER_NETWORK,
        *_pitr_passthrough_env(),
        "-e", f"MYSQL_ROOT_PASSWORD={settings.MYSQL_ROOT_PASSWORD}",
        "-e", "BACKUP_EXISTING_DATA=true",
        "-v", f"{_host_bind_path('/var/lib/mysql')}:/var/lib/mysql",
        "-v", f"{_host_bind_path('/etc/mysql/conf.d')}:/etc/mysql/conf.d",
        "-v", f"{_host_bind_path('/backups')}:/backups",
        "-v", f"{_host_bind_path('/shared')}:/shared",
        "-e", "TZ=Asia/Shanghai",
        image,
        "bash", "-c", restore_shell,
    ]

    restore = _docker_run(restore_cmd, timeout=7200)
    logs.append(f"[全量恢复] exit={restore.returncode}\n{(restore.stdout or '') + (restore.stderr or '')}".strip())

    start = _docker_run(["docker", "start", container], timeout=120)
    logs.append(f"[启动容器] exit={start.returncode}\n{(start.stdout or '') + (start.stderr or '')}".strip())

    return subprocess.CompletedProcess(
        args=restore_cmd,
        returncode=restore.returncode if restore.returncode != 0 else start.returncode,
        stdout="\n\n".join(logs),
        stderr="",
    )


DOCKER_NETWORK = "mysql_backup-network"

MYSQLD_ARGS = [
    "--default-authentication-plugin=mysql_native_password",
    "--log-bin=mysql-bin",
    "--binlog-format=ROW",
    "--server-id=99",
    "--character-set-server=utf8mb4",
    "--collation-server=utf8mb4_general_ci",
    "--explicit_defaults_for_timestamp=true",
    "--bind-address=0.0.0.0",
    "--default-time-zone=+08:00",
    "--binlog-expire-logs-seconds=2592000",
]


def _docker_run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise DockerClientError("docker 命令不可用") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerClientError(f"命令执行超时 ({timeout}s)") from exc


def _pitr_passthrough_env() -> list[str]:
    args: list[str] = []
    keys = [
        "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET", "S3_BACKUP_ENABLED",
        "S3_REGION", "S3_USE_SSL", "S3_FORCE_PATH_STYLE", "S3_ALIAS",
        "STORAGES_CONFIG_FILE", "BACKUP_POLICY_FILE", "BACKUP_BASE_DIR", "BACKUP_TIMEZONE",
    ]
    for key in keys:
        val = os.environ.get(key)
        if val is not None and str(val).strip() != "":
            args.extend(["-e", f"{key}={val}"])
    return args


def _pitr_volume_mounts(volume_name: str) -> list[str]:
    return [
        "-v",
        f"{volume_name}:/var/lib/mysql",
        "-v",
        f"{_host_bind_path('/etc/mysql/conf.d')}:/etc/mysql/conf.d",
        "-v",
        f"{_host_bind_path('/backups')}:/backups",
        "-v",
        f"{_host_bind_path('/shared')}:/shared",
        "-e",
        "TZ=Asia/Shanghai",
    ]


def run_pitr_on_docker_volume(
    volume_name: str,
    target_time: str,
    full_backup_timestamp: str = "",
) -> subprocess.CompletedProcess:
    """在独立 Docker 卷上执行 PITR，不影响生产实例"""
    image = settings.DOCKER_MYSQL_IMAGE
    if not image:
        raise DockerClientError("未配置 MYSQL_IMAGE")

    _docker_run(["docker", "volume", "create", volume_name], timeout=60)

    cmd = [
        "docker", "run", "--rm", "--network", DOCKER_NETWORK,
        *_pitr_passthrough_env(),
        "-e", f"RESTORE_TZ={settings.RESTORE_TIMEZONE}",
        "-e", "AUTO_STOP_MYSQL=true",
        "-e", f"MYSQL_ROOT_PASSWORD={settings.MYSQL_ROOT_PASSWORD}",
        *_pitr_volume_mounts(volume_name),
        image,
        "python3", "-m", "mysql_backup", "restore", "pitr", target_time,
    ]
    if full_backup_timestamp:
        cmd.append(full_backup_timestamp)
    return _docker_run(cmd, timeout=7200)


def start_temp_mysql_container(container_name: str, volume_name: str) -> None:
    image = settings.DOCKER_MYSQL_IMAGE
    if not image:
        raise DockerClientError("未配置 MYSQL_IMAGE")

    _docker_run(["docker", "rm", "-f", container_name], timeout=60)
    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "--network", DOCKER_NETWORK,
        "--entrypoint", "/usr/local/bin/docker-entrypoint.sh",
        "-e", f"MYSQL_ROOT_PASSWORD={settings.MYSQL_ROOT_PASSWORD}",
        *_pitr_volume_mounts(volume_name),
        image,
        "mysqld",
        *MYSQLD_ARGS,
    ]
    result = _docker_run(cmd, timeout=120)
    if result.returncode != 0:
        raise DockerClientError((result.stderr or result.stdout or "启动临时 MySQL 失败").strip())


def wait_mysql_container_ready(container_name: str, timeout: int = 180) -> None:
    import time

    password = settings.MYSQL_ROOT_PASSWORD
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _docker_run(
            [
                "docker", "exec", container_name,
                "mysqladmin", "ping", "-h127.0.0.1", "-uroot", f"-p{password}", "--silent",
            ],
            timeout=15,
        )
        if result.returncode == 0:
            time.sleep(8)
            return
        time.sleep(3)
    raise DockerClientError(f"临时 MySQL {container_name} 在 {timeout}s 内未就绪")


def dump_database_from_container(
    container_name: str,
    database: str,
    dump_path: Path,
    check_only: bool = False,
) -> bool:
    password = settings.MYSQL_ROOT_PASSWORD
    exists = _docker_run(
        [
            "docker", "exec", container_name,
            "mysql", "-h127.0.0.1", "-uroot", f"-p{password}", "-N", "-e",
            f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = '{database}'",
        ],
        timeout=60,
    )
    if not (exists.stdout or "").strip():
        return False
    if check_only:
        return True

    dump_path.parent.mkdir(parents=True, exist_ok=True)
    with dump_path.open("w", encoding="utf-8") as outfile:
        result = subprocess.run(
            [
                "docker", "exec", container_name,
                "mysqldump",
                "-h127.0.0.1", "-uroot", f"-p{password}",
                "--single-transaction", "--routines", "--triggers", "--events",
                "--set-gtid-purged=OFF",
                database,
            ],
            stdout=outfile,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3600,
            check=False,
        )
    if result.returncode != 0:
        raise DockerClientError((result.stderr or "mysqldump 失败").strip()[-2000:])
    return True


def prepare_production_database(database: str, write_mode: str) -> None:
    from apps.core.mysql_client import mysql_cursor

    with mysql_cursor() as cur:
        if write_mode == "overwrite":
            cur.execute(
                """
                SELECT id FROM information_schema.processlist
                WHERE db = %s AND id != CONNECTION_ID()
                """,
                [database],
            )
            for (pid,) in cur.fetchall():
                try:
                    cur.execute(f"KILL {int(pid)}")
                except Exception:
                    pass
            cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
        cur.execute(
            f"CREATE DATABASE `{database}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )


def import_database_to_production(database: str, dump_path: Path) -> None:
    container = settings.DOCKER_MYSQL_CONTAINER
    password = settings.MYSQL_ROOT_PASSWORD
    with dump_path.open("r", encoding="utf-8") as infile:
        result = subprocess.run(
            [
                "docker", "exec", "-i", container,
                "mysql", "-h127.0.0.1", "-uroot", f"-p{password}", database,
            ],
            stdin=infile,
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    if result.returncode != 0:
        raise DockerClientError((result.stderr or result.stdout or "导入失败").strip()[-2000:])


def cleanup_pitr_resources(container_name: str, volume_name: str) -> None:
    _docker_run(["docker", "rm", "-f", container_name], timeout=120)
    _docker_run(["docker", "volume", "rm", "-f", volume_name], timeout=120)


def reload_backup_scheduler() -> subprocess.CompletedProcess:
    return exec_in_mysql_container(
        ["python3", "-m", "mysql_backup", "schedule", "start"],
        timeout=120,
        check=False,
    )


def tail_backup_log(lines: int = 200) -> str:
    result = exec_in_mysql_container(
        ["tail", "-n", str(lines), "/backups/backup.log"],
        timeout=30,
        check=False,
    )
    return result.stdout or result.stderr or ""


def _parse_docker_size_pair(text: str) -> tuple[int, int]:
    """解析 docker stats 的 '512MiB / 15.64GiB' 格式为字节"""
    units = {
        "B": 1,
        "KIB": 1024,
        "MIB": 1024**2,
        "GIB": 1024**3,
        "TIB": 1024**4,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
    }

    def to_bytes(part: str) -> int:
        part = part.strip().upper()
        match = re.match(r"^([\d.]+)\s*([A-Z]+)$", part)
        if not match:
            return 0
        value = float(match.group(1))
        unit = match.group(2)
        return int(value * units.get(unit, 1))

    if "/" not in text:
        return 0, 0
    used, total = text.split("/", 1)
    return to_bytes(used), to_bytes(total)


def get_mysql_service_status() -> dict:
    """查询生产 MySQL 容器运行状态与就绪情况。"""
    container = settings.DOCKER_MYSQL_CONTAINER
    result = subprocess.run(
        ["docker", "inspect", container, "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or f"无法 inspect 容器 {container}").strip()
        return {
            "container": container,
            "exists": False,
            "running": False,
            "ready": False,
            "status": "not_found",
            "error": err[:300],
        }

    info = json.loads(result.stdout or "{}")
    state = info.get("State") or {}
    status = str(state.get("Status") or "unknown")
    running = bool(state.get("Running"))
    ready = False
    ready_error = ""
    if running:
        try:
            ready = _mysql_container_ping(container, timeout=10)
        except Exception as exc:
            ready_error = str(exc)[:200]

    return {
        "container": container,
        "exists": True,
        "running": running,
        "ready": ready,
        "status": status,
        "paused": bool(state.get("Paused")),
        "restarting": bool(state.get("Restarting")),
        "oom_killed": bool(state.get("OOMKilled")),
        "pid": _safe_int(state.get("Pid")),
        "exit_code": _safe_int(state.get("ExitCode")),
        "error": (state.get("Error") or ready_error or "")[:300],
        "started_at": state.get("StartedAt") or "",
        "finished_at": state.get("FinishedAt") or "",
        "image": ((info.get("Config") or {}).get("Image") or ""),
    }


def _mysql_container_ping(container_name: str, timeout: int = 15) -> bool:
    password = settings.MYSQL_ROOT_PASSWORD
    result = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "mysqladmin",
            "ping",
            "-h127.0.0.1",
            "-uroot",
            f"-p{password}",
            "--silent",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return result.returncode == 0


def control_mysql_service(action: str, wait_ready: bool = True, ready_timeout: int = 180) -> dict:
    """对生产 MySQL 容器执行 start / stop / restart。"""
    action = (action or "").strip().lower()
    if action not in {"start", "stop", "restart"}:
        raise ValueError("action 只能是 start、stop 或 restart")

    container = settings.DOCKER_MYSQL_CONTAINER
    before = get_mysql_service_status()
    if not before.get("exists"):
        raise DockerClientError(before.get("error") or f"容器 {container} 不存在")

    if action == "start" and before.get("running"):
        status = get_mysql_service_status()
        status["action"] = action
        status["message"] = "MySQL 已在运行"
        return status
    if action == "stop" and not before.get("running"):
        status = get_mysql_service_status()
        status["action"] = action
        status["message"] = "MySQL 已处于停止状态"
        return status

    result = subprocess.run(
        ["docker", action, container],
        capture_output=True,
        text=True,
        timeout=120 if action != "restart" else 180,
        check=False,
    )
    if result.returncode != 0:
        raise DockerClientError(
            (result.stderr or result.stdout or f"docker {action} {container} 失败").strip()[:500]
        )

    if action in {"start", "restart"} and wait_ready:
        wait_mysql_container_ready(container, timeout=ready_timeout)

    status = get_mysql_service_status()
    status["action"] = action
    if action == "stop":
        status["message"] = "MySQL 已停止"
    elif status.get("ready"):
        status["message"] = "MySQL 已启动并就绪" if action == "start" else "MySQL 已重启并就绪"
    else:
        status["message"] = f"docker {action} 已执行，等待 MySQL 就绪中"
    return status


def get_mysql_host_stats() -> dict:
    """采集 MySQL 容器的主机级负载、内存与磁盘占用"""
    container = settings.DOCKER_MYSQL_CONTAINER
    stats: dict = {"available": False}

    try:
        proc = subprocess.run(
            ["docker", "stats", container, "--no-stream", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            raw = json.loads(proc.stdout.strip())
            mem_used, mem_total = _parse_docker_size_pair(raw.get("MemUsage", ""))
            stats.update(
                {
                    "available": True,
                    "cpu_percent": float(str(raw.get("CPUPerc", "0")).replace("%", "") or 0),
                    "memory_used_bytes": mem_used,
                    "memory_total_bytes": mem_total,
                    "memory_usage_percent": round(mem_used / mem_total * 100, 1) if mem_total else 0,
                    "pids": _safe_int(raw.get("PIDs")),
                }
            )
        elif proc.stderr:
            stats["error"] = proc.stderr.strip()[:200]
    except Exception as exc:
        stats["error"] = str(exc)

    load_result = exec_in_mysql_container(["cat", "/proc/loadavg"], timeout=10, check=False)
    if load_result.returncode == 0 and load_result.stdout.strip():
        parts = load_result.stdout.strip().split()
        if len(parts) >= 3:
            stats["load_1m"] = float(parts[0])
            stats["load_5m"] = float(parts[1])
            stats["load_15m"] = float(parts[2])
            stats["available"] = True

    df_result = exec_in_mysql_container(["df", "-B1", "/var/lib/mysql"], timeout=10, check=False)
    if df_result.returncode == 0:
        lines = [line for line in df_result.stdout.strip().splitlines() if line.strip()]
        if len(lines) >= 2:
            cols = lines[-1].split()
            if len(cols) >= 5:
                stats["disk_total_bytes"] = _safe_int(cols[1])
                stats["disk_used_bytes"] = _safe_int(cols[2])
                stats["disk_available_bytes"] = _safe_int(cols[3])
                stats["disk_usage_percent"] = float(str(cols[4]).replace("%", "") or 0)
                stats["available"] = True

    mem_result = exec_in_mysql_container(["grep", "-E", "^(MemTotal|MemAvailable):", "/proc/meminfo"], timeout=10, check=False)
    if mem_result.returncode == 0:
        meminfo = {}
        for line in mem_result.stdout.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                meminfo[key.strip()] = _safe_int(val.strip().split()[0]) * 1024
        if meminfo.get("MemTotal"):
            total = meminfo["MemTotal"]
            available = meminfo.get("MemAvailable", 0)
            used = total - available
            stats["host_memory_total_bytes"] = total
            stats["host_memory_used_bytes"] = used
            stats["host_memory_usage_percent"] = round(used / total * 100, 1) if total else 0
            stats["available"] = True

    return stats


def _safe_int(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0
