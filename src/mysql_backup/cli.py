#!/usr/bin/env python3
"""
MySQL 备份恢复工具 - 统一 CLI

用法:
    python3 -m mysql_backup <category> <command> [options]
    mysql-backup <category> <command> [options]
"""

from __future__ import annotations

import argparse
import sys

from mysql_backup.core.logger import Colors

CLI = "python3 -m mysql_backup"


def show_help() -> None:
    """显示详细的使用帮助"""
    bold, cyan, green, yellow, nc = "\033[1m", "\033[0;36m", "\033[0;32m", "\033[1;33m", "\033[0m"

    print(f"""
{bold}{cyan}╔══════════════════════════════════════════════════════════════════════════════╗{nc}
{bold}{cyan}║                    MySQL 备份恢复工具 - 使用帮助                            ║{nc}
{bold}{cyan}╚══════════════════════════════════════════════════════════════════════════════╝{nc}

{bold}基本用法:{nc}
    {CLI} <category> <command> [options]
    mysql-backup <category> <command> [options]

{bold}{green}备份命令 (backup):{nc}
    {yellow}backup full{nc}
        示例: {CLI} backup full

    {yellow}backup incremental{nc}
        示例: {CLI} backup incremental

    {yellow}backup cleanup [--local-only|--s3-only]{nc}
        示例: {CLI} backup cleanup
        示例: {CLI} backup cleanup --local-only
        示例: {CLI} backup cleanup --s3-only

{bold}{green}恢复命令 (restore):{nc}
    {yellow}restore backup{nc}
        示例: {CLI} restore backup

    {yellow}restore apply [restore_dir]{nc}
        示例: {CLI} restore apply
        示例: {CLI} restore apply /backups/restore/20251128_120000

    {yellow}restore pitr <target_time> [full_backup] [incremental_backups...]{nc}
        示例: {CLI} restore pitr "2025-11-28 14:30:00"
        示例: {CLI} restore pitr "2025-11-28 14:30:00" 20251128_120000

{bold}{green}Binlog 命令 (binlog):{nc}
    {yellow}binlog to-sql / to-insert / apply-generic / apply-universal / apply-pitr{nc}
        示例: {CLI} binlog apply-pitr

{bold}{green}通知命令 (notify):{nc}
    {yellow}notify dingtalk <status> [message]{nc}
        示例: {CLI} notify dingtalk success "备份完成"

{bold}{green}调度命令 (schedule):{nc}
    {yellow}schedule start{nc}
        示例: {CLI} schedule start

    {yellow}schedule update{nc}
        示例: {CLI} schedule update

{bold}{yellow}注意事项:{nc}
  • 时间点恢复时间格式: YYYY-MM-DD HH:MM:SS（本地时区，默认 Asia/Shanghai）
  • 备份时间戳格式: YYYYMMDD_HHMMSS
  • 集成测试请在宿主机运行: python tests/test.py

{bold}{cyan}更多信息:{nc}
  查看日志: /backups/backup.log
""")


