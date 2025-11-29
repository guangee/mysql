#!/usr/bin/env python3
"""
test2.py - MySQL 备份恢复测试脚本（Python版本）

测试流程：
1. 重新构建镜像
2. 初始化环境
3. 创建测试表并初始化数据
4. 全量备份
5. 随机操作并增量备份
6. 记录数据快照和MD5
7. 模拟误操作
8. 执行时间点恢复
9. 验证恢复结果
"""

import os
import sys
import subprocess
import hashlib
import time
import random
import string
from datetime import datetime
from pathlib import Path

# 配置
CONTAINER_NAME = "mysql8035"
MYSQL_ROOT_PASSWORD = "rootpassword"
MYSQL_DATABASE = "testdb"
MYSQL_PORT = "3307"
IMAGE_NAME = "zziaguan/mysql:8.0.35"
TZ_REGION = "Asia/Shanghai"

TABLES = ["customers", "orders", "inventory", "audit_logs", "metrics"]

DATA_SNAPSHOT_BEFORE = "./backups/test2_snapshot_before.txt"
DATA_SNAPSHOT_AFTER = "./backups/test2_snapshot_after.txt"
BINLOG_POS_FILE = "./backups/binlog_base_position.txt"

# 颜色输出
class Colors:
    BLUE = '\033[0;34m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    NC = '\033[0m'

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

def random_word():
    """生成随机单词"""
    return ''.join(random.choices(string.ascii_lowercase, k=6))

def random_sentence():
    """生成随机句子"""
    return random_word() + random_word() + random_word()

def mysql_exec(sql):
    """执行SQL命令"""
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

def wait_for_mysql():
    """等待MySQL启动"""
    log_info("等待 MySQL 就绪...")
    for _ in range(60):
        cmd = [
            "docker", "exec", CONTAINER_NAME,
            "mysqladmin", "ping", "-h", "127.0.0.1", "-u", "root",
            f"-p{MYSQL_ROOT_PASSWORD}", "--silent"
        ]
        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode == 0:
            log_success("MySQL 已启动")
            time.sleep(3)
            return
        time.sleep(2)
    log_error("MySQL 启动超时")
    sys.exit(1)

def build_image():
    """重新构建Docker镜像"""
    log_step("0. 重新构建 Docker 镜像")
    log_info(f"构建镜像: {IMAGE_NAME}")
    cmd = ["docker", "build", "-t", IMAGE_NAME, "."]
    log_info(f"[命令] {' '.join(cmd)}")
    if subprocess.run(cmd, check=False).returncode == 0:
        log_success("镜像构建成功")
    else:
        log_error("镜像构建失败")
        sys.exit(1)

def start_environment():
    """初始化环境"""
    log_step("1. 初始化环境")
    
    log_info("停止并删除容器...")
    cmd = ["docker-compose", "down", "-v"]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, capture_output=True, check=False)
    
    log_info("清理数据目录...")
    mysql_data = Path("./mysql_data")
    if mysql_data.exists():
        log_info("清理mysql_data目录内容（保留目录）...")
        for item in mysql_data.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                import shutil
                shutil.rmtree(item)
        log_success("mysql_data目录内容已清理")
    else:
        mysql_data.mkdir(parents=True, exist_ok=True)
        log_success("已创建 mysql_data 目录")
    
    log_info("清理备份目录...")
    backups = Path("./backups")
    if backups.exists():
        log_info("清理backups目录内容（保留目录）...")
        for item in backups.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                import shutil
                shutil.rmtree(item)
        log_success("backups目录内容已清理")
    else:
        backups.mkdir(parents=True, exist_ok=True)
        log_success("已创建 backups 目录")
    
    Path("./mysql_data").mkdir(parents=True, exist_ok=True)
    Path("./backups").mkdir(parents=True, exist_ok=True)
    log_info("目录清理完成，保留目录结构以维持Docker volume映射")
    
    log_info("启动容器...")
    cmd = ["docker-compose", "up", "-d"]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    wait_for_mysql()

