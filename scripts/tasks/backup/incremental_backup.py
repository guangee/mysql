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
INCREMENTAL_BACKUP_DIR = BACKUP_BASE_DIR / "incremental" / TIMESTAMP

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
                        if latest_backup.startswith("backup_") and latest_backup.endswith(".tar.gz"):
                            latest_timestamp = latest_backup[7:-7]  # 移除 backup_ 和 .tar.gz
        except Exception:
            pass
    
    if not latest_timestamp:
        log("错误: S3 中未找到全量备份")
        return False
    
    log(f"找到最新的全量备份时间戳: {latest_timestamp}")
    
    latest_backup = f"backup_{latest_timestamp}.tar.gz"
    restore_dir = BACKUP_BASE_DIR / "full" / latest_timestamp
    restore_dir.mkdir(parents=True, exist_ok=True)
    
    # 下载并解压
    backup_tar = restore_dir / "backup.tar.gz"
    try:
        subprocess.run(
            ["mc", "cp", f"{S3_ALIAS}/{S3_BUCKET}/full/{latest_backup}", str(backup_tar)],
            check=True,
            capture_output=True
        )
        
        with tarfile.open(backup_tar, "r:gz") as tar:
            tar.extractall(path=restore_dir)
        backup_tar.unlink()
        
        # 更新标记文件
        (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP").write_text(str(restore_dir))
        (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_TIMESTAMP").write_text(latest_timestamp)
        (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP_FILE").write_text(latest_backup)
        
        log(f"全量备份已恢复到: {restore_dir}")
        return True
    except Exception as e:
        log(f"错误: 下载全量备份失败: {e}")
        return False

def get_base_backup() -> Optional[Path]:
    """获取基础备份路径（始终基于最新的全量备份）"""
    # 检查本地是否有最新的全量备份目录
    latest_backup_file = BACKUP_BASE_DIR / "LATEST_FULL_BACKUP"
    if latest_backup_file.exists():
        try:
            base_backup = Path(latest_backup_file.read_text().strip())
            if base_backup.exists() and base_backup.is_dir():
                log(f"使用本地全量备份作为基础: {base_backup}")
                return base_backup
        except Exception:
            pass
    
    # 本地没有，如果启用了 S3 备份，尝试从 S3 下载
    if S3_BACKUP_ENABLED:
        log("本地未找到全量备份，从 S3 下载最新的全量备份...")
        if download_latest_full_backup():
            try:
                base_backup = Path(latest_backup_file.read_text().strip())
                if base_backup.exists() and base_backup.is_dir():
                    log(f"已下载并准备全量备份作为基础: {base_backup}")
                    return base_backup
            except Exception:
                pass
    else:
        log("S3 备份已禁用，无法从 S3 下载基础备份")
    
    log("错误: 无法找到或准备基础备份，请先执行全量备份")
    return None

def perform_incremental_backup():
    """执行增量备份"""
    log("开始增量备份...")
    
    # 获取基础备份
    base_backup = get_base_backup()
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
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if result.stdout:
            for line in result.stdout.split('\n'):
                if line.strip():
                    log(f"xtrabackup: {line}")
    except subprocess.CalledProcessError as e:
        log("错误: 增量备份失败")
        if e.stderr:
            log(f"错误详情: {e.stderr}")
        return 1
    
    log(f"增量备份完成: {INCREMENTAL_BACKUP_DIR}")
    
    # 解压备份文件（用于验证和后续处理）
    log("解压备份文件...")
    try:
        subprocess.run(
            ["xtrabackup", "--decompress", f"--target-dir={INCREMENTAL_BACKUP_DIR}"],
            check=True,
            capture_output=True
        )
    except subprocess.CalledProcessError as e:
        log(f"错误: 解压备份文件失败: {e}")
        return 1
    
    # 列出备份的数据库（排除系统数据库）
    log("分析备份中包含的数据库...")
    backed_up_databases = []
    
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
                if db:
                    backed_up_databases.append(db)
    except Exception:
        pass  # 忽略错误
    
    # 压缩备份（不进行 prepare，prepare 应该在恢复时进行）
    log("压缩备份文件...")
    backup_tar = INCREMENTAL_BACKUP_DIR / "backup.tar.gz"
    
    try:
        with tarfile.open(backup_tar, "w:gz") as tar:
            for item in INCREMENTAL_BACKUP_DIR.iterdir():
                if item.name != "backup.tar.gz":
                    tar.add(item, arcname=item.name)
        
        # 删除已打包的文件
        for item in INCREMENTAL_BACKUP_DIR.iterdir():
            if item.name != "backup.tar.gz":
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
    except Exception as e:
        log(f"警告: 压缩过程可能有问题: {e}")
    
    # 保存最新的增量备份信息
    try:
        (BACKUP_BASE_DIR / "LATEST_INCREMENTAL_BACKUP_TIMESTAMP").write_text(TIMESTAMP)
        (BACKUP_BASE_DIR / "LATEST_INCREMENTAL_BACKUP_FILE").write_text(f"backup_{TIMESTAMP}.tar.gz")
    except Exception as e:
        log(f"警告: 保存元数据失败: {e}")
    
    # 如果启用了 S3 备份，上传到 S3
    if S3_BACKUP_ENABLED:
        log("S3 备份已启用，开始上传备份到 S3...")
        s3_path = f"{S3_ALIAS}/{S3_BUCKET}/incremental/backup_{TIMESTAMP}.tar.gz"
        
        try:
            subprocess.run(
                ["mc", "cp", str(backup_tar), s3_path],
                check=True,
                capture_output=True
            )
            log(f"备份成功上传到 S3: backup_{TIMESTAMP}.tar.gz")
            
            # 将元数据上传到 S3
            try:
                subprocess.run(
                    ["mc", "pipe", f"{S3_ALIAS}/{S3_BUCKET}/.metadata/latest_incremental_backup_timestamp.txt"],
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
                shutil.rmtree(INCREMENTAL_BACKUP_DIR, ignore_errors=True)
                log("本地备份文件已清理（立即删除模式）")
            else:
                # 记录删除时间
                delete_time = int((datetime.now() + timedelta(hours=LOCAL_BACKUP_RETENTION_HOURS)).timestamp())
                (INCREMENTAL_BACKUP_DIR / ".delete_after").write_text(str(delete_time))
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
    
    log("增量备份流程完成")
    log(f"备份时间戳: {TIMESTAMP}")
    if backed_up_databases:
        log(f"备份的数据库（基于全量备份）: {', '.join(backed_up_databases)}")
    else:
        log("注意: 增量备份基于全量备份，数据库列表请参考对应的全量备份")
    
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
    log("========== 增量备份开始 ==========")
    
    # 如果启用了 S3 备份，配置 S3 客户端
    if S3_BACKUP_ENABLED:
        setup_s3()
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
        
        backup_file_path = BACKUP_BASE_DIR / "incremental" / latest_timestamp / "backup.tar.gz"
        backup_size = ""
        if backup_file_path.exists():
            size_bytes = backup_file_path.stat().st_size
            if size_bytes < 1024 * 1024:
                backup_size = f"{size_bytes / 1024:.2f} KB"
            else:
                backup_size = f"{size_bytes / (1024 * 1024):.2f} MB"
        
        backup_info = f"**备份类型**: 增量备份\n\n**备份时间戳**: {latest_timestamp}\n\n**备份文件**: backup_{latest_timestamp}.tar.gz"
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

