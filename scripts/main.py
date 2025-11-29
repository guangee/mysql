#!/usr/bin/env python3
"""
MySQL 备份恢复工具 - 统一入口

用法:
    python main.py <command> [options]

命令:
    backup
        full             执行全量备份
        incremental      执行增量备份
        cleanup          清理过期备份
    
    restore
        backup           恢复备份
        apply            应用恢复
        pitr             时间点恢复
    
    binlog
        to-sql           转换 binlog 为 SQL
        to-insert        转换 binlog 为 INSERT 语句
        apply-generic    应用 binlog（通用）
        apply-universal  应用 binlog（通用，自动检测表结构）
        apply-pitr       应用 PITR binlog
    
    notify
        dingtalk         发送钉钉通知
    
    schedule
        start            启动备份调度服务
    
    test
        full-flow        完整流程测试
        pitr             时间点恢复测试
        pitr-between     两次增量备份之间的 PITR 测试
"""

import sys
import argparse
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from core.logger import Logger, Colors

def show_help():
    """显示详细的使用帮助"""
    # 定义颜色代码
    BOLD = '\033[1m'
    CYAN = '\033[0;36m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    NC = '\033[0m'  # No Color
    
    help_text = f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════════════════════╗{NC}
{BOLD}{CYAN}║                    MySQL 备份恢复工具 - 使用帮助                            ║{NC}
{BOLD}{CYAN}╚══════════════════════════════════════════════════════════════════════════════╝{NC}

{BOLD}基本用法:{NC}
    python3 /scripts/main.py <category> <command> [options]

{BOLD}{GREEN}📦 备份命令 (backup):{NC}
    {YELLOW}backup full{NC}
        执行全量备份
        示例: python3 /scripts/main.py backup full

    {YELLOW}backup incremental{NC}
        执行增量备份（基于最新的全量备份）
        示例: python3 /scripts/main.py backup incremental

    {YELLOW}backup cleanup{NC}
        清理过期备份
        示例: python3 /scripts/main.py backup cleanup

{BOLD}{GREEN}🔄 恢复命令 (restore):{NC}
    {YELLOW}restore backup{NC}
        恢复备份（交互式选择备份）
        示例: python3 /scripts/main.py restore backup

    {YELLOW}restore apply [restore_dir]{NC}
        应用恢复（将备份应用到数据目录）
        参数:
            restore_dir  - 恢复目录路径（可选，默认: /backups/restore）
        示例: python3 /scripts/main.py restore apply
        示例: python3 /scripts/main.py restore apply /backups/restore/20251128_120000

    {YELLOW}restore pitr <target_time> [full_backup] [incremental_backups...]{NC}
        时间点恢复（Point-in-Time Recovery）
        参数:
            target_time          - 目标时间点 (格式: YYYY-MM-DD HH:MM:SS，使用本地时区)
            full_backup          - 全量备份时间戳 (可选，格式: YYYYMMDD_HHMMSS)
            incremental_backups  - 增量备份列表 (可选)
        示例: python3 /scripts/main.py restore pitr "2025-11-28 14:30:00"
        示例: python3 /scripts/main.py restore pitr "2025-11-28 14:30:00" 20251128_120000
        示例: python3 /scripts/main.py restore pitr "2025-11-28 14:30:00" 20251128_120000 20251128_130000

{BOLD}{GREEN}📋 Binlog 命令 (binlog):{NC}
    {YELLOW}binlog to-sql{NC}
        转换 binlog 为 SQL 文件
        示例: python3 /scripts/main.py binlog to-sql

    {YELLOW}binlog to-insert{NC}
        转换 binlog 为 INSERT 语句
        示例: python3 /scripts/main.py binlog to-insert

    {YELLOW}binlog apply-generic{NC}
        应用 binlog（通用方法）
        示例: python3 /scripts/main.py binlog apply-generic

    {YELLOW}binlog apply-universal{NC}
        应用 binlog（通用，自动检测表结构）
        示例: python3 /scripts/main.py binlog apply-universal

    {YELLOW}binlog apply-pitr{NC}
        应用 PITR binlog（时间点恢复专用）
        示例: python3 /scripts/main.py binlog apply-pitr

{BOLD}{GREEN}🔔 通知命令 (notify):{NC}
    {YELLOW}notify dingtalk <status> [message]{NC}
        发送钉钉通知
        参数:
            status   - 状态: success 或 failure
            message  - 消息内容（可选）
        示例: python3 /scripts/main.py notify dingtalk success "备份完成"
        示例: python3 /scripts/main.py notify dingtalk failure "备份失败"

{BOLD}{GREEN}⏰ 调度命令 (schedule):{NC}
    {YELLOW}schedule start{NC}
        启动备份调度服务（自动定时备份）
        示例: python3 /scripts/main.py schedule start

{BOLD}{GREEN}🧪 测试命令 (test):{NC}
    {YELLOW}test full-flow{NC}
        完整流程测试（全量备份 -> 增量备份 -> 恢复）
        示例: python3 /scripts/main.py test full-flow

    {YELLOW}test pitr{NC}
        时间点恢复测试
        示例: python3 /scripts/main.py test pitr

    {YELLOW}test pitr-between{NC}
        两次增量备份之间的 PITR 测试
        示例: python3 /scripts/main.py test pitr-between

{BOLD}{GREEN}💡 常用场景示例:{NC}

{CYAN}1. 执行全量备份:{NC}
   python3 /scripts/main.py backup full

{CYAN}2. 执行增量备份:{NC}
   python3 /scripts/main.py backup incremental

{CYAN}3. 恢复到指定时间点:{NC}
   python3 /scripts/main.py restore pitr "2025-11-28 14:30:00"

{CYAN}4. 查看所有命令:{NC}
   python3 /scripts/main.py --help

{CYAN}5. 查看特定命令的帮助:{NC}
   python3 /scripts/main.py restore --help
   python3 /scripts/main.py backup --help

{BOLD}{YELLOW}⚠️  注意事项:{NC}
  • 时间点恢复的时间格式: YYYY-MM-DD HH:MM:SS（使用本地时区，默认 Asia/Shanghai）
  • 备份时间戳格式: YYYYMMDD_HHMMSS
  • 执行恢复前请确保 MySQL 服务已停止
  • 建议在执行重要操作前先备份数据

{BOLD}{CYAN}📚 更多信息:{NC}
  查看详细文档: /scripts/README.md
  查看日志文件: /backups/backup.log
"""
    print(help_text)

def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="MySQL 备份恢复工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    subparsers = parser.add_subparsers(dest='category', help='命令类别')
    
    # backup 命令
    backup_parser = subparsers.add_parser('backup', help='备份相关命令')
    backup_subparsers = backup_parser.add_subparsers(dest='command', help='备份命令')
    
    backup_subparsers.add_parser('full', help='执行全量备份')
    backup_subparsers.add_parser('incremental', help='执行增量备份')
    backup_subparsers.add_parser('cleanup', help='清理过期备份')
    
    # restore 命令
    restore_parser = subparsers.add_parser('restore', help='恢复相关命令')
    restore_subparsers = restore_parser.add_subparsers(dest='command', help='恢复命令')
    
    restore_subparsers.add_parser('backup', help='恢复备份')
    apply_parser = restore_subparsers.add_parser('apply', help='应用恢复')
    apply_parser.add_argument('restore_dir', nargs='?', help='恢复目录路径（可选，默认: /backups/restore）')
    pitr_parser = restore_subparsers.add_parser('pitr', help='时间点恢复')
    pitr_parser.add_argument('target_time', help='目标时间点 (YYYY-MM-DD HH:MM:SS)')
    pitr_parser.add_argument('full_backup', nargs='?', help='全量备份时间戳 (可选)')
    pitr_parser.add_argument('incremental_backups', nargs='*', help='增量备份列表 (可选)')
    
    # binlog 命令
    binlog_parser = subparsers.add_parser('binlog', help='binlog 相关命令')
    binlog_subparsers = binlog_parser.add_subparsers(dest='command', help='binlog 命令')
    
    binlog_subparsers.add_parser('to-sql', help='转换 binlog 为 SQL')
    binlog_subparsers.add_parser('to-insert', help='转换 binlog 为 INSERT 语句')
    binlog_subparsers.add_parser('apply-generic', help='应用 binlog（通用）')
    binlog_subparsers.add_parser('apply-universal', help='应用 binlog（通用，自动检测表结构）')
    binlog_subparsers.add_parser('apply-pitr', help='应用 PITR binlog')
    
    # notify 命令
    notify_parser = subparsers.add_parser('notify', help='通知相关命令')
    notify_subparsers = notify_parser.add_subparsers(dest='command', help='通知命令')
    
    dingtalk_parser = notify_subparsers.add_parser('dingtalk', help='发送钉钉通知')
    dingtalk_parser.add_argument('status', choices=['success', 'failure'], help='状态')
    dingtalk_parser.add_argument('message', nargs='?', default='', help='消息内容')
    
    # schedule 命令
    schedule_parser = subparsers.add_parser('schedule', help='调度相关命令')
    schedule_subparsers = schedule_parser.add_subparsers(dest='command', help='调度命令')
    
    schedule_subparsers.add_parser('start', help='启动备份调度服务')
    
    # test 命令
    test_parser = subparsers.add_parser('test', help='测试相关命令')
    test_subparsers = test_parser.add_subparsers(dest='command', help='测试命令')
    
    test_subparsers.add_parser('full-flow', help='完整流程测试')
    test_subparsers.add_parser('pitr', help='时间点恢复测试')
    test_subparsers.add_parser('pitr-between', help='两次增量备份之间的 PITR 测试')
    
    # help 命令
    help_parser = subparsers.add_parser('help', help='显示详细的使用帮助')
    
    args = parser.parse_args()
    
    # 处理 help 命令
    if args.category == 'help' or (not args.category and len(sys.argv) > 1 and sys.argv[1] == 'help'):
        show_help()
        sys.exit(0)
    
    if not args.category:
        parser.print_help()
        sys.exit(1)
    
    # 根据命令执行相应的模块
    try:
        if args.category == 'backup':
            if args.command == 'full':
                from tasks.backup.full_backup import main as backup_main
                backup_main()
            elif args.command == 'incremental':
                from tasks.backup.incremental_backup import main as incremental_main
                incremental_main()
            elif args.command == 'cleanup':
                from tasks.backup.cleanup_old_backups import main as cleanup_main
                cleanup_main()
            else:
                backup_parser.print_help()
        
        elif args.category == 'restore':
            if args.command == 'backup':
                from tasks.restore.restore_backup import main as restore_main
                restore_main()
            elif args.command == 'apply':
                from tasks.restore.apply_restore import main as apply_main
                # 如果提供了恢复目录参数，需要设置 sys.argv
                if args.restore_dir:
                    sys.argv = ['apply_restore.py', args.restore_dir]
                apply_main()
            elif args.command == 'pitr':
                from tasks.restore.point_in_time_restore import main as pitr_main
                # 构建参数列表
                pitr_args = [args.target_time]
                if args.full_backup:
                    pitr_args.append(args.full_backup)
                pitr_args.extend(args.incremental_backups)
                sys.argv = ['point_in_time_restore.py'] + pitr_args
                pitr_main()
            else:
                restore_parser.print_help()
        
        elif args.category == 'binlog':
            if args.command == 'to-sql':
                from tasks.binlog.convert_binlog_to_sql import main as to_sql_main
                to_sql_main()
            elif args.command == 'to-insert':
                from tasks.binlog.convert_binlog_to_insert import main as to_insert_main
                to_insert_main()
            elif args.command == 'apply-generic':
                from tasks.binlog.apply_binlog_generic import main as apply_generic_main
                apply_generic_main()
            elif args.command == 'apply-universal':
                from tasks.binlog.apply_binlog_universal import main as apply_universal_main
                apply_universal_main()
            elif args.command == 'apply-pitr':
                from tasks.binlog.apply_pitr_binlog import main as apply_pitr_main
                apply_pitr_main()
            else:
                binlog_parser.print_help()
        
        elif args.category == 'notify':
            if args.command == 'dingtalk':
                from tasks.notify.dingtalk_notify import main as dingtalk_main
                sys.argv = ['dingtalk_notify.py', args.status, args.message]
                dingtalk_main()
            else:
                notify_parser.print_help()
        
        elif args.category == 'schedule':
            if args.command == 'start':
                from tasks.schedule.start_backup import main as schedule_main
                schedule_main()
            else:
                schedule_parser.print_help()
        
        elif args.category == 'test':
            if args.command == 'full-flow':
                from tests.test_full_flow import main as test_full_flow_main
                test_full_flow_main()
            elif args.command == 'pitr':
                from tests.test_pitr import main as test_pitr_main
                test_pitr_main()
            elif args.command == 'pitr-between':
                from tests.test_pitr_between_incremental import main as test_pitr_between_main
                test_pitr_between_main()
            else:
                test_parser.print_help()
        
        elif args.category == 'help':
            show_help()
        
        else:
            parser.print_help()
    
    except ImportError as e:
        print(f"{Colors.RED}错误: 无法导入模块: {e}{Colors.NC}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"{Colors.RED}错误: {e}{Colors.NC}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

