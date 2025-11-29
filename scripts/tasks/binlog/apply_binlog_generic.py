#!/usr/bin/env python3
"""
通用的binlog应用脚本
支持任意表和任意列结构，自动检测表结构并生成正确的INSERT语句

用法: apply_binlog_generic.py <binlog_file> <stop_datetime> <database> [mysql_opts]
示例: apply_binlog_generic.py /backups/binlog_backup_*/mysql-bin.000004 '2025-11-27 16:15:54' testdb
      apply_binlog_generic.py /backups/binlog_backup_*/mysql-bin.000004 '2025-11-27 16:15:54' testdb '-h 127.0.0.1 -u root -prootpassword'
"""

import sys
import subprocess
import re
import shlex

def get_table_columns(db, table, mysql_opts):
    """从MySQL获取表的列信息"""
    # 解析mysql_opts
    opts_parts = shlex.split(mysql_opts)
    
    # 构建mysql命令
    cmd = ["mysql"] + opts_parts + ["-N", "-e", f"""
        SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = '{db}' AND TABLE_NAME = '{table}'
        ORDER BY ORDINAL_POSITION;
    """]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            return None, None
        
        col_names = []
        col_types = {}
        
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                col_name = parts[0]
                col_type = parts[1]
                col_names.append(col_name)
                col_types[col_name] = col_type
        
        return col_names, col_types
    
    except Exception:
        return None, None

def convert_binlog_to_insert(binlog_file, stop_datetime, database, mysql_opts="-h 127.0.0.1 -u root -prootpassword"):
    """转换binlog为INSERT语句"""
    
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
    
    # 缓存表结构
    col_cache = {}  # {db.table: [col_names]}
    type_cache = {}  # {db.table: {col_name: col_type}}
    
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
                
                # 如果表结构未缓存，获取表结构
                cache_key = f"{current_db}.{current_table}"
                if cache_key not in col_cache:
                    col_names, col_types = get_table_columns(current_db, current_table, mysql_opts)
                    if col_names:
                        col_cache[cache_key] = col_names
                        type_cache[cache_key] = col_types
                
                continue
            
            # 在INSERT块中
            if in_insert:
                # 跳过SET行
                if set_pattern.match(line):
                    continue
                
                # 提取列值：@1=value, @2=value等
                match = value_pattern.match(line)
                if match:
                    col_num = int(match.group(1))
                    col_value = match.group(2).strip()
                    
                    # 获取列类型
                    cache_key = f"{current_db}.{current_table}"
                    if cache_key in col_cache and col_num <= len(col_cache[cache_key]):
                        col_name = col_cache[cache_key][col_num - 1]
                        col_type = type_cache.get(cache_key, {}).get(col_name, "")
                        
                        # 处理时间戳类型
                        if col_type in ("timestamp", "datetime"):
                            # 如果是数字时间戳，转换为FROM_UNIXTIME
                            if re.match(r'^\d+$', col_value):
                                col_value = f"FROM_UNIXTIME({col_value})"
                    
                    values.append(col_value)
                    col_count += 1
                    continue
                
                # 检测INSERT块结束（遇到下一个事件标记）
                if event_marker_pattern.match(line):
                    if values and current_table:
                        # 获取列名
                        cache_key = f"{current_db}.{current_table}"
                        col_names = col_cache.get(cache_key, [])
                        
                        # 生成INSERT语句
                        values_str = ", ".join(values)
                        if col_names:
                            col_names_str = ", ".join([f"`{col}`" for col in col_names])
                            print(f"INSERT INTO `{current_db}`.`{current_table}` ({col_names_str}) VALUES ({values_str});")
                        else:
                            # 如果没有列名，使用VALUES语法
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
        print("用法: apply_binlog_generic.py <binlog_file> <stop_datetime> <database> [mysql_opts]", file=sys.stderr)
        print("示例: apply_binlog_generic.py /backups/binlog_backup_*/mysql-bin.000004 '2025-11-27 16:15:54' testdb", file=sys.stderr)
        print("      apply_binlog_generic.py /backups/binlog_backup_*/mysql-bin.000004 '2025-11-27 16:15:54' testdb '-h 127.0.0.1 -u root -prootpassword'", file=sys.stderr)
        sys.exit(1)
    
    binlog_file = sys.argv[1]
    stop_datetime = sys.argv[2]
    database = sys.argv[3]
    mysql_opts = sys.argv[4] if len(sys.argv) > 4 else "-h 127.0.0.1 -u root -prootpassword"
    
    convert_binlog_to_insert(binlog_file, stop_datetime, database, mysql_opts)

if __name__ == "__main__":
    main()

