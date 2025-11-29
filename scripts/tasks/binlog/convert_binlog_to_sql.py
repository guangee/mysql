#!/usr/bin/env python3
"""
将ROW格式的binlog转换为可执行的INSERT语句

读取mysqlbinlog的输出（--base64-output=DECODE-ROWS --verbose格式）
并转换为INSERT语句
"""

import sys
import re

def convert_binlog_to_sql():
    """将binlog输出转换为SQL INSERT语句"""
    in_insert = False
    table_name = ""
    values = []
    column_count = 0
    
    # 正则表达式模式
    insert_pattern = re.compile(r'^###\s+INSERT\s+INTO\s+.*\.(.*)\s*$')
    set_pattern = re.compile(r'^###\s+SET\s*$')
    value_pattern = re.compile(r'^###\s+@(\d+)=(.*)$')
    separator_pattern = re.compile(r'^--')
    
    for line in sys.stdin:
        line = line.rstrip('\n\r')
        
        # 检测INSERT语句开始
        match = insert_pattern.match(line)
        if match:
            table_name = match.group(1).strip('`')
            in_insert = True
            values = []
            column_count = 0
            continue
        
        # 在INSERT块中，提取SET值
        if in_insert:
            # 检测SET行（跳过）
            if set_pattern.match(line):
                continue
            
            # 检测值行：@1=value, @2=value等
            match = value_pattern.match(line)
            if match:
                col_num = match.group(1)
                col_value = match.group(2).strip()
                
                # 处理字符串值（移除单引号，但保留内容）
                if col_value.startswith("'") and col_value.endswith("'"):
                    col_value = f"'{col_value[1:-1]}'"
                
                values.append(col_value)
                column_count += 1
                continue
            
            # 检测INSERT块结束（--分隔符）
            if separator_pattern.match(line):
                if values:
                    # 构建INSERT语句
                    # 假设列顺序：id, data_value, inserted_at, note
                    if len(values) >= 4:
                        # inserted_at是时间戳，需要转换为FROM_UNIXTIME
                        print(f"INSERT INTO {table_name} (id, data_value, inserted_at, note) VALUES ({values[0]}, {values[1]}, FROM_UNIXTIME({values[2]}), {values[3]});")
                    elif len(values) >= 3:
                        print(f"INSERT INTO {table_name} (id, data_value, inserted_at) VALUES ({values[0]}, {values[1]}, FROM_UNIXTIME({values[2]}));")
                
                in_insert = False
                values = []
                column_count = 0

if __name__ == "__main__":
    try:
        convert_binlog_to_sql()
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

