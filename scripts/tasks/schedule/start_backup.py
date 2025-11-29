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
    
    # 清除现有的备份相关 cron 任务
    new_crontab_lines = []
    for line in existing_crontab.split('\n'):
        if 'full-backup' not in line and 'incremental-backup' not in line and 'cleanup-old-backups' not in line:
            if line.strip():
                new_crontab_lines.append(line)
    
    # 添加全量备份任务
    new_crontab_lines.append(f"{FULL_BACKUP_SCHEDULE} /scripts/tasks/backup/full_backup.py >> {BACKUP_BASE_DIR}/backup.log 2>&1")
    
    # 添加增量备份任务
    new_crontab_lines.append(f"{INCREMENTAL_BACKUP_SCHEDULE} /scripts/tasks/backup/incremental_backup.py >> {BACKUP_BASE_DIR}/backup.log 2>&1")
    
    # 添加本地过期备份清理任务（每小时执行一次，只清理本地）
    new_crontab_lines.append(f"0 * * * * /scripts/tasks/backup/cleanup_old_backups.py --local-only >> {BACKUP_BASE_DIR}/backup.log 2>&1")
    
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
    log(f"  增量备份: {INCREMENTAL_BACKUP_SCHEDULE}")
    log("  本地过期备份清理: 每小时执行一次")
    
    # 显示 cron 任务
    log("当前 cron 任务:")
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'full_backup' in line or 'incremental_backup' in line:
                    log(f"  {line}")
    except Exception:
        pass
    
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
        
        # 每10分钟输出一次心跳日志（避免日志过多）
        if heartbeat_count % 10 == 0:
            log(f"备份调度服务运行中... (已运行 {heartbeat_count // 10} 分钟)")
        
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