def main(argv: list[str] | None = None) -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(
        prog="mysql-backup",
        description="MySQL 备份恢复工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="category", help="命令类别")

    backup_parser = subparsers.add_parser("backup", help="备份相关命令")
    backup_sub = backup_parser.add_subparsers(dest="command", help="备份命令")
    backup_sub.add_parser("full", help="执行全量备份")
    backup_sub.add_parser("incremental", help="执行增量备份")
    cleanup_parser = backup_sub.add_parser("cleanup", help="清理过期备份")
    cleanup_parser.add_argument(
        "--local-only",
        action="store_true",
        help="只清理本地备份，不清理 S3",
    )
    cleanup_parser.add_argument(
        "--s3-only",
        action="store_true",
        help="只清理对象存储备份，不清理本地",
    )

    restore_parser = subparsers.add_parser("restore", help="恢复相关命令")
    restore_sub = restore_parser.add_subparsers(dest="command", help="恢复命令")
    restore_sub.add_parser("backup", help="恢复备份")
    apply_parser = restore_sub.add_parser("apply", help="应用恢复")
    apply_parser.add_argument("restore_dir", nargs="?", help="恢复目录路径")
    pitr_parser = restore_sub.add_parser("pitr", help="时间点恢复")
    pitr_parser.add_argument("target_time", help="目标时间点 (YYYY-MM-DD HH:MM:SS)")
    pitr_parser.add_argument("full_backup", nargs="?", help="全量备份时间戳")
    pitr_parser.add_argument("incremental_backups", nargs="*", help="增量备份列表")

    binlog_parser = subparsers.add_parser("binlog", help="binlog 相关命令")
    binlog_sub = binlog_parser.add_subparsers(dest="command", help="binlog 命令")
    binlog_sub.add_parser("to-sql", help="转换 binlog 为 SQL")
    binlog_sub.add_parser("to-insert", help="转换 binlog 为 INSERT 语句")
    binlog_sub.add_parser("apply-generic", help="应用 binlog（通用）")
    binlog_sub.add_parser("apply-universal", help="应用 binlog（自动检测表结构）")
    binlog_sub.add_parser("apply-pitr", help="应用 PITR binlog")

    notify_parser = subparsers.add_parser("notify", help="通知相关命令")
    notify_sub = notify_parser.add_subparsers(dest="command", help="通知命令")
    dingtalk_parser = notify_sub.add_parser("dingtalk", help="发送钉钉通知")
    dingtalk_parser.add_argument("status", choices=["success", "failure"], help="状态")
    dingtalk_parser.add_argument("message", nargs="?", default="", help="消息内容")

    schedule_parser = subparsers.add_parser("schedule", help="调度相关命令")
    schedule_sub = schedule_parser.add_subparsers(dest="command", help="调度命令")
    schedule_sub.add_parser("start", help="启动备份调度服务")
    schedule_sub.add_parser("update", help="按策略重写备份 crontab 后退出")

    subparsers.add_parser("help", help="显示详细的使用帮助")

    args = parser.parse_args(argv)

    if args.category == "help" or (len(sys.argv) > 1 and sys.argv[1] == "help"):
        show_help()
        return

    if not args.category:
        parser.print_help()
        sys.exit(1)

    try:
        if args.category == "backup":
            if args.command == "full":
                from mysql_backup.tasks.backup.full_backup import main as backup_main
                backup_main()
            elif args.command == "incremental":
                from mysql_backup.tasks.backup.incremental_backup import main as incremental_main
                incremental_main()
            elif args.command == "cleanup":
                from mysql_backup.tasks.backup.cleanup_old_backups import main as cleanup_main
                cleanup_main(
                    local_only=getattr(args, "local_only", False),
                    s3_only=getattr(args, "s3_only", False),
                )
            else:
                backup_parser.print_help()
                sys.exit(1)

        elif args.category == "restore":
            if args.command == "backup":
                from mysql_backup.tasks.restore.restore_backup import main as restore_main
                restore_main()
            elif args.command == "apply":
                from mysql_backup.tasks.restore.apply_restore import main as apply_main
                if args.restore_dir:
                    sys.argv = ["apply_restore.py", args.restore_dir]
                apply_main()
            elif args.command == "pitr":
                from mysql_backup.tasks.restore.point_in_time_restore import main as pitr_main
                pitr_args = [args.target_time]
                if args.full_backup:
                    pitr_args.append(args.full_backup)
                pitr_args.extend(args.incremental_backups or [])
                sys.argv = ["point_in_time_restore.py"] + pitr_args
                pitr_main()
            else:
                restore_parser.print_help()
                sys.exit(1)

        elif args.category == "binlog":
            if args.command == "to-sql":
                from mysql_backup.tasks.binlog.convert_binlog_to_sql import main as to_sql_main
                to_sql_main()
            elif args.command == "to-insert":
                from mysql_backup.tasks.binlog.convert_binlog_to_insert import main as to_insert_main
                to_insert_main()
            elif args.command == "apply-generic":
                from mysql_backup.tasks.binlog.apply_binlog_generic import main as apply_generic_main
                apply_generic_main()
            elif args.command == "apply-universal":
                from mysql_backup.tasks.binlog.apply_binlog_universal import main as apply_universal_main
                apply_universal_main()
            elif args.command == "apply-pitr":
                from mysql_backup.tasks.binlog.apply_pitr_binlog import main as apply_pitr_main
                apply_pitr_main()
            else:
                binlog_parser.print_help()
                sys.exit(1)

        elif args.category == "notify":
            if args.command == "dingtalk":
                from mysql_backup.tasks.notify.dingtalk_notify import main as dingtalk_main
                sys.argv = ["dingtalk_notify.py", args.status, args.message]
                dingtalk_main()
            else:
                notify_parser.print_help()
                sys.exit(1)

        elif args.category == "schedule":
            if args.command == "start":
                from mysql_backup.tasks.schedule.start_backup import main as schedule_main
                schedule_main()
            elif args.command == "update":
                from mysql_backup.tasks.schedule.update_crontab import main as update_main
                update_main()
            else:
                schedule_parser.print_help()
                sys.exit(1)

        else:
            parser.print_help()
            sys.exit(1)

    except ImportError as e:
        print(f"{Colors.RED}错误: 无法导入模块: {e}{Colors.NC}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"{Colors.RED}错误: {e}{Colors.NC}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
