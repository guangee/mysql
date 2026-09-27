"""
备份存储工具模块

提供 S3 上传校验与本地备份清理的共享逻辑
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

S3_BACKUP_ENABLED = os.environ.get("S3_BACKUP_ENABLED", "true").lower() == "true"
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "mysql-backups")
S3_USE_SSL = os.environ.get("S3_USE_SSL", "true").lower() == "true"
S3_ALIAS = os.environ.get("S3_ALIAS", "s3")
LOCAL_BACKUP_RETENTION_HOURS = int(os.environ.get("LOCAL_BACKUP_RETENTION_HOURS", "0"))

TIMESTAMP_PATTERN = re.compile(r"^\d{8}_\d{6}$")


def setup_s3(log: Callable[[str], None]) -> bool:
    """配置 S3 客户端，返回是否配置成功"""
    if not S3_ENDPOINT or not S3_ACCESS_KEY or not S3_SECRET_KEY:
        log("错误: S3 配置不完整，请设置 S3_ENDPOINT, S3_ACCESS_KEY 和 S3_SECRET_KEY")
        return False

    s3_url = f"https://{S3_ENDPOINT}" if S3_USE_SSL else f"http://{S3_ENDPOINT}"

    try:
        subprocess.run(
            ["mc", "alias", "set", S3_ALIAS, s3_url, S3_ACCESS_KEY, S3_SECRET_KEY, "--api", "s3v4"],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["mc", "mb", f"{S3_ALIAS}/{S3_BUCKET}"],
            check=False,
            capture_output=True,
        )
    except Exception as e:
        log(f"错误: S3 客户端配置失败: {e}")
        return False

    log(f"S3 配置完成 (Endpoint: {S3_ENDPOINT}, Bucket: {S3_BUCKET})")
    return True


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


def upload_and_verify(local_path: Path, s3_path: str, log: Callable[[str], None]) -> bool:
    """上传文件到 S3 并校验一致性"""
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


def apply_local_retention(
    backup_dir: Path,
    verified: bool,
    log: Callable[[str], None],
) -> bool:
    """
    根据保留策略处理本地备份目录。
    仅在 verified=True 时才会删除本地文件。
    """
    if not verified:
        log("上传校验未通过，保留本地备份")
        return False

    if LOCAL_BACKUP_RETENTION_HOURS == 0:
        if not backup_dir.exists():
            log(f"本地备份目录已不存在: {backup_dir}")
            return True
        try:
            shutil.rmtree(backup_dir)
            if backup_dir.exists():
                log(f"错误: 本地备份目录删除失败: {backup_dir}")
                return False
            log("本地备份文件已清理（上传校验通过后立即删除）")
            return True
        except Exception as e:
            log(f"错误: 删除本地备份目录失败: {e}")
            return False

    delete_time = int((datetime.now() + timedelta(hours=LOCAL_BACKUP_RETENTION_HOURS)).timestamp())
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / ".delete_after").write_text(str(delete_time))
        delete_time_str = datetime.fromtimestamp(delete_time).strftime("%Y-%m-%d %H:%M:%S")
        log(f"本地备份文件将保留 {LOCAL_BACKUP_RETENTION_HOURS} 小时，预计删除时间: {delete_time_str}")
        return True
    except Exception as e:
        log(f"警告: 写入删除标记失败: {e}")
        return False


def s3_full_backup_path(timestamp: str) -> str:
    """获取全量备份在 S3 上的路径"""
    return f"{S3_ALIAS}/{S3_BUCKET}/full/backup_{timestamp}.tar.gz"


def s3_incremental_backup_path(timestamp: str) -> str:
    """获取增量备份在 S3 上的路径"""
    return f"{S3_ALIAS}/{S3_BUCKET}/incremental/backup_{timestamp}.tar.gz"


def s3_object_exists(s3_path: str) -> bool:
    """检查 S3 对象是否存在"""
    return get_s3_object_size(s3_path) is not None


def cleanup_local_full_base_if_on_s3(
    base_backup_dir: Path,
    log: Callable[[str], None],
) -> bool:
    """
    增量备份完成后，若全量基线目录是从 S3 恢复的临时副本，
    且 S3 上已有对应全量备份，则清理本地全量目录以释放磁盘。
    """
    if LOCAL_BACKUP_RETENTION_HOURS != 0:
        return False

    timestamp = base_backup_dir.name
    if not TIMESTAMP_PATTERN.match(timestamp):
        log(f"跳过清理: 无法识别全量备份时间戳: {base_backup_dir}")
        return False

    s3_path = s3_full_backup_path(timestamp)
    if not s3_object_exists(s3_path):
        log(f"S3 上未找到对应全量备份，保留本地基线: {s3_path}")
        return False

    if not base_backup_dir.exists():
        return True

    backup_base_dir = base_backup_dir.parent.parent
    latest_marker = backup_base_dir / "LATEST_FULL_BACKUP"

    try:
        shutil.rmtree(base_backup_dir)
        if base_backup_dir.exists():
            log(f"错误: 全量基线目录删除失败: {base_backup_dir}")
            return False

        if latest_marker.exists():
            try:
                if Path(latest_marker.read_text().strip()) == base_backup_dir:
                    latest_marker.unlink(missing_ok=True)
                    (backup_base_dir / "LATEST_FULL_BACKUP_TIMESTAMP").unlink(missing_ok=True)
                    (backup_base_dir / "LATEST_FULL_BACKUP_FILE").unlink(missing_ok=True)
            except Exception:
                pass

        log(f"已清理本地全量基线目录（S3 已有 verified 备份）: {base_backup_dir}")
        return True
    except Exception as e:
        log(f"错误: 清理全量基线目录失败: {e}")
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
    latest_full_ts = ""
    latest_full_marker = backup_base_dir / "LATEST_FULL_BACKUP_TIMESTAMP"
    if latest_full_marker.exists():
        try:
            latest_full_ts = latest_full_marker.read_text().strip()
        except Exception:
            pass

    for backup_type, s3_path_fn in (
        ("full", s3_full_backup_path),
        ("incremental", s3_incremental_backup_path),
    ):
        type_dir = backup_base_dir / backup_type
        if not type_dir.exists():
            continue

        for backup_dir in type_dir.iterdir():
            if not backup_dir.is_dir() or not TIMESTAMP_PATTERN.match(backup_dir.name):
                continue

            timestamp = backup_dir.name
            backup_tar = backup_dir / "backup.tar.gz"
            s3_path = s3_path_fn(timestamp)

            if backup_tar.exists():
                if not verify_s3_upload(backup_tar, s3_path, log):
                    continue
            elif not s3_object_exists(s3_path):
                continue
            elif backup_type == "full" and timestamp == latest_full_ts:
                # 当前标记的全量目录可能正被增量备份使用，跳过
                continue

            try:
                shutil.rmtree(backup_dir)
                if not backup_dir.exists():
                    log(f"已清理 S3 已确认的本地孤儿备份: {backup_dir}")
                    cleaned += 1
            except Exception as e:
                log(f"警告: 清理孤儿备份失败 {backup_dir}: {e}")

    return cleaned
