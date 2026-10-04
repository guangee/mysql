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

from mysql_backup.tasks.schedule.crontab_config import apply_backup_crontab, get_backup_schedules

# 配置变量
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

    apply_backup_crontab(log)
    backup_schedules = get_backup_schedules()
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
                ["python3 -m mysql_backup backup full"],
                check=False
            )
        except Exception as e:
            log(f"警告: 首次全量备份失败: {e}")
    
    # 备份服务已在后台运行
    log("备份调度服务已启动，等待计划任务执行...")
    log(f"查看日志: tail -f {BACKUP_BASE_DIR}/backup.log")
    if backup_schedules["full_backup_enabled"]:
        log(f"全量备份计划: {backup_schedules['full_backup_schedule']}")
    else:
        log("全量备份计划: 已禁用")
    if backup_schedules["incremental_backup_enabled"]:
        log(f"增量备份计划: {backup_schedules['incremental_backup_schedule']}")
    else:
        log("增量备份计划: 已禁用")
    
    # 保持脚本运行（但不阻塞 MySQL 主进程）
    # 使用无限循环等待，但定期检查 MySQL 进程
    heartbeat_count = 0
    while True:
        time.sleep(60)
        heartbeat_count += 1
        # 每分钟输出心跳，便于确认调度进程存活及下次增量时间
        log(f"[心跳] 备份调度运行中，已运行 {heartbeat_count} 分钟 | 增量: {backup_schedules['incremental_backup_schedule'] if backup_schedules['incremental_backup_enabled'] else '已禁用'}")
        
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

