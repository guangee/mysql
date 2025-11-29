#!/usr/bin/env python3
"""
全量备份脚本

执行 MySQL 全量备份，支持本地存储和 S3 上传
"""

import os
import sys
import subprocess
import shutil
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

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
S3_BACKUP_ENABLED = os.environ.get("S3_BACKUP_ENABLED", "true").lower() == "true"
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "mysql-backups")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_USE_SSL = os.environ.get("S3_USE_SSL", "true").lower() == "true"
S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "false").lower() == "true"
S3_ALIAS = os.environ.get("S3_ALIAS", "s3")
LOCAL_BACKUP_RETENTION_HOURS = int(os.environ.get("LOCAL_BACKUP_RETENTION_HOURS", "0"))

# 备份目录
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
FULL_BACKUP_DIR = BACKUP_BASE_DIR / "full" / TIMESTAMP

# 日志文件
LOG_FILE = BACKUP_BASE_DIR / "backup.log"

def log(message: str):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}"
    print(log_message)
    
    # 同时写入日志文件
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_message + "\n")
    except Exception:
        pass  # 忽略日志写入错误

def setup_s3():
    """配置 S3 客户端"""
    log("配置 S3 兼容对象存储客户端...")
    
    # 验证必要的配置
    if not S3_ENDPOINT or not S3_ACCESS_KEY or not S3_SECRET_KEY:
        log("错误: S3 配置不完整，请设置 S3_ENDPOINT, S3_ACCESS_KEY 和 S3_SECRET_KEY")
        sys.exit(1)
    
    # 构建 S3 URL
    if S3_USE_SSL:
        s3_url = f"https://{S3_ENDPOINT}"
    else:
        s3_url = f"http://{S3_ENDPOINT}"
    
    # 配置 S3 别名
    try:
        subprocess.run(
            ["mc", "alias", "set", S3_ALIAS, s3_url, S3_ACCESS_KEY, S3_SECRET_KEY, "--api", "s3v4"],
            check=False,
            capture_output=True
        )
    except Exception:
        pass  # 忽略错误
    
    # 创建存储桶（如果不存在）
    try:
        subprocess.run(
            ["mc", "mb", f"{S3_ALIAS}/{S3_BUCKET}"],
            check=False,
            capture_output=True
        )
    except Exception:
        pass  # 忽略错误
    
    log(f"S3 配置完成 (Endpoint: {S3_ENDPOINT}, Bucket: {S3_BUCKET})")

