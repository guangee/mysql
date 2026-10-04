#!/usr/bin/env python3
"""
增量备份脚本

执行 MySQL 增量备份，基于最新的全量备份
支持从本地或S3获取基础备份
"""

import os
import sys
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from mysql_backup.core.backup_day_manifest import (
    collect_database_metadata,
    upsert_day_manifest_entry,
)
from mysql_backup.core.backup_naming import (
    build_backup_name,
    day_dir_for,
    extract_backup_timestamp,
    find_backup_archive,
    resolve_backup_archive,
    resolve_backup_dir,
    s3_backup_key,
    timestamp_to_display,
)
from mysql_backup.core.backup_storage import (
    apply_local_retention,
    cleanup_local_full_base_if_on_s3,
    cleanup_local_orphan_backups_on_s3,
    ensure_backup_archived,
    materialize_backup_to,
    pack_backup_directory,
    setup_s3 as setup_s3_storage,
    stream_command,
    upload_and_verify_all,
    upload_metadata_to_all,
)

# 配置变量
MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
# 优先使用 MYSQL_BACKUP_USER，如果没有则使用 MYSQL_USER，最后默认使用 root
MYSQL_USER = os.environ.get("MYSQL_BACKUP_USER") or os.environ.get("MYSQL_USER", "root")
# 优先使用 MYSQL_BACKUP_PASSWORD，如果没有则根据用户类型选择密码
if os.environ.get("MYSQL_BACKUP_PASSWORD"):
    MYSQL_PASSWORD = os.environ.get("MYSQL_BACKUP_PASSWORD")
elif MYSQL_USER == "root":
    MYSQL_PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD") or os.environ.get("MYSQL_PASSWORD", "")
else:
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")

BACKUP_BASE_DIR = Path(os.environ.get("BACKUP_BASE_DIR", "/backups"))
S3_BACKUP_ENABLED = os.environ.get("S3_BACKUP_ENABLED", "false").lower() == "true"
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "mysql-backups")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_USE_SSL = os.environ.get("S3_USE_SSL", "true").lower() == "true"
S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "false").lower() == "true"
S3_ALIAS = os.environ.get("S3_ALIAS", "s3")
LOCAL_BACKUP_RETENTION_HOURS = int(os.environ.get("LOCAL_BACKUP_RETENTION_HOURS", "0"))

TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP_NAME = build_backup_name(TIMESTAMP, "incr")
DAY_DIR = day_dir_for(BACKUP_BASE_DIR / "incremental", TIMESTAMP)
WORK_DIR = BACKUP_BASE_DIR / ".work" / f"incr_{TIMESTAMP}"
BACKUP_FILENAME = BACKUP_NAME.filename
FINAL_ARCHIVE = DAY_DIR / BACKUP_FILENAME
INCREMENTAL_BACKUP_DIR = WORK_DIR  # xtrabackup 目标仍用此变量名

# 日志文件
LOG_FILE = BACKUP_BASE_DIR / "backup.log"

def log(message: str):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}"
    print(log_message, flush=True)
    
    # 同时写入日志文件
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_message + "\n")
    except Exception:
        pass  # 忽略日志写入错误

def setup_s3():
    """配置 S3 客户端（兼容旧调用）"""
    log("配置 S3 兼容对象存储客户端...")
    return setup_s3_storage(log)

