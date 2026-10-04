"""
备份存储工具模块

提供 S3 上传校验与本地备份清理的共享逻辑
支持从 shared/storages.json 读取多对象存储并同步上传
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

S3_BACKUP_ENABLED = os.environ.get("S3_BACKUP_ENABLED", "false").lower() == "true"
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "mysql-backups")
S3_USE_SSL = os.environ.get("S3_USE_SSL", "true").lower() == "true"
S3_ALIAS = os.environ.get("S3_ALIAS", "s3")
LOCAL_BACKUP_RETENTION_HOURS = int(os.environ.get("LOCAL_BACKUP_RETENTION_HOURS", "0"))
STORAGES_CONFIG_FILE = os.environ.get("STORAGES_CONFIG_FILE", "/shared/storages.json")

from mysql_backup.core.backup_naming import (
    DAY_MANIFEST_NAME,
    LEGACY_BACKUP_FILE,
    extract_backup_timestamp,
    find_backup_archive,
    is_backup_dir_name,
    is_date_dir_name,
    list_archives_in_dir,
    make_backup_filename,
    parse_backup_name,
    parse_backup_filename_timestamp as _parse_backup_filename_timestamp_naive,
    resolve_backup_archive,
    s3_backup_key,
    s3_backup_object_names_for_lookup,
)

TIMESTAMP_PATTERN = re.compile(r"^\d{8}_\d{6}$")
BACKUP_FILENAME_TS = re.compile(
    r"(?:(\d{8}_\d{6})_(?:full|incr|incremental)_v[0-9A-Za-z._+-]+|backup_(\d{8}_\d{6}))"
)


def backup_timezone() -> ZoneInfo:
    return ZoneInfo(os.environ.get("BACKUP_TIMEZONE", "Asia/Shanghai"))


def parse_backup_filename_timestamp(filename: str) -> datetime | None:
    """从备份文件名解析备份时间（兼容新旧命名）"""
    dt = _parse_backup_filename_timestamp_naive(filename)
    if dt is None:
        return None
    return dt.replace(tzinfo=backup_timezone())


def is_backup_expired_by_retention(
    filename: str,
    retention_days: int,
    now: datetime | None = None,
) -> bool:
    """与控制台列表一致：备份时间 + 保留天数 <= 当前时间即为过期"""
    created = parse_backup_filename_timestamp(filename)
    if not created:
        return False
    tz = backup_timezone()
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    return now >= created + timedelta(days=retention_days)


def load_storages_from_file() -> list[dict]:
    """从 JSON 文件加载多对象存储配置"""
    config_path = Path(STORAGES_CONFIG_FILE)
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [s for s in data if s.get("enabled", True)]
    except Exception:
        pass
    return []


def get_active_storages() -> list[dict]:
    """获取当前启用的存储列表，无配置文件时回退到环境变量"""
    storages = load_storages_from_file()
    if storages:
        return storages
    if S3_BACKUP_ENABLED and S3_ENDPOINT and S3_ACCESS_KEY and S3_SECRET_KEY:
        return [
            {
                "id": 0,
                "name": "default",
                "alias": S3_ALIAS,
                "endpoint": S3_ENDPOINT,
                "access_key": S3_ACCESS_KEY,
                "secret_key": S3_SECRET_KEY,
                "bucket": S3_BUCKET,
                "use_ssl": S3_USE_SSL,
                "enabled": True,
            }
        ]
    return []


def _storage_alias(storage: dict) -> str:
    alias = storage.get("alias") or f"s3_{storage.get('id', 0)}"
    return str(alias)


def setup_storage(storage: dict, log: Callable[[str], None]) -> bool:
    """配置单个 S3 存储客户端"""
    endpoint = storage.get("endpoint", "")
    access_key = storage.get("access_key", "")
    secret_key = storage.get("secret_key", "")
    bucket = storage.get("bucket", S3_BUCKET)
    use_ssl = storage.get("use_ssl", S3_USE_SSL)
    alias = _storage_alias(storage)
    name = storage.get("name", alias)

    if not endpoint or not access_key or not secret_key:
        log(f"[存储:{name}] 错误: 配置不完整")
        return False

    s3_url = f"https://{endpoint}" if use_ssl else f"http://{endpoint}"
    try:
        subprocess.run(
            ["mc", "alias", "set", alias, s3_url, access_key, secret_key, "--api", "s3v4"],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["mc", "mb", f"{alias}/{bucket}"],
            check=False,
            capture_output=True,
        )
    except Exception as e:
        log(f"[存储:{name}] 错误: S3 客户端配置失败: {e}")
        return False

    log(f"[存储:{name}] S3 配置完成 (Endpoint: {endpoint}, Bucket: {bucket})")
    return True


def setup_all_s3(log: Callable[[str], None]) -> bool:
    """配置所有启用的 S3 存储"""
    storages = get_active_storages()
    if not storages:
        log("错误: 未找到可用的 S3 存储配置")
        return False
    ok = True
    for storage in storages:
        if not setup_storage(storage, log):
            ok = False
    return ok


def s3_object_path(storage: dict, relative_key: str) -> str:
    """构建 mc 路径 alias/bucket/relative_key"""
    alias = _storage_alias(storage)
    bucket = storage.get("bucket", S3_BUCKET)
    return f"{alias}/{bucket}/{relative_key.lstrip('/')}"


def setup_s3(log: Callable[[str], None]) -> bool:
    """配置 S3 客户端，返回是否配置成功（兼容单存储与多存储）"""
    return setup_all_s3(log)


def compute_file_sha256(file_path: Path) -> str:
    """计算文件的 SHA256 哈希值"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def compute_file_md5(file_path: Path) -> str:
    """计算文件的 MD5 哈希值"""
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            md5.update(chunk)
    return md5.hexdigest()


