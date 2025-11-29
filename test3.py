#!/usr/bin/env python3
"""
test3.py - 测试在两次增量备份之间进行 PITR 恢复的场景

测试流程：
1. 清理环境并重新构建镜像
2. 启动容器并创建测试表
3. 全量备份
4. 执行数据操作
5. 增量备份 #1
6. 执行数据操作
7. 记录 MD5（这是要恢复到的状态）
8. 增量备份 #2
9. 执行更多数据操作（模拟误操作）
10. 停止 MySQL
11. 使用 docker-compose 执行 PITR 恢复
12. 重启 MySQL
13. 验证 MD5
"""

import os
import sys
import subprocess
import hashlib
import time
import random
import string
from datetime import datetime, timedelta
from pathlib import Path

# 尝试导入 MySQL 客户端库
try:
    import pymysql
    MYSQL_LIB = 'pymysql'
except ImportError:
    try:
        import mysql.connector
        MYSQL_LIB = 'mysql.connector'
    except ImportError:
        print("错误: 未找到 MySQL 客户端库，请安装 PyMySQL 或 mysql-connector-python", file=sys.stderr)
        print("安装命令: pip3 install pymysql 或 pip3 install mysql-connector-python", file=sys.stderr)
        sys.exit(1)

# 配置
CONTAINER_NAME = "mysql8035"
MYSQL_ROOT_PASSWORD = "rootpassword"
MYSQL_DATABASE = "testdb"
MYSQL_PORT = "3307"
IMAGE_NAME = "zziaguan/mysql:8.0.35"
TZ_REGION = "Asia/Shanghai"

TABLES = ["customers", "orders", "inventory", "audit_logs", "metrics"]

DATA_SNAPSHOT_BETWEEN = "./backups/test3_snapshot_between.txt"
DATA_SNAPSHOT_AFTER = "./backups/test3_snapshot_after.txt"
BINLOG_POS_FILE = "./backups/binlog_base_position.txt"
BASELINE_SNAPSHOT_CONTENT = "./backups/test3_baseline_snapshot_content.txt"
RESTORED_SNAPSHOT_CONTENT = "./backups/test3_restored_snapshot_content.txt"

# 颜色输出
class Colors:
    BLUE = '\033[0;34m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    NC = '\033[0m'  # No Color

def log_info(msg):
    print(f"{Colors.BLUE}[INFO]{Colors.NC} {msg}", flush=True)

def log_success(msg):
    print(f"{Colors.GREEN}[SUCCESS]{Colors.NC} {msg}", flush=True)

def log_warn(msg):
    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {msg}", flush=True)

def log_error(msg):
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}", flush=True)

def log_step(msg):
    print("")
    print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
    print(f"{Colors.GREEN}{msg}{Colors.NC}")
    print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
    print("")

def run_cmd(cmd, check=True, capture_output=False, shell=False):
    """执行命令"""
    log_info(f"执行命令: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            check=check,
            capture_output=capture_output,
            shell=shell,
            text=True
        )
        if capture_output:
            return result.stdout.strip()
        return result.returncode == 0 if not check else True
    except subprocess.CalledProcessError as e:
        if check:
            log_error(f"命令执行失败: {e}")
            if e.stdout:
                print(e.stdout)
            if e.stderr:
                print(e.stderr)
            sys.exit(1)
        return False

def cleanup_environment():
    """清理环境"""
    log_step("0. 清理环境")
    
    log_info("停止并删除容器...")
    run_cmd(["docker", "stop", CONTAINER_NAME], check=False)
    run_cmd(["docker", "rm", CONTAINER_NAME], check=False)
    
    log_info("清理数据目录...")
    dirs = ["./mysql_data", "./backups", "./mysql_config"]
    for dir_path in dirs:
        if os.path.exists(dir_path):
            log_info(f"清理 {dir_path} 目录内容（保留目录）...")
            for item in os.listdir(dir_path):
                item_path = os.path.join(dir_path, item)
                try:
                    if os.path.isdir(item_path):
                        import shutil
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
                except Exception as e:
                    log_warn(f"删除 {item_path} 失败: {e}")
            log_success(f"{dir_path} 目录内容已清理")
        else:
            os.makedirs(dir_path, exist_ok=True)
            log_success(f"已创建 {dir_path} 目录")
    
    log_success("环境清理完成")

