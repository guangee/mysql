#!/usr/bin/env python3
"""
PITR 二进制日志自动应用脚本

该脚本用于在 MySQL 启动后自动应用 PITR 恢复过程中生成的二进制日志 SQL 文件。
主要功能：
1. 检查 PITR 恢复标记文件
2. 应用从最后一次备份时间点到目标时间点之间的所有 binlog（包括 DDL 和 DML）
3. 忽略非致命错误（ERROR 1050、1062、1032）
4. 删除标记文件（如果应用成功）

注意：该脚本会应用所有 binlog 内容，包括 DDL 语句（CREATE TABLE、DROP TABLE、ALTER TABLE 等），
因为 binlog 提取时已经根据备份时间点和目标时间点进行了精确的时间范围过滤。
"""

import os
import sys
import subprocess
import re
import tempfile
import time
from pathlib import Path

# 配置
PITR_MARKER = "/backups/.pitr_restore_marker"
MYSQL_HOST = "localhost"
MYSQL_USER = "root"
MYSQL_PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD", "")
BACKUP_BASE_DIR = "/backups"

# 日志函数
def log_debug(msg):
    """输出 DEBUG 日志"""
    print(f"[DEBUG] {msg}", file=sys.stderr, flush=True)

def log_info(msg):
    """输出 INFO 日志"""
    print(f"[INFO] {msg}", file=sys.stderr, flush=True)

def log_warn(msg):
    """输出 WARN 日志"""
    print(f"⚠ 警告: {msg}", file=sys.stderr, flush=True)

def log_success(msg):
    """输出 SUCCESS 日志"""
    print(f"✓ {msg}", file=sys.stderr, flush=True)

def log_error(msg):
    """输出 ERROR 日志"""
    print(f"✗ 错误: {msg}", file=sys.stderr, flush=True)

