#!/usr/bin/env python3
"""
时间点恢复（Point-in-Time Recovery, PITR）脚本

用法: point_in_time_restore.py <目标时间点> [全量备份时间戳] [增量备份1] [增量备份2] ...

参数:
  目标时间点         要恢复到的目标时间点，格式: YYYY-MM-DD HH:MM:SS
                     例如: 2025-11-26 14:30:00
  
  全量备份时间戳     可选，全量备份的时间戳（格式: YYYYMMDD_HHMMSS）
                     如果不提供，将使用最新的全量备份
  
  增量备份列表       可选，要应用的增量备份文件名列表
                     如果不提供，将自动查找并应用所有相关增量备份

示例:
  # 恢复到指定时间点（自动查找备份）
  point_in_time_restore.py "2025-11-26 14:30:00"
  
  # 恢复到指定时间点，使用指定的全量备份
  point_in_time_restore.py "2025-11-26 14:30:00" 20251126_020000
  
  # 恢复到指定时间点，使用指定的全量备份和增量备份
  point_in_time_restore.py "2025-11-26 14:30:00" 20251126_020000 backup_20251126_030000.tar.gz backup_20251126_040000.tar.gz

注意:
  1. 此脚本需要在 MySQL 停止的情况下运行
  2. 目标时间点必须在全量备份时间之后
  3. 需要确保二进制日志文件可用
  4. 建议先备份现有数据
"""

import os
import sys
import subprocess
import shutil
import tarfile
import re
import tempfile
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

# 配置变量
BACKUP_BASE_DIR = Path(os.environ.get("BACKUP_BASE_DIR", "/backups"))
MYSQL_DATA_DIR = Path(os.environ.get("MYSQL_DATA_DIR", "/var/lib/mysql"))
MYSQL_ROOT_PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD", "rootpassword")
MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
RESTORE_TZ = os.environ.get("RESTORE_TZ", "Asia/Shanghai")
AUTO_STOP_MYSQL = os.environ.get("AUTO_STOP_MYSQL", "true").lower() == "true"
S3_BACKUP_ENABLED = os.environ.get("S3_BACKUP_ENABLED", "false").lower() == "true"
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "mysql-backups")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_USE_SSL = os.environ.get("S3_USE_SSL", "true").lower() == "true"
S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "false").lower() == "true"
S3_ALIAS = os.environ.get("S3_ALIAS", "s3")

# 设置时区
os.environ["TZ"] = RESTORE_TZ

def log(message: str):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def error_exit(message: str):
    """错误处理"""
    log(f"错误: {message}")
    sys.exit(1)

def show_usage():
    """显示使用说明"""
    print(__doc__)
    sys.exit(1)

def validate_datetime(datetime_str: str) -> bool:
    """验证时间格式"""
    try:
        # 尝试解析时间格式 YYYY-MM-DD HH:MM:SS
        datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
        return True
    except ValueError:
        return False

def setup_s3_if_needed() -> bool:
    """配置 S3 客户端（如果需要）"""
    if not S3_BACKUP_ENABLED:
        return False
    
    if not S3_ENDPOINT or not S3_ACCESS_KEY or not S3_SECRET_KEY:
        log("警告: S3 备份已启用但配置不完整，无法从 S3 下载备份")
        return False
    
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
        return True
    except Exception:
        return False

def download_full_backup_from_s3(timestamp: str, target_dir: Path) -> bool:
    """从 S3 下载全量备份"""
    timestamp = timestamp.strip()
    
    if not timestamp:
        log("错误: 时间戳参数为空")
        return False
    
    log(f"从 S3 下载全量备份: backup_{timestamp}.tar.gz")
    target_dir.mkdir(parents=True, exist_ok=True)
    
    backup_file = f"backup_{timestamp}.tar.gz"
    s3_path = f"{S3_ALIAS}/{S3_BUCKET}/full/{backup_file}"
    local_file = target_dir / "backup.tar.gz"
    
    log(f"S3 路径: {s3_path}")
    log(f"本地文件: {local_file}")
    
    try:
        result = subprocess.run(
            ["mc", "cp", s3_path, str(local_file)],
            check=True,
            capture_output=True,
            text=True
        )
        if result.stdout:
            for line in result.stdout.split('\n'):
                if line.strip():
                    log(f"mc: {line}")
        
        if local_file.exists():
            log("下载成功，解压备份文件...")
            with tarfile.open(local_file, "r:gz") as tar:
                tar.extractall(path=target_dir)
            local_file.unlink()
            log(f"备份已解压到: {target_dir}")
            return True
        else:
            log("错误: 下载的文件不存在")
            return False
    except subprocess.CalledProcessError as e:
        log(f"错误: 从 S3 下载备份失败 (退出码: {e.returncode})")
        return False
    except Exception as e:
        log(f"错误: 从 S3 下载备份失败: {e}")
        return False

def download_incremental_backup_from_s3(timestamp: str, target_dir: Path) -> bool:
    """从 S3 下载增量备份"""
    log(f"从 S3 下载增量备份: backup_{timestamp}.tar.gz")
    target_dir.mkdir(parents=True, exist_ok=True)
    
    backup_file = f"backup_{timestamp}.tar.gz"
    s3_path = f"{S3_ALIAS}/{S3_BUCKET}/incremental/{backup_file}"
    local_file = target_dir / "backup.tar.gz"
    
    try:
        result = subprocess.run(
            ["mc", "cp", s3_path, str(local_file)],
            check=True,
            capture_output=True,
            text=True
        )
        if result.stdout:
            for line in result.stdout.split('\n'):
                if line.strip():
                    log(f"mc: {line}")
        
        if local_file.exists():
            log("下载成功，解压备份文件...")
            with tarfile.open(local_file, "r:gz") as tar:
                tar.extractall(path=target_dir)
            local_file.unlink()
            log(f"备份已解压到: {target_dir}")
            return True
        else:
            log("错误: 从 S3 下载备份失败")
            return False
    except Exception as e:
        log(f"错误: 从 S3 下载备份失败: {e}")
        return False