def perform_full_backup():
    """执行全量备份"""
    log("开始全量备份...")
    
    # 创建备份目录
    FULL_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    # 执行 xtrabackup 全量备份
    log(f"执行 XtraBackup 全量备份到 {FULL_BACKUP_DIR}...")
    
    # 构建 xtrabackup 命令
    cmd = [
        "xtrabackup",
        "--backup",
        f"--target-dir={FULL_BACKUP_DIR}",
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
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if result.stdout:
            for line in result.stdout.split('\n'):
                if line.strip():
                    log(f"xtrabackup: {line}")
    except subprocess.CalledProcessError as e:
        log("错误: 全量备份失败")
        if e.stderr:
            log(f"错误详情: {e.stderr}")
        return 1
    
    log(f"全量备份完成: {FULL_BACKUP_DIR}")
    
    # 准备备份（应用日志）
    log("准备备份（应用日志）...")
    try:
        subprocess.run(
            ["xtrabackup", "--decompress", f"--target-dir={FULL_BACKUP_DIR}"],
            check=True,
            capture_output=True
        )
        subprocess.run(
            ["xtrabackup", "--prepare", f"--target-dir={FULL_BACKUP_DIR}"],
            check=True,
            capture_output=True
        )
    except subprocess.CalledProcessError as e:
        log(f"错误: 准备备份失败: {e}")
        return 1
    
    # 列出备份的数据库（排除系统数据库）
    log("分析备份中包含的数据库...")
    backed_up_databases = []
    system_dbs = {"information_schema", "performance_schema", "mysql", "sys"}
    
    # 从 MySQL 查询用户数据库列表
    mysql_cmd = ["mysql", "-N", "-e",
                 "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME NOT IN ('information_schema', 'performance_schema', 'mysql', 'sys') ORDER BY SCHEMA_NAME;"]
    
    if MYSQL_HOST in ("localhost", "127.0.0.1"):
        mysql_cmd.insert(1, "--socket=/var/run/mysqld/mysqld.sock")
    else:
        mysql_cmd.insert(1, f"-h{MYSQL_HOST}")
        mysql_cmd.insert(2, f"-P{MYSQL_PORT}")
    
    mysql_cmd.extend([f"-u{MYSQL_USER}", f"-p{MYSQL_PASSWORD}"])
    
    try:
        result = subprocess.run(mysql_cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            for db in result.stdout.strip().split('\n'):
                db = db.strip()
                if db and db not in system_dbs:
                    backed_up_databases.append(db)
    except Exception:
        pass  # 忽略错误
    
    # 重新压缩
    log("重新压缩备份文件...")
    backup_tar = FULL_BACKUP_DIR / "backup.tar.gz"
    
    try:
        log("开始打包文件...")
        with tarfile.open(backup_tar, "w:gz") as tar:
            for item in FULL_BACKUP_DIR.iterdir():
                if item.name != "backup.tar.gz":
                    tar.add(item, arcname=item.name)
        
        backup_size = backup_tar.stat().st_size
        backup_size_human = f"{backup_size / (1024*1024):.2f} MB"
        log(f"压缩完成，文件大小: {backup_size_human}")
        
        log("清理源文件...")
        for item in FULL_BACKUP_DIR.iterdir():
            if item.name != "backup.tar.gz":
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
        log("源文件清理完成")
    except Exception as e:
        log(f"警告: 压缩过程可能有问题: {e}，但继续执行...")
    
    # 保存最新的全量备份信息
    log("保存备份元数据...")
    try:
        (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP").write_text(str(FULL_BACKUP_DIR))
        (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_TIMESTAMP").write_text(TIMESTAMP)
        (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_FILE").write_text(f"backup_{TIMESTAMP}.tar.gz")
        log("备份元数据已保存")
    except Exception as e:
        log(f"警告: 保存元数据失败: {e}")
    
    # 清除增量备份标记
    for marker in ["LATEST_INCREMENTAL_BACKUP", "LATEST_INCREMENTAL_BACKUP_TIMESTAMP", "LATEST_INCREMENTAL_BACKUP_FILE"]:
        marker_file = BACKUP_BASE_DIR / marker
        if marker_file.exists():
            marker_file.unlink()
    
    # 如果启用了 S3 备份，上传到 S3
    if S3_BACKUP_ENABLED:
        log("S3 备份已启用，开始上传备份到 S3...")
        s3_path = f"{S3_ALIAS}/{S3_BUCKET}/full/backup_{TIMESTAMP}.tar.gz"
        log(f"上传文件: {backup_tar} -> {s3_path}")
        
        try:
            result = subprocess.run(
                ["mc", "cp", str(backup_tar), s3_path],
                check=True,
                capture_output=True,
                text=True
            )
            if result.stdout:
                for line in result.stdout.split('\n'):
                    if line.strip():
                        log(f"mc: {line}")
            
            log(f"备份成功上传到 S3: backup_{TIMESTAMP}.tar.gz")
            
            # 将元数据上传到 S3
            try:
                subprocess.run(
                    ["mc", "pipe", f"{S3_ALIAS}/{S3_BUCKET}/.metadata/latest_full_backup_timestamp.txt"],
                    input=TIMESTAMP,
                    text=True,
                    check=False,
                    capture_output=True
                )
            except Exception:
                pass  # 忽略元数据上传错误
            
            # 处理本地备份文件保留策略
            if LOCAL_BACKUP_RETENTION_HOURS == 0:
                # 立即删除本地备份文件
                shutil.rmtree(FULL_BACKUP_DIR, ignore_errors=True)
                log("本地备份文件已清理（立即删除模式）")
            else:
                # 记录删除时间
                delete_time = int((datetime.now() + timedelta(hours=LOCAL_BACKUP_RETENTION_HOURS)).timestamp())
                (FULL_BACKUP_DIR / ".delete_after").write_text(str(delete_time))
                delete_time_str = (datetime.now() + timedelta(hours=LOCAL_BACKUP_RETENTION_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
                log(f"本地备份文件将保留 {LOCAL_BACKUP_RETENTION_HOURS} 小时，预计删除时间: {delete_time_str}")
        except subprocess.CalledProcessError as e:
            log("错误: 上传到 S3 失败，保留本地备份")
            if e.stderr:
                log(f"错误详情: {e.stderr}")
            return 1
    else:
        log("S3 备份已禁用，仅保留本地备份")
        log(f"备份文件位置: {backup_tar}")
        log("注意: 本地备份将永久保留，不会自动删除")
    
    log("全量备份流程完成")
    log(f"备份文件: {backup_tar}")
    log(f"备份时间戳: {TIMESTAMP}")
    if backed_up_databases:
        log(f"已备份的数据库: {', '.join(backed_up_databases)}")
    else:
        log("警告: 未找到用户数据库（可能只包含系统数据库）")
    
    return 0

def send_dingtalk_notify(status: str, message: str):
    """发送钉钉通知"""
    notify_script = Path("/scripts/tasks/notify/dingtalk_notify.py")
    if notify_script.exists():
        try:
            subprocess.run(
                ["/scripts/tasks/notify/dingtalk_notify.py", status, message],
                check=False,
                capture_output=True
            )
        except Exception:
            pass  # 忽略通知错误

def main():
    """主函数"""
    log("========== 全量备份开始 ==========")
    
    # 如果启用了 S3 备份，配置 S3 客户端
    if S3_BACKUP_ENABLED:
        setup_s3()
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
        
        backup_file_path = BACKUP_BASE_DIR / "full" / latest_timestamp / "backup.tar.gz"
        backup_size = ""
        if backup_file_path.exists():
            size_bytes = backup_file_path.stat().st_size
            if size_bytes < 1024 * 1024:
                backup_size = f"{size_bytes / 1024:.2f} KB"
            else:
                backup_size = f"{size_bytes / (1024 * 1024):.2f} MB"
        
        backup_info = f"**备份类型**: 全量备份\n\n**备份时间戳**: {latest_timestamp}\n\n**备份文件**: backup_{latest_timestamp}.tar.gz"
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