def download_latest_full_backup() -> bool:
    """从 S3 下载最新的全量备份"""
    log("从 S3 下载最新的全量备份...")
    
    # 尝试从元数据获取最新的全量备份时间戳
    try:
        result = subprocess.run(
            ["mc", "cat", f"{S3_ALIAS}/{S3_BUCKET}/.metadata/latest_full_backup_timestamp.txt"],
            capture_output=True,
            text=True,
            check=False
        )
        latest_timestamp = result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        latest_timestamp = ""
    
    if not latest_timestamp:
        # 如果没有元数据，从文件列表获取最新的全量备份文件
        try:
            result = subprocess.run(
                ["mc", "ls", f"{S3_ALIAS}/{S3_BUCKET}/full/"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines:
                    # 解析最后一行（最新的）
                    parts = lines[-1].split()
                    if len(parts) >= 6:
                        latest_backup = parts[5]
                        # 提取时间戳
                        from mysql_backup.core.backup_naming import extract_backup_timestamp as _extract_ts
                        ts = _extract_ts(latest_backup)
                        if ts:
                            latest_timestamp = ts
        except Exception:
            pass
    
    if not latest_timestamp:
        log("错误: S3 中未找到全量备份")
        return False
    
    log(f"找到最新的全量备份时间戳: {latest_timestamp}")
    
    from mysql_backup.core.backup_naming import (
        build_backup_name,
        extract_backup_timestamp as _extract_ts,
        s3_backup_object_names_for_lookup,
    )

    candidates = s3_backup_object_names_for_lookup(latest_timestamp, "full")
    # mc ls 最后一项可能已是完整新文件名
    try:
        listed = subprocess.run(
            ["mc", "ls", f"{S3_ALIAS}/{S3_BUCKET}/full/"],
            capture_output=True,
            text=True,
            check=False,
        )
        if listed.returncode == 0:
            for line in listed.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 6 and _extract_ts(parts[5]) == latest_timestamp:
                    if parts[5] not in candidates:
                        candidates.insert(0, parts[5])
    except Exception:
        pass

    name_info = build_backup_name(latest_timestamp, "full")
    restore_dir = day_dir_for(BACKUP_BASE_DIR / "full", latest_timestamp)
    restore_dir.mkdir(parents=True, exist_ok=True)

    last_error = None
    for latest_backup in candidates:
        backup_tar = restore_dir / latest_backup
        # 兼容旧扁平 key 与 日期/文件名 key
        s3_candidates = [
            f"{S3_ALIAS}/{S3_BUCKET}/full/{latest_timestamp[:8]}/{latest_backup}",
            f"{S3_ALIAS}/{S3_BUCKET}/full/{latest_backup}",
        ]
        for s3_path in s3_candidates:
            try:
                subprocess.run(
                    ["mc", "cp", s3_path, str(backup_tar)],
                    check=True,
                    capture_output=True,
                )
                (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP").write_text(str(backup_tar))
                (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_TIMESTAMP").write_text(latest_timestamp)
                (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_FILE").write_text(latest_backup)
                (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_ID").write_text(name_info.backup_id)
                log(f"全量备份压缩包已下载到: {backup_tar}")
                return True
            except Exception as e:
                last_error = e
                backup_tar.unlink(missing_ok=True)
                continue

    log(f"错误: 下载全量备份失败: {last_error}")
    return False


def _resolve_full_archive_path() -> Optional[Path]:
    """定位最新全量备份压缩包路径。"""
    latest_backup_file = BACKUP_BASE_DIR / "LATEST_FULL_BACKUP"
    if latest_backup_file.exists():
        try:
            marked = Path(latest_backup_file.read_text().strip())
            if marked.is_file() and marked.name.endswith(".tar.gz"):
                return marked
            if marked.is_dir():
                archive = find_backup_archive(marked)
                if archive:
                    return archive
            marker_ts = (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_TIMESTAMP").read_text().strip()
            resolved = resolve_backup_archive(BACKUP_BASE_DIR / "full", marker_ts)
            if resolved is not None:
                return resolved
        except Exception as exc:
            log(f"读取本地全量备份失败: {exc}")
    # 回退：最新压缩包
    from mysql_backup.core.backup_naming import list_backup_archives
    items = list_backup_archives(BACKUP_BASE_DIR / "full")
    if items:
        return items[-1][1]
    return None


def _materialize_full_base(archive_path: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """
    为增量备份准备 basedir。
    返回: (实际 basedir, 用完后需要清理的临时目录或 None)
    """
    if archive_path.is_dir() and (archive_path / "xtrabackup_checkpoints").is_file():
        return archive_path, None

    archive = archive_path if archive_path.is_file() else find_backup_archive(archive_path)
    if archive is None:
        return None, None

    work_dir = BACKUP_BASE_DIR / ".work" / f"inc_base_{extract_backup_timestamp(archive.name) or archive.stem}_{os.getpid()}"
    if not materialize_backup_to(archive.parent, work_dir, log, archive=archive):
        shutil.rmtree(work_dir, ignore_errors=True)
        return None, None
    if not (work_dir / "xtrabackup_checkpoints").is_file():
        zst_files = list(work_dir.glob("*.zst"))
        if zst_files:
            log("工作区全量备份需要先 decompress...")
            try:
                stream_command(
                    ["xtrabackup", "--decompress", f"--target-dir={work_dir}"],
                    log,
                    prefix="xtrabackup: ",
                )
            except subprocess.CalledProcessError:
                shutil.rmtree(work_dir, ignore_errors=True)
                return None, None
    if not (work_dir / "xtrabackup_checkpoints").is_file():
        shutil.rmtree(work_dir, ignore_errors=True)
        return None, None
    return work_dir, work_dir


def get_base_backup() -> Tuple[Optional[Path], bool, Optional[Path], Optional[Path]]:
    """
    获取基础备份路径（始终基于最新的全量备份）
    返回: (basedir, 是否从 S3 下载, 源归档目录, 临时工作目录)
    """
    downloaded_from_s3 = False
    archive_dir = _resolve_full_archive_path()
    if archive_dir is not None:
        basedir, temp_dir = _materialize_full_base(archive_dir)
        if basedir is not None:
            log(f"使用本地全量备份作为基础: {archive_dir} (work={basedir})")
            return basedir, False, archive_dir, temp_dir
        log(f"本地全量目录不可用: {archive_dir}，尝试从 S3 准备")

    if S3_BACKUP_ENABLED:
        log("本地未找到全量备份，从 S3 下载最新的全量备份...")
        if download_latest_full_backup():
            downloaded_from_s3 = True
            archive_dir = _resolve_full_archive_path()
            if archive_dir is not None:
                basedir, temp_dir = _materialize_full_base(archive_dir)
                if basedir is not None:
                    log(f"已下载并准备全量备份作为基础: {archive_dir}")
                    return basedir, downloaded_from_s3, archive_dir, temp_dir
    else:
        log("S3 备份已禁用，无法从 S3 下载基础备份")

    log("错误: 无法找到或准备基础备份，请先执行全量备份")
    return None, False, None, None

def perform_incremental_backup():
    """执行增量备份"""
    log("开始增量备份...")

    base_backup, downloaded_from_s3, archive_dir, temp_base_dir = get_base_backup()
    if not base_backup:
        return 1

    log(f"基础备份路径: {base_backup}")
    
    # 创建增量备份目录
    INCREMENTAL_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    # 执行 xtrabackup 增量备份
    log(f"执行 XtraBackup 增量备份到 {INCREMENTAL_BACKUP_DIR}...")
    
    # 构建 xtrabackup 命令
    cmd = [
        "xtrabackup",
        "--backup",
        f"--target-dir={INCREMENTAL_BACKUP_DIR}",
        f"--incremental-basedir={base_backup}",
        "--compress",
        "--compress-threads=2",
        "--parallel=2"
    ]
    
    # 如果 host 是 localhost，使用 socket 连接（更可靠）
    if MYSQL_HOST in ("localhost", "127.0.0.1"):
        cmd.extend(["--socket=/var/run/mysqld/mysqld.sock"])
    else:
        cmd.extend([f"--host={MYSQL_HOST}", f"--port={MYSQL_PORT}"])
    
    cmd.extend([f"--user={MYSQL_USER}", f"--password={MYSQL_PASSWORD}"])
    
    try:
        stream_command(cmd, log, prefix="xtrabackup: ")
    except subprocess.CalledProcessError:
        log("错误: 增量备份失败")
        if temp_base_dir is not None:
            shutil.rmtree(temp_base_dir, ignore_errors=True)
        if archive_dir is not None:
            target = archive_dir.parent if archive_dir.is_file() else archive_dir
            ensure_backup_archived(target, log)
        return 1
    
    log(f"增量备份完成: {INCREMENTAL_BACKUP_DIR}")
    
    # 解压备份文件（用于验证和后续处理）
    log("解压备份文件...")
    try:
        stream_command(
            ["xtrabackup", "--decompress", f"--target-dir={INCREMENTAL_BACKUP_DIR}"],
            log,
            prefix="xtrabackup: ",
        )
    except subprocess.CalledProcessError as e:
        log(f"错误: 解压备份文件失败: {e}")
        if temp_base_dir is not None:
            shutil.rmtree(temp_base_dir, ignore_errors=True)
        if archive_dir is not None:
            target = archive_dir.parent if archive_dir.is_file() else archive_dir
            ensure_backup_archived(target, log)
        return 1
    
    log("分析备份中包含的数据库...")
    backed_up_databases = collect_database_metadata(
        mysql_host=MYSQL_HOST,
        mysql_port=MYSQL_PORT,
        mysql_user=MYSQL_USER,
        mysql_password=MYSQL_PASSWORD,
    )

    DAY_DIR.mkdir(parents=True, exist_ok=True)
    log(f"打包增量备份为 {FINAL_ARCHIVE}，并清理工作区...")
    backup_tar = pack_backup_directory(WORK_DIR, log, archive_name=BACKUP_FILENAME, dest_archive=FINAL_ARCHIVE)
    if backup_tar is None:
        log("错误: 打包失败，保留剩余本地文件")
        if temp_base_dir is not None:
            shutil.rmtree(temp_base_dir, ignore_errors=True)
        if archive_dir is not None:
            target = archive_dir.parent if archive_dir.is_file() else archive_dir
            ensure_backup_archived(target, log)
        return 1
    
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    upsert_day_manifest_entry(
        DAY_DIR,
        kind="incr",
        filename=BACKUP_FILENAME,
        timestamp=TIMESTAMP,
        version=BACKUP_NAME.version,
        size_bytes=backup_tar.stat().st_size,
        databases=backed_up_databases,
        recoverable_to=timestamp_to_display(TIMESTAMP),
        log=log,
    )

    # 保存最新的增量备份信息
    try:
        (BACKUP_BASE_DIR / "LATEST_INCREMENTAL_BACKUP").write_text(str(backup_tar))
        (BACKUP_BASE_DIR / "LATEST_INCREMENTAL_BACKUP_TIMESTAMP").write_text(TIMESTAMP)
        (BACKUP_BASE_DIR / "LATEST_INCREMENTAL_BACKUP_FILE").write_text(BACKUP_FILENAME)
        (BACKUP_BASE_DIR / "LATEST_INCREMENTAL_BACKUP_ID").write_text(BACKUP_NAME.backup_id)
    except Exception as e:
        log(f"警告: 保存元数据失败: {e}")
    
    # 如果启用了 S3 备份，上传到 S3
    if S3_BACKUP_ENABLED:
        log("S3 备份已启用，开始上传备份到所有配置的存储...")
        relative_key = s3_backup_key("incremental", TIMESTAMP, BACKUP_FILENAME)

        if not upload_and_verify_all(backup_tar, relative_key, log):
            log("错误: 上传校验失败（部分或全部存储失败），保留本地备份")
            return 1

        log(f"备份成功上传到 S3 并校验通过: {relative_key}")

        upload_metadata_to_all(".metadata/latest_incremental_backup_timestamp.txt", TIMESTAMP, log)
        day_xml = DAY_DIR / "day.xml"
        if day_xml.is_file():
            upload_and_verify_all(day_xml, s3_backup_key("incremental", TIMESTAMP, "day.xml"), log)

        if not apply_local_retention(backup_tar, verified=True, log=log):
            return 1

        if downloaded_from_s3 or LOCAL_BACKUP_RETENTION_HOURS == 0:
            if archive_dir is not None:
                cleanup_local_full_base_if_on_s3(archive_dir, log)
        cleanup_local_orphan_backups_on_s3(BACKUP_BASE_DIR, log)
    else:
        log("S3 备份已禁用，仅保留本地备份")
        log(f"备份文件位置: {backup_tar}")
        log("注意: 本地备份将永久保留，不会自动删除")
    
    if temp_base_dir is not None:
        shutil.rmtree(temp_base_dir, ignore_errors=True)
        log(f"已清理增量基础工作目录: {temp_base_dir}")
    if archive_dir is not None:
        target = archive_dir.parent if archive_dir.is_file() else archive_dir
        ensure_backup_archived(target, log)

    log("增量备份流程完成")
    log(f"备份时间戳: {TIMESTAMP}")
    if backed_up_databases:
        log(
            "备份的数据库（基于全量备份）: "
            + ", ".join(db["name"] for db in backed_up_databases)
        )
    else:
        log("注意: 增量备份基于全量备份，数据库列表请参考对应的全量备份")

    return 0

def send_dingtalk_notify(status: str, message: str):
    """发送钉钉通知"""
    try:
        subprocess.run(
            [sys.executable, "-m", "mysql_backup", "notify", "dingtalk", status, message],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass

def main():
    """主函数"""
    log("========== 增量备份开始 ==========")
    log(f"[增量备份] 脚本被触发 PID={os.getpid()} | S3_ENDPOINT={os.environ.get('S3_ENDPOINT', '(未设置)')} | BACKUP_BASE_DIR={os.environ.get('BACKUP_BASE_DIR', '(未设置)')}")
    
    # 如果启用了 S3 备份，配置 S3 客户端
    if S3_BACKUP_ENABLED:
        if not setup_s3():
            sys.exit(1)
    else:
        log("S3 备份已禁用，跳过 S3 配置")
    
    # 执行备份
    backup_result = perform_incremental_backup()
    
    if backup_result == 0:
        # 备份成功
        try:
            latest_timestamp = (BACKUP_BASE_DIR / "LATEST_INCREMENTAL_BACKUP_TIMESTAMP").read_text().strip()
        except Exception:
            latest_timestamp = TIMESTAMP
        
        backup_file_path = FINAL_ARCHIVE if FINAL_ARCHIVE.exists() else None
        backup_size = ""
        if backup_file_path and backup_file_path.exists():
            size_bytes = backup_file_path.stat().st_size
            if size_bytes < 1024 * 1024:
                backup_size = f"{size_bytes / 1024:.2f} KB"
            else:
                backup_size = f"{size_bytes / (1024 * 1024):.2f} MB"

        backup_info = (
            f"**备份类型**: 增量备份\n\n"
            f"**备份时间戳**: {latest_timestamp}\n\n"
            f"**备份文件**: {BACKUP_FILENAME}\n\n"
            f"**程序版本**: {BACKUP_NAME.version}"
        )
        if backup_size:
            backup_info += f"\n\n**文件大小**: {backup_size}"
        if S3_BACKUP_ENABLED:
            backup_info += "\n\n**S3 状态**: ✅ 已上传"
        else:
            backup_info += "\n\n**存储位置**: 本地"
        
        send_dingtalk_notify("success", backup_info)
        log("========== 增量备份结束 ==========")
    else:
        # 备份失败
        error_msg = f"**备份类型**: 增量备份\n\n**错误时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n**错误信息**: 备份过程中发生错误，请查看日志文件 {LOG_FILE} 获取详细信息"
        send_dingtalk_notify("failure", error_msg)
        log("========== 增量备份失败 ==========")
        sys.exit(1)

if __name__ == "__main__":
    main()