def build_image():
    """重新构建 Docker 镜像"""
    log_step("1. 重新构建 Docker 镜像")
    
    log_info(f"构建镜像: {IMAGE_NAME}")
    if run_cmd(["docker", "build", "-t", IMAGE_NAME, "."]):
        log_success("镜像构建成功")
    else:
        log_error("镜像构建失败")
        sys.exit(1)

def start_environment():
    """启动环境"""
    log_step("2. 启动 MySQL 容器")
    
    log_info("启动容器...")
    # 检查容器是否存在
    result = run_cmd(["docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"], check=False, capture_output=True)
    if result and CONTAINER_NAME in result:
        # 容器存在，启动它
        run_cmd(["docker", "start", CONTAINER_NAME])
    else:
        # 容器不存在，使用docker-compose启动
        run_cmd(["docker-compose", "up", "-d"])
    
    wait_for_mysql()

def wait_for_mysql(max_wait=60):
    """等待 MySQL 启动"""
    log_info("等待 MySQL 就绪...")
    for i in range(max_wait):
        result = run_cmd([
            "docker", "exec", CONTAINER_NAME,
            "mysqladmin", "ping", "-h", "127.0.0.1",
            "-u", "root", f"-p{MYSQL_ROOT_PASSWORD}", "--silent"
        ], check=False, capture_output=True)
        if result:
            log_success("MySQL 已启动")
            time.sleep(3)
            return
        time.sleep(2)
    log_error("MySQL 启动超时")
    sys.exit(1)

def get_mysql_connection():
    """获取 MySQL 数据库连接"""
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            if MYSQL_LIB == 'pymysql':
                return pymysql.connect(
                    host='127.0.0.1',
                    port=int(MYSQL_PORT),
                    user='root',
                    password=MYSQL_ROOT_PASSWORD,
                    database=MYSQL_DATABASE,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=10
                )
            else:  # mysql.connector
                # 使用 buffered=True 避免 "Unread result found" 错误
                conn = mysql.connector.connect(
                    host='127.0.0.1',
                    port=int(MYSQL_PORT),
                    user='root',
                    password=MYSQL_ROOT_PASSWORD,
                    database=MYSQL_DATABASE,
                    charset='utf8mb4',
                    connection_timeout=10,
                    buffered=True  # 启用缓冲，避免未读取结果的问题
                )
                return conn
        except Exception as e:
            if attempt < max_retries - 1:
                log_warn(f"数据库连接失败（尝试 {attempt + 1}/{max_retries}）: {e}，{retry_delay} 秒后重试...")
                time.sleep(retry_delay)
            else:
                log_warn(f"数据库连接失败: {e}")
                return None
    return None

def mysql_exec(sql):
    """执行 MySQL 命令（使用 MySQL 库）"""
    conn = None
    try:
        conn = get_mysql_connection()
        if conn is None:
            return False
        
        cursor = conn.cursor()
        # 处理多语句 SQL（按分号分割）
        statements = [s.strip() for s in sql.split(';') if s.strip()]
        for statement in statements:
            if statement:
                cursor.execute(statement)
        conn.commit()
        cursor.close()
        return True
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        # 不输出警告，避免日志噪音（某些 SQL 可能预期会失败）
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def mysql_query(sql, fetch_one=False):
    """执行 MySQL 查询并返回结果"""
    conn = None
    try:
        conn = get_mysql_connection()
        if conn is None:
            return None
        
        cursor = conn.cursor()
        cursor.execute(sql)
        
        if fetch_one:
            result = cursor.fetchone()
        else:
            result = cursor.fetchall()
        
        cursor.close()
        return result
    except Exception as e:
        log_warn(f"SQL 查询失败: {e}")
        return None
    finally:
        if conn:
            conn.close()

