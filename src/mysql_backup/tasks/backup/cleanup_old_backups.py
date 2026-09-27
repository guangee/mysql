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
import json
from datetime import datetime, timedelta
from pathlib import Path

from mysql_backup.core.backup_storage import (
    cleanup_local_orphan_backups_on_s3,
    get_active_storages,
    is_backup_expired_by_retention,
    setup_storage,
    setup_s3 as setup_s3_storage,
    _storage_alias,
)

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
BACKUP_POLICY_FILE = Path(os.environ.get("BACKUP_POLICY_FILE", "/shared/backup_policy.json"))
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
    if not setup_s3_storage(log):
        sys.exit(1)

def _remove_empty_backup_dirs(root: Path) -> int:
    """没有备份内容的时间戳目录直接删掉，避免空目录一直占着。"""
    if not root.is_dir():
        return 0
    removed = 0
    for backup_dir in list(root.iterdir()):
        if not backup_dir.is_dir():
            continue
        payload = [item for item in backup_dir.iterdir() if item.name != ".delete_after"]
        if payload:
            continue
        shutil.rmtree(backup_dir, ignore_errors=True)
        if not backup_dir.exists():
            log(f"删除空的本地备份目录: {backup_dir}")
            removed += 1
    return removed


def cleanup_local_expired_backups():
    """清理本地过期的备份文件"""
    log("开始清理本地过期备份文件...")
    
    current_time = datetime.now().timestamp()
    cleaned_count = 0
    cleaned_count += _remove_empty_backup_dirs(BACKUP_BASE_DIR / "full")
    cleaned_count += _remove_empty_backup_dirs(BACKUP_BASE_DIR / "incremental")
    
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

    # 清理已上传至 S3 且校验一致的孤儿本地备份
    if S3_BACKUP_ENABLED and setup_s3_storage(log):
        orphan_cleaned = cleanup_local_orphan_backups_on_s3(BACKUP_BASE_DIR, log)
        if orphan_cleaned > 0:
            log(f"已清理 {orphan_cleaned} 个 S3 已确认的本地孤儿备份")

def load_retention_days() -> tuple[int, int]:
    """读取全量/增量保留天数，优先 shared/backup_policy.json"""
    default = BACKUP_RETENTION_DAYS
    full_days = int(os.environ.get("FULL_BACKUP_RETENTION_DAYS", str(default)))
    incremental_days = int(os.environ.get("INCREMENTAL_BACKUP_RETENTION_DAYS", str(default)))
    if BACKUP_POLICY_FILE.exists():
        try:
            data = json.loads(BACKUP_POLICY_FILE.read_text(encoding="utf-8"))
            full_days = int(data.get("full_retention_days", full_days))
            incremental_days = int(data.get("incremental_retention_days", incremental_days))
        except Exception:
            pass
    return full_days, incremental_days


def _cleanup_s3_by_retention(storage: dict, subdir: str, retention_days: int) -> tuple[int, int]:
    """按文件名时间戳 + 保留天数清理对象存储（与控制台过期判定一致）"""
    name = storage.get("name") or _storage_alias(storage)
    alias = _storage_alias(storage)
    bucket = storage.get("bucket", S3_BUCKET)
    mc_prefix = f"{alias}/{bucket}/{subdir}/"
    tz = backup_timezone()
    now = datetime.now(tz)
    scanned = 0
    deleted = 0

    result = subprocess.run(
        ["mc", "find", mc_prefix, "--name", "backup_*.tar.gz"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        log(f"[存储:{name}] 列举 {subdir} 备份失败: {err[:200]}")
        return scanned, deleted

    for line in result.stdout.splitlines():
        path = line.strip()
        if not path:
            continue
        scanned += 1
        filename = path.rsplit("/", 1)[-1]
        if not is_backup_expired_by_retention(filename, retention_days, now):
            continue
        rm = subprocess.run(["mc", "rm", path], capture_output=True, text=True, check=False)
        if rm.returncode == 0:
            deleted += 1
            log(f"[存储:{name}] 已删除过期 {subdir} 备份: {filename}")
        else:
            err = (rm.stderr or rm.stdout or "").strip()
            log(f"[存储:{name}] 删除失败 {filename}: {err[:120]}")

    log(f"[存储:{name}] {subdir} 扫描 {scanned} 个，删除 {deleted} 个过期备份（保留 {retention_days} 天）")
    return scanned, deleted


def cleanup_s3_old_backups():
    """清理各对象存储上过期的全量/增量备份"""
    full_days, incremental_days = load_retention_days()
    log(f"开始清理对象存储：全量保留 {full_days} 天、增量保留 {incremental_days} 天（按备份文件名时间判定）...")

    storages = get_active_storages()
    if not storages:
        if not S3_BACKUP_ENABLED:
            log("S3 备份已禁用，跳过 S3 清理")
            return
        setup_s3()
        storages = get_active_storages()

    if not storages:
        log("未配置对象存储，跳过 S3 清理")
        return

    total_scanned = 0
    total_deleted = 0
    for storage in storages:
        name = storage.get("name") or _storage_alias(storage)
        if not setup_storage(storage, log):
            log(f"[存储:{name}] 客户端配置失败，跳过清理")
            continue

        scanned, deleted = _cleanup_s3_by_retention(storage, "full", full_days)
        total_scanned += scanned
        total_deleted += deleted
        scanned, deleted = _cleanup_s3_by_retention(storage, "incremental", incremental_days)
        total_scanned += scanned
        total_deleted += deleted

    log(f"对象存储清理完成：共扫描 {total_scanned} 个，删除 {total_deleted} 个过期备份")

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

def main(local_only: bool = False, s3_only: bool = False):
    """主函数"""
    if local_only or "--local-only" in sys.argv:
        cleanup_local_expired_backups()
    elif s3_only or "--s3-only" in sys.argv:
        if S3_BACKUP_ENABLED or get_active_storages():
            cleanup_s3_old_backups()
        else:
            log("S3 备份已禁用，跳过 S3 清理")
    else:
        cleanup_old_backups()

if __name__ == "__main__":
    main()

