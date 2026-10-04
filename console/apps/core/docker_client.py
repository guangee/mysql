"""MySQL 运维执行层：allinone 本机执行，split 通过 docker exec/run。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from django.conf import settings

from apps.core.runtime import (
    backup_base_dir,
    is_allinone,
    mysql_config_dir,
    mysql_data_dir,
    read_mysql_file,
    run_host_cmd,
    run_mysql_cmd,
    write_mysql_file,
)


class DockerClientError(Exception):
    pass


_MOUNT_SOURCE_CACHE: dict[str, str] | None = None

DOCKER_NETWORK = "mysql_backup-network"

MYSQLD_ARGS = [
    "--default-authentication-plugin=mysql_native_password",
    "--log-bin=mysql-bin",
    "--binlog-format=ROW",
    "--server-id=99",
    "--character-set-server=utf8mb4",
    "--collation-server=utf8mb4_general_ci",
    "--explicit_defaults_for_timestamp=true",
    "--bind-address=127.0.0.1",
    "--default-time-zone=+08:00",
    "--binlog-expire-logs-seconds=2592000",
]

PITR_TEMP_PORT = int(os.environ.get("PITR_TEMP_MYSQL_PORT", "3307"))


def _wrap(exc: Exception) -> DockerClientError:
    return DockerClientError(str(exc))


def _load_mysql_container_mount_sources() -> dict[str, str]:
    global _MOUNT_SOURCE_CACHE
    if _MOUNT_SOURCE_CACHE is not None:
        return _MOUNT_SOURCE_CACHE
    if is_allinone():
        raise DockerClientError("allinone 模式不使用 docker volume 挂载映射")

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
    mounts = _load_mysql_container_mount_sources()
    host_source = mounts.get(mount_dest)
    if not host_source:
        raise DockerClientError(f"MySQL 容器未挂载 {mount_dest}")
    return host_source


def read_file_in_mysql_container(container_path: str, timeout: int = 30) -> str:
    try:
        return read_mysql_file(container_path, timeout=timeout)
    except Exception as exc:
        raise _wrap(exc) from exc


def write_file_in_mysql_container(container_path: str, content: str, timeout: int = 30) -> None:
    try:
        write_mysql_file(container_path, content, timeout=timeout)
    except Exception as exc:
        raise _wrap(exc) from exc


def exec_in_mysql_container(
    command: list[str],
    timeout: int = 3600,
    check: bool = True,
) -> subprocess.CompletedProcess:
    try:
        return run_mysql_cmd(command, timeout=timeout, check=check)
    except Exception as exc:
        if isinstance(exc, DockerClientError):
            raise
        raise _wrap(exc) from exc


def run_backup_command(backup_type: str) -> subprocess.CompletedProcess:
    if backup_type not in {"full", "incremental"}:
        raise ValueError(f"未知备份类型: {backup_type}")
    return exec_in_mysql_container(
        ["python3", "-u", "-m", "mysql_backup", "backup", backup_type],
        timeout=3600,
        check=False,
    )


def run_backup_command_streaming(backup_type: str, on_line, timeout: int = 3600) -> subprocess.CompletedProcess:
    import select

    if backup_type not in {"full", "incremental"}:
        raise ValueError(f"未知备份类型: {backup_type}")

    if is_allinone():
        cmd = ["python3", "-u", "-m", "mysql_backup", "backup", backup_type]
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    else:
        container = settings.DOCKER_MYSQL_CONTAINER
        cmd = [
            "docker", "exec",
            "-e", "PYTHONUNBUFFERED=1",
            container,
            "python3", "-u", "-m", "mysql_backup", "backup", backup_type,
        ]
        env = None

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError as exc:
        raise DockerClientError("无法启动备份命令") from exc

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
        "-v", f"{volume_name}:/var/lib/mysql",
        "-v", f"{_host_bind_path('/etc/mysql/conf.d')}:/etc/mysql/conf.d",
        "-v", f"{_host_bind_path('/backups')}:/backups",
        "-v", f"{_host_bind_path('/shared')}:/shared",
        "-e", "TZ=Asia/Shanghai",
    ]


def _local_restore_env(data_dir: Path, auto_stop: str = "false") -> dict:
    env = os.environ.copy()
    env["MYSQL_DATA_DIR"] = str(data_dir)
    env["PRODUCTION_MYSQL_DATA_DIR"] = str(mysql_data_dir())
    env["AUTO_STOP_MYSQL"] = auto_stop
    env["MYSQL_ROOT_PASSWORD"] = settings.MYSQL_ROOT_PASSWORD
    env["RESTORE_TZ"] = settings.RESTORE_TIMEZONE
    env["BACKUP_BASE_DIR"] = str(backup_base_dir())
    env["TZ"] = "Asia/Shanghai"
    return env


def run_pitr_restore(target_time: str, full_backup_timestamp: str = "") -> subprocess.CompletedProcess:
    if is_allinone():
        return _run_pitr_restore_local(target_time, full_backup_timestamp)

    container = settings.DOCKER_MYSQL_CONTAINER
    image = settings.DOCKER_MYSQL_IMAGE
    if not image:
        raise DockerClientError("未配置 MYSQL_IMAGE，无法执行时间点恢复")

    logs: list[str] = []
    stop = _docker_run(["docker", "stop", container], timeout=180)
    logs.append(f"[停止容器] exit={stop.returncode}\n{(stop.stdout or '') + (stop.stderr or '')}".strip())

    pitr_cmd = [
        "docker", "run", "--rm", "--network", DOCKER_NETWORK,
        *_pitr_passthrough_env(),
        "-e", f"RESTORE_TZ={settings.RESTORE_TIMEZONE}",
        "-e", "AUTO_STOP_MYSQL=true",
        "-e", f"MYSQL_ROOT_PASSWORD={settings.MYSQL_ROOT_PASSWORD}",
        "-v", f"{_host_bind_path('/var/lib/mysql')}:/var/lib/mysql",
        "-v", f"{_host_bind_path('/etc/mysql/conf.d')}:/etc/mysql/conf.d",
        "-v", f"{_host_bind_path('/backups')}:/backups",
        "-v", f"{_host_bind_path('/shared')}:/shared",
        "-e", "TZ=Asia/Shanghai",
        image,
        "python3", "-m", "mysql_backup", "restore", "pitr",
        target_time,
    ]
    if full_backup_timestamp:
        pitr_cmd.append(full_backup_timestamp)

    pitr = _docker_run(pitr_cmd, timeout=7200)
    logs.append(f"[PITR 脚本] exit={pitr.returncode}\n{(pitr.stdout or '') + (pitr.stderr or '')}".strip())
    start = _docker_run(["docker", "start", container], timeout=120)
    logs.append(f"[启动容器] exit={start.returncode}\n{(start.stdout or '') + (start.stderr or '')}".strip())
    return subprocess.CompletedProcess(
        args=pitr_cmd,
        returncode=pitr.returncode if pitr.returncode != 0 else start.returncode,
        stdout="\n\n".join(logs),
        stderr="",
    )


def _run_pitr_restore_local(target_time: str, full_backup_timestamp: str = "") -> subprocess.CompletedProcess:
    logs: list[str] = []
    control_mysql_service("stop", wait_ready=False)
    logs.append("[停止 MySQL] done")
    cmd = ["python3", "-m", "mysql_backup", "restore", "pitr", target_time]
    if full_backup_timestamp:
        cmd.append(full_backup_timestamp)
    env = _local_restore_env(mysql_data_dir(), auto_stop="false")
    pitr = subprocess.run(cmd, capture_output=True, text=True, timeout=7200, check=False, env=env)
    logs.append(f"[PITR 脚本] exit={pitr.returncode}\n{(pitr.stdout or '') + (pitr.stderr or '')}".strip())
    # 脚本内 copy binlog 后可能留下 root 属主文件，启动前强制纠正
    data_dir = mysql_data_dir()
    subprocess.run(["chown", "-R", "mysql:mysql", str(data_dir)], capture_output=True, check=False)
    subprocess.run(["chmod", "700", str(data_dir)], capture_output=True, check=False)
    logs.append("[权限] chown mysql:mysql datadir done")
    start_rc = 0
    try:
        start = control_mysql_service("start", wait_ready=True, ready_timeout=300)
        logs.append(f"[启动 MySQL] {start.get('message') or start.get('status') or ''}")
        if not _mysql_ping_local(timeout=10):
            start_rc = 1
            logs.append("[启动 MySQL] ping 未就绪")
    except Exception as exc:
        start_rc = 1
        logs.append(f"[启动 MySQL] failed: {exc}")
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=pitr.returncode if pitr.returncode != 0 else start_rc,
        stdout="\n\n".join(logs),
        stderr="",
    )


def run_full_backup_restore(full_backup_timestamp: str) -> subprocess.CompletedProcess:
    ts = (full_backup_timestamp or "").strip()
    if not ts:
        raise DockerClientError("未指定全量备份时间戳")

    if is_allinone():
        logs: list[str] = []
        control_mysql_service("stop", wait_ready=False)
        logs.append("[停止 MySQL] done")
        restore_shell = (
            "set -e; "
            f"python3 -m mysql_backup restore backup {ts}; "
            'RESTORE_DIR=$(dirname "$(find /backups/restore -name backup-my.cnf | head -1)"); '
            '[ -f "$RESTORE_DIR/backup-my.cnf" ] || RESTORE_DIR=/backups/restore; '
            'python3 -m mysql_backup restore apply "$RESTORE_DIR"'
        )
        env = _local_restore_env(mysql_data_dir(), auto_stop="false")
        env["BACKUP_EXISTING_DATA"] = "true"
        restore = subprocess.run(
            ["bash", "-c", restore_shell],
            capture_output=True,
            text=True,
            timeout=7200,
            check=False,
            env=env,
        )
        logs.append(f"[全量恢复] exit={restore.returncode}\n{(restore.stdout or '') + (restore.stderr or '')}".strip())
        control_mysql_service("start", wait_ready=True)
        logs.append("[启动 MySQL] done")
        return subprocess.CompletedProcess(
            args=["bash", "-c", restore_shell],
            returncode=restore.returncode,
            stdout="\n\n".join(logs),
            stderr="",
        )

    container = settings.DOCKER_MYSQL_CONTAINER
    image = settings.DOCKER_MYSQL_IMAGE
    if not image:
        raise DockerClientError("未配置 MYSQL_IMAGE，无法执行全量恢复")
    logs = []
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


def run_pitr_on_docker_volume(
    volume_name: str,
    target_time: str,
    full_backup_timestamp: str = "",
) -> subprocess.CompletedProcess:
    """在独立目录/卷上执行 PITR，不影响生产实例。"""
    if is_allinone():
        work = backup_base_dir() / "pitr-work" / volume_name
        data_dir = work / "datadir"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["python3", "-m", "mysql_backup", "restore", "pitr", target_time]
        if full_backup_timestamp:
            cmd.append(full_backup_timestamp)
        env = _local_restore_env(data_dir, auto_stop="false")
        return subprocess.run(cmd, capture_output=True, text=True, timeout=7200, check=False, env=env)

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


def _temp_mysql_paths(name: str) -> dict[str, Path]:
    work = backup_base_dir() / "pitr-work" / name
    return {
        "work": work,
        "datadir": work / "datadir",
        "socket": work / "mysql.sock",
        "pid": work / "mysqld.pid",
        "log": work / "mysqld.err",
    }


def start_temp_mysql_container(container_name: str, volume_name: str) -> None:
    if is_allinone():
        paths = _temp_mysql_paths(volume_name)
        data_dir = paths["datadir"]
        if not data_dir.exists():
            raise DockerClientError(f"临时数据目录不存在: {data_dir}")
        # stop leftover
        stop_temp_mysql_local(volume_name)
        # mysqld --user=mysql 需要能写 socket/pid/log；恢复目录常为 root 创建
        paths["work"].mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["chown", "-R", "mysql:mysql", str(paths["work"])],
            capture_output=True,
            check=False,
        )
        for p in (paths["socket"], paths["pid"], paths["log"]):
            if p.exists():
                p.unlink(missing_ok=True)
        paths["log"].touch(exist_ok=True)
        subprocess.run(["chown", "mysql:mysql", str(paths["log"])], capture_output=True, check=False)
        cmd = [
            "mysqld",
            "--user=mysql",
            f"--datadir={data_dir}",
            f"--socket={paths['socket']}",
            f"--pid-file={paths['pid']}",
            f"--port={PITR_TEMP_PORT}",
            f"--log-error={paths['log']}",
            *MYSQLD_ARGS,
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        spawn_pid = paths["work"] / "mysqld.spawn.pid"
        spawn_pid.write_text(str(proc.pid), encoding="utf-8")
        subprocess.run(["chown", "mysql:mysql", str(spawn_pid)], capture_output=True, check=False)
        return

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


def stop_temp_mysql_local(volume_name: str) -> None:
    paths = _temp_mysql_paths(volume_name)
    password = settings.MYSQL_ROOT_PASSWORD
    sock = paths["socket"]
    if sock.exists():
        subprocess.run(
            [
                "mysqladmin", "shutdown",
                f"--socket={sock}",
                "-uroot", f"-p{password}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        time.sleep(1)
    pid_file = paths["pid"]
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, 15)
            time.sleep(1)
        except (OSError, ValueError):
            pass
    spawn = paths["work"] / "mysqld.spawn.pid"
    if spawn.exists():
        try:
            pid = int(spawn.read_text(encoding="utf-8").strip())
            os.kill(pid, 9)
        except (OSError, ValueError):
            pass


def wait_mysql_container_ready(container_name: str, timeout: int = 180) -> None:
    password = settings.MYSQL_ROOT_PASSWORD
    deadline = time.time() + timeout
    if is_allinone():
        # container_name unused; volume name encoded as mysql-db-pitr-<id> matches volume
        volume_name = container_name
        paths = _temp_mysql_paths(volume_name)
        sock = paths["socket"]
        while time.time() < deadline:
            if sock.exists():
                result = subprocess.run(
                    [
                        "mysqladmin", "ping",
                        f"--socket={sock}",
                        "-uroot", f"-p{password}",
                        "--silent",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if result.returncode == 0:
                    time.sleep(3)
                    return
            time.sleep(2)
        err = ""
        if paths["log"].exists():
            err = paths["log"].read_text(encoding="utf-8", errors="replace")[-1000:]
        raise DockerClientError(f"临时 MySQL 在 {timeout}s 内未就绪: {err}")

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
    if is_allinone():
        paths = _temp_mysql_paths(container_name)
        sock = str(paths["socket"])
        exists = subprocess.run(
            [
                "mysql", f"--socket={sock}", "-uroot", f"-p{password}", "-N", "-e",
                f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = '{database}'",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if not (exists.stdout or "").strip():
            return False
        if check_only:
            return True
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        with dump_path.open("w", encoding="utf-8") as outfile:
            result = subprocess.run(
                [
                    "mysqldump", f"--socket={sock}",
                    "-uroot", f"-p{password}",
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
    password = settings.MYSQL_ROOT_PASSWORD
    if is_allinone():
        with dump_path.open("r", encoding="utf-8") as infile:
            result = subprocess.run(
                ["mysql", "-h127.0.0.1", "-uroot", f"-p{password}", database],
                stdin=infile,
                capture_output=True,
                text=True,
                timeout=3600,
                check=False,
            )
    else:
        container = settings.DOCKER_MYSQL_CONTAINER
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
    if is_allinone():
        stop_temp_mysql_local(volume_name)
        work = backup_base_dir() / "pitr-work" / volume_name
        shutil.rmtree(work, ignore_errors=True)
        return
    _docker_run(["docker", "rm", "-f", container_name], timeout=120)
    _docker_run(["docker", "volume", "rm", "-f", volume_name], timeout=120)


def _parse_docker_size_pair(text: str) -> tuple[int, int]:
    units = {
        "B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
        "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
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


def _safe_int(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _mysql_ping_local(timeout: int = 15) -> bool:
    password = settings.MYSQL_ROOT_PASSWORD
    sock = "/var/run/mysqld/mysqld.sock"
    candidates = [
        ["mysqladmin", "ping", "-h127.0.0.1", "-uroot", f"-p{password}", "--silent"],
        ["mysqladmin", "ping", f"--socket={sock}", "-uroot", f"-p{password}", "--silent"],
        ["mysqladmin", "ping", "-h127.0.0.1", "-uroot", "--silent"],
        ["mysqladmin", "ping", f"--socket={sock}", "-uroot", "--silent"],
    ]
    for cmd in candidates:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return True
    return False


def _mysql_container_ping(container_name: str, timeout: int = 15) -> bool:
    if is_allinone():
        return _mysql_ping_local(timeout=timeout)
    password = settings.MYSQL_ROOT_PASSWORD
    result = subprocess.run(
        [
            "docker", "exec", container_name,
            "mysqladmin", "ping", "-h127.0.0.1", "-uroot", f"-p{password}", "--silent",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return result.returncode == 0


def get_mysql_service_status() -> dict:
    if is_allinone():
        running = False
        try:
            result = subprocess.run(
                ["pgrep", "-x", "mysqld"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            running = result.returncode == 0
        except Exception:
            running = Path("/var/run/mysqld/mysqld.sock").exists()
        ready = False
        ready_error = ""
        if running:
            try:
                ready = _mysql_ping_local(timeout=10)
            except Exception as exc:
                ready_error = str(exc)[:200]
        return {
            "container": "local",
            "exists": True,
            "running": running,
            "ready": ready,
            "status": "running" if running else "exited",
            "paused": False,
            "restarting": False,
            "oom_killed": False,
            "pid": 0,
            "exit_code": 0,
            "error": ready_error,
            "started_at": "",
            "finished_at": "",
            "image": "allinone",
            "runtime_mode": "allinone",
        }

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
            "runtime_mode": "split",
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
        "runtime_mode": "split",
    }


def _start_mysqld_local() -> None:
    helper = Path("/usr/local/bin/mysql-service")
    if helper.exists():
        # 异步拉起，由 control_mysql_service(wait_ready=True) 轮询就绪，避免子进程 120s 超时误杀
        subprocess.Popen(
            ["bash", str(helper), "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return
    # fallback: docker-entrypoint style
    cmd = ["mysqld", "--user=mysql"]
    subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _stop_mysqld_local() -> None:
    helper = Path("/usr/local/bin/mysql-service")
    if helper.exists():
        subprocess.run(
            ["bash", str(helper), "stop"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return
    password = settings.MYSQL_ROOT_PASSWORD
    subprocess.run(
        ["mysqladmin", "shutdown", "-h127.0.0.1", "-uroot", f"-p{password}"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    time.sleep(2)
    subprocess.run(["pkill", "-x", "mysqld"], check=False, capture_output=True)


def control_mysql_service(action: str, wait_ready: bool = True, ready_timeout: int = 180) -> dict:
    action = (action or "").strip().lower()
    if action not in {"start", "stop", "restart"}:
        raise ValueError("action 只能是 start、stop 或 restart")

    before = get_mysql_service_status()
    if not before.get("exists"):
        raise DockerClientError(before.get("error") or "MySQL 不存在")

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

    if is_allinone():
        if action == "stop":
            _stop_mysqld_local()
        elif action == "start":
            _start_mysqld_local()
        else:
            _stop_mysqld_local()
            time.sleep(2)
            _start_mysqld_local()
        if action in {"start", "restart"} and wait_ready:
            deadline = time.time() + ready_timeout
            while time.time() < deadline:
                if _mysql_ping_local(timeout=10):
                    break
                time.sleep(2)
    else:
        container = settings.DOCKER_MYSQL_CONTAINER
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
        status["message"] = f"{action} 已执行，等待 MySQL 就绪中"
    return status


def get_mysql_host_stats() -> dict:
    stats: dict = {"available": False}

    if is_allinone():
        try:
            load = Path("/proc/loadavg").read_text(encoding="utf-8").split()
            if len(load) >= 3:
                stats["load_1m"] = float(load[0])
                stats["load_5m"] = float(load[1])
                stats["load_15m"] = float(load[2])
                stats["available"] = True
        except Exception as exc:
            stats["error"] = str(exc)[:200]

        try:
            meminfo = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    meminfo[key.strip()] = _safe_int(val.strip().split()[0]) * 1024
            total = meminfo.get("MemTotal", 0)
            available = meminfo.get("MemAvailable", 0)
            if total:
                used = total - available
                stats["memory_used_bytes"] = used
                stats["memory_total_bytes"] = total
                stats["memory_usage_percent"] = round(used / total * 100, 1)
                stats["host_memory_total_bytes"] = total
                stats["host_memory_used_bytes"] = used
                stats["host_memory_usage_percent"] = round(used / total * 100, 1)
                stats["available"] = True
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["df", "-B1", str(mysql_data_dir())],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            lines = [line for line in (result.stdout or "").strip().splitlines() if line.strip()]
            if len(lines) >= 2:
                cols = lines[-1].split()
                if len(cols) >= 5:
                    stats["disk_total_bytes"] = _safe_int(cols[1])
                    stats["disk_used_bytes"] = _safe_int(cols[2])
                    stats["disk_available_bytes"] = _safe_int(cols[3])
                    stats["disk_usage_percent"] = float(str(cols[4]).replace("%", "") or 0)
                    stats["available"] = True
        except Exception:
            pass

        # rough cpu from /proc/stat delta not available; leave cpu_percent unset or 0
        stats.setdefault("cpu_percent", 0.0)
        return stats

    container = settings.DOCKER_MYSQL_CONTAINER
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

    mem_result = exec_in_mysql_container(
        ["grep", "-E", "^(MemTotal|MemAvailable):", "/proc/meminfo"],
        timeout=10,
        check=False,
    )
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