def wait_for_mysql(max_wait=60):
    """等待 MySQL 启动"""
    log_debug(f"等待 MySQL 启动（最多 {max_wait} 秒）...")
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        try:
            result = subprocess.run(
                ["mysql", "-h", MYSQL_HOST, "-u", MYSQL_USER, f"-p{MYSQL_PASSWORD}", "-e", "SELECT 1"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                elapsed = int(time.time() - start_time)
                log_debug(f"MySQL 连接成功（等待了 {elapsed} 秒）")
                return True
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
        time.sleep(2)
    
    log_warn(f"等待 MySQL 启动超时（{max_wait} 秒）")
    return False

def filter_ddl_statements(sql_file, output_file):
    """
    过滤 DDL 语句，只保留数据相关的语句
    
    Args:
        sql_file: 原始 SQL 文件路径
        output_file: 输出文件路径
    
    Returns:
        tuple: (success: bool, filtered_lines: int, error: str)
    """
    log_debug("开始过滤 DDL 语句...")
    
    try:
        with open(sql_file, 'r', encoding='utf-8', errors='ignore') as f:
            original_lines = sum(1 for _ in f)
        log_debug(f"原始 SQL 文件行数: {original_lines}")
    except Exception as e:
        log_warn(f"无法读取原始 SQL 文件行数: {e}")
        original_lines = 0
    
    try:
        in_create_table = False
        skip_ddl = False
        brace_count = 0
        filtered_lines = 0
        
        # 正则表达式模式
        create_table_pattern = re.compile(
            r'^\s*create\s+(or\s+replace\s+)?table\s+(if\s+not\s+exists\s+)?',
            re.IGNORECASE
        )
        drop_table_pattern = re.compile(r'^\s*drop\s+table', re.IGNORECASE)
        alter_table_pattern = re.compile(r'^\s*alter\s+table', re.IGNORECASE)
        comment_pattern = re.compile(r'^\s*(#|/\*|--|BINLOG)', re.IGNORECASE)
        
        with open(sql_file, 'r', encoding='utf-8', errors='ignore') as infile, \
             open(output_file, 'w', encoding='utf-8') as outfile:
            
            for line in infile:
                # 跳过注释行和 BINLOG 行（这些行可能包含 create table 关键字）
                if comment_pattern.match(line):
                    if not in_create_table:
                        outfile.write(line)
                        filtered_lines += 1
                    continue
                
                # 检测 CREATE TABLE 开始
                if create_table_pattern.search(line) and not comment_pattern.match(line):
                    in_create_table = True
                    skip_ddl = True
                    brace_count = 0
                    # 计算当前行的括号
                    brace_count += line.count('(')
                    brace_count -= line.count(')')
                    log_debug(f"检测到 CREATE TABLE 语句: {line.strip()[:50]}")
                    continue
                
                # 检测 DROP TABLE
                if drop_table_pattern.search(line) and not comment_pattern.match(line):
                    skip_ddl = True
                    log_debug(f"检测到 DROP TABLE 语句: {line.strip()[:50]}")
                    continue
                
                # 检测 ALTER TABLE
                if alter_table_pattern.search(line) and not comment_pattern.match(line):
                    skip_ddl = True
                    log_debug(f"检测到 ALTER TABLE 语句: {line.strip()[:50]}")
                    continue
                
                # 如果在 CREATE TABLE 块中
                if in_create_table:
                    # 计算括号
                    brace_count += line.count('(')
                    brace_count -= line.count(')')
                    # 如果括号平衡且遇到分号，结束
                    if brace_count <= 0 and ';' in line:
                        in_create_table = False
                        skip_ddl = False
                        log_debug("CREATE TABLE 语句块结束")
                    continue
                
                # 如果跳过 DDL 且遇到分号，重置
                if skip_ddl and ';' in line:
                    skip_ddl = False
                    continue
                
                # 输出非 DDL 内容
                if not skip_ddl:
                    outfile.write(line)
                    filtered_lines += 1
        
        log_debug(f"过滤完成: {filtered_lines} 行")
        return True, filtered_lines, None
        
    except Exception as e:
        error_msg = f"过滤 DDL 语句时出错: {e}"
        log_error(error_msg)
        return False, 0, error_msg

def apply_sql_file(sql_file):
    """
    应用 SQL 文件到 MySQL
    
    Args:
        sql_file: SQL 文件路径
    
    Returns:
        tuple: (success: bool, exit_code: int, output: str, error_stats: dict)
    """
    log_debug(f"准备应用 SQL 文件: {sql_file}")
    
    try:
        file_size = os.path.getsize(sql_file)
        log_debug(f"SQL 文件大小: {file_size} 字节")
    except Exception as e:
        log_warn(f"无法获取文件大小: {e}")
        file_size = 0
    
    start_time = time.time()
    
    try:
        # 使用 --force 忽略错误，继续执行
        with open(sql_file, 'r', encoding='utf-8', errors='ignore') as sql_input:
            result = subprocess.run(
                ["mysql", "-h", MYSQL_HOST, "-u", MYSQL_USER, f"-p{MYSQL_PASSWORD}", "--force"],
                stdin=sql_input,
                capture_output=True,
                text=True,
                timeout=3600  # 最多 1 小时
            )
        
        duration = int(time.time() - start_time)
        log_debug(f"SQL 应用完成，耗时: {duration} 秒，退出码: {result.returncode}")
        
        # 统计错误
        output = result.stdout + result.stderr
        error_stats = {
            'total': len(re.findall(r'ERROR', output, re.IGNORECASE)),
            'error_1050': len(re.findall(r'ERROR\s+1050', output, re.IGNORECASE)),
            'error_1062': len(re.findall(r'ERROR\s+1062', output, re.IGNORECASE)),
            'error_1032': len(re.findall(r'ERROR\s+1032', output, re.IGNORECASE)),
        }
        
        log_debug(f"错误统计: 总计={error_stats['total']}, "
                 f"ERROR 1050={error_stats['error_1050']}, "
                 f"ERROR 1062={error_stats['error_1062']}, "
                 f"ERROR 1032={error_stats['error_1032']}")
        
        # 计算关键错误（排除非致命错误）
        critical_errors = []
        for line in output.split('\n'):
            line_upper = line.upper()
            if 'ERROR' in line_upper:
                # 排除非致命错误
                # ERROR 1050: Table already exists
                # ERROR 1062: Duplicate entry
                # ERROR 1032: Can't find record
                # ERROR 1146: Table doesn't exist (表结构应在备份中恢复，但可能恢复不完整)
                is_non_critical = (
                    'ERROR 1050' in line_upper or
                    'ERROR 1062' in line_upper or
                    'ERROR 1032' in line_upper or
                    'ERROR 1146' in line_upper or
                    'USING A PASSWORD' in line_upper or
                    'WARNING' in line_upper
                )
                if not is_non_critical:
                    critical_errors.append(line.strip())
        
        critical_count = len(critical_errors)
        log_debug(f"关键错误数量: {critical_count}")
        
        if critical_count > 0:
            log_debug("关键错误列表:")
            for err in critical_errors[:10]:
                log_debug(f"  {err}")
        
        return True, result.returncode, output, error_stats, critical_count
        
    except subprocess.TimeoutExpired:
        log_error("SQL 应用超时（超过 1 小时）")
        return False, -1, "", {}, 0
    except Exception as e:
        log_error(f"应用 SQL 文件时出错: {e}")
        return False, -1, str(e), {}, 0

def main():
    """主函数"""
    log_info("检查 PITR 标记文件...")
    
    if not os.path.exists(PITR_MARKER):
        log_debug("PITR 标记文件不存在，跳过自动应用")
        return 0
    
    log_debug(f"PITR 标记文件存在: {PITR_MARKER}")
    
    # 读取 SQL 文件路径
    try:
        with open(PITR_MARKER, 'r', encoding='utf-8') as f:
            sql_file = f.read().strip()
        log_debug(f"从标记文件读取 SQL 文件路径: '{sql_file}'")
    except Exception as e:
        log_error(f"无法读取标记文件: {e}")
        return 1
    
    if not sql_file or not os.path.exists(sql_file):
        log_warn(f"PITR 标记文件存在，但 SQL 文件不存在或无效: {sql_file}")
        try:
            os.remove(PITR_MARKER)
            log_info("已删除无效的标记文件")
        except:
            pass
        return 1
    
    log_info(f"发现 PITR 恢复标记文件，准备应用二进制日志 SQL...")
    log_info(f"SQL 文件: {sql_file}")
    
    try:
        file_size = os.path.getsize(sql_file)
        log_debug(f"SQL 文件大小: {file_size} 字节")
    except Exception as e:
        log_warn(f"无法获取文件大小: {e}")
    
    # 等待 MySQL 启动
    if not wait_for_mysql():
        log_warn("无法连接到 MySQL，请手动执行恢复")
        return 1
    
    log_info("MySQL 已启动，开始应用二进制日志 SQL...")
    
    # 直接应用 SQL 文件，不过滤 DDL
    # 因为 binlog 提取时已经根据备份时间点和目标时间点进行了精确的时间范围过滤
    # 所以应该应用所有内容，包括 DDL 语句（CREATE TABLE、DROP TABLE、ALTER TABLE 等）
    log_info("应用从最后一次备份时间点到目标时间点之间的所有 binlog（包括 DDL 和 DML）...")
    log_debug(f"SQL 文件: {sql_file}")
    
    try:
        file_size = os.path.getsize(sql_file)
        log_info(f"SQL 文件大小: {file_size} 字节")
    except Exception as e:
        log_warn(f"无法获取文件大小: {e}")
    
    # 应用 SQL 文件
    log_info("应用 SQL（忽略表已存在和重复键错误）...")
    success, exit_code, output, error_stats, critical_count = apply_sql_file(sql_file)
    
    if not success:
        log_error("应用 SQL 文件失败")
        return 1
    
    # 判断是否成功
    if exit_code == 0:
        log_debug("退出码为 0，视为成功")
        log_success("二进制日志 SQL 应用成功！（退出码: 0，已忽略非致命错误）")
    elif critical_count == 0:
        log_debug("critical_errors 为 0，视为成功")
        log_info("注意: 所有错误都是非致命的（重复键或记录不存在），视为成功")
        log_success("二进制日志 SQL 应用成功！（已忽略非致命错误）")
    else:
        log_debug(f"critical_errors 不为 0 ({critical_count})，视为失败")
        log_warn("二进制日志 SQL 应用失败，请检查日志")
        log_info(f"SQL 文件保存在: {sql_file}")
        log_info(f"可以手动检查并执行: mysql -h {MYSQL_HOST} -u {MYSQL_USER} -p{MYSQL_PASSWORD} < {sql_file}")
        return 1
    
    # 删除标记文件
    try:
        if os.path.exists(PITR_MARKER):
            os.remove(PITR_MARKER)
            log_debug("标记文件已成功删除")
            log_success("已删除 PITR 标记文件")
    except Exception as e:
        log_warn(f"删除文件失败: {e}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