def create_tables_and_seed():
    """创建测试表并初始化数据（每次SQL执行后立即提交，显示进度）"""
    log_step("3. 创建测试表并初始化数据")
    
    # 使用连接复用，提高性能
    conn = get_mysql_connection()
    if conn is None:
        log_error("无法获取数据库连接")
        return
    
    try:
        cursor = conn.cursor()
        
        # 创建表的SQL列表
        create_table_sqls = [
            """CREATE TABLE IF NOT EXISTS customers (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100),
  email VARCHAR(150),
  loyalty_points INT DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS orders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  customer_id INT,
  product_name VARCHAR(100),
  quantity INT,
  price DECIMAL(10, 2),
  order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS inventory (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_name VARCHAR(100),
  quantity INT,
  price DECIMAL(10, 2),
  last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS audit_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  action VARCHAR(50),
  table_name VARCHAR(100),
  record_id INT,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS metrics (
  id INT AUTO_INCREMENT PRIMARY KEY,
  metric_name VARCHAR(100),
  metric_value DECIMAL(10, 2),
  recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB"""
        ]
        
        # 创建表，每次执行后立即提交
        log_info("创建表...")
        for i, sql in enumerate(create_table_sqls, 1):
            try:
                cursor.execute(sql)
                conn.commit()
                table_name = ["customers", "orders", "inventory", "audit_logs", "metrics"][i-1]
                log_info(f"  [{i}/{len(create_table_sqls)}] 表 {table_name} 创建完成")
            except Exception as e:
                log_warn(f"创建表失败: {e}")
                conn.rollback()
        
        # 初始化数据，每次插入后立即提交
        log_info("初始化数据...")
        total_records = 10
        for i in range(1, total_records + 1):
            try:
                # 插入customers数据
                cursor.execute(f"INSERT INTO customers (name, email, loyalty_points) VALUES ('user{i:03d}', 'user{i:03d}@example.com', {random.randint(0, 500)})")
                conn.commit()
                
                # 插入inventory数据
                cursor.execute(f"INSERT INTO inventory (product_name, quantity, price) VALUES ('product{i:03d}', {random.randint(10, 100)}, {random.randint(10, 1000) / 10.0})")
                conn.commit()
                
                # 每5条记录显示一次进度
                if i % 5 == 0 or i == total_records:
                    log_info(f"  进度: {i}/{total_records} ({100 * i // total_records}%)")
            except Exception as e:
                log_warn(f"插入数据失败 (记录 {i}): {e}")
                conn.rollback()
        
        cursor.close()
        log_success("测试表创建并初始化完成")
    except Exception as e:
        log_error(f"创建表和初始化数据时发生错误: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def perform_full_backup():
    """执行全量备份"""
    log_step("4. 执行全量备份")
    
    # 使用 docker exec 执行容器内的备份脚本
    run_cmd(["docker", "exec", CONTAINER_NAME, "python3", "/scripts/main.py", "backup", "full"])
    log_success("全量备份完成")

def perform_incremental_backup():
    """执行增量备份"""
    # 使用 docker exec 执行容器内的备份脚本
    run_cmd(["docker", "exec", CONTAINER_NAME, "python3", "/scripts/main.py", "backup", "incremental"], check=False)
    log_success("增量备份完成")

def random_operations(count=50):
    """执行随机数据操作（优化版本，避免使用ORDER BY RAND()）"""
    operations = []
    
    # 使用连接池，复用连接以提高性能
    conn = get_mysql_connection()
    if conn is None:
        log_error("无法获取数据库连接")
        return
    
    try:
        cursor = conn.cursor()
        
        # 预先获取每个表的ID范围，避免使用ORDER BY RAND()
        table_id_ranges = {}
        for table in TABLES:
            try:
                # 获取表的最小和最大ID
                cursor.execute(f"SELECT MIN(id) as min_id, MAX(id) as max_id, COUNT(*) as cnt FROM {table}")
                result = cursor.fetchone()
                if result:
                    if isinstance(result, dict):
                        min_id = result.get('min_id')
                        max_id = result.get('max_id')
                        cnt = result.get('cnt', 0)
                    else:
                        min_id = result[0] if len(result) > 0 else None
                        max_id = result[1] if len(result) > 1 else None
                        cnt = result[2] if len(result) > 2 else 0
                    
                    if min_id is not None and max_id is not None and cnt > 0:
                        table_id_ranges[table] = (min_id, max_id, cnt)
            except Exception as e:
                log_warn(f"无法获取表 {table} 的ID范围: {e}")
        
        # 执行操作，每次操作后立即提交
        for i in range(count):
            table = random.choice(TABLES)
            op_type = random.choice(["insert", "update", "delete"])
            
            try:
                if op_type == "insert":
                    if table == "customers":
                        name = ''.join(random.choices(string.ascii_lowercase, k=8))
                        cursor.execute(f"INSERT INTO customers (name, email, loyalty_points) VALUES ('{name}', '{name}@example.com', {random.randint(0, 500)})")
                    elif table == "orders":
                        customer_id = random.randint(1, 10)
                        product = f"product{random.randint(1, 10):03d}"
                        cursor.execute(f"INSERT INTO orders (customer_id, product_name, quantity, price) VALUES ({customer_id}, '{product}', {random.randint(1, 10)}, {random.randint(10, 1000) / 10.0})")
                    elif table == "inventory":
                        product = f"product{random.randint(1, 20):03d}"
                        cursor.execute(f"INSERT INTO inventory (product_name, quantity, price) VALUES ('{product}', {random.randint(10, 100)}, {random.randint(10, 1000) / 10.0})")
                    elif table == "audit_logs":
                        cursor.execute(f"INSERT INTO audit_logs (action, table_name, record_id) VALUES ('INSERT', '{random.choice(TABLES)}', {random.randint(1, 100)})")
                    elif table == "metrics":
                        cursor.execute(f"INSERT INTO metrics (metric_name, metric_value) VALUES ('metric_{random.randint(1, 10)}', {random.randint(0, 1000) / 10.0})")
                
                elif op_type == "update":
                    # 使用ID范围而不是ORDER BY RAND()，大幅提升性能
                    if table in table_id_ranges:
                        min_id, max_id, cnt = table_id_ranges[table]
                        if cnt > 0:
                            # 随机选择一个ID范围内的值
                            target_id = random.randint(int(min_id), int(max_id))
                            if table == "customers":
                                cursor.execute(f"UPDATE customers SET loyalty_points = {random.randint(0, 500)} WHERE id = {target_id} LIMIT 1")
                            elif table == "orders":
                                cursor.execute(f"UPDATE orders SET quantity = {random.randint(1, 10)} WHERE id = {target_id} LIMIT 1")
                            elif table == "inventory":
                                cursor.execute(f"UPDATE inventory SET quantity = {random.randint(10, 100)} WHERE id = {target_id} LIMIT 1")
                            elif table == "audit_logs":
                                cursor.execute(f"UPDATE audit_logs SET action = 'UPDATE' WHERE id = {target_id} LIMIT 1")
                            elif table == "metrics":
                                cursor.execute(f"UPDATE metrics SET metric_value = {random.randint(0, 1000) / 10.0} WHERE id = {target_id} LIMIT 1")
                    else:
                        # 如果无法获取ID范围，跳过这个操作
                        continue
                
                elif op_type == "delete":
                    # 使用ID范围而不是ORDER BY RAND()
                    if table in table_id_ranges:
                        min_id, max_id, cnt = table_id_ranges[table]
                        if cnt > 0:
                            target_id = random.randint(int(min_id), int(max_id))
                            if table == "customers":
                                # 先删除关联的orders
                                cursor.execute(f"DELETE FROM orders WHERE customer_id = {target_id}")
                                cursor.execute(f"DELETE FROM customers WHERE id = {target_id} LIMIT 1")
                            elif table == "orders":
                                cursor.execute(f"DELETE FROM orders WHERE id = {target_id} LIMIT 1")
                            elif table == "inventory":
                                cursor.execute(f"DELETE FROM inventory WHERE id = {target_id} LIMIT 1")
                            elif table == "audit_logs":
                                cursor.execute(f"DELETE FROM audit_logs WHERE id = {target_id} LIMIT 1")
                            elif table == "metrics":
                                cursor.execute(f"DELETE FROM metrics WHERE id = {target_id} LIMIT 1")
                    else:
                        continue
                
                operations.append(f"{op_type.upper()} on {table}")
                
                # 每次操作后立即提交
                conn.commit()
                
                # 如果是删除操作，更新ID范围
                if op_type == "delete" and table in table_id_ranges:
                    try:
                        cursor.execute(f"SELECT MIN(id) as min_id, MAX(id) as max_id, COUNT(*) as cnt FROM {table}")
                        result = cursor.fetchone()
                        # 确保结果被完全读取
                        if MYSQL_LIB == 'mysql.connector':
                            try:
                                cursor.fetchall()
                            except:
                                pass
                        if result:
                            if isinstance(result, dict):
                                min_id = result.get('min_id')
                                max_id = result.get('max_id')
                                cnt = result.get('cnt', 0)
                            else:
                                min_id = result[0] if len(result) > 0 else None
                                max_id = result[1] if len(result) > 1 else None
                                cnt = result[2] if len(result) > 2 else 0
                            
                            if min_id is not None and max_id is not None and cnt > 0:
                                table_id_ranges[table] = (min_id, max_id, cnt)
                    except:
                        pass
                
                # 每10个操作显示一次进度
                if (i + 1) % 10 == 0:
                    log_info(f"进度: {i + 1}/{count} ({100 * (i + 1) // count}%)")
                    
            except Exception as e:
                log_warn(f"操作失败: {op_type} on {table}: {e}")
                conn.rollback()
        
        cursor.close()
        
        log_success(f"执行随机操作 {count} 次完成（成功: {len(operations)}）")
    except Exception as e:
        log_error(f"执行随机操作时发生错误: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def dump_all_tables(output_file):
    """导出所有表的数据（每次查询后立即提交，显示进度）"""
    conn = None
    try:
        conn = get_mysql_connection()
        if conn is None:
            log_error("无法连接到数据库")
            return
        
        cursor = conn.cursor()
        total_tables = len(TABLES)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for idx, table in enumerate(TABLES, 1):
                f.write(f"TABLE:{table}\n")
                
                # 检查表是否存在
                check_sql = f"SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_schema='{MYSQL_DATABASE}' AND table_name='{table}'"
                try:
                    cursor.execute(check_sql)
                    result = cursor.fetchone()
                    # 确保结果被完全读取（对于某些连接器，需要读取所有结果）
                    if MYSQL_LIB == 'mysql.connector':
                        # mysql.connector 需要确保所有结果都被读取
                        try:
                            cursor.fetchall()  # 读取剩余结果（如果有）
                        except:
                            pass  # 如果没有剩余结果，忽略错误
                    
                    # 处理不同库的返回格式
                    count = 0
                    if result:
                        if isinstance(result, dict):
                            count = result.get('cnt', 0)
                        elif isinstance(result, (tuple, list)):
                            count = result[0] if len(result) > 0 else 0
                    
                    if count > 0:
                        # 导出表数据
                        dump_sql = f"SELECT * FROM {table} ORDER BY 1"
                        cursor.execute(dump_sql)
                        rows = cursor.fetchall()
                        # 确保所有结果都被读取
                        if MYSQL_LIB == 'mysql.connector':
                            # 对于mysql.connector，fetchall()已经读取了所有结果
                            pass
                        
                        row_count = 0
                        for row in rows:
                            if isinstance(row, dict):
                                # PyMySQL 返回字典
                                values = [str(v) if v is not None else 'NULL' for v in row.values()]
                            else:
                                # mysql.connector 返回元组或列表
                                values = [str(v) if v is not None else 'NULL' for v in row]
                            f.write('|'.join(values) + '\n')
                            row_count += 1
                        
                        # 每次查询后提交（对于查询操作，提交是可选的，但可以确保连接状态正确）
                        conn.commit()
                        log_info(f"  [{idx}/{total_tables}] 表 {table} 导出完成（{row_count} 条记录）")
                    else:
                        f.write("TABLE_NOT_EXISTS\n")
                        conn.commit()  # 提交检查查询
                        log_info(f"  [{idx}/{total_tables}] 表 {table} 不存在，跳过")
                except Exception as e:
                    log_warn(f"导出表 {table} 失败: {e}")
                    f.write("TABLE_ERROR\n")
                    # 如果出错，尝试清理未读取的结果
                    try:
                        if MYSQL_LIB == 'mysql.connector':
                            cursor.fetchall()  # 读取剩余结果
                        conn.rollback()
                    except:
                        pass
        
        cursor.close()
        log_success(f"所有表数据导出完成（共 {total_tables} 个表）")
    except Exception as e:
        log_error(f"导出表数据失败: {e}")
        # 尝试清理连接
        if conn:
            try:
                conn.rollback()
            except:
                pass
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def compute_md5(file_path):
    """计算文件的 MD5"""
    md5_hash = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()

def record_binlog_position():
    """记录 binlog 位点（使用 MySQL 库）"""
    log_step("记录 binlog 起始位点")
    
    conn = None
    try:
        conn = get_mysql_connection()
        if conn is None:
            log_warn("无法连接到数据库，无法记录 binlog 位点")
            return None, None
        
        cursor = conn.cursor()
        cursor.execute("SHOW MASTER STATUS")
        result = cursor.fetchone()
        # 确保结果被完全读取（对于某些连接器，需要读取所有结果）
        if MYSQL_LIB == 'mysql.connector':
            cursor.fetchall()  # 读取剩余结果（如果有）
        cursor.close()
        
        if result:
            if isinstance(result, dict):
                file_name = result.get('File', '')
                position = str(result.get('Position', ''))
            elif isinstance(result, (tuple, list)):
                file_name = result[0] if len(result) > 0 else ''
                position = str(result[1]) if len(result) > 1 else ''
            else:
                file_name = ''
                position = ''
            
            if file_name and position:
                os.makedirs("./backups", exist_ok=True)
                with open(BINLOG_POS_FILE, 'w') as f:
                    f.write(f"file={file_name}\n")
                    f.write(f"pos={position}\n")
                
                log_info(f"记录 binlog 起始位点: {file_name} @ {position}")
                return file_name, position
        
        log_warn("无法记录 binlog 位点：SHOW MASTER STATUS 返回空结果")
        return None, None
    except Exception as e:
        log_warn(f"无法记录 binlog 位点: {e}")
        return None, None
    finally:
        if conn:
            conn.close()

def clear_all_tables():
    """清空所有表的数据（每次SQL执行后立即提交，显示进度）"""
    log_step("清空所有表的数据（用于验证恢复效果）")
    
    conn = None
    try:
        conn = get_mysql_connection()
        if conn is None:
            log_error("无法连接到数据库")
            return
        
        cursor = conn.cursor()
        
        log_info("开始清空所有表的数据...")
        
        # 先禁用外键检查，确保可以清空所有表
        log_info("禁用外键检查以清空所有表...")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        conn.commit()
        
        # 按照依赖关系清空：先清空有外键的表，再清空被引用的表
        # orders 表有外键引用 customers，所以先清空 orders
        clear_order = ["orders", "audit_logs", "metrics", "inventory", "customers"]
        total_tables = len(clear_order)
        
        for idx, table in enumerate(clear_order, 1):
            try:
                # 先检查表是否存在
                check_sql = f"SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_schema='{MYSQL_DATABASE}' AND table_name='{table}'"
                cursor.execute(check_sql)
                result = cursor.fetchone()
                # 确保结果被完全读取
                if MYSQL_LIB == 'mysql.connector':
                    try:
                        cursor.fetchall()
                    except:
                        pass
                conn.commit()
                
                table_exists = False
                if result:
                    if isinstance(result, dict):
                        table_exists = result.get('cnt', 0) > 0
                    else:
                        table_exists = result[0] > 0
                
                if table_exists:
                    # 获取表中的记录数
                    count_sql = f"SELECT COUNT(*) as cnt FROM {table}"
                    cursor.execute(count_sql)
                    count_result = cursor.fetchone()
                    # 确保结果被完全读取
                    if MYSQL_LIB == 'mysql.connector':
                        try:
                            cursor.fetchall()
                        except:
                            pass
                    conn.commit()
                    # 处理不同库的返回格式
                    count = 0
                    if count_result:
                        if isinstance(count_result, dict):
                            count = count_result.get('cnt', 0)
                        elif isinstance(count_result, (tuple, list)):
                            count = count_result[0] if len(count_result) > 0 else 0
                    log_info(f"[{idx}/{total_tables}] 表 {table} 当前记录数: {count}")
                    
                    # 清空表数据
                    cursor.execute(f"DELETE FROM {table}")
                    conn.commit()
                    
                    # 验证是否清空成功
                    cursor.execute(count_sql)
                    count_result_after = cursor.fetchone()
                    # 确保结果被完全读取
                    if MYSQL_LIB == 'mysql.connector':
                        try:
                            cursor.fetchall()
                        except:
                            pass
                    conn.commit()
                    count_after = 0
                    if count_result_after:
                        if isinstance(count_result_after, dict):
                            count_after = count_result_after.get('cnt', 0)
                        elif isinstance(count_result_after, (tuple, list)):
                            count_after = count_result_after[0] if len(count_result_after) > 0 else 0
                    
                    if count_after == 0:
                        log_success(f"  表 {table} 已清空（清空前: {count}, 清空后: {count_after}）")
                    else:
                        log_warn(f"  表 {table} 清空不完整（清空前: {count}, 清空后: {count_after}）")
                        # 尝试使用 TRUNCATE TABLE
                        log_info(f"  尝试使用 TRUNCATE TABLE 清空表 {table}...")
                        cursor.execute(f"TRUNCATE TABLE {table}")
                        conn.commit()
                else:
                    log_warn(f"[{idx}/{total_tables}] 表 {table} 不存在，跳过")
            except Exception as e:
                log_warn(f"清空表 {table} 失败: {e}")
                conn.rollback()
        
        # 重新启用外键检查
        log_info("重新启用外键检查...")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()
        
        # 验证所有表是否已清空
        log_info("验证所有表是否已清空...")
        all_cleared = True
        total_tables = len(TABLES)
        for idx, table in enumerate(TABLES, 1):
            try:
                count_sql = f"SELECT COUNT(*) as cnt FROM {table}"
                cursor.execute(count_sql)
                count_result = cursor.fetchone()
                # 确保结果被完全读取
                if MYSQL_LIB == 'mysql.connector':
                    try:
                        cursor.fetchall()
                    except:
                        pass
                conn.commit()
                # 处理不同库的返回格式
                count = 0
                if count_result:
                    if isinstance(count_result, dict):
                        count = count_result.get('cnt', 0)
                    elif isinstance(count_result, (tuple, list)):
                        count = count_result[0] if len(count_result) > 0 else 0
                
                if count == 0:
                    log_success(f"  [{idx}/{total_tables}] 表 {table} 已清空（记录数: {count}）")
                else:
                    log_warn(f"  [{idx}/{total_tables}] 表 {table} 仍有数据（记录数: {count}）")
                    all_cleared = False
            except Exception as e:
                log_warn(f"验证表 {table} 失败: {e}")
                all_cleared = False
        
        cursor.close()
        
        if all_cleared:
            log_success("所有表数据已清空，准备进行恢复测试")
        else:
            log_warn("部分表可能仍有数据，但继续执行恢复测试")
    
    except Exception as e:
        log_error(f"清空表数据时发生错误: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def stop_mysql():
    """停止 MySQL 容器"""
    log_step("停止 MySQL 容器以准备恢复")
    run_cmd(["docker", "stop", CONTAINER_NAME])

def run_point_in_time_restore(target_time):
    """执行时间点恢复"""
    log_step("执行时间点恢复")
    
    log_info(f"恢复目标时间: {target_time} (时区: {TZ_REGION})")
    
    # 使用 docker run 执行恢复脚本
    # 通过 main.py 统一入口调用时间点恢复
    # 需要映射必要的卷
    run_cmd([
        "docker", "run", "--rm",
        "-v", f"{os.path.abspath('./backups')}:/backups",
        "-v", f"{os.path.abspath('./mysql_data')}:/var/lib/mysql",
        "-e", f"RESTORE_TZ={TZ_REGION}",
        IMAGE_NAME, "python3", "/scripts/main.py", "restore", "pitr", target_time
    ])
    
    log_success("时间点恢复完成")

def restart_mysql():
    """重启 MySQL 容器"""
    log_step("重新启动 MySQL 容器")
    
    run_cmd(["docker", "start", CONTAINER_NAME])
    wait_for_mysql()

def verify_restore():
    """验证恢复结果"""
    log_step("验证恢复结果")
    
    dump_all_tables(DATA_SNAPSHOT_AFTER)
    restored_md5 = compute_md5(DATA_SNAPSHOT_AFTER)
    log_info(f"恢复后MD5: {restored_md5}")
    
    # 保存恢复后快照内容到单独文件
    with open(DATA_SNAPSHOT_AFTER, 'r', encoding='utf-8') as f:
        snapshot_content = f.read()
    with open(RESTORED_SNAPSHOT_CONTENT, 'w', encoding='utf-8') as f:
        f.write(f"MD5: {restored_md5}\n")
        f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n")
        f.write("Snapshot Content:\n")
        f.write("=" * 80 + "\n")
        f.write(snapshot_content)
    log_info(f"恢复后快照内容已保存到: {RESTORED_SNAPSHOT_CONTENT}")
    
    if os.path.exists(DATA_SNAPSHOT_BETWEEN):
        baseline_md5 = compute_md5(DATA_SNAPSHOT_BETWEEN)
        log_info(f"基准MD5: {baseline_md5}")
        
        if restored_md5 == baseline_md5:
            log_success("MD5 校验一致，恢复测试通过")
            log_info(f"基准快照内容: {BASELINE_SNAPSHOT_CONTENT}")
            log_info(f"恢复后快照内容: {RESTORED_SNAPSHOT_CONTENT}")
            return True
        else:
            log_error("MD5 不一致，恢复失败")
            log_info(f"基准快照内容: {BASELINE_SNAPSHOT_CONTENT}")
            log_info(f"恢复后快照内容: {RESTORED_SNAPSHOT_CONTENT}")
            log_info("请对比两个快照文件以查看差异")
            return False
    else:
        log_error("未找到基准快照文件")
        return False

def main():
    """主测试流程"""
    log_step("========== 开始测试：两次增量备份之间的 PITR 恢复 ==========")
    
    # 步骤 0: 清理环境
    cleanup_environment()
    
    # 步骤 1: 重新构建镜像
    build_image()
    
    # 步骤 2: 启动环境
    start_environment()
    
    # 步骤 3: 创建测试表并初始化数据
    create_tables_and_seed()
    
    # 步骤 4: 执行全量备份
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)
    perform_full_backup()
    
    # 步骤 5: 执行随机操作
    log_step("5. 随机操作阶段 #1（50 次）")
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)
    random_operations(50)
    
    # 步骤 6: 执行增量备份 #1
    log_step("6. 执行增量备份 #1")
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)
    perform_incremental_backup()
    
    # 步骤 7: 执行随机操作
    log_step("7. 随机操作阶段 #2（50 次）")
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)
    random_operations(50)
    
    # 步骤 8: 记录数据快照和 MD5（这是要恢复到的状态）
    log_step("8. 生成数据快照并计算 MD5（两次增量备份之间）")
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)

    # 记录时间点（在数据快照之前，确保时间点准确）
    target_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_info(f"记录时间点(本地): {target_time}")
    log_info("注意: 恢复脚本会在目标时间基础上加1秒，以确保包含该时间点的所有操作")

    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)

    os.makedirs("./backups", exist_ok=True)
    dump_all_tables(DATA_SNAPSHOT_BETWEEN)
    baseline_md5 = compute_md5(DATA_SNAPSHOT_BETWEEN)
    log_info(f"快照MD5: {baseline_md5}")

    # 保存基准快照内容到单独文件
    with open(DATA_SNAPSHOT_BETWEEN, 'r', encoding='utf-8') as f:
        snapshot_content = f.read()
    with open(BASELINE_SNAPSHOT_CONTENT, 'w', encoding='utf-8') as f:
        f.write(f"MD5: {baseline_md5}\n")
        f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n")
        f.write("Snapshot Content:\n")
        f.write("=" * 80 + "\n")
        f.write(snapshot_content)
    log_info(f"基准快照内容已保存到: {BASELINE_SNAPSHOT_CONTENT}")
    
    # 步骤 9: 执行增量备份 #2
    log_step("9. 执行增量备份 #2")
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)
    perform_incremental_backup()
    
    # 步骤 10: 执行更多操作（模拟误操作）
    log_step("10. 模拟误操作（更新/删除 20 次）")
    log_info("等待 3 秒以确保时间点区别明显...")
    time.sleep(3)
    random_operations(20)
    log_warn("已模拟误操作（删除/更新）")
    
    # 步骤 11: 停止 MySQL
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)
    stop_mysql()
    
    # 步骤 11.5: 清空所有表的数据（用于验证恢复效果）
    # 注意：这里需要先启动 MySQL 才能清空数据
    log_step("11.5. 清空所有表数据（用于验证恢复效果）")
    log_info("重新启动 MySQL 以清空数据...")
    run_cmd(["docker", "start", CONTAINER_NAME])
    wait_for_mysql()
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)
    clear_all_tables()
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)
    stop_mysql()
    
    # 步骤 12: 执行时间点恢复
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)
    run_point_in_time_restore(target_time)
    
    # 步骤 13: 重启 MySQL
    log_info("等待 3 秒以确保时间点准确...")
    time.sleep(3)
    restart_mysql()
    
    # 步骤 14: 验证恢复结果
    log_info("等待 3 秒以确保 MySQL 完全启动...")
    time.sleep(3)
    success = verify_restore()
    
    # 显示可用命令
    # log_step("显示可用命令")
    # cmd = ["docker-compose", "exec", "-T", "mysql", "python3", "/scripts/main.py", "help"]
    # log_info(f"[命令] {' '.join(cmd)}")
    # print("")
    # run_cmd(cmd, check=False)
    # print("")
    
    if success:
        log_success("========== 测试通过 ==========")
        return 0
    else:
        log_error("========== 测试失败 ==========")
        return 1

if __name__ == "__main__":
    sys.exit(main())