def create_tables_and_seed():
    """创建测试表并初始化数据"""
    log_step("2. 创建 5 张测试表并初始化数据")
    
    sql = """
CREATE TABLE IF NOT EXISTS customers (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100),
  email VARCHAR(150),
  loyalty_points INT DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS orders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  customer_id INT,
  amount DECIMAL(10,2),
  status VARCHAR(20),
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS inventory (
  id INT AUTO_INCREMENT PRIMARY KEY,
  item_name VARCHAR(120),
  quantity INT,
  last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS audit_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  message TEXT,
  severity VARCHAR(20),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS metrics (
  id INT AUTO_INCREMENT PRIMARY KEY,
  metric_key VARCHAR(100),
  metric_value DECIMAL(12,4),
  captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
"""
    
    cmd = [
        "docker", "exec", "-i", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE
    ]
    subprocess.run(cmd, input=sql, text=True, check=True)
    
    # 添加初始数据
    for i in range(1, 21):
        name = f"user{i:03d}"
        email = f"{name}@example.com"
        mysql_exec(f"INSERT INTO customers (name, email, loyalty_points) VALUES ('{name}', '{email}', FLOOR(RAND()*500));")
    
    for i in range(1, 21):
        mysql_exec(f"INSERT INTO inventory (item_name, quantity) VALUES ('item_{i}', FLOOR(RAND()*200));")
    
    for i in range(1, 21):
        mysql_exec(f"INSERT INTO audit_logs (message, severity) VALUES ('initial log {i}', 'INFO');")
        mysql_exec(f"INSERT INTO metrics (metric_key, metric_value) VALUES ('metric_{i}', RAND()*1000);")
    
    random_operations(50)
    log_success("基础数据准备完成")

def insert_into_table(table):
    """插入数据到指定表"""
    if table == "customers":
        name = f"{random_word()}_{random_word()}"
        email = f"{name}@example.com"
        mysql_exec(f"INSERT INTO customers (name, email, loyalty_points) VALUES ('{name}', '{email}', FLOOR(RAND()*1000));")
    elif table == "orders":
        mysql_exec("INSERT INTO orders (customer_id, amount, status) SELECT id, ROUND(RAND()*8000,2), CASE FLOOR(RAND()*3) WHEN 0 THEN 'pending' WHEN 1 THEN 'paid' ELSE 'shipped' END FROM customers ORDER BY RAND() LIMIT 1;")
    elif table == "inventory":
        mysql_exec(f"INSERT INTO inventory (item_name, quantity) VALUES ('{random_word()}_item', FLOOR(RAND()*300));")
    elif table == "audit_logs":
        mysql_exec(f"INSERT INTO audit_logs (message, severity) VALUES ('{random_sentence()}', CASE FLOOR(RAND()*3) WHEN 0 THEN 'INFO' WHEN 1 THEN 'WARN' ELSE 'ERROR' END);")
    elif table == "metrics":
        mysql_exec(f"INSERT INTO metrics (metric_key, metric_value) VALUES ('{random_word()}_metric', RAND()*10000);")

def update_table(table):
    """更新指定表的数据"""
    if table == "customers":
        mysql_exec("UPDATE customers SET loyalty_points = loyalty_points + FLOOR(RAND()*50), name = CONCAT(name, '_u') ORDER BY RAND() LIMIT 1;")
    elif table == "orders":
        mysql_exec("UPDATE orders SET amount = amount + ROUND(RAND()*200,2), status = CASE FLOOR(RAND()*3) WHEN 0 THEN 'pending' WHEN 1 THEN 'paid' ELSE 'shipped' END, updated_at = NOW() ORDER BY RAND() LIMIT 1;")
    elif table == "inventory":
        mysql_exec("UPDATE inventory SET quantity = GREATEST(quantity + FLOOR(RAND()*20) - 10, 0), last_check = NOW() ORDER BY RAND() LIMIT 1;")
    elif table == "audit_logs":
        mysql_exec("UPDATE audit_logs SET message = CONCAT(message, '_update'), severity = CASE severity WHEN 'INFO' THEN 'WARN' ELSE 'INFO' END ORDER BY RAND() LIMIT 1;")
    elif table == "metrics":
        mysql_exec("UPDATE metrics SET metric_value = metric_value + RAND()*10, captured_at = NOW() ORDER BY RAND() LIMIT 1;")

def random_operations(total):
    """执行随机操作"""
    for i in range(1, total + 1):
        table = random.choice(TABLES)
        if random.randint(0, 1):
            insert_into_table(table)
        else:
            update_table(table)
    log_info(f"执行随机操作 {total} 次完成")