def get_s3_object_stat(s3_path: str) -> Optional[dict]:
    """通过 mc stat 获取 S3 对象元信息"""
    try:
        result = subprocess.run(
            ["mc", "stat", "--json", s3_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


def get_s3_object_size(s3_path: str) -> Optional[int]:
    """通过 mc stat 获取 S3 对象大小"""
    stat_data = get_s3_object_stat(s3_path)
    if not stat_data:
        return None
    size = stat_data.get("size")
    return int(size) if size is not None else None


def get_s3_object_sha256(s3_path: str) -> Optional[str]:
    """通过 mc hash 获取 S3 对象的 SHA256"""
    try:
        result = subprocess.run(
            ["mc", "hash", "sha256", s3_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and len(parts[1]) == 64:
                return parts[1].lower()
            if len(line) == 64:
                return line.lower()
        return None
    except Exception:
        return None


def verify_s3_upload(local_path: Path, s3_path: str, log: Callable[[str], None]) -> bool:
    """
    校验 S3 上的对象与本地文件一致（大小 + 内容哈希）
    优先 SHA256（mc hash），不可用时回退到 MD5 与 S3 ETag 比对
    """
    if not local_path.exists() or not local_path.is_file():
        log(f"错误: 本地文件不存在: {local_path}")
        return False

    local_size = local_path.stat().st_size
    stat_data = get_s3_object_stat(s3_path)
    if not stat_data:
        log(f"错误: 无法获取 S3 对象信息: {s3_path}")
        return False

    remote_size = stat_data.get("size")
    if remote_size is None or local_size != int(remote_size):
        log(f"错误: 文件大小不一致 (本地: {local_size}, S3: {remote_size})")
        return False

    log(f"文件大小校验通过: {local_size} 字节")

    log("获取 S3 对象 SHA256...")
    remote_sha256 = get_s3_object_sha256(s3_path)
    if remote_sha256:
        log("计算本地文件 SHA256...")
        local_sha256 = compute_file_sha256(local_path)
        log(f"本地 SHA256: {local_sha256}")
        if local_sha256 != remote_sha256:
            log(f"错误: SHA256 不一致 (本地: {local_sha256}, S3: {remote_sha256})")
            return False
        log("SHA256 校验通过，S3 对象与本地文件一致")
        return True

    remote_etag = str(stat_data.get("etag", "")).strip('"').lower()
    if remote_etag and "-" not in remote_etag:
        log("mc hash 不可用，使用 MD5 与 S3 ETag 校验...")
        local_md5 = compute_file_md5(local_path)
        log(f"本地 MD5: {local_md5}, S3 ETag: {remote_etag}")
        if local_md5 != remote_etag:
            log("错误: MD5 与 S3 ETag 不一致")
            return False
        log("MD5/ETag 校验通过，S3 对象与本地文件一致")
        return True

    log("错误: 无法校验 S3 对象内容（缺少 hash 命令且 ETag 不可用）")
    return False


def stream_command(cmd: list[str], log: Callable[[str], None], prefix: str = "") -> None:
    """逐行输出子进程日志，便于控制台实时看到备份进度。"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        if line:
            log(f"{prefix}{line}" if prefix else line)
    code = proc.wait()
    if code != 0:
        raise subprocess.CalledProcessError(code, cmd)


def upload_and_verify(local_path: Path, s3_path: str, log: Callable[[str], None]) -> bool:
    """上传文件到 S3 并校验一致性（单目标）"""
    log(f"上传文件: {local_path} -> {s3_path}")
    try:
        result = subprocess.run(
            ["mc", "cp", str(local_path), s3_path],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            for line in result.stdout.splitlines():
                if line.strip():
                    log(f"mc: {line}")
    except subprocess.CalledProcessError as e:
        log("错误: 上传到 S3 失败")
        if e.stderr:
            log(f"错误详情: {e.stderr}")
        return False

    return verify_s3_upload(local_path, s3_path, log)


def upload_and_verify_all(local_path: Path, relative_key: str, log: Callable[[str], None]) -> bool:
    """上传文件到所有启用的 S3 存储并校验（sync_all 策略）"""
    storages = get_active_storages()
    if not storages:
        log("错误: 无可用存储配置")
        return False

    all_ok = True
    any_ok = False
    for storage in storages:
        name = storage.get("name", _storage_alias(storage))
        if not setup_storage(storage, log):
            log(f"[存储:{name}] 失败 - 配置失败")
            all_ok = False
            continue
        s3_path = s3_object_path(storage, relative_key)
        if upload_and_verify(local_path, s3_path, log):
            log(f"[存储:{name}] 成功")
            any_ok = True
        else:
            log(f"[存储:{name}] 失败 - 上传或校验未通过")
            all_ok = False

    if not any_ok:
        return False
    return all_ok


def upload_metadata_to_all(relative_key: str, content: str, log: Callable[[str], None]) -> None:
    """将元数据写入所有启用的存储"""
    for storage in get_active_storages():
        name = storage.get("name", _storage_alias(storage))
        s3_path = s3_object_path(storage, relative_key)
        try:
            subprocess.run(
                ["mc", "pipe", s3_path],
                input=content,
                text=True,
                check=False,
                capture_output=True,
            )
        except Exception as e:
            log(f"[存储:{name}] 元数据上传警告: {e}")


def backup_payload_items(backup_dir: Path) -> list[Path]:
    """备份目录中的有效内容（忽略删除标记）。"""
    if not backup_dir.is_dir():
        return []
    return [item for item in backup_dir.iterdir() if item.name != ".delete_after"]


def _archive_names_for_dir(backup_dir: Path) -> set[str]:
    names = {LEGACY_BACKUP_FILE, f"{backup_dir.name}.tar.gz"}
    parsed = parse_backup_name(backup_dir.name)
    if parsed and parsed.filename:
        names.add(parsed.filename)
    return names


def is_backup_archived(backup_dir: Path) -> bool:
    """目录里是否只剩一个有效压缩包。"""
    archive = find_backup_archive(backup_dir)
    if archive is None:
        return False
    return all(item.name == archive.name for item in backup_payload_items(backup_dir))


def pack_backup_directory(
    backup_dir: Path,
    log: Callable[[str], None],
    archive_name: str | None = None,
    dest_archive: Path | None = None,
) -> Optional[Path]:
    """
    将备份工作目录打成压缩包。
    - archive_name: 目标文件名
    - dest_archive: 若指定则写入该路径（可为日期目录下的最终位置）
    每个条目写入后立即从工作目录删除，避免双份占盘。
    """
    if dest_archive is not None:
        archive = dest_archive
        archive.parent.mkdir(parents=True, exist_ok=True)
    elif archive_name:
        archive = backup_dir / archive_name
    else:
        parsed = parse_backup_name(backup_dir.name)
        if parsed and parsed.kind:
            archive = backup_dir / parsed.filename
        elif TIMESTAMP_PATTERN.match(backup_dir.name):
            archive = backup_dir / LEGACY_BACKUP_FILE
        else:
            archive = backup_dir / f"{backup_dir.name}.tar.gz"

    skip_names = _archive_names_for_dir(backup_dir) | {archive.name, DAY_MANIFEST_NAME}
    try:
        with tarfile.open(archive, "w:gz") as tar:
            for item in list(backup_dir.iterdir()):
                if item.name in skip_names or item.name.endswith(".tar.gz") or item.name == DAY_MANIFEST_NAME:
                    if item.resolve() == archive.resolve():
                        continue
                    continue
                tar.add(item, arcname=item.name)
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
    except Exception as exc:
        log(f"错误: 打包备份失败: {exc}")
        return None

    if not archive.is_file() or archive.stat().st_size <= 0:
        log("错误: 打包后的备份文件为空")
        return None
    return archive


def ensure_backup_archived(backup_dir: Path, log: Callable[[str], None]) -> Optional[Path]:
    """
    清理备份目录中的解压残留。
    - 日期目录：保留全部 *.tar.gz 与 day.xml，删除其它残留
    - 旧单备份目录：保留压缩包，必要时打包
    """
    if not backup_dir.is_dir():
        return None

    if is_date_dir_name(backup_dir.name):
        keep = {DAY_MANIFEST_NAME}
        archives = list_archives_in_dir(backup_dir)
        keep.update(p.name for p in archives)
        removed = False
        for item in list(backup_payload_items(backup_dir)):
            if item.name in keep:
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
            removed = True
        if removed:
            log(f"已清理日期目录中的解压残留: {backup_dir}")
        return archives[-1] if archives else None

    archive = find_backup_archive(backup_dir)
    archive_names = _archive_names_for_dir(backup_dir) | {DAY_MANIFEST_NAME}
    others = [
        item
        for item in backup_payload_items(backup_dir)
        if item.name not in archive_names and not (archive and item.name == archive.name)
    ]
    if not others:
        return archive

    if archive is not None:
        for item in others:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                try:
                    item.unlink()
                except FileNotFoundError:
                    pass
        log(f"已删除与压缩包重复的解压内容: {backup_dir}")
        return archive

    log(f"打包未压缩备份目录: {backup_dir}")
    return pack_backup_directory(backup_dir, log)


def materialize_backup_to(
    source: Path,
    work_dir: Path,
    log: Callable[[str], None],
    archive: Path | None = None,
) -> bool:
    """
    将备份内容落到工作目录，不破坏源压缩包。
    source 可为日期/旧备份目录，或直接传入 archive=压缩包路径。
    """
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    archive_path = archive
    source_dir = source
    if source.is_file() and source.name.endswith(".tar.gz"):
        archive_path = source
        source_dir = source.parent
    if archive_path is None and source_dir.is_dir():
        # 日期目录必须显式指定 archive，避免解错包
        if is_date_dir_name(source_dir.name):
            log(f"错误: 日期目录含多个备份，需指定具体压缩包: {source_dir}")
            return False
        archive_path = find_backup_archive(source_dir)

    if source_dir.is_dir() and (source_dir / "xtrabackup_checkpoints").is_file() and not is_date_dir_name(source_dir.name):
        log(f"复制备份目录到工作区: {source_dir} -> {work_dir}")
        for item in source_dir.iterdir():
            if item.name.endswith(".tar.gz") or item.name == DAY_MANIFEST_NAME:
                continue
            dest = work_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        return (work_dir / "xtrabackup_checkpoints").is_file()

    if archive_path is not None and archive_path.is_file():
        log(f"解压备份到工作区: {archive_path} -> {work_dir}")
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=work_dir)
        if (work_dir / "xtrabackup_checkpoints").is_file():
            return True
        if list(work_dir.glob("*.zst")) or list(work_dir.rglob("*.zst")):
            return True
        return any(work_dir.iterdir())

    log(f"错误: 源备份既无 checkpoints 也无压缩包: {source}")
    return False


def apply_local_retention(
    backup_path: Path,
    verified: bool,
    log: Callable[[str], None],
) -> bool:
    """
    根据保留策略处理本地备份（文件或目录）。
    仅在 verified=True 时才会删除本地文件。
    日期目录下若传入单个压缩包，只删该文件并保留目录/day.xml。
    """
    if not verified:
        log("上传校验未通过，保留本地备份")
        return False

    if LOCAL_BACKUP_RETENTION_HOURS == 0:
        if not backup_path.exists():
            log(f"本地备份已不存在: {backup_path}")
            return True
        try:
            parent = backup_path.parent if backup_path.is_file() else backup_path
            if backup_path.is_file():
                backup_path.unlink()
                log(f"本地备份文件已清理（上传校验通过后立即删除）: {backup_path.name}")
                # 日期目录若已空（无其它 tar），可删目录；否则重建 xml
                if parent.is_dir() and is_date_dir_name(parent.name):
                    from mysql_backup.core.backup_day_manifest import rebuild_day_manifest
                    kind = "full" if parent.parent.name == "full" else "incr"
                    if list_archives_in_dir(parent):
                        rebuild_day_manifest(parent, kind=kind, log=log)
                    else:
                        shutil.rmtree(parent, ignore_errors=True)
                return True

            shutil.rmtree(backup_path)
            if backup_path.exists():
                log(f"错误: 本地备份目录删除失败: {backup_path}")
                return False
            log("本地备份文件已清理（上传校验通过后立即删除）")
            return True
        except Exception as e:
            log(f"错误: 删除本地备份失败: {e}")
            return False

    delete_time = int((datetime.now() + timedelta(hours=LOCAL_BACKUP_RETENTION_HOURS)).timestamp())
    try:
        marker_dir = backup_path.parent if backup_path.is_file() else backup_path
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / ".delete_after").write_text(str(delete_time))
        delete_time_str = datetime.fromtimestamp(delete_time).strftime("%Y-%m-%d %H:%M:%S")
        log(f"本地备份文件将保留 {LOCAL_BACKUP_RETENTION_HOURS} 小时，预计删除时间: {delete_time_str}")
        return True
    except Exception as e:
        log(f"警告: 写入删除标记失败: {e}")
        return False


def s3_full_backup_path(timestamp_or_filename: str, version: str | None = None) -> str:
    """获取全量备份在 S3 上的路径（接受时间戳或完整文件名）。"""
    name = timestamp_or_filename
    if not str(name).endswith(".tar.gz"):
        name = make_backup_filename(str(timestamp_or_filename), "full", version)
    return f"{S3_ALIAS}/{S3_BUCKET}/full/{Path(name).name}"


def s3_incremental_backup_path(timestamp_or_filename: str, version: str | None = None) -> str:
    """获取增量备份在 S3 上的路径（接受时间戳或完整文件名）。"""
    name = timestamp_or_filename
    if not str(name).endswith(".tar.gz"):
        name = make_backup_filename(str(timestamp_or_filename), "incr", version)
    return f"{S3_ALIAS}/{S3_BUCKET}/incremental/{Path(name).name}"


def s3_backup_paths_for_lookup(kind: str, timestamp: str, version: str | None = None) -> list[str]:
    """兼容新旧对象名的候选 S3 路径（含日期子目录）。"""
    kind_norm = "full" if kind in {"full"} else "incr"
    prefix = "full" if kind_norm == "full" else "incremental"
    date = str(timestamp)[:8]
    paths = []
    for name in s3_backup_object_names_for_lookup(timestamp, kind_norm, version):
        paths.append(f"{S3_ALIAS}/{S3_BUCKET}/{prefix}/{date}/{name}")
        paths.append(f"{S3_ALIAS}/{S3_BUCKET}/{prefix}/{name}")
    return paths


def s3_object_exists(s3_path: str) -> bool:
    """检查 S3 对象是否存在"""
    return get_s3_object_size(s3_path) is not None


def cleanup_local_full_base_if_on_s3(
    base_backup: Path,
    log: Callable[[str], None],
) -> bool:
    """
    增量备份完成后，若全量基线来自 S3 且远端已有对应对象，则清理本地压缩包。
    base_backup 可为压缩包路径或旧单备份目录。
    """
    if LOCAL_BACKUP_RETENTION_HOURS != 0:
        return False

    archive = base_backup if base_backup.is_file() else find_backup_archive(base_backup)
    timestamp = extract_backup_timestamp(archive.name if archive else base_backup.name)
    if not timestamp:
        log(f"跳过清理: 无法识别全量备份时间戳: {base_backup}")
        return False

    s3_paths = s3_backup_paths_for_lookup("full", timestamp)
    if archive is not None:
        s3_paths = [
            f"{S3_ALIAS}/{S3_BUCKET}/full/{timestamp[:8]}/{archive.name}",
            f"{S3_ALIAS}/{S3_BUCKET}/full/{archive.name}",
            *s3_paths,
        ]
    s3_path = next((p for p in s3_paths if s3_object_exists(p)), None)
    if not s3_path:
        log(f"S3 上未找到对应全量备份，保留本地基线: {s3_paths[0]}")
        return False

    if archive is None or not archive.exists():
        return True

    backup_base_dir = archive.parent.parent if is_date_dir_name(archive.parent.name) else archive.parent.parent
    # archive: /backups/full/YYYYMMDD/file.tar.gz -> parent.parent = /backups
    if archive.parent.name in {"full", "incremental"}:
        backup_base_dir = archive.parent.parent
    else:
        backup_base_dir = archive.parent.parent.parent if archive.parent.parent.name in {"full", "incremental"} else archive.parent.parent

    # simpler: walk up to backups root markers
    cursor = archive.parent
    while cursor.name not in {"full", "incremental"} and cursor != cursor.parent:
        cursor = cursor.parent
    backup_base_dir = cursor.parent if cursor.name in {"full", "incremental"} else archive.parent.parent

    latest_marker = backup_base_dir / "LATEST_FULL_BACKUP"
    try:
        day_dir = archive.parent
        archive.unlink()
        if is_date_dir_name(day_dir.name):
            from mysql_backup.core.backup_day_manifest import rebuild_day_manifest
            if list_archives_in_dir(day_dir):
                rebuild_day_manifest(day_dir, kind="full", log=log)
            else:
                shutil.rmtree(day_dir, ignore_errors=True)
        elif day_dir.is_dir() and day_dir.name not in {"full", "incremental"}:
            shutil.rmtree(day_dir, ignore_errors=True)

        if latest_marker.exists():
            try:
                marked = Path(latest_marker.read_text().strip())
                if marked == archive or marked == day_dir:
                    latest_marker.unlink(missing_ok=True)
                    (backup_base_dir / "LATEST_FULL_BACKUP_TIMESTAMP").unlink(missing_ok=True)
                    (backup_base_dir / "LATEST_FULL_BACKUP_FILE").unlink(missing_ok=True)
            except Exception:
                pass

        log(f"已清理本地全量基线（S3 已有 verified 备份）: {archive}")
        return True
    except Exception as e:
        log(f"错误: 清理全量基线失败: {e}")
        return False


def cleanup_local_orphan_backups_on_s3(
    backup_base_dir: Path,
    log: Callable[[str], None],
) -> int:
    """
    清理已上传至 S3 且校验一致的本地孤儿备份目录。
    用于处理上传后删除失败的历史遗留目录。
    """
    if not S3_BACKUP_ENABLED or LOCAL_BACKUP_RETENTION_HOURS != 0:
        return 0

    cleaned = 0

    for backup_type, s3_path_fn in (
        ("full", s3_full_backup_path),
        ("incremental", s3_incremental_backup_path),
    ):
        type_dir = backup_base_dir / backup_type
        if not type_dir.exists():
            continue

        kind = "full" if backup_type == "full" else "incr"
        for backup_dir in type_dir.iterdir():
            if not backup_dir.is_dir() or not is_backup_dir_name(backup_dir.name):
                continue

            archives = list_archives_in_dir(backup_dir)
            if is_date_dir_name(backup_dir.name):
                for backup_tar in archives:
                    timestamp = extract_backup_timestamp(backup_tar.name)
                    if not timestamp:
                        continue
                    candidates = s3_backup_paths_for_lookup(kind, timestamp)
                    matched = any(verify_s3_upload(backup_tar, s3_path, log) for s3_path in candidates)
                    if not matched:
                        continue
                    try:
                        backup_tar.unlink()
                        cleaned += 1
                        log(f"已清理 S3 已确认的本地孤儿备份: {backup_tar}")
                    except Exception as e:
                        log(f"警告: 清理孤儿备份失败 {backup_tar}: {e}")
                if list_archives_in_dir(backup_dir):
                    from mysql_backup.core.backup_day_manifest import rebuild_day_manifest
                    rebuild_day_manifest(backup_dir, kind=kind, log=log)
                else:
                    shutil.rmtree(backup_dir, ignore_errors=True)
                continue

            timestamp = extract_backup_timestamp(backup_dir.name)
            if not timestamp:
                continue
            backup_tar = find_backup_archive(backup_dir)
            candidates = s3_backup_paths_for_lookup(kind, timestamp)
            if backup_tar is not None:
                matched = any(verify_s3_upload(backup_tar, s3_path, log) for s3_path in candidates)
                if not matched:
                    continue
            else:
                if not any(s3_object_exists(p) for p in candidates):
                    continue

            try:
                shutil.rmtree(backup_dir)
                if not backup_dir.exists():
                    log(f"已清理 S3 已确认的本地孤儿备份: {backup_dir}")
                    cleaned += 1
            except Exception as e:
                log(f"警告: 清理孤儿备份失败 {backup_dir}: {e}")

    return cleaned
