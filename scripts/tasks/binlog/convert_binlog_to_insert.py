#!/usr/bin/env python3
"""
通用的binlog转INSERT语句脚本
支持任意表和任意列结构

用法: convert_binlog_to_insert.py <binlog_file> [stop_datetime] [database]
示例: convert_binlog_to_insert.py /backups/binlog_backup_*/mysql-bin.000004 '2025-11-27 16:15:54' testdb
"""

import sys
import subprocess
import re

def convert_binlog_to_insert(binlog_file, stop_datetime=None, database=None):
    """从binlog中提取表结构信息并生成INSERT语句"""
    
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
                    values.append(col_value)
                    col_count += 1
                    continue
                
                # 检测INSERT块结束（遇到下一个事件标记）
                if event_marker_pattern.match(line):
                    if values and current_table:
                        # 生成INSERT语句
                        # 注意：这里我们不知道列名，所以使用VALUES语法
                        # 实际应用中，可以从MySQL查询表结构获取列名
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
    if len(sys.argv) < 2:
        print("用法: convert_binlog_to_insert.py <binlog_file> [stop_datetime] [database]", file=sys.stderr)
        print("示例: convert_binlog_to_insert.py /backups/binlog_backup_*/mysql-bin.000004 '2025-11-27 16:15:54' testdb", file=sys.stderr)
        sys.exit(1)
    
    binlog_file = sys.argv[1]
    stop_datetime = sys.argv[2] if len(sys.argv) > 2 else None
    database = sys.argv[3] if len(sys.argv) > 3 else None
    
    convert_binlog_to_insert(binlog_file, stop_datetime, database)

if __name__ == "__main__":
    main()