def find_latest_backup_before_target(target_datetime: str) -> Tuple[str, str]:
    """查找目标时间之前的最新备份（全量或增量）

    Args:
        target_datetime: 目标时间点，格式: YYYY-MM-DD HH:MM:SS

    Returns:
        (备份类型, 备份时间戳)，备份类型可以是 "full" 或 "incremental"
    """
    # 转换目标时间为时间戳格式用于比较
    try:
        target_dt = datetime.strptime(target_datetime, "%Y-%m-%d %H:%M:%S")
        target_timestamp = target_dt.strftime("%Y%m%d_%H%M%S")
        log(f"查找目标时间 ({target_datetime}) 之前的最新备份")
    except ValueError:
        log(f"错误: 无法解析目标时间: {target_datetime}")
        return ("full", "")
    
    # 收集所有备份（全量和增量）
    all_backups = []

    # 查找全量备份
    full_dir = BACKUP_BASE_DIR / "full"
    if full_dir.exists():
        for backup_dir in full_dir.iterdir():
            if backup_dir.is_dir():
                dir_name = backup_dir.name
                if re.match(r'^\d{8}_\d{6}$', dir_name) and dir_name <= target_timestamp:
                    all_backups.append(("full", dir_name))

    # 查找增量备份
    inc_dir = BACKUP_BASE_DIR / "incremental"
    if inc_dir.exists():
        for backup_dir in inc_dir.iterdir():
            if backup_dir.is_dir():
                dir_name = backup_dir.name
                if re.match(r'^\d{8}_\d{6}$', dir_name) and dir_name <= target_timestamp:
                    all_backups.append(("incremental", dir_name))

    # 从 S3 查找备份（如果启用了 S3）
    if setup_s3_if_needed():
        # 从 S3 查找全量备份
        try:
            result = subprocess.run(
                ["mc", "ls", f"{S3_ALIAS}/{S3_BUCKET}/full/"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    parts = line.split()
                    if len(parts) >= 6:
                        filename = parts[5]
                        match = re.match(r'^backup_(\d{8}_\d{6})\.tar\.gz$', filename)
                        if match:
                            backup_ts = match.group(1)
                            if backup_ts <= target_timestamp:
                                all_backups.append(("full", backup_ts))
        except Exception:
            pass

        # 从 S3 查找增量备份
        try:
            result = subprocess.run(
                ["mc", "ls", f"{S3_ALIAS}/{S3_BUCKET}/incremental/"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    parts = line.split()
                    if len(parts) >= 6:
                        filename = parts[5]
                        match = re.match(r'^backup_(\d{8}_\d{6})\.tar\.gz$', filename)
                        if match:
                            backup_ts = match.group(1)
                            if backup_ts <= target_timestamp:
                                all_backups.append(("incremental", backup_ts))
        except Exception:
            pass

    # 找到最新的备份
    if all_backups:
        # 按时间戳排序，找到最新的
        all_backups.sort(key=lambda x: x[1], reverse=True)
        backup_type, backup_timestamp = all_backups[0]
        log(f"找到目标时间之前的最新的备份: {backup_type} {backup_timestamp}")
        return (backup_type, backup_timestamp)
    else:
        log("错误: 未找到目标时间之前的任何备份")
        return ("full", "")

def find_incremental_backups(full_backup_timestamp: str, target_datetime: str) -> List[Path]:
    """查找需要应用的增量备份"""
    log("查找需要应用的增量备份...")
    log(f"全量备份时间戳: {full_backup_timestamp}")
    log(f"目标时间点: {target_datetime}")
    
    # 转换目标时间为时间戳
    try:
        target_dt = datetime.strptime(target_datetime, "%Y-%m-%d %H:%M:%S")
        target_timestamp = target_dt.strftime("%Y%m%d_%H%M%S")
    except ValueError:
        error_exit(f"无法解析目标时间: {target_datetime}")
    
    # 查找全量备份之后、目标时间之前的增量备份
    # 注意：只包含目标时间点之前的增量备份（inc_timestamp < target_timestamp）
    # 因为目标时间点的数据应该通过 binlog 恢复
    incremental_backups = []
    incremental_dir = BACKUP_BASE_DIR / "incremental"
    if incremental_dir.exists():
        for inc_dir in incremental_dir.iterdir():
            if inc_dir.is_dir():
                inc_timestamp = inc_dir.name
                # 验证时间戳格式
                if re.match(r'^\d{8}_\d{6}$', inc_timestamp):
                    # 比较时间戳（时间戳格式 YYYYMMDD_HHMMSS 可以直接字符串比较）
                    # 增量备份必须在全量备份之后，且在目标时间之前（不包括目标时间点）
                    if inc_timestamp > full_backup_timestamp and inc_timestamp < target_timestamp:
                        incremental_backups.append(inc_dir)
                        log(f"找到增量备份: {inc_timestamp} (全量备份之后，目标时间之前)")
    
    # 按时间排序（从旧到新）
    incremental_backups.sort()
    
    if incremental_backups:
        log(f"共找到 {len(incremental_backups)} 个增量备份需要应用")
    else:
        log("未找到需要应用的增量备份（全量备份之后、目标时间之前）")
    
    return incremental_backups

def apply_binlog_to_datetime(target_datetime: str, full_backup_timestamp: Optional[str] = None, applied_incremental_backups: Optional[List[str]] = None):
    """应用二进制日志到指定时间点"""
    # 显示当前时间（本地和UTC）
    # 使用明确的时区来获取正确的本地时间和 UTC 时间
    tz_offset = timedelta(hours=8)  # Asia/Shanghai 是 UTC+8
    tz_shanghai = timezone(tz_offset)
    now_aware = datetime.now(timezone.utc)
    now_local = now_aware.astimezone(tz_shanghai).strftime("%Y-%m-%d %H:%M:%S")
    now_utc = now_aware.strftime("%Y-%m-%d %H:%M:%S")
    
    log("========== 开始应用二进制日志到时间点 ==========")
    log(f"当前时间:")
    log(f"  本地时区 ({RESTORE_TZ}): {now_local}")
    log(f"  UTC: {now_utc}")
    log(f"目标时间点: {target_datetime}")
    log(f"当前时区设置: {os.environ.get('TZ', '未设置')} (RESTORE_TZ={RESTORE_TZ})")
    
    # 获取目标时间戳
    # 注意：这里只是用于日志显示，实际的时间解析在后面的代码中进行（使用明确的时区）
    target_timestamp = ""
    if os.environ.get("PITR_TARGET_EPOCH"):
        target_timestamp = os.environ.get("PITR_TARGET_EPOCH")
        log(f"使用外部提供的时间戳: {target_timestamp}")
    else:
        # 如果提供了时间戳环境变量，使用它；否则从字符串解析（使用明确的时区）
        try:
            # 明确指定时区：目标时间是本地时区（Asia/Shanghai，UTC+8）
            tz_offset = timedelta(hours=8)  # Asia/Shanghai 是 UTC+8
            tz_shanghai = timezone(tz_offset)
            target_dt_local = datetime.strptime(target_datetime, "%Y-%m-%d %H:%M:%S")
            target_dt_local = target_dt_local.replace(tzinfo=tz_shanghai)
            target_timestamp = str(int(target_dt_local.timestamp()))
        except Exception:
            target_timestamp = "0"
    
    # 显示目标时间信息（使用明确的时区转换）
    try:
        if target_timestamp and target_timestamp != "0":
            # 从时间戳转换为 UTC，然后转换为本地时区
            target_epoch = int(target_timestamp)
            target_dt_utc = datetime.utcfromtimestamp(target_epoch)
            # 转换为本地时区
            tz_offset = timedelta(hours=8)  # Asia/Shanghai 是 UTC+8
            tz_shanghai = timezone(tz_offset)
            target_dt_local = target_dt_utc.replace(tzinfo=timezone.utc).astimezone(tz_shanghai)
            target_time_human = target_dt_local.strftime("%Y-%m-%d %H:%M:%S")
            target_time_utc = target_dt_utc.strftime("%Y-%m-%d %H:%M:%S")
            log(f"目标时间戳: {target_timestamp}")
            log(f"  本地时区 ({RESTORE_TZ}): {target_time_human}")
            log(f"  UTC: {target_time_utc}")
        else:
            log(f"目标时间戳: {target_timestamp} (无效)")
    except Exception as e:
        log(f"警告: 无法显示目标时间信息: {e}")
    
    # 查找二进制日志文件（只从MySQL数据目录读取，禁止从备份文件夹提取）
    binlog_index = None
    binlog_dir = None
    
    log("查找二进制日志文件（只从MySQL数据目录读取，禁止从备份文件夹提取）...")
    
    # 只从MySQL数据目录查找，禁止从备份文件夹（binlog_backup_*）读取
    binlog_index_file = MYSQL_DATA_DIR / "mysql-bin.index"
    if binlog_index_file.exists():
        log(f"从数据目录找到二进制日志索引: {binlog_index_file}")
        binlog_index = binlog_index_file
        binlog_dir = MYSQL_DATA_DIR
    else:
        # 尝试从原始位置查找
        original_binlog_index = Path("/var/lib/mysql/mysql-bin.index")
        if original_binlog_index.exists():
            log(f"从原始位置找到二进制日志索引: {original_binlog_index}")
            binlog_index = original_binlog_index
            binlog_dir = Path("/var/lib/mysql")
        else:
            log("警告: 未找到二进制日志索引文件")
            log("注意: binlog只从MySQL数据目录读取，不会从备份文件夹提取")
    
    if not binlog_index or not binlog_index.exists():
        log("警告: 未找到二进制日志索引文件")
        log("跳过二进制日志应用")
        return
    
    log(f"找到二进制日志索引: {binlog_index}")
    
    # 读取二进制日志文件列表
    binlog_files = []
    try:
        with open(binlog_index, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # 处理路径：可能是绝对路径或相对路径
                if line.startswith("/"):
                    binlog_file = Path(line)
                else:
                    binlog_file = binlog_dir / line
                
                # 检查文件是否存在
                if binlog_file.exists():
                    binlog_files.append(binlog_file)
                    log(f"找到二进制日志文件: {binlog_file}")
                else:
                    # 尝试只使用文件名
                    filename = Path(line).name
                    binlog_file = binlog_dir / filename
                    if binlog_file.exists():
                        binlog_files.append(binlog_file)
                        log(f"找到二进制日志文件（使用文件名）: {binlog_file}")
                    else:
                        log(f"警告: 二进制日志文件不存在: {binlog_file} (索引中的路径: {line})")
    except Exception as e:
        log(f"错误: 读取二进制日志索引失败: {e}")
        return
    
    if not binlog_files:
        log("警告: 未找到二进制日志文件")
        log("跳过二进制日志应用")
        return
    
    log(f"========== 找到 {len(binlog_files)} 个二进制日志文件 ==========")
    for i, file in enumerate(binlog_files, 1):
        try:
            file_size = file.stat().st_size
            if file_size < 1024 * 1024:
                size_str = f"{file_size / 1024:.2f} KB"
            else:
                size_str = f"{file_size / (1024 * 1024):.2f} MB"
        except Exception:
            size_str = "未知"
        
        log(f"  [{i}] {file.name} ({size_str})")
    
    # 创建 SQL 文件
    temp_sql = BACKUP_BASE_DIR / f"pitr_replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}.sql"
    log("========== 创建二进制日志SQL文件 ==========")
    log(f"SQL文件路径: {temp_sql}")
    
    # 检查 mysqlbinlog 是否可用
    if not shutil.which("mysqlbinlog"):
        log("警告: mysqlbinlog 命令不可用，跳过二进制日志应用")
        log("注意: 数据将恢复到最后一个备份的时间点，而不是目标时间点")
        return
    
    # 获取最后一次备份时间戳（全量或增量，使用较新的那个）
    # 优先使用传入的参数（实际应用的备份），而不是从文件读取（可能包含目标时间之后的备份）
    latest_backup_timestamp = ""
    actual_full_backup_timestamp = full_backup_timestamp
    actual_incremental_backup_timestamp = ""
    
    # 使用传入的全量备份时间戳作为基础
    actual_full_backup_timestamp = full_backup_timestamp
    if actual_full_backup_timestamp:
        log(f"使用传入的全量备份时间戳: {actual_full_backup_timestamp}")

    # 如果传入了已应用的增量备份列表，使用其中最新的（目标时间之前的最后一个）
    actual_incremental_backup_timestamp = None
    if applied_incremental_backups:
        # 从增量备份路径中提取时间戳，找到最新的
        incremental_timestamps = []
        for inc_path in applied_incremental_backups:
            inc_dir = Path(inc_path)
            inc_timestamp = inc_dir.name
            if re.match(r'^\d{8}_\d{6}$', inc_timestamp):
                incremental_timestamps.append(inc_timestamp)

        if incremental_timestamps:
            incremental_timestamps.sort(reverse=True)  # 从新到旧排序
            actual_incremental_backup_timestamp = incremental_timestamps[0]
            log(f"从已应用的增量备份列表中找到最新的增量备份时间戳: {actual_incremental_backup_timestamp}")

    # 如果没有增量备份时间戳，从传入的参数推断
    if not actual_incremental_backup_timestamp and applied_incremental_backups:
        # 如果有增量备份但没有时间戳，说明可能有问题
        log("警告: 传入了增量备份但无法确定时间戳")
    
    # 使用较新的备份时间戳作为起始点（但必须是目标时间之前的）
    if actual_incremental_backup_timestamp and actual_full_backup_timestamp:
        if actual_incremental_backup_timestamp > actual_full_backup_timestamp:
            latest_backup_timestamp = actual_incremental_backup_timestamp
            log(f"检测到增量备份时间戳: {actual_incremental_backup_timestamp}（比全量备份 {actual_full_backup_timestamp} 更新），将从增量备份时间点开始提取二进制日志")
        else:
            latest_backup_timestamp = actual_full_backup_timestamp
            log(f"检测到全量备份时间戳: {actual_full_backup_timestamp}（比增量备份 {actual_incremental_backup_timestamp} 更新），将从全量备份时间点开始提取二进制日志")
    elif actual_incremental_backup_timestamp:
        latest_backup_timestamp = actual_incremental_backup_timestamp
        log(f"检测到增量备份时间戳: {actual_incremental_backup_timestamp}，将从增量备份时间点开始提取二进制日志")
    elif actual_full_backup_timestamp:
        latest_backup_timestamp = actual_full_backup_timestamp
        log(f"检测到全量备份时间戳: {actual_full_backup_timestamp}，将从全量备份时间点开始提取二进制日志")
    
    # 计算并打印详细的时间点信息
    log("========== 时间点信息汇总 ==========")
    
    # 计算最后一次备份的时间点（本地时区和 UTC）
    # 注意：备份时间戳是在容器内生成的，由于容器系统时区是 UTC，所以备份时间戳是 UTC 时间
    latest_backup_datetime_local = None
    latest_backup_datetime_utc = None
    backup_timestamp_epoch = None
    if latest_backup_timestamp:
        try:
            backup_date = f"{latest_backup_timestamp[0:4]}-{latest_backup_timestamp[4:6]}-{latest_backup_timestamp[6:8]}"
            backup_time = f"{latest_backup_timestamp[9:11]}:{latest_backup_timestamp[11:13]}:{latest_backup_timestamp[13:15]}"
            latest_backup_datetime_utc = f"{backup_date} {backup_time}"  # 备份时间戳是 UTC 时间
            
            # 备份时间戳是 UTC 时间，先解析为 UTC
            backup_dt_utc = datetime.strptime(latest_backup_datetime_utc, "%Y-%m-%d %H:%M:%S")
            backup_dt_utc = backup_dt_utc.replace(tzinfo=timezone.utc)
            backup_timestamp_epoch = int(backup_dt_utc.timestamp())
            
            # 转换为本地时区（Asia/Shanghai，UTC+8）
            tz_offset = timedelta(hours=8)  # Asia/Shanghai 是 UTC+8
            tz_shanghai = timezone(tz_offset)
            backup_dt_local = backup_dt_utc.astimezone(tz_shanghai)
            latest_backup_datetime_local = backup_dt_local.strftime("%Y-%m-%d %H:%M:%S")
            
            log(f"最后一次备份时间点:")
            log(f"  UTC (原始备份时间戳): {latest_backup_datetime_utc}")
            log(f"  本地时区 ({RESTORE_TZ}): {latest_backup_datetime_local}")
            log(f"  时间戳: {backup_timestamp_epoch}")
        except Exception as e:
            log(f"警告: 解析备份时间戳失败: {e}")
            import traceback
            log(f"错误详情: {traceback.format_exc()}")
    else:
        log("最后一次备份时间点: 未找到（将从最早的 binlog 开始）")
    
    # 计算目标时间点（本地时区和 UTC）
    # 注意：目标时间也是本地时区（Asia/Shanghai，UTC+8）
    target_datetime_local = target_datetime
    target_datetime_utc = None
    target_timestamp_epoch = None
    try:
        # 明确指定时区：目标时间也是本地时区（Asia/Shanghai，UTC+8）
        tz_offset = timedelta(hours=8)  # Asia/Shanghai 是 UTC+8
        tz_shanghai = timezone(tz_offset)
        target_dt_local = datetime.strptime(target_datetime, "%Y-%m-%d %H:%M:%S")
        target_dt_local = target_dt_local.replace(tzinfo=tz_shanghai)
        target_timestamp_epoch = int(target_dt_local.timestamp())
        target_datetime_utc = datetime.utcfromtimestamp(target_timestamp_epoch).strftime("%Y-%m-%d %H:%M:%S")
        
        log(f"目标恢复时间点:")
        log(f"  本地时区 ({RESTORE_TZ}): {target_datetime_local}")
        log(f"  UTC: {target_datetime_utc}")
        log(f"  时间戳: {target_timestamp_epoch}")
    except Exception as e:
        log(f"警告: 解析目标时间失败: {e}")
        import traceback
        log(f"错误详情: {traceback.format_exc()}")
    
    # 计算提取的时间范围
    start_datetime_utc = latest_backup_datetime_utc if latest_backup_datetime_utc else None
    stop_datetime_utc = None
    if target_timestamp_epoch:
        stop_timestamp = target_timestamp_epoch + 1
        stop_datetime_utc = datetime.utcfromtimestamp(stop_timestamp).strftime("%Y-%m-%d %H:%M:%S")
    
    # 验证时间范围是否有效
    if start_datetime_utc and stop_datetime_utc:
        # 比较时间戳
        start_epoch = datetime.strptime(start_datetime_utc, "%Y-%m-%d %H:%M:%S").timestamp()
        stop_epoch = datetime.strptime(stop_datetime_utc, "%Y-%m-%d %H:%M:%S").timestamp()
        
        if start_epoch >= stop_epoch:
            log("警告: 开始时间大于或等于结束时间，这是不正常的！")
            log(f"  开始时间 (UTC): {start_datetime_utc} (时间戳: {int(start_epoch)})")
            log(f"  结束时间 (UTC): {stop_datetime_utc} (时间戳: {int(stop_epoch)})")
            log(f"  时间差: {int(start_epoch - stop_epoch)} 秒")
            log("")
            log("可能的原因:")
            log("  1. 目标时间点在最后一次备份之前")
            log("  2. 时间戳解析错误")
            log("  3. 时区转换问题")
            log("")

            # 如果目标时间点在最后一次备份时间点之前或相等，不应用 binlog
            # 如果目标时间点在最后一次备份时间点之后，从备份时间点开始应用 binlog
            if target_timestamp_epoch <= backup_timestamp_epoch:
                log(f"目标时间点 ({target_datetime_utc}) 在最后一次备份时间点 ({latest_backup_datetime_utc}) 之前或相等")
                log("不需要应用 binlog，因为备份数据已经是目标时间点的状态")
                log("跳过 binlog 应用步骤")
                return
            else:
                log(f"目标时间点 ({target_datetime_utc}) 在最后一次备份时间点 ({latest_backup_datetime_utc}) 之后")
                log("将从备份时间点开始应用 binlog 到目标时间点")
                # 继续执行 binlog 应用
    
    log(f"预期提取的时间范围:")
    if start_datetime_utc:
        log(f"  开始时间 (UTC): {start_datetime_utc}")
    else:
        log(f"  开始时间 (UTC): 从最早的 binlog 开始")
    if stop_datetime_utc:
        log(f"  结束时间 (UTC): {stop_datetime_utc} (目标时间 + 1秒，确保包含目标时间点的所有操作)")
    else:
        log(f"  结束时间 (UTC): 未设置")
    
    log("=====================================")
    
    if latest_backup_timestamp:
        log(f"将从最后一次备份时间点（{latest_backup_timestamp}）到目标时间点之间的所有 binlog 应用到数据库（包括 DDL 语句）")
    
    # 一次性提取所有二进制日志到目标时间点
    # 使用一次性传入所有binlog文件的方式，更高效
    log("----------------------------------------")
    log("开始一次性提取所有二进制日志文件...")
    log(f"需要处理的文件数: {len(binlog_files)}")
    for i, binlog_file in enumerate(binlog_files, 1):
        log(f"  [{i}] {binlog_file.name} ({binlog_file})")
    
    # 构建 mysqlbinlog 命令参数
    # 注意：mysqlbinlog 的 --start-datetime 和 --stop-datetime 参数期望的是 UTC 时间
    # binlog 文件中存储的时间戳是 UTC，直接使用 UTC 时间匹配
    binlog_cmd_args = ["mysqlbinlog", "--skip-gtids"]

    # 如果 start_datetime_utc 不为 None，从备份时间点开始提取
    # 只有当目标时间点在备份时间之后时才设置开始时间
    if start_datetime_utc is not None:
        binlog_cmd_args.extend(["--start-datetime", start_datetime_utc])
    if start_datetime_utc is not None:
        binlog_cmd_args.extend(["--start-datetime", start_datetime_utc])
        log(f"提取开始时间: {start_datetime_utc} (UTC)")
    else:
        log(f"提取开始时间: 从最早的 binlog 开始（不设置 --start-datetime 参数）")

    # 添加停止时间点（目标时间 + 1秒）
    # 计算目标时间 + 1秒的 UTC 时间
    stop_datetime_utc = None
    if target_timestamp_epoch:
        stop_timestamp = target_timestamp_epoch + 1
        # 将时间戳转换回 UTC 时间字符串
        stop_dt_utc = datetime.utcfromtimestamp(stop_timestamp)
        stop_datetime_utc = stop_dt_utc.strftime("%Y-%m-%d %H:%M:%S")

    if stop_datetime_utc:
        binlog_cmd_args.extend(["--stop-datetime", stop_datetime_utc])
        log(f"提取结束时间: {stop_datetime_utc} (UTC, 目标时间 + 1秒)")
    else:
        log("警告: 无法设置停止时间点，跳过提取")
        return
    
    # 添加所有binlog文件路径到命令参数
    binlog_file_paths = [str(binlog_file) for binlog_file in binlog_files]
    binlog_cmd_args.extend(binlog_file_paths)
    
    # 一次性提取所有binlog文件到SQL文件
    # 直接使用mysqlbinlog的原生输出，不需要转换，可以直接用mysql命令执行
    processed_files = 0
    try:
        # 日志显示实际使用的参数（本地时区时间）
        start_time_log = latest_backup_datetime_local if start_datetime_utc is not None and latest_backup_datetime_local else None
        stop_time_log = stop_datetime_utc if stop_datetime_utc else 'N/A'

        # 构建日志显示的命令字符串
        cmd_parts = ["mysqlbinlog", "--skip-gtids"]
        if start_time_log:
            cmd_parts.extend(["--start-datetime", start_time_log])
        if stop_time_log != 'N/A':
            cmd_parts.extend(["--stop-datetime", stop_time_log])
        cmd_parts.append(f"[共{len(binlog_files)}个文件]")
        log(f"执行命令: {' '.join(cmd_parts)}")
        log("注意: 使用mysqlbinlog原生输出格式，可以直接用mysql命令执行")
        
        # 直接提取到SQL文件（原生格式，不需要转换）
        with open(temp_sql, "w") as output_file:
            result = subprocess.run(
                binlog_cmd_args,
                stdout=output_file,
                stderr=subprocess.PIPE,
                text=True,
                check=False
            )
        
        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else "未知错误"
            log(f"警告: 提取二进制日志失败（退出码: {result.returncode}）")
            if error_msg:
                log(f"错误信息: {error_msg}")
            # 如果失败，尝试逐个文件处理（降级方案）
            log("尝试降级方案：逐个文件处理...")
            processed_files = 0
            for binlog_file in binlog_files:
                try:
                    single_cmd = ["mysqlbinlog", "--skip-gtids"]
                    # 使用本地时区时间，而不是 UTC
                    # 注意：只有当 start_datetime_utc 不为 None 时才添加 --start-datetime 参数
                    if start_datetime_utc is not None and latest_backup_datetime_local:
                        single_cmd.extend(["--start-datetime", latest_backup_datetime_local])
                    if stop_datetime_utc:
                        single_cmd.extend(["--stop-datetime", stop_datetime_utc])
                    single_cmd.append(str(binlog_file))
                    
                    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.sql') as tmp:
                        tmp_path = tmp.name
                    
                    with open(tmp_path, "w") as tmp_file:
                        single_result = subprocess.run(
                            single_cmd,
                            stdout=tmp_file,
                            stderr=subprocess.DEVNULL,
                            check=False
                        )
                    
                    if single_result.returncode == 0:
                        with open(tmp_path, "r") as tmp_file:
                            content = tmp_file.read()
                            if content and len(content.strip()) > 0:
                                with open(temp_sql, "a") as out:
                                    out.write(content)
                                processed_files += 1
                                log(f"✓ 已处理文件: {binlog_file.name}")
                    
                    os.unlink(tmp_path)
                except Exception as e:
                    log(f"警告: 处理文件 {binlog_file.name} 失败: {e}")
            
            if processed_files == 0:
                log("错误: 所有文件处理失败")
                if temp_sql.exists():
                    temp_sql.unlink()
                return
        else:
            processed_files = len(binlog_files)
            log(f"✓ 成功提取所有 {processed_files} 个二进制日志文件")
            
            # 检查输出文件是否有内容
            if temp_sql.exists():
                file_size = temp_sql.stat().st_size
                if file_size > 0:
                    log(f"✓ 二进制日志已提取到: {temp_sql} (大小: {file_size} 字节)")
                else:
                    log("警告: 提取的二进制日志文件为空")
            else:
                log("警告: 提取的二进制日志文件不存在")
    
    except Exception as e:
        log(f"错误: 提取二进制日志时发生异常: {e}")
        import traceback
        log(f"错误详情: {traceback.format_exc()}")
        if temp_sql.exists():
            temp_sql.unlink()
        return
    
    log("----------------------------------------")
    log("========== 二进制日志提取完成 ==========")
    log(f"处理文件数: {processed_files}")
    
    if not temp_sql.exists() or temp_sql.stat().st_size == 0:
        log("未找到需要应用的二进制日志")
        if temp_sql.exists():
            temp_sql.unlink()
        return
    
    try:
        file_size = temp_sql.stat().st_size
        if file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.2f} KB"
        else:
            size_str = f"{file_size / (1024 * 1024):.2f} MB"
    except Exception:
        size_str = "未知"
    
    log(f"二进制日志已提取到: {temp_sql}")
    log(f"文件大小: {size_str}")
    
    # 保存 SQL 文件路径到标记文件
    pitr_marker = BACKUP_BASE_DIR / ".pitr_restore_marker"
    try:
        # 确保目录存在
        pitr_marker.parent.mkdir(parents=True, exist_ok=True)
        log(f"[DEBUG] 准备创建标记文件: {pitr_marker}")
        log(f"[DEBUG] 标记文件父目录: {pitr_marker.parent}")
        log(f"[DEBUG] 父目录是否存在: {pitr_marker.parent.exists()}")
        log(f"[DEBUG] SQL 文件路径: {temp_sql}")
        log(f"[DEBUG] SQL 文件是否存在: {temp_sql.exists()}")
        
        # 写入标记文件（使用绝对路径）
        # 注意：使用 sync() 确保文件系统同步，特别是在 docker-compose run --rm 临时容器中
        with open(pitr_marker, 'w') as f:
            f.write(str(temp_sql))
            f.flush()
            os.fsync(f.fileno())
        
        # 强制同步文件系统（确保在临时容器删除前文件已持久化）
        try:
            os.sync()
        except AttributeError:
            # os.sync() 在某些系统上可能不可用，忽略
            pass
        
        # 验证文件是否创建成功（多次验证，确保文件已持久化）
        import time
        for verify_attempt in range(3):
            if pitr_marker.exists():
                try:
                    marker_content = pitr_marker.read_text().strip()
                    if marker_content:
                        log(f"✓ 已保存 PITR 标记文件: {pitr_marker}")
                        log(f"  标记文件内容: {marker_content}")
                        log(f"  SQL 文件位置: {temp_sql}")
                        log(f"  SQL 文件是否存在: {temp_sql.exists()}")
                        if temp_sql.exists():
                            log(f"  SQL 文件大小: {temp_sql.stat().st_size} 字节")
                        
                        # 验证标记文件内容是否正确
                        if marker_content == str(temp_sql):
                            log(f"✓ 标记文件内容验证通过")
                        else:
                            log(f"⚠ 警告: 标记文件内容不匹配")
                            log(f"  期望: {temp_sql}")
                            log(f"  实际: {marker_content}")
                        break
                except Exception as e:
                    if verify_attempt < 2:
                        log(f"[DEBUG] 验证标记文件失败（尝试 {verify_attempt + 1}/3）: {e}，等待后重试...")
                        time.sleep(0.5)
                        continue
                    else:
                        log(f"✗ 错误: 无法读取标记文件: {e}")
            else:
                if verify_attempt < 2:
                    log(f"[DEBUG] 标记文件不存在（尝试 {verify_attempt + 1}/3），等待后重试...")
                    time.sleep(0.5)
                    continue
                else:
                    log(f"✗ 错误: 标记文件创建失败，文件不存在: {pitr_marker}")
                    log(f"  请检查目录权限: {pitr_marker.parent}")
        log("")
        log("注意: 二进制日志 SQL 已提取，将在 MySQL 启动后自动应用")
        log(f"如果自动应用失败，可以手动执行:")
        log(f"  mysql -h {MYSQL_HOST} -P {MYSQL_PORT} -u root -p{MYSQL_ROOT_PASSWORD} < {temp_sql}")
    except Exception as e:
        log(f"✗ 错误: 保存 PITR 标记文件失败: {e}")
        import traceback
        log(f"错误详情: {traceback.format_exc()}")

def restore_to_point_in_time(target_datetime: str, full_backup_timestamp: Optional[str] = None, incremental_backups: Optional[List[str]] = None):
    """恢复备份到指定时间点"""
    # 显示当前时间（本地和UTC）
    # 使用明确的时区来获取正确的本地时间和 UTC 时间
    tz_offset = timedelta(hours=8)  # Asia/Shanghai 是 UTC+8
    tz_shanghai = timezone(tz_offset)
    now_aware = datetime.now(timezone.utc)
    now_local = now_aware.astimezone(tz_shanghai).strftime("%Y-%m-%d %H:%M:%S")
    now_utc = now_aware.strftime("%Y-%m-%d %H:%M:%S")
    
    log("========== 开始时间点恢复 ==========")
    log(f"当前时间:")
    log(f"  本地时区 ({RESTORE_TZ}): {now_local}")
    log(f"  UTC: {now_utc}")
    log(f"目标时间点: {target_datetime}")
    
    # 如果没有提供全量备份时间戳，查找目标时间之前的最新备份
    backup_type = "full"
    backup_timestamp = full_backup_timestamp
    if not backup_timestamp:
        backup_type, backup_timestamp = find_latest_backup_before_target(target_datetime)
        log(f"使用目标时间之前的最新备份: {backup_type} {backup_timestamp}")

    # 处理不同类型的备份
    if backup_type == "incremental":
        # 如果找到的是增量备份，这已经是目标时间之前的最新状态
        # 我们需要找到这个增量备份对应的全量备份，然后应用这个增量备份
        # 对于增量备份，我们需要找到对应的全量备份作为基础
        # 通常增量备份基于最新的全量备份，这里查找所有全量备份中的最新一个
        full_backups = []
        full_dir = BACKUP_BASE_DIR / "full"
        if full_dir.exists():
            for backup_dir in full_dir.iterdir():
                if backup_dir.is_dir():
                    dir_name = backup_dir.name
                    if re.match(r'^\d{8}_\d{6}$', dir_name):
                        full_backups.append(dir_name)

        if full_backups:
            full_backups.sort(reverse=True)
            full_backup_timestamp = full_backups[0]
            log(f"为增量备份找到对应的全量备份基础: {full_backup_timestamp}")
        else:
            error_exit("未找到全量备份，无法应用增量备份")
        incremental_backups = [str(BACKUP_BASE_DIR / "incremental" / backup_timestamp)]  # 只应用这一个增量备份
        log(f"使用增量备份 {backup_timestamp} 进行恢复（基于全量备份 {full_backup_timestamp}）")
    else:
        # 全量备份
        full_backup_timestamp = backup_timestamp
        incremental_backups = []  # 查找需要应用的所有增量备份

    # 验证全量备份存在
    full_backup_dir = BACKUP_BASE_DIR / "full" / full_backup_timestamp
    
    # 检查备份目录是否存在，如果不存在或只有压缩文件，尝试从S3下载
    if not full_backup_dir.exists() or not (full_backup_dir / "xtrabackup_checkpoints").exists():
        # 检查是否有压缩文件
        if (full_backup_dir / "backup.tar.gz").exists():
            log("发现压缩的备份文件，开始解压...")
            with tarfile.open(full_backup_dir / "backup.tar.gz", "r:gz") as tar:
                tar.extractall(path=full_backup_dir)
            (full_backup_dir / "backup.tar.gz").unlink()
            log("备份文件已解压")
        elif setup_s3_if_needed():
            log("本地备份不存在或不完整，尝试从 S3 下载...")
            if download_full_backup_from_s3(full_backup_timestamp, full_backup_dir):
                log("从 S3 下载并解压成功")
            else:
                error_exit(f"无法从 S3 下载备份，且本地备份不存在: {full_backup_dir}")
        else:
            error_exit(f"全量备份目录不存在或不完整: {full_backup_dir}")
    
    log(f"全量备份目录: {full_backup_dir}")
    
    # ========== 第三步之前：保存binlog文件到临时目录 ==========
    log("========== 保存binlog文件到临时目录（在备份准备之前）==========")
    binlog_temp_dir = BACKUP_BASE_DIR / f"binlog_temp_pitr_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    binlog_temp_dir.mkdir(parents=True, exist_ok=True)
    
    binlog_index_file = MYSQL_DATA_DIR / "mysql-bin.index"
    binlog_files_saved = []
    
    if binlog_index_file.exists():
        log(f"找到二进制日志索引文件: {binlog_index_file}")
        log(f"临时保存目录: {binlog_temp_dir}")

        # 读取并显示索引文件内容，用于调试
        try:
            with open(binlog_index_file, "r") as f:
                index_content = f.read().strip()
                log(f"binlog索引文件内容: {index_content.replace(chr(10), ' | ')}")
        except Exception as e:
            log(f"警告: 读取binlog索引文件内容失败: {e}")

        # 复制binlog索引文件
        try:
            shutil.copy2(binlog_index_file, binlog_temp_dir / "mysql-bin.index")
            log(f"已保存binlog索引文件: mysql-bin.index")
        except Exception as e:
            log(f"警告: 保存binlog索引文件失败: {e}")
        
        # 读取并复制所有binlog文件
        try:
            with open(binlog_index_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    log(f"处理binlog条目: {line}")

                    # 处理路径
                    if line.startswith("/"):
                        binlog_file = Path(line)
                        log(f"使用绝对路径: {binlog_file}")
                    else:
                        binlog_file = MYSQL_DATA_DIR / line.lstrip("./")
                        log(f"使用相对路径: {binlog_file} (基于 {MYSQL_DATA_DIR})")

                    # 如果文件不存在，尝试只使用文件名
                    if not binlog_file.exists():
                        filename = Path(line).name
                        binlog_file_alt = MYSQL_DATA_DIR / filename
                        log(f"主路径不存在，尝试备选路径: {binlog_file_alt}")
                        if binlog_file_alt.exists():
                            binlog_file = binlog_file_alt
                            log(f"使用备选路径成功: {binlog_file}")
                        else:
                            log(f"文件不存在，跳过: {line}")
                            continue

                    if binlog_file.exists() and binlog_file.name.startswith("mysql-bin."):
                        try:
                            file_size = binlog_file.stat().st_size
                            if file_size < 1024 * 1024:
                                size_str = f"{file_size / 1024:.2f} KB"
                            else:
                                size_str = f"{file_size / (1024 * 1024):.2f} MB"
                            log(f"保存binlog文件: {binlog_file.name} ({size_str})")
                            shutil.copy2(binlog_file, binlog_temp_dir / binlog_file.name)
                            binlog_files_saved.append(binlog_file.name)
                        except Exception as e:
                            log(f"警告: 保存binlog文件失败: {binlog_file.name}: {e}")
                    elif not binlog_file.exists():
                        log(f"警告: binlog文件不存在: {binlog_file} (索引中的路径: {line})")
        except Exception as e:
            log(f"警告: 读取binlog索引失败: {e}")
        
        if binlog_files_saved:
            log(f"已保存 {len(binlog_files_saved)} 个binlog文件到临时目录: {binlog_temp_dir}")
        else:
            log("警告: 未找到任何binlog文件需要保存")
    else:
        log(f"警告: 未找到二进制日志索引文件: {binlog_index_file}")
        log("注意: 将跳过binlog文件保存，恢复后可能无法进行时间点恢复")
    
    # 如果没有提供增量备份，自动查找
    if not incremental_backups:
        log("自动查找需要应用的增量备份...")
        found_backups = find_incremental_backups(full_backup_timestamp, target_datetime)
        incremental_backups = [str(b) for b in found_backups]
        if incremental_backups:
            log(f"找到 {len(incremental_backups)} 个增量备份需要应用")
        else:
            log("未找到需要应用的增量备份")
    else:
        log(f"使用指定的增量备份: {incremental_backups}")
    
    # 准备全量备份
    log("准备全量备份...")
    os.chdir(full_backup_dir)
    
    # 检查备份文件状态
    if not (full_backup_dir / "xtrabackup_checkpoints").exists():
        # 如果备份是压缩的，先解压
        if (full_backup_dir / "backup.tar.gz").exists():
            log("解压全量备份...")
            with tarfile.open(full_backup_dir / "backup.tar.gz", "r:gz") as tar:
                tar.extractall(path=full_backup_dir)
            (full_backup_dir / "backup.tar.gz").unlink()
        
        # 解压 XtraBackup 文件
        zst_files = list(full_backup_dir.glob("*.zst"))
        if zst_files:
            log("解压 XtraBackup 压缩文件...")
            subprocess.run(
                ["xtrabackup", "--decompress", f"--target-dir={full_backup_dir}"],
                check=False,
                capture_output=True
            )
        
        # 再次检查
        if not (full_backup_dir / "xtrabackup_checkpoints").exists():
            error_exit("备份文件不完整，找不到 xtrabackup_checkpoints 文件")
    else:
        log("备份文件已就绪，跳过解压步骤")
    
    # 准备全量备份（使用 --apply-log-only，因为后面还要应用增量备份）
    log("准备全量备份（--apply-log-only，准备应用增量备份）...")
    subprocess.run(
        ["xtrabackup", "--prepare", "--apply-log-only", f"--target-dir={full_backup_dir}"],
        check=False,
        capture_output=True
    )
    
    # 应用增量备份
    for inc_backup_path in incremental_backups:
        inc_backup_dir = Path(inc_backup_path)
        inc_timestamp = inc_backup_dir.name
        
        # 检查增量备份是否存在，如果不存在或只有压缩文件，尝试从S3下载
        if not inc_backup_dir.exists() or not (inc_backup_dir / "xtrabackup_checkpoints").exists():
            if (inc_backup_dir / "backup.tar.gz").exists():
                log("发现压缩的增量备份文件，开始解压...")
                inc_backup_dir.mkdir(parents=True, exist_ok=True)
                with tarfile.open(inc_backup_dir / "backup.tar.gz", "r:gz") as tar:
                    tar.extractall(path=inc_backup_dir)
                (inc_backup_dir / "backup.tar.gz").unlink()
            elif setup_s3_if_needed():
                log(f"本地增量备份不存在或不完整，尝试从 S3 下载: {inc_timestamp}")
                if download_incremental_backup_from_s3(inc_timestamp, inc_backup_dir):
                    log("从 S3 下载并解压成功")
                else:
                    log(f"警告: 无法从 S3 下载增量备份: {inc_timestamp}，跳过此增量备份")
                    continue
            else:
                log(f"警告: 增量备份不存在或不完整: {inc_backup_dir}，跳过")
                continue
        
        if inc_backup_dir.exists():
            log(f"应用增量备份: {inc_timestamp}")
            os.chdir(inc_backup_dir)
            
            # 检查备份文件状态
            if not (inc_backup_dir / "xtrabackup_checkpoints").exists():
                # 如果备份是压缩的，先解压
                if (inc_backup_dir / "backup.tar.gz").exists():
                    log("解压增量备份...")
                    with tarfile.open(inc_backup_dir / "backup.tar.gz", "r:gz") as tar:
                        tar.extractall(path=inc_backup_dir)
                    (inc_backup_dir / "backup.tar.gz").unlink()
                
                # 解压 XtraBackup 文件
                zst_files = list(inc_backup_dir.glob("*.zst"))
                if zst_files:
                    log("解压增量备份压缩文件...")
                    subprocess.run(
                        ["xtrabackup", "--decompress", f"--target-dir={inc_backup_dir}"],
                        check=False,
                        capture_output=True
                    )
                
                # 再次检查
                if not (inc_backup_dir / "xtrabackup_checkpoints").exists():
                    log(f"警告: 增量备份文件不完整，跳过: {inc_backup_dir}")
                    continue
            
            # 准备增量备份（合并到全量备份，使用 --apply-log-only，因为后面还要应用二进制日志）
            log("合并增量备份到全量备份（--apply-log-only，准备应用二进制日志）...")
            subprocess.run(
                ["xtrabackup", "--prepare", "--apply-log-only", f"--target-dir={full_backup_dir}", f"--incremental-dir={inc_backup_dir}"],
                check=False,
                capture_output=True
            )
            
            os.chdir(full_backup_dir)
    
    # 最终准备（不使用 --apply-log-only，完成所有日志应用）
    log("最终准备备份（完成所有日志应用）...")
    os.chdir(full_backup_dir)
    result = subprocess.run(
        ["xtrabackup", "--prepare", f"--target-dir={full_backup_dir}"],
        check=False,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        error_msg = result.stderr.strip() if result.stderr else "未知错误"
        log(f"最终准备备份失败（退出码: {result.returncode}）: {error_msg}")
        if result.stdout:
            log(f"xtrabackup 输出: {result.stdout}")
        error_exit("备份准备失败，无法继续恢复")
    else:
        log("备份最终准备完成")
    
    # 注意：binlog文件会由apply_restore.py自动保存和恢复，不需要手动保存到备份目录
    # binlog文件只从mysql_data目录读取，禁止从备份文件夹提取
    log("========== 检查二进制日志文件 ==========")
    binlog_index_file = MYSQL_DATA_DIR / "mysql-bin.index"
    if binlog_index_file.exists():
        log(f"找到二进制日志索引文件: {binlog_index_file}")
        log("注意: binlog文件将从mysql_data目录读取，不会从备份文件夹提取")
    else:
        log(f"警告: 未找到二进制日志索引文件: {binlog_index_file}")
        log("注意: binlog文件将从mysql_data目录读取（恢复后会自动恢复）")
    
    # 应用恢复到数据目录
    log("========== 应用备份到数据目录 ==========")
    if MYSQL_DATA_DIR.exists() and any(MYSQL_DATA_DIR.iterdir()):
        log("警告: 数据目录不为空，将清空现有数据")
        log("备份现有数据（不包括二进制日志，已单独保存）...")
        backup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        existing_backup = BACKUP_BASE_DIR / f"existing_data_backup_{backup_timestamp}"
        existing_backup.mkdir(parents=True, exist_ok=True)
        
        # 排除二进制日志文件（已单独保存）
        for item in MYSQL_DATA_DIR.iterdir():
            if not item.name.startswith("mysql-bin."):
                try:
                    if item.is_file():
                        shutil.copy2(item, existing_backup / item.name)
                    elif item.is_dir():
                        shutil.copytree(item, existing_backup / item.name, dirs_exist_ok=True)
                except Exception as e:
                    log(f"警告: 备份文件失败: {item}: {e}")
        
        log(f"现有数据已备份到: {existing_backup}")
        
        # 清空数据目录（binlog文件已在前面保存到临时目录，xtrabackup要求目录完全为空）
        log("清空数据目录（binlog文件已保存到临时目录，xtrabackup要求目录完全为空）...")
        for item in MYSQL_DATA_DIR.iterdir():
            try:
                # 完全清空目录，包括binlog文件（已在前面保存）
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                log(f"警告: 删除文件失败: {item}: {e}")
        log("数据目录已完全清空（binlog文件已保存到临时目录）")
    
    # 使用 xtrabackup 恢复
    log("执行 xtrabackup --copy-back...")
    result = subprocess.run(
        ["xtrabackup", "--copy-back", f"--target-dir={full_backup_dir}", f"--datadir={MYSQL_DATA_DIR}"],
        check=False,
        capture_output=True,
        text=True
    )
    if result.stdout:
        for line in result.stdout.split('\n'):
            if line.strip():
                log(f"xtrabackup: {line}")
    
    # 修复权限
    log("修复数据目录权限...")
    try:
        subprocess.run(["chown", "-R", "mysql:mysql", str(MYSQL_DATA_DIR)], check=False, capture_output=True)
        subprocess.run(["chmod", "700", str(MYSQL_DATA_DIR)], check=False, capture_output=True)
    except Exception as e:
        log(f"警告: 无法修复权限，可能需要手动修复: {e}")
    
    # 检查并处理从备份中恢复的binlog文件（如果备份中包含binlog，会被xtrabackup恢复，需要删除）
    log("检查从备份中恢复的binlog文件...")
    restored_binlog_files = list(MYSQL_DATA_DIR.glob("mysql-bin.*"))
    if restored_binlog_files:
        log(f"发现 {len(restored_binlog_files)} 个从备份恢复的binlog文件，这些是备份时的旧binlog，需要删除")
        for binlog_file in restored_binlog_files:
            try:
                log(f"删除从备份恢复的旧binlog文件: {binlog_file.name}")
                binlog_file.unlink()
            except Exception as e:
                log(f"警告: 删除binlog文件失败: {binlog_file}: {e}")
        
        # 删除从备份恢复的binlog索引文件（如果有）
        restored_binlog_index = MYSQL_DATA_DIR / "mysql-bin.index"
        if restored_binlog_index.exists():
            try:
                log("删除从备份恢复的旧binlog索引文件")
                restored_binlog_index.unlink()
            except Exception as e:
                log(f"警告: 删除binlog索引文件失败: {e}")
    
    # ========== 第五步之后：恢复binlog文件到mysql_data目录 ==========
    log("========== 恢复binlog文件到mysql_data目录（在应用备份之后）==========")
    if binlog_temp_dir.exists() and (binlog_temp_dir / "mysql-bin.index").exists():
        log(f"从临时目录恢复binlog文件: {binlog_temp_dir}")
        try:
            # 恢复binlog索引文件
            saved_binlog_index = binlog_temp_dir / "mysql-bin.index"
            if saved_binlog_index.exists():
                shutil.copy2(saved_binlog_index, MYSQL_DATA_DIR / "mysql-bin.index")
                log("已恢复binlog索引文件: mysql-bin.index")
            
            # 恢复binlog文件
            binlog_files_to_restore = list(binlog_temp_dir.glob("mysql-bin.[0-9]*"))
            if binlog_files_to_restore:
                log(f"恢复 {len(binlog_files_to_restore)} 个binlog文件...")
                for binlog_file in binlog_files_to_restore:
                    try:
                        target_file = MYSQL_DATA_DIR / binlog_file.name
                        shutil.copy2(binlog_file, target_file)
                        log(f"已恢复binlog文件: {binlog_file.name}")
                    except Exception as e:
                        log(f"警告: 恢复binlog文件失败: {binlog_file.name}: {e}")
                log("binlog文件恢复完成")
            else:
                log("警告: 临时目录中未找到binlog文件")
            
            # 清理临时目录
            try:
                shutil.rmtree(binlog_temp_dir)
                log(f"已清理临时binlog目录: {binlog_temp_dir}")
            except Exception as e:
                log(f"警告: 清理临时binlog目录失败: {e}")
        except Exception as e:
            log(f"警告: 恢复binlog文件时出错: {e}")
            import traceback
            log(f"错误详情: {traceback.format_exc()}")
    else:
        log("注意: 临时binlog目录不存在或无效，跳过binlog文件恢复")
    
    log("备份恢复完成")
    
    # 准备传递给 apply_binlog_to_datetime 的增量备份列表
    # 只包含已应用的增量备份（目标时间之前的）
    applied_inc_backups = []
    for inc_backup_path in incremental_backups:
        inc_backup_dir = Path(inc_backup_path)
        if inc_backup_dir.exists() and (inc_backup_dir / "xtrabackup_checkpoints").exists():
            applied_inc_backups.append(str(inc_backup_dir))
    
    # 应用二进制日志到指定时间点
    # 传入全量备份时间戳和已应用的增量备份列表，确保使用正确的起始时间点
    log("========== 应用二进制日志到时间点 ==========")
    apply_binlog_to_datetime(target_datetime, full_backup_timestamp, applied_inc_backups if applied_inc_backups else None)
    
    # 显示当前时间（本地和UTC）
    # 使用明确的时区来获取正确的本地时间和 UTC 时间
    tz_offset = timedelta(hours=8)  # Asia/Shanghai 是 UTC+8
    tz_shanghai = timezone(tz_offset)
    now_aware = datetime.now(timezone.utc)
    now_local = now_aware.astimezone(tz_shanghai).strftime("%Y-%m-%d %H:%M:%S")
    now_utc = now_aware.strftime("%Y-%m-%d %H:%M:%S")
    
    log("========== 时间点恢复完成 ==========")
    log(f"完成时间:")
    log(f"  本地时区 ({RESTORE_TZ}): {now_local}")
    log(f"  UTC: {now_utc}")
    log(f"恢复目录: {full_backup_dir}")
    log(f"目标时间点: {target_datetime}")
    log("")
    log("下一步:")
    log("  1. 检查数据目录权限是否正确")
    log("  2. 启动 MySQL 服务")
    log("  3. 验证数据是否恢复到目标时间点")

def stop_mysql_if_running():
    """检查并停止 MySQL 进程（如果需要）"""
    # 检查 MySQL 进程是否在运行
    try:
        result = subprocess.run(
            ["pgrep", "-f", "mysqld"],
            capture_output=True,
            check=False
        )
        mysql_running = result.returncode == 0
    except Exception:
        mysql_running = False
    
    # 检查 socket 文件
    socket_file = Path("/var/run/mysqld/mysqld.sock")
    if not mysql_running and socket_file.exists():
        mysql_running = True
    
    if mysql_running:
        log("警告: MySQL 进程正在运行")
        log("时间点恢复需要在 MySQL 停止的情况下进行")
        
        # 检查是否在交互式环境中
        is_interactive = sys.stdin.isatty()
        if is_interactive and not AUTO_STOP_MYSQL:
            # 交互式环境且未设置自动停止，询问用户
            log("是否要停止 MySQL 进程？(y/n，默认: y)")
            try:
                import select
                if select.select([sys.stdin], [], [], 10)[0]:
                    answer = sys.stdin.readline().strip()
                else:
                    answer = "y"
            except Exception:
                answer = "y"
            
            if answer.lower() not in ("y", "yes", ""):
                error_exit("请先停止 MySQL 进程")
        else:
            # 非交互式环境或设置了自动停止，直接停止
            log("自动停止 MySQL 进程...")
        
        log("停止 MySQL 进程...")
        # 尝试优雅停止
        if shutil.which("mysqladmin"):
            try:
                subprocess.run(
                    ["mysqladmin", "shutdown", "-u", "root", f"-p{MYSQL_ROOT_PASSWORD}"],
                    check=False,
                    capture_output=True
                )
            except Exception:
                pass
        
        # 等待进程退出
        wait_count = 0
        while wait_count < 10:
            try:
                result = subprocess.run(
                    ["pgrep", "-f", "mysqld"],
                    capture_output=True,
                    check=False
                )
                if result.returncode != 0:
                    break
            except Exception:
                break
            import time
            time.sleep(1)
            wait_count += 1
        
        # 如果仍在运行，强制停止
        try:
            result = subprocess.run(
                ["pgrep", "-f", "mysqld"],
                capture_output=True,
                check=False
            )
            if result.returncode == 0:
                log("警告: MySQL 进程仍在运行，强制停止...")
                subprocess.run(["pkill", "-9", "-f", "mysqld"], check=False, capture_output=True)
                import time
                time.sleep(2)
        except Exception:
            pass
        
        log("MySQL 进程已停止")
    else:
        log("MySQL 进程未运行，可以继续执行恢复")

def main():
    """主函数"""
    if len(sys.argv) < 2:
        show_usage()
    
    target_datetime = sys.argv[1]
    args = sys.argv[2:]
    
    # 验证时间格式
    if not validate_datetime(target_datetime):
        error_exit(f"无效的时间格式: {target_datetime}，请使用格式: YYYY-MM-DD HH:MM:SS")
    
    # 检查并停止 MySQL 进程
    stop_mysql_if_running()
    
    # 解析参数
    full_backup_timestamp = None
    incremental_backups = []
    
    if args:
        # 第一个参数可能是全量备份时间戳
        first_arg = args[0]
        if re.match(r'^\d{8}_\d{6}$', first_arg):
            full_backup_timestamp = first_arg
            incremental_backups = args[1:]
        else:
            # 否则都是增量备份
            incremental_backups = args
    
    # 执行恢复
    restore_to_point_in_time(target_datetime, full_backup_timestamp, incremental_backups if incremental_backups else None)

if __name__ == "__main__":
    main()

