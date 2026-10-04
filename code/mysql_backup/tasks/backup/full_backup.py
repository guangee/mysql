#!/usr/bin/env python3
"""
全量备份脚本

执行 MySQL 全量备份，支持本地存储和 S3 上传
"""

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

from mysql_backup.core.backup_day_manifest import (
    collect_database_metadata,
    upsert_day_manifest_entry,
)
from mysql_backup.core.backup_naming import (
    build_backup_name,
    day_dir_for,
    find_backup_archive,
    s3_backup_key,
    timestamp_to_display,
)
from mysql_backup.core.backup_storage import (
    apply_local_retention,
    cleanup_local_orphan_backups_on_s3,
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

# 按日目录 + 工作目录（xtrabackup 写入工作区，最终压缩包落到日期目录）
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP_NAME = build_backup_name(TIMESTAMP, "full")
DAY_DIR = day_dir_for(BACKUP_BASE_DIR / "full", TIMESTAMP)
WORK_DIR = BACKUP_BASE_DIR / ".work" / f"full_{TIMESTAMP}"
BACKUP_FILENAME = BACKUP_NAME.filename
FINAL_ARCHIVE = DAY_DIR / BACKUP_FILENAME

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

def perform_full_backup():
    """执行全量备份"""
    log("开始全量备份...")
    
    # 创建备份目录
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    DAY_DIR.mkdir(parents=True, exist_ok=True)
    
    # 执行 xtrabackup 全量备份
    log(f"执行 XtraBackup 全量备份到工作区 {WORK_DIR}...")
    
    # 构建 xtrabackup 命令
    cmd = [
        "xtrabackup",
        "--backup",
        f"--target-dir={WORK_DIR}",
        "--compress",
        "--compress-threads=4",
        "--parallel=4"
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
        log("错误: 全量备份失败")
        return 1
    
    log(f"全量备份完成: {WORK_DIR}")
    
    # 准备备份（应用日志）
    log("准备备份（应用日志）...")
    try:
        log("解压备份文件...")
        stream_command(
            ["xtrabackup", "--decompress", f"--target-dir={WORK_DIR}"],
            log,
            prefix="xtrabackup: ",
        )
        log("应用 redo 日志...")
        stream_command(
            ["xtrabackup", "--prepare", f"--target-dir={WORK_DIR}"],
            log,
            prefix="xtrabackup: ",
        )
    except subprocess.CalledProcessError as e:
        log(f"错误: 准备备份失败: {e}")
        return 1
    
    # 采集用户库元数据（名称/大小/表数/近似行数）
    log("分析备份中包含的数据库...")
    backed_up_databases = collect_database_metadata(
        mysql_host=MYSQL_HOST,
        mysql_port=MYSQL_PORT,
        mysql_user=MYSQL_USER,
        mysql_password=MYSQL_PASSWORD,
    )
    if backed_up_databases:
        log(
            "已采集库元数据: "
            + ", ".join(
                f"{db['name']}(tables={db['table_count']}, size={db['size_bytes']})"
                for db in backed_up_databases
            )
        )
    else:
        log("警告: 未找到用户数据库（可能只包含系统数据库）")

    log(f"打包备份为 {FINAL_ARCHIVE}，并清理工作区未压缩文件...")
    backup_tar = pack_backup_directory(WORK_DIR, log, archive_name=BACKUP_FILENAME, dest_archive=FINAL_ARCHIVE)
    if backup_tar is None:
        log("错误: 打包失败，保留剩余本地文件")
        return 1
    # 清理空工作目录
    try:
        import shutil
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    except Exception:
        pass
    backup_size_human = f"{backup_tar.stat().st_size / (1024*1024):.2f} MB"
    log(f"压缩完成，文件大小: {backup_size_human}")

    upsert_day_manifest_entry(
        DAY_DIR,
        kind="full",
        filename=BACKUP_FILENAME,
        timestamp=TIMESTAMP,
        version=BACKUP_NAME.version,
        size_bytes=backup_tar.stat().st_size,
        databases=backed_up_databases,
        recoverable_to=timestamp_to_display(TIMESTAMP),
        log=log,
    )

    # 保存最新的全量备份信息
    log("保存备份元数据...")
    try:
        (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP").write_text(str(backup_tar))
        (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_TIMESTAMP").write_text(TIMESTAMP)
        (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_FILE").write_text(BACKUP_FILENAME)
        (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_ID").write_text(BACKUP_NAME.backup_id)
        log(f"备份元数据已保存: {BACKUP_FILENAME} @ {DAY_DIR.name}")
    except Exception as e:
        log(f"警告: 保存元数据失败: {e}")
    
    # 清除增量备份标记
    for marker in ["LATEST_INCREMENTAL_BACKUP", "LATEST_INCREMENTAL_BACKUP_TIMESTAMP", "LATEST_INCREMENTAL_BACKUP_FILE"]:
        marker_file = BACKUP_BASE_DIR / marker
        if marker_file.exists():
            marker_file.unlink()
    
    # 如果启用了 S3 备份，上传到 S3
    if S3_BACKUP_ENABLED:
        log("S3 备份已启用，开始上传备份到所有配置的存储...")
        relative_key = s3_backup_key("full", TIMESTAMP, BACKUP_FILENAME)

        if not upload_and_verify_all(backup_tar, relative_key, log):
            log("错误: 上传校验失败（部分或全部存储失败），保留本地备份")
            return 1

        log(f"备份成功上传到 S3 并校验通过: {relative_key}")

        upload_metadata_to_all(".metadata/latest_full_backup_timestamp.txt", TIMESTAMP, log)
        # 同步当日说明
        day_xml = DAY_DIR / "day.xml"
        if day_xml.is_file():
            upload_and_verify_all(day_xml, s3_backup_key("full", TIMESTAMP, "day.xml"), log)

        if not apply_local_retention(backup_tar, verified=True, log=log):
            return 1
        cleanup_local_orphan_backups_on_s3(BACKUP_BASE_DIR, log)
    else:
        log("S3 备份已禁用，仅保留本地备份")
        log(f"备份文件位置: {backup_tar}")
        log("注意: 本地备份将永久保留，不会自动删除")
    
    log("全量备份流程完成")
    log(f"备份文件: {backup_tar}")
    log(f"备份时间戳: {TIMESTAMP}")
    if backed_up_databases:
        log(f"已备份的数据库: {', '.join(db['name'] for db in backed_up_databases)}")
    
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
    log("========== 全量备份开始 ==========")
    
    # 如果启用了 S3 备份，配置 S3 客户端
    if S3_BACKUP_ENABLED:
        log("配置 S3 兼容对象存储客户端...")
        if not setup_s3_storage(log):
            sys.exit(1)
    else:
        log("S3 备份已禁用，跳过 S3 配置")
    
    # 执行备份
    backup_result = perform_full_backup()
    
    if backup_result == 0:
        # 备份成功
        try:
            latest_timestamp = (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_TIMESTAMP").read_text().strip()
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
            f"**备份类型**: 全量备份\n\n"
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
        log("========== 全量备份结束 ==========")
    else:
        # 备份失败
        error_msg = f"**备份类型**: 全量备份\n\n**错误时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n**错误信息**: 备份过程中发生错误，请查看日志文件 {LOG_FILE} 获取详细信息"
        send_dingtalk_notify("failure", error_msg)
        log("========== 全量备份失败 ==========")
        sys.exit(1)

if __name__ == "__main__":
    main()