def delete_from_table(table):
    """从指定表删除数据"""
    if table == "customers":
        mysql_exec("SET @cid := (SELECT id FROM customers ORDER BY RAND() LIMIT 1); DELETE FROM orders WHERE customer_id = @cid; DELETE FROM customers WHERE id = @cid;")
    elif table == "orders":
        mysql_exec("DELETE FROM orders ORDER BY RAND() LIMIT 1;")
    elif table == "inventory":
        mysql_exec("DELETE FROM inventory ORDER BY RAND() LIMIT 1;")
    elif table == "audit_logs":
        mysql_exec("DELETE FROM audit_logs ORDER BY RAND() LIMIT 1;")
    elif table == "metrics":
        mysql_exec("DELETE FROM metrics ORDER BY RAND() LIMIT 1;")

def perform_full_backup():
    """执行全量备份"""
    log_step("3. 执行全量备份")
    cmd = ["docker-compose", "exec", "-T", "mysql", "python3", "/scripts/main.py", "backup", "full"]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    log_success("全量备份完成")

def perform_incremental_backup():
    """执行增量备份"""
    log_step("4. 执行增量备份")
    cmd = ["docker-compose", "exec", "-T", "mysql", "python3", "/scripts/main.py", "backup", "incremental"]
    log_info(f"[命令] {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        log_warn("增量备份返回非零，继续流程")

def dump_all_tables(output_file):
    """导出所有表的数据"""
    with open(output_file, 'w') as f:
        for table in TABLES:
            f.write(f"TABLE:{table}\n")
            # 检查表是否存在
            check_sql = f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{MYSQL_DATABASE}' AND table_name='{table}';"
            cmd = [
                "docker", "exec", CONTAINER_NAME,
                "mysql", "-h", "127.0.0.1", "-u", "root",
                f"-p{MYSQL_ROOT_PASSWORD}", "--batch", "--skip-column-names",
                MYSQL_DATABASE, "-e", check_sql
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            table_exists = result.stdout.strip().replace('\r', '').replace('\n', '') if result.stdout else "0"
            
            if table_exists == "1":
                dump_sql = f"SELECT * FROM {table} ORDER BY 1;"
                cmd = [
                    "docker", "exec", CONTAINER_NAME,
                    "mysql", "-h", "127.0.0.1", "-u", "root",
                    f"-p{MYSQL_ROOT_PASSWORD}", "--batch", "--skip-column-names",
                    MYSQL_DATABASE, "-e", dump_sql
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if result.stdout:
                    for line in result.stdout.split('\n'):
                        if line.strip() and not line.startswith('Warning'):
                            f.write(line.replace('\t', '|') + '\n')
            else:
                f.write("TABLE_NOT_EXISTS\n")

def compute_md5(file_path):
    """计算文件的MD5"""
    md5_hash = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()

def record_snapshot():
    """记录数据快照并计算MD5"""
    log_step("5. 生成数据快照并计算 MD5")
    Path("./backups").mkdir(parents=True, exist_ok=True)
    dump_all_tables(DATA_SNAPSHOT_BEFORE)
    global BASELINE_MD5, PITR_TARGET_TIME, PITR_TARGET_EPOCH, PITR_TARGET_TIME_UTC, PITR_STOP_TIME_UTC
    BASELINE_MD5 = compute_md5(DATA_SNAPSHOT_BEFORE)
    
    # 使用本地时区记录目标时间
    os.environ['TZ'] = TZ_REGION
    time.tzset()
    PITR_TARGET_TIME = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    PITR_TARGET_EPOCH = int(datetime.now().timestamp())
    
    # 计算 UTC 时间（使用 utcfromtimestamp，不依赖 TZ 环境变量）
    PITR_TARGET_TIME_UTC = datetime.utcfromtimestamp(PITR_TARGET_EPOCH).strftime('%Y-%m-%d %H:%M:%S')
    PITR_STOP_TIME_UTC = datetime.utcfromtimestamp(PITR_TARGET_EPOCH + 1).strftime('%Y-%m-%d %H:%M:%S')
    
    # 恢复时区设置
    os.environ['TZ'] = TZ_REGION
    time.tzset()
    
    log_info(f"快照MD5: {BASELINE_MD5}")
    log_info(f"记录时间点(本地): {PITR_TARGET_TIME}")
    log_info(f"记录时间点(UTC): {PITR_TARGET_TIME_UTC}")

def record_binlog_position():
    """记录binlog起始位点"""
    log_step("记录 binlog 起始位点")
    Path("./backups").mkdir(parents=True, exist_ok=True)
    
    # 尝试多次，因为 binlog 可能需要一些时间才能写入
    max_retries = 5
    for attempt in range(max_retries):
        cmd = [
            "docker", "exec", CONTAINER_NAME,
            "mysql", "-h", "127.0.0.1", "-u", "root",
            f"-p{MYSQL_ROOT_PASSWORD}", "-AN", "-e", "SHOW MASTER STATUS;"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        if result.returncode != 0:
            log_warn(f"SHOW MASTER STATUS 执行失败 (尝试 {attempt + 1}/{max_retries}): {result.stderr}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            else:
                log_error("SHOW MASTER STATUS 执行失败")
                sys.exit(1)
        
        output = result.stdout.strip()
        if not output:
            log_warn(f"SHOW MASTER STATUS 结果为空 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                # 执行一个写操作，确保 binlog 有内容
                log_info("执行一个写操作以触发 binlog 写入...")
                mysql_exec("SELECT 1;")
                time.sleep(1)
                continue
            else:
                log_error("SHOW MASTER STATUS 结果为空，binlog 可能未启用或未写入")
                sys.exit(1)
        
        # 解析输出（使用 -AN 参数时，输出只有数据行，没有表头）
        lines = [line for line in output.split('\n') if line.strip()]
        if not lines:
            log_warn(f"SHOW MASTER STATUS 输出为空行 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            else:
                log_error("SHOW MASTER STATUS 输出为空行")
                sys.exit(1)
        
        # 取第一行数据（使用 -AN 时通常只有一行）
        data_line = lines[0] if lines else ""
        parts = data_line.split('\t')
        
        if len(parts) < 2:
            log_warn(f"无法解析 binlog 位点信息 (尝试 {attempt + 1}/{max_retries}): {data_line}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            else:
                log_error(f"无法解析 binlog 位点信息: {data_line}")
                sys.exit(1)
        
        file = parts[0].strip()
        pos = parts[1].strip()
        
        if not file or not pos:
            log_warn(f"binlog 文件名或位置为空 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            else:
                log_error("binlog 文件名或位置为空")
                sys.exit(1)
        
        # 成功获取到 binlog 位点
        with open(BINLOG_POS_FILE, 'w') as f:
            f.write(f"file={file}\n")
            f.write(f"pos={pos}\n")
            f.write(f"recorded_at={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        log_info(f"记录 binlog 起始位点: {file} @ {pos}")
        return
    
    # 如果所有重试都失败
    log_error("无法获取 binlog 位点，所有重试都失败")
    sys.exit(1)

def simulate_misoperations():
    """模拟误操作"""
    log_step("6. 模拟误操作（更新/删除 10 次）")
    log_info("在执行误操作前等待 2 秒，确保时间点区别明显...")
    time.sleep(2)
    for i in range(1, 11):
        table = random.choice(TABLES)
        if random.randint(0, 1):
            try:
                delete_from_table(table)
            except Exception:
                log_warn(f"删除 {table} 时出现问题（可能由于外键约束），已跳过")
        else:
            try:
                update_table(table)
            except Exception:
                log_warn(f"更新 {table} 时出现问题，已跳过")
    log_warn("已模拟误操作（删除/更新）")

def stop_mysql():
    """停止MySQL容器"""
    log_step("7. 停止 MySQL 容器以准备恢复")
    cmd = ["docker-compose", "down"]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, capture_output=True, check=False)

def run_point_in_time_restore():
    """执行时间点恢复"""
    log_step("8. 执行时间点恢复")
    
    # 使用 docker-compose run --rm 执行恢复脚本，不自己写逻辑
    log_info(f"恢复目标时间: {PITR_TARGET_TIME} (时区: {TZ_REGION})")
    cmd = [
        "docker-compose", "run", "--rm",
        "-e", f"RESTORE_TZ={TZ_REGION}",
        "mysql", "python3", "/scripts/main.py", "restore", "pitr", PITR_TARGET_TIME
    ]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    
    # 等待一下，确保文件系统同步完成（docker-compose run --rm 容器删除后，volume 中的文件需要时间同步）
    log_info("等待文件系统同步完成...")
    time.sleep(2)
    
    # 验证标记文件是否已创建（在宿主机上）
    marker_file = Path("./backups/.pitr_restore_marker")
    if marker_file.exists():
        log_success(f"✓ 标记文件已创建: {marker_file}")
        log_info(f"  标记文件内容: {marker_file.read_text().strip()}")
    else:
        log_warn(f"⚠ 标记文件在宿主机上不存在: {marker_file}")
        log_warn("  这可能是文件系统同步延迟，将在容器启动后由 docker-entrypoint.sh 自动应用")
    
    log_success("时间点恢复完成")

def check_backups_directory():
    """检查backups目录下的文件列表"""
    log_step("9. 检查backups目录（在重启MySQL之前）")
    
    log_info("检查宿主机backups目录第一层文件列表...")
    backups_dir = Path("./backups")
    if backups_dir.exists():
        log_info(f"backups目录路径: {backups_dir.absolute()}")
        items = list(backups_dir.iterdir())
        if items:
            log_info(f"找到 {len(items)} 个项目:")
            for item in sorted(items):
                item_type = "目录" if item.is_dir() else "文件"
                size_info = ""
                if item.is_file():
                    try:
                        size = item.stat().st_size
                        if size < 1024:
                            size_info = f" ({size} 字节)"
                        elif size < 1024 * 1024:
                            size_info = f" ({size / 1024:.2f} KB)"
                        else:
                            size_info = f" ({size / (1024 * 1024):.2f} MB)"
                    except:
                        pass
                log_info(f"  [{item_type}] {item.name}{size_info}")
            
            # 特别检查标记文件
            marker_file = backups_dir / ".pitr_restore_marker"
            if marker_file.exists():
                log_success(f"✓ 标记文件存在: {marker_file}")
                try:
                    marker_content = marker_file.read_text().strip()
                    log_info(f"  标记文件内容（容器内路径）: {marker_content}")
                    
                    # 检查SQL文件是否存在
                    # 标记文件中存储的是容器内的绝对路径（如 /backups/xxx.sql）
                    # 需要转换为宿主机的相对路径（如 ./backups/xxx.sql）
                    if marker_content.startswith("/backups/"):
                        # 提取文件名
                        sql_filename = marker_content.replace("/backups/", "")
                        sql_file = backups_dir / sql_filename
                        log_info(f"  转换后的宿主机路径: {sql_file}")
                    else:
                        # 如果已经是相对路径或其他格式，直接使用
                        sql_file = Path(marker_content)
                    
                    if sql_file.exists():
                        sql_size = sql_file.stat().st_size
                        log_success(f"  ✓ SQL文件存在: {sql_file} ({sql_size} 字节)")
                    else:
                        log_warn(f"  ⚠ SQL文件不存在: {sql_file}")
                        log_warn(f"    尝试查找同名文件...")
                        # 尝试在backups目录下查找同名文件
                        if sql_file.name in [f.name for f in backups_dir.iterdir() if f.is_file()]:
                            log_info(f"    找到同名文件: {backups_dir / sql_file.name}")
                        else:
                            log_warn(f"    未找到同名文件")
                except Exception as e:
                    log_warn(f"  无法读取标记文件内容: {e}")
                    import traceback
                    log_warn(f"  错误详情: {traceback.format_exc()}")
            else:
                log_warn(f"⚠ 标记文件不存在: {marker_file}")
        else:
            log_warn("backups目录为空")
    else:
        log_warn("backups目录不存在")
    
    # 也检查容器内的backups目录（如果容器还在运行）
    log_info("检查容器内backups目录第一层文件列表...")
    cmd = ["docker", "exec", CONTAINER_NAME, "ls", "-lah", "/backups/"]
    log_info(f"[命令] {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        log_info("容器内backups目录内容:")
        for line in result.stdout.split('\n'):
            if line.strip():
                log_info(f"  {line}")
    else:
        log_warn("无法检查容器内backups目录（容器可能未运行）")
    
    # 标记文件检查完成（容器启动时会自动应用，无需手动处理）
    log_info("")
    log_info("注意: 标记文件已创建，容器启动时会自动应用二进制日志")

def restart_mysql():
    """重新启动MySQL容器"""
    log_step("10. 重新启动 MySQL 容器")
    cmd = ["docker-compose", "up", "-d"]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    wait_for_mysql()
    
    # 等待一下，让 docker-entrypoint.sh 有时间自动应用二进制日志
    log_info("等待容器自动应用二进制日志（docker-entrypoint.sh 会在后台自动处理）...")
    time.sleep(10)
    
    # 简单检查标记文件状态（仅用于日志记录，不影响流程）
    marker_file = Path("./backups/.pitr_restore_marker")
    if marker_file.exists():
        log_info(f"标记文件仍然存在（可能正在处理中）: {marker_file}")
    else:
        log_info("标记文件已不存在（可能已被 docker-entrypoint.sh 自动应用并删除）")
    
    log_info("注意: 标记文件由容器自动处理，测试脚本主要关注最终恢复状态验证")

def apply_binlog_events():
    """应用二进制日志至目标时间点"""
    log_step("11. 检查二进制日志应用状态")
    
    # 注意: docker-entrypoint.sh 会在容器启动时自动应用二进制日志
    # 这里只做检查，不重复应用
    log_info("检查标记文件状态（docker-entrypoint.sh 应该已经自动处理）...")
    marker_file = Path("./backups/.pitr_restore_marker")
    
    if marker_file.exists():
        log_info("标记文件仍然存在，尝试手动应用...")
        # 如果标记文件还存在，说明可能没有被自动应用，尝试手动应用
        cmd = [
            "docker-compose", "exec", "-T", "mysql",
            "python3", "/scripts/main.py", "binlog", "apply-pitr"
        ]
        log_info(f"[命令] {' '.join(cmd)}")
        result = subprocess.run(cmd, check=False)
        
        if result.returncode == 0:
            log_success("二进制日志应用完成")
        else:
            log_warn("二进制日志应用可能失败，但继续验证恢复状态")
    else:
        log_info("标记文件不存在，说明二进制日志可能已被自动应用")
        log_info("继续验证恢复状态...")

def verify_restore():
    """验证恢复结果"""
    log_step("12. 验证恢复结果")
    dump_all_tables(DATA_SNAPSHOT_AFTER)
    RESTORED_MD5 = compute_md5(DATA_SNAPSHOT_AFTER)
    log_info(f"恢复后MD5: {RESTORED_MD5}")
    if RESTORED_MD5 == BASELINE_MD5:
        log_success("MD5 校验一致，恢复测试通过")
        return 0
    else:
        log_error("MD5 不一致，恢复失败")
        return 1

# 全局变量
BASELINE_MD5 = None
PITR_TARGET_TIME = None
PITR_TARGET_EPOCH = None
PITR_TARGET_TIME_UTC = None
PITR_STOP_TIME_UTC = None

def main():
    """主函数"""
    build_image()
    start_environment()
    create_tables_and_seed()
    
    perform_full_backup()
    
    log_step("随机操作阶段 #1（100 次）")
    random_operations(100)
    
    perform_incremental_backup()
    record_binlog_position()
    
    log_step("随机操作阶段 #2（100 次）")
    random_operations(100)
    
    record_snapshot()
    
    simulate_misoperations()
    stop_mysql()
    run_point_in_time_restore()
    check_backups_directory()
    restart_mysql()
    apply_binlog_events()
    result = verify_restore()
    
    # 显示可用命令
    # log_step("显示可用命令")
    # cmd = ["docker-compose", "exec", "-T", "mysql", "python3", "/scripts/main.py", "help"]
    # log_info(f"[命令] {' '.join(cmd)}")
    # print("")
    # subprocess.run(cmd, check=False)
    # print("")
    
    return result

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-existing-tables":
        # 表已存在场景测试（简化版本，完整版本需要更多代码）
        log_info("表已存在场景测试需要更多实现，请使用原 test2.sh 的完整版本")
        sys.exit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "--test-all":
        log_info("========== 执行完整测试流程 ==========")
        result = main()
        if result == 0:
            log_info("")
            log_info("========== 执行表已存在场景测试 ==========")
            log_info("表已存在场景测试需要更多实现，请使用原 test2.sh 的完整版本")
            sys.exit(1)
        else:
            log_error("完整测试流程失败，跳过表已存在场景测试")
            sys.exit(1)
    else:
        result = main()
        sys.exit(0 if result == 0 else 1)

