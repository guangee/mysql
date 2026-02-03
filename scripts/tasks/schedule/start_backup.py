#!/usr/bin/env python3
"""
启动备份调度服务

配置并启动定时备份任务
"""

import os
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path

# 配置变量
FULL_BACKUP_SCHEDULE = os.environ.get("FULL_BACKUP_SCHEDULE", "0 2 * * 0")
INCREMENTAL_BACKUP_SCHEDULE = os.environ.get("INCREMENTAL_BACKUP_SCHEDULE", "0 3 * * *")
BACKUP_BASE_DIR = Path(os.environ.get("BACKUP_BASE_DIR", "/backups"))

# 需要注入到 crontab 的环境变量（cron 默认环境极少，备份脚本依赖这些变量）
CRON_ENV_VARS = [
    "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET", "S3_BACKUP_ENABLED",
    "S3_REGION", "S3_USE_SSL", "S3_FORCE_PATH_STYLE", "S3_ALIAS",
    "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD",
    "MYSQL_BACKUP_USER", "MYSQL_BACKUP_PASSWORD",
    "BACKUP_BASE_DIR", "LOCAL_BACKUP_RETENTION_HOURS", "BACKUP_RETENTION_DAYS",
    "TZ",
]

def log(message: str):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}"
    print(log_message)
    
    # 同时写入日志文件
    try:
        log_file = BACKUP_BASE_DIR / "backup.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_message + "\n")
    except Exception:
        pass  # 忽略日志写入错误

def main():
    """主函数"""
    # 创建必要的目录
    (BACKUP_BASE_DIR / "full").mkdir(parents=True, exist_ok=True)
    (BACKUP_BASE_DIR / "incremental").mkdir(parents=True, exist_ok=True)
    
    # 配置 cron
    log("配置备份计划任务...")
    
    # 读取现有的 crontab
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            check=False
        )
        existing_crontab = result.stdout if result.returncode == 0 else ""
    except Exception:
        existing_crontab = ""
    
    # 清除现有的备份相关 cron 任务（按脚本路径匹配：full_backup / incremental_backup / cleanup_old_backups）
    new_crontab_lines = []
    for line in existing_crontab.split('\n'):
        if 'full_backup' not in line and 'incremental_backup' not in line and 'cleanup_old_backups' not in line:
            if line.strip():
                new_crontab_lines.append(line)
    
    # 写入 env 文件，cron 执行时通过 source 加载（crontab 内 VAR=value 在某些环境下不生效）
    env_file = BACKUP_BASE_DIR / "backup.env"
    env_count = 0
    try:
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("# 备份任务环境变量，由 start_backup 生成，cron 执行前 source 此文件\n")
            for var in CRON_ENV_VARS:
                val = os.environ.get(var)
                if val is not None and str(val).strip() != "":
                    # 单引号包裹，值内单引号改为 '\''
                    safe = str(val).replace("\\", "\\\\").replace("'", "'\"'\"'").replace("\n", " ").strip()
                    f.write(f"export {var}='{safe}'\n")
                    env_count += 1
        log(f"[调度] 已写入 {env_count} 个环境变量到 {env_file}，cron 将通过 source 加载")
    except Exception as e:
        log(f"警告: 写入 {env_file} 失败: {e}")
    
    # cron 任务使用 . backup.env && script 确保脚本能拿到环境变量
    source_cmd = f". {env_file} &&"
    
    # 添加全量备份任务（执行前 source env）
    new_crontab_lines.append(f"{FULL_BACKUP_SCHEDULE} {source_cmd} /scripts/tasks/backup/full_backup.py >> {BACKUP_BASE_DIR}/backup.log 2>&1")
    
    # 添加增量备份任务
    new_crontab_lines.append(f"{INCREMENTAL_BACKUP_SCHEDULE} {source_cmd} /scripts/tasks/backup/incremental_backup.py >> {BACKUP_BASE_DIR}/backup.log 2>&1")
    
    # 添加本地过期备份清理任务（每小时执行一次，只清理本地）
    new_crontab_lines.append(f"0 * * * * {source_cmd} /scripts/tasks/backup/cleanup_old_backups.py --local-only >> {BACKUP_BASE_DIR}/backup.log 2>&1")
    
    # 写入新的 crontab
    new_crontab = '\n'.join(new_crontab_lines) + '\n'
    try:
        process = subprocess.Popen(
            ["crontab", "-"],
            stdin=subprocess.PIPE,
            text=True
        )
        process.communicate(input=new_crontab)
        if process.returncode != 0:
            log(f"警告: 配置 crontab 失败，退出码: {process.returncode}")
    except Exception as e:
        log(f"警告: 配置 crontab 失败: {e}")
    
    log("备份计划任务已配置:")
    log(f"  全量备份: {FULL_BACKUP_SCHEDULE}")
    log(f"  增量备份: {INCREMENTAL_BACKUP_SCHEDULE} （每分钟执行，便于排查）")
    log("  本地过期备份清理: 每小时执行一次")
    
    # 输出完整 crontab 便于排查（敏感值已存在 env 中，此处仅确认任务行）
    log("当前 crontab 中的备份任务行:")
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if 'full_backup' in line or 'incremental_backup' in line or 'cleanup_old_backups' in line:
                    log(f"  [cron] {line}")
            log(f"  [cron] 以上任务通过 source {env_file} 加载环境变量（S3_ENDPOINT 等）")
        else:
            log(f"  [cron] 读取 crontab 失败 returncode={result.returncode} stderr={result.stderr}")
    except Exception as e:
        log(f"  [cron] 读取 crontab 异常: {e}")
    
    # 启动 cron 服务
    log("启动 cron 服务...")
    try:
        subprocess.run(
            ["service", "cron", "start"],
            check=False,
            capture_output=True
        )
    except Exception:
        # 如果 service 命令不可用，尝试直接启动 cron
        try:
            subprocess.Popen(
                ["/usr/sbin/cron"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            log("警告: 无法启动 cron 服务")
    
    # 执行一次全量备份（如果还没有基础备份）
    if not (BACKUP_BASE_DIR / "LATEST_FULL_BACKUP").exists():
        log("未找到基础备份，执行首次全量备份...")
        try:
            subprocess.run(
                ["/scripts/tasks/backup/full_backup.py"],
                check=False
            )
        except Exception as e:
            log(f"警告: 首次全量备份失败: {e}")
    
    # 备份服务已在后台运行
    log("备份调度服务已启动，等待计划任务执行...")
    log(f"查看日志: tail -f {BACKUP_BASE_DIR}/backup.log")
    log(f"下次全量备份: {FULL_BACKUP_SCHEDULE}")
    log(f"下次增量备份: {INCREMENTAL_BACKUP_SCHEDULE}")
    
    # 保持脚本运行（但不阻塞 MySQL 主进程）
    # 使用无限循环等待，但定期检查 MySQL 进程
    heartbeat_count = 0
    while True:
        time.sleep(60)
        heartbeat_count += 1
        # 每分钟输出心跳，便于确认调度进程存活及下次增量时间
        log(f"[心跳] 备份调度运行中，已运行 {heartbeat_count} 分钟 | 增量备份: 每 1 分钟执行，请查看 backup.log 确认是否被 cron 触发")
        
        # 检查 MySQL 进程是否还在运行
        try:
            result = subprocess.run(
                ["pgrep", "-x", "mysqld"],
                capture_output=True,
                check=False
            )
            if result.returncode != 0:
                log("检测到 MySQL 进程已停止，备份服务将退出")
                break
        except Exception:
            pass

if __name__ == "__main__":
    main()

