#!/usr/bin/env python3
"""
更新 MySQL 容器内备份相关 crontab（仅写 crontab 后退出，不阻塞）。
"""

import sys
from pathlib import Path


from mysql_backup.tasks.schedule.crontab_config import apply_backup_crontab, log_default  # noqa: E402


def main():
    ok = apply_backup_crontab(log_default)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
