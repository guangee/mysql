#!/usr/bin/env python3
"""
通用的binlog应用脚本（简化版）
自动检测表结构，支持任意表和列

用法: apply_binlog_universal.py <binlog_file> <stop_datetime> <database> [mysql_opts]
示例: apply_binlog_universal.py /backups/binlog_backup_*/mysql-bin.000004 '2025-11-27 16:15:54' testdb
"""

import sys
import subprocess
import re

def convert_binlog_to_insert(binlog_file, stop_datetime, database, mysql_opts="-h 127.0.0.1 -u root -prootpassword"):
    """转换binlog为INSERT语句（通用版本）"""
    
    # 构建mysqlbinlog命令
    cmd = ["mysqlbinlog", "--skip-gtids"]
    
    if stop_datetime:
        cmd.extend(["--stop-datetime", stop_datetime])
    
    if database:
        cmd.extend(["--database", database])
    
    cmd.extend(["--base64-output=DECODE-ROWS", "--verbose", binlog_file])
    
    # 状态变量
    in_insert = False
    current_table = ""
    current_db = ""
    values = []
    col_count = 0
    
    # 正则表达式模式
    insert_pattern = re.compile(r'^### INSERT INTO `([^`]+)`\.`([^`]+)`')
    set_pattern = re.compile(r'^### SET')
    value_pattern = re.compile(r'^###\s+@(\d+)=(.*)$')
    event_marker_pattern = re.compile(r'^# at \d+$')
    
    try:
        # 执行mysqlbinlog命令
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1
        )
        
        for line in process.stdout:
            line = line.rstrip('\n\r')
            
            # 检测INSERT语句开始
            match = insert_pattern.match(line)
            if match:
                current_db = match.group(1)
                current_table = match.group(2)
                in_insert = True
                values = []
                col_count = 0
                continue
            
            # 在INSERT块中
            if in_insert:
                # 跳过SET行
                if set_pattern.match(line):
                    continue
                
                # 提取列值：@1=value, @2=value等
                match = value_pattern.match(line)
                if match:
                    col_value = match.group(2).strip()
                    
                    # 检查是否是数字时间戳（10位或13位数字）
                    # 但这里我们不知道列类型，所以先保持原样
                    # 如果后续发现是timestamp类型，再转换
                    
                    values.append(col_value)
                    col_count += 1
                    continue
                
                # 检测INSERT块结束（遇到下一个事件标记）
                if event_marker_pattern.match(line):
                    if values and current_table:
                        # 生成INSERT语句（使用VALUES语法，不指定列名）
                        # MySQL会根据表结构自动匹配
                        values_str = ", ".join(values)
                        print(f"INSERT INTO `{current_db}`.`{current_table}` VALUES ({values_str});")
                    
                    in_insert = False
                    values = []
                    col_count = 0
                    current_table = ""
                    current_db = ""
        
        process.wait()
        
    except FileNotFoundError:
        print(f"错误: mysqlbinlog 命令未找到", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    """主函数"""
    if len(sys.argv) < 4:
        print("用法: apply_binlog_universal.py <binlog_file> <stop_datetime> <database> [mysql_opts]", file=sys.stderr)
        print("示例: apply_binlog_universal.py /backups/binlog_backup_*/mysql-bin.000004 '2025-11-27 16:15:54' testdb", file=sys.stderr)
        sys.exit(1)
    
    binlog_file = sys.argv[1]
    stop_datetime = sys.argv[2]
    database = sys.argv[3]
    mysql_opts = sys.argv[4] if len(sys.argv) > 4 else "-h 127.0.0.1 -u root -prootpassword"
    
    convert_binlog_to_insert(binlog_file, stop_datetime, database, mysql_opts)

if __name__ == "__main__":
    main()

