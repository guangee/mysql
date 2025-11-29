#!/usr/bin/env python3
"""
清理过期备份脚本

清理本地和S3上的过期备份文件
支持参数: --local-only (只清理本地备份)
"""

import os
import sys
import subprocess
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

# 配置变量
S3_BACKUP_ENABLED = os.environ.get("S3_BACKUP_ENABLED", "true").lower() == "true"
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "mysql-backups")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_USE_SSL = os.environ.get("S3_USE_SSL", "true").lower() == "true"
S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "false").lower() == "true"
S3_ALIAS = os.environ.get("S3_ALIAS", "s3")
BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))
BACKUP_BASE_DIR = Path(os.environ.get("BACKUP_BASE_DIR", "/backups"))

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
    
    log(f"S3 配置完成 (Endpoint: {S3_ENDPOINT}, Bucket: {S3_BUCKET})")

def cleanup_local_expired_backups():
    """清理本地过期的备份文件"""
    log("开始清理本地过期备份文件...")
    
    current_time = datetime.now().timestamp()
    cleaned_count = 0
    
    # 清理全量备份目录中的过期备份
    full_backup_dir = BACKUP_BASE_DIR / "full"
    if full_backup_dir.exists():
        for backup_dir in full_backup_dir.iterdir():
            if backup_dir.is_dir():
                delete_after_file = backup_dir / ".delete_after"
                if delete_after_file.exists():
                    try:
                        with open(delete_after_file, "r") as f:
                            delete_time = float(f.read().strip() or "0")
                        
                        if delete_time > 0 and current_time >= delete_time:
                            log(f"删除过期本地备份: {backup_dir}")
                            shutil.rmtree(backup_dir, ignore_errors=True)
                            cleaned_count += 1
                    except Exception:
                        pass  # 忽略错误
    
    # 清理增量备份目录中的过期备份
    incremental_backup_dir = BACKUP_BASE_DIR / "incremental"
    if incremental_backup_dir.exists():
        for backup_dir in incremental_backup_dir.iterdir():
            if backup_dir.is_dir():
                delete_after_file = backup_dir / ".delete_after"
                if delete_after_file.exists():
                    try:
                        with open(delete_after_file, "r") as f:
                            delete_time = float(f.read().strip() or "0")
                        
                        if delete_time > 0 and current_time >= delete_time:
                            log(f"删除过期本地备份: {backup_dir}")
                            shutil.rmtree(backup_dir, ignore_errors=True)
                            cleaned_count += 1
                    except Exception:
                        pass  # 忽略错误
    
    # 清理 PITR 恢复过程中生成的临时文件
    pitr_sql_retention_days = int(os.environ.get("PITR_SQL_RETENTION_DAYS", "7"))
    pitr_sql_keep_count = int(os.environ.get("PITR_SQL_KEEP_COUNT", "3"))
    
    if BACKUP_BASE_DIR.exists():
        # 1. 删除超过保留天数的 PITR SQL 文件
        pitr_sql_deleted = 0
        cutoff_time = datetime.now() - timedelta(days=pitr_sql_retention_days)
        
        for sql_file in BACKUP_BASE_DIR.glob("pitr_replay_*.sql"):
            try:
                if datetime.fromtimestamp(sql_file.stat().st_mtime) < cutoff_time:
                    sql_file.unlink()
                    pitr_sql_deleted += 1
            except Exception:
                pass
        
        if pitr_sql_deleted > 0:
            log(f"删除 {pitr_sql_deleted} 个超过 {pitr_sql_retention_days} 天的 PITR SQL 文件")
            cleaned_count += pitr_sql_deleted
        
        # 只保留最近 N 个 PITR SQL 文件，删除其他的
        pitr_sql_files = sorted(
            BACKUP_BASE_DIR.glob("pitr_replay_*.sql"),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        
        if len(pitr_sql_files) > pitr_sql_keep_count:
            excess_files = pitr_sql_files[pitr_sql_keep_count:]
            excess_count = len(excess_files)
            for sql_file in excess_files:
                try:
                    sql_file.unlink()
                except Exception:
                    pass
            
            if excess_count > 0:
                log(f"删除 {excess_count} 个多余的 PITR SQL 文件（保留最近 {pitr_sql_keep_count} 个）")
                cleaned_count += excess_count
        
        # 2. 清理旧的 binlog_backup 目录
        binlog_backup_retention_days = int(os.environ.get("BINLOG_BACKUP_RETENTION_DAYS", "7"))
        binlog_backup_keep_count = int(os.environ.get("BINLOG_BACKUP_KEEP_COUNT", "3"))
        
        cutoff_time = datetime.now() - timedelta(days=binlog_backup_retention_days)
        binlog_backup_deleted = 0
        
        for binlog_dir in BACKUP_BASE_DIR.glob("binlog_backup_*"):
            if binlog_dir.is_dir():
                try:
                    if datetime.fromtimestamp(binlog_dir.stat().st_mtime) < cutoff_time:
                        shutil.rmtree(binlog_dir, ignore_errors=True)
                        binlog_backup_deleted += 1
                except Exception:
                    pass
        
        if binlog_backup_deleted > 0:
            log(f"删除 {binlog_backup_deleted} 个超过 {binlog_backup_retention_days} 天的 binlog_backup 目录")
            cleaned_count += binlog_backup_deleted
        
        # 只保留最近 N 个 binlog_backup 目录
        binlog_backup_dirs = sorted(
            [d for d in BACKUP_BASE_DIR.glob("binlog_backup_*") if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
            reverse=True
        )
        
        if len(binlog_backup_dirs) > binlog_backup_keep_count:
            excess_dirs = binlog_backup_dirs[binlog_backup_keep_count:]
            excess_count = len(excess_dirs)
            for binlog_dir in excess_dirs:
                try:
                    shutil.rmtree(binlog_dir, ignore_errors=True)
                except Exception:
                    pass
            
            if excess_count > 0:
                log(f"删除 {excess_count} 个多余的 binlog_backup 目录（保留最近 {binlog_backup_keep_count} 个）")
                cleaned_count += excess_count
        
        # 3. 清理旧的 .pitr_restore_marker 文件
        marker_file = BACKUP_BASE_DIR / ".pitr_restore_marker"
        if marker_file.exists():
            try:
                with open(marker_file, "r") as f:
                    marker_sql_file = f.read().strip()
                
                if marker_sql_file and not Path(marker_sql_file).exists():
                    log("删除无效的 PITR 标记文件（对应的 SQL 文件不存在）")
                    marker_file.unlink()
                    cleaned_count += 1
            except Exception:
                pass
    
    if cleaned_count > 0:
        log(f"已清理 {cleaned_count} 个过期本地备份和临时文件")
    else:
        log("没有需要清理的过期本地备份")

def cleanup_s3_old_backups():
    """清理 S3 上的旧备份"""
    log(f"开始清理 S3 上 {BACKUP_RETENTION_DAYS} 天前的备份...")
    
    setup_s3()
    
    # 清理全量备份
    log("清理 S3 全量备份...")
    try:
        subprocess.run(
            ["mc", "find", f"{S3_ALIAS}/{S3_BUCKET}/full/", "--name", "backup_*.tar.gz",
             "--older-than", f"{BACKUP_RETENTION_DAYS}d", "--exec", "mc rm {}"],
            check=False,
            capture_output=True
        )
    except Exception:
        pass
    
    # 清理增量备份
    log("清理 S3 增量备份...")
    try:
        subprocess.run(
            ["mc", "find", f"{S3_ALIAS}/{S3_BUCKET}/incremental/", "--name", "backup_*.tar.gz",
             "--older-than", f"{BACKUP_RETENTION_DAYS}d", "--exec", "mc rm {}"],
            check=False,
            capture_output=True
        )
    except Exception:
        pass
    
    log("S3 清理完成")

def cleanup_old_backups():
    """清理旧备份（本地和 S3）"""
    # 清理本地过期备份
    cleanup_local_expired_backups()
    
    # 如果启用了 S3 备份，清理 S3 上的旧备份
    if S3_BACKUP_ENABLED:
        cleanup_s3_old_backups()
    else:
        log("S3 备份已禁用，跳过 S3 清理")
    
    log("备份清理完成")

def main():
    """主函数"""
    # 如果指定了参数 --local-only，只清理本地备份
    if len(sys.argv) > 1 and sys.argv[1] == "--local-only":
        cleanup_local_expired_backups()
    else:
        # 默认清理本地和 S3
        cleanup_old_backups()

if __name__ == "__main__":
    main()

