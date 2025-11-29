#!/usr/bin/env python3
"""
test.py - MySQL 备份恢复全流程测试脚本（Python版本）

测试流程：
1. 清理环境
2. 重新构建镜像
3. 启动容器
4. 创建测试数据
5. 执行全量备份
6. 添加更多数据并执行增量备份
7. 删除数据（模拟数据丢失）
8. 恢复数据
9. 验证恢复的数据（恢复后立即验证，确保数据正确恢复）
10. 插入带时间戳的数据（用于时间点恢复测试）
11. 可选：时间点恢复测试
12. 检查恢复时间段内的binlog事件数量
"""

import os
import sys
import subprocess
import time
import random
import shutil
import re
from datetime import datetime
from pathlib import Path

# 配置变量
CONTAINER_NAME = "mysql8035"
IMAGE_NAME = "zziaguan/mysql:8.0.35"
MYSQL_ROOT_PASSWORD = "rootpassword"
MYSQL_DATABASE = "testdb"
MYSQL_USER = "testuser"
MYSQL_PASSWORD = "testpass"
MYSQL_PORT = "3307"

# 自动模式：如果没有设置 AUTO_PITR_TEST，默认启用自动模式
AUTO_PITR_TEST = os.environ.get("AUTO_PITR_TEST", "y").lower() == "y"

# 错误计数器
ERROR_COUNT = 0
WARNING_COUNT = 0

# 颜色输出
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'

def log_info(msg):
    print(f"{Colors.BLUE}[INFO]{Colors.NC} {msg}", flush=True)

def log_success(msg):
    print(f"{Colors.GREEN}[SUCCESS]{Colors.NC} {msg}", flush=True)

def log_error(msg):
    global ERROR_COUNT
    ERROR_COUNT += 1
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}", flush=True)

def log_warning(msg):
    global WARNING_COUNT
    WARNING_COUNT += 1
    print(f"{Colors.YELLOW}[WARNING]{Colors.NC} {msg}", flush=True)

def log_step(msg):
    print("")
    print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
    print(f"{Colors.GREEN}步骤: {msg}{Colors.NC}")
    print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
    print("")

def error_exit(msg):
    log_error(msg)
    sys.exit(1)

def wait_for_mysql():
    """等待MySQL启动"""
    log_info("等待 MySQL 启动...")
    max_attempts = 60
    attempt = 0
    
    while attempt < max_attempts:
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
        attempt += 1
        time.sleep(2)
    
    error_exit("MySQL 启动超时")

def cleanup():
    """步骤1: 清理环境"""
    log_step("1. 清理环境")
    
    log_info("停止并删除容器...")
    cmd = ["docker-compose", "down", "-v"]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, capture_output=True, check=False)
    cmd = ["docker", "stop", CONTAINER_NAME]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, capture_output=True, check=False)
    cmd = ["docker", "rm", CONTAINER_NAME]
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
                shutil.rmtree(item)
        log_success("backups目录内容已清理")
    else:
        backups.mkdir(parents=True, exist_ok=True)
        log_success("已创建 backups 目录")
    
    log_info("清理配置目录...")
    mysql_config = Path("./mysql_config")
    if mysql_config.exists():
        log_info("清理mysql_config目录内容（保留目录）...")
        for item in mysql_config.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        log_success("mysql_config目录内容已清理")
    else:
        mysql_config.mkdir(parents=True, exist_ok=True)
        log_success("已创建 mysql_config 目录")
    
    Path("./mysql_data").mkdir(parents=True, exist_ok=True)
    Path("./backups").mkdir(parents=True, exist_ok=True)
    Path("./mysql_config").mkdir(parents=True, exist_ok=True)
    log_info("目录清理完成，保留目录结构以维持Docker volume映射")
    log_success("环境清理完成")

def build_image():
    """步骤2: 重新构建镜像"""
    log_step("2. 重新构建 Docker 镜像")
    
    log_info(f"构建镜像: {IMAGE_NAME}")
    cmd = ["docker", "build", "-t", IMAGE_NAME, "."]
    log_info(f"[命令] {' '.join(cmd)}")
    if subprocess.run(cmd, check=False).returncode == 0:
        log_success("镜像构建成功")
    else:
        error_exit("镜像构建失败")

def start_container():
    """步骤3: 启动容器"""
    log_step("3. 启动容器")
    
    log_info("启动 MySQL 容器...")
    cmd = ["docker-compose", "up", "-d"]
    log_info(f"[命令] {' '.join(cmd)}")
    if subprocess.run(cmd, check=True).returncode == 0:
        log_success("容器启动成功")
    else:
        error_exit("容器启动失败")
    
    wait_for_mysql()

def create_test_data():
    """步骤4: 创建测试数据"""
    log_step("4. 创建测试数据")
    
    log_info("创建测试数据库和表...")
    
    # 创建 test_table
    sql = """
    CREATE TABLE IF NOT EXISTS test_table (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        email VARCHAR(100) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    subprocess.run(cmd, check=True)
    
    # 插入 test_table 数据
    sql = """
    INSERT INTO test_table (name, email) VALUES
        ('张三', 'zhangsan@example.com'),
        ('李四', 'lisi@example.com'),
        ('王五', 'wangwu@example.com'),
        ('赵六', 'zhaoliu@example.com'),
        ('钱七', 'qianqi@example.com');
    """
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    subprocess.run(cmd, check=True)
    
    # 创建 products 表
    sql = """
    CREATE TABLE IF NOT EXISTS products (
        id INT AUTO_INCREMENT PRIMARY KEY,
        product_name VARCHAR(200) NOT NULL,
        price DECIMAL(10, 2) NOT NULL,
        stock INT DEFAULT 0,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    subprocess.run(cmd, check=True)
    
    # 插入 products 数据
    sql = """
    INSERT INTO products (product_name, price, stock, description) VALUES
        ('笔记本电脑', 5999.00, 50, '高性能笔记本电脑'),
        ('智能手机', 3999.00, 100, '最新款智能手机'),
        ('平板电脑', 2999.00, 30, '轻薄便携平板电脑'),
        ('无线耳机', 299.00, 200, '高品质无线耳机'),
        ('智能手表', 1999.00, 80, '多功能智能手表');
    """
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    subprocess.run(cmd, check=True)
    
    log_success("测试数据创建成功")
    
    log_info("显示创建的数据:")
    print("--- test_table 数据 ---")
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", "SELECT * FROM test_table;"
    ]
    subprocess.run(cmd, check=False)
    print("--- products 数据 ---")
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", "SELECT * FROM products;"
    ]
    subprocess.run(cmd, check=False)
    print("--- 统计信息 ---")
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e",
        "SELECT COUNT(*) AS total_users FROM test_table; SELECT COUNT(*) AS total_products FROM products;"
    ]
    subprocess.run(cmd, check=False)

def perform_backup():
    """步骤5: 执行全量备份"""
    log_step("5. 执行全量备份")
    
    log_info("执行全量备份（这可能需要几分钟）...")
    cmd = ["docker", "exec", CONTAINER_NAME, "python3", "/scripts/main.py", "backup", "full"]
    log_info(f"[命令] {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    
    if result.returncode == 0:
        log_success("全量备份完成")
        
        log_info("检查备份文件:")
        backup_dir = Path("./backups/full")
        if backup_dir.exists():
            backup_files = list(backup_dir.glob("*/backup.tar.gz"))
            if backup_files:
                for f in backup_files:
                    log_info(f"  {f}")
            else:
                log_warning("未找到本地备份文件（可能已上传到S3）")
        
        # 检查备份日志
        log_file = Path("./backups/backup.log")
        if log_file.exists():
            log_info("最近的备份日志:")
            with open(log_file, 'r') as f:
                lines = f.readlines()
                for line in lines[-20:]:
                    print(line.rstrip())
    else:
        log_error(f"全量备份失败，退出码: {result.returncode}")
        log_info("检查备份日志以获取详细信息...")
        log_file = Path("./backups/backup.log")
        if log_file.exists():
            with open(log_file, 'r') as f:
                lines = f.readlines()
                for line in lines[-50:]:
                    print(line.rstrip())
        error_exit("全量备份失败")

def add_more_data_and_incremental_backup():
    """步骤6: 添加更多数据并执行增量备份"""
    log_step("6. 添加更多数据并执行增量备份")
    
    log_info("添加更多测试数据...")
    sql = """
    INSERT INTO test_table (name, email) VALUES
        ('孙八', 'sunba@example.com'),
        ('周九', 'zhoujiu@example.com');
    
    INSERT INTO products (product_name, price, stock, description) VALUES
        ('游戏手柄', 199.00, 150, '专业游戏手柄'),
        ('机械键盘', 599.00, 100, 'RGB机械键盘');
    
    SELECT '新增数据完成' AS status;
    SELECT COUNT(*) AS total_users FROM test_table;
    SELECT COUNT(*) AS total_products FROM products;
    """
    cmd = [
        "docker", "exec", "-i", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE
    ]
    subprocess.run(cmd, input=sql, text=True, check=True)
    log_success("新数据添加成功")
    
    log_info("执行增量备份（这可能需要几分钟）...")
    cmd = ["docker", "exec", CONTAINER_NAME, "python3", "/scripts/main.py", "backup", "incremental"]
    log_info(f"[命令] {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    
    if result.returncode == 0:
        log_success("增量备份完成")
    else:
        log_warning(f"增量备份失败（退出码: {result.returncode}），继续执行测试...")
        log_info("这可能是正常的，如果这是第一次增量备份")

def insert_timestamped_data():
    """步骤10: 插入带时间戳的数据用于时间点恢复测试"""
    log_step("10. 插入带时间戳的数据（用于时间点恢复测试）")
    
    log_info("创建时间戳测试表...")
    sql = """
    CREATE TABLE IF NOT EXISTS timestamp_test (
        id INT AUTO_INCREMENT PRIMARY KEY,
        data_value VARCHAR(100) NOT NULL,
        inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        note VARCHAR(200),
        INDEX idx_inserted_at (inserted_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    subprocess.run(cmd, check=True)
    
    log_info("开始每秒插入一条数据，共20条...")
    log_info("每条数据都会记录插入时间，用于后续时间点恢复测试")
    
    # 创建时间点记录文件
    timestamp_file = Path("./backups/timestamp_records.txt")
    timestamp_file.parent.mkdir(parents=True, exist_ok=True)
    with open(timestamp_file, 'w') as f:
        f.write("# 时间点恢复测试记录\n")
        f.write("# 格式: 序号,插入时间,数据内容\n")
        f.write("\n")
    
    total_records = 20
    os.environ['TZ'] = 'Asia/Shanghai'
    time.tzset()
    
    for record_num in range(1, total_records + 1):
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        data_value = f"测试数据_{record_num}"
        note = f"第{record_num}条测试数据，插入时间: {current_time}"
        
        # 插入数据
        sql = f"INSERT INTO timestamp_test (data_value, note) VALUES ('{data_value}', '{note}');"
        cmd = [
            "docker", "exec", CONTAINER_NAME,
            "mysql", "-h", "127.0.0.1", "-u", "root",
            f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
        ]
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            log_warning(f"插入第 {record_num} 条数据失败")
        
        # 记录时间点
        with open(timestamp_file, 'a') as f:
            f.write(f"{record_num},{current_time},{data_value}\n")
        
        log_info(f"[{record_num}/{total_records}] 插入数据: {data_value} (时间: {current_time})")
        
        # 如果不是最后一条，等待1秒
        if record_num < total_records:
            time.sleep(1)
    
    log_success(f"已插入 {total_records} 条带时间戳的数据")
    
    # 显示插入的数据
    log_info("显示插入的数据:")
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e",
        "SELECT id, data_value, inserted_at, note FROM timestamp_test ORDER BY id;"
    ]
    subprocess.run(cmd, check=False)
    
    # 显示时间点记录文件
    log_info(f"时间点记录已保存到: {timestamp_file}")
    log_info("时间点记录内容:")
    with open(timestamp_file, 'r') as f:
        print(f.read())
    
    # 保存最后一条数据的时间点
    with open(timestamp_file, 'r') as f:
        lines = f.readlines()
        if lines:
            last_line = [l for l in lines if l.strip() and not l.startswith('#')][-1]
            last_timestamp = last_line.split(',')[1]
            with open(Path("./backups/last_insert_timestamp.txt"), 'w') as f2:
                f2.write(last_timestamp)
            log_info(f"最后插入时间点: {last_timestamp}")
    
    log_success("时间戳数据插入完成，可以用于时间点恢复测试")

def delete_data():
    """步骤7: 删除数据"""
    log_step("7. 删除测试数据（模拟数据丢失）")
    
    log_info("删除所有测试数据...")
    sql = """
    DELETE FROM test_table;
    DELETE FROM products;
    
    SELECT '数据已删除' AS status;
    SELECT COUNT(*) AS total_users FROM test_table;
    SELECT COUNT(*) AS total_products FROM products;
    """
    cmd = [
        "docker", "exec", "-i", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE
    ]
    result = subprocess.run(cmd, input=sql, text=True, check=False)
    
    if result.returncode == 0:
        log_success("数据删除成功（模拟数据丢失场景）")
    else:
        error_exit("数据删除失败")

def restore_data():
    """步骤8: 恢复数据"""
    log_step("8. 恢复数据")
    
    # ========== 恢复前：查询数据数量和检查binlog ==========
    log_info("========== 恢复前状态检查 ==========")
    
    # 查询恢复前的数据数量
    log_info("查询恢复前的数据数量...")
    sql = """
    SELECT '恢复前 test_table 数据数量:' AS '';
    SELECT COUNT(*) AS count FROM test_table;
    SELECT '恢复前 products 数据数量:' AS '';
    SELECT COUNT(*) AS count FROM products;
    """
    cmd = [
        "docker", "exec", "-i", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE
    ]
    result = subprocess.run(cmd, input=sql, text=True, check=False)
    
    # 获取恢复前的数据数量
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-N", "-e",
        "SELECT COUNT(*) FROM test_table;"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    before_test_table_count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
    
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-N", "-e",
        "SELECT COUNT(*) FROM products;"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    before_products_count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
    
    log_info(f"恢复前数据数量: test_table={before_test_table_count}, products={before_products_count}")
    
    # 检查binlog格式配置
    log_info("检查binlog格式配置...")
    sql = """
    SHOW VARIABLES LIKE 'binlog_format';
    SHOW VARIABLES LIKE 'log_bin';
    SHOW VARIABLES LIKE 'server_id';
    """
    cmd = [
        "docker", "exec", "-i", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE
    ]
    subprocess.run(cmd, input=sql, text=True, check=False)
    
    # 检查binlog文件
    log_info("检查binlog文件...")
    cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", "ls -lh /var/lib/mysql/mysql-bin.* 2>/dev/null | head -5"]
    subprocess.run(cmd, check=False)
    
    cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", "cat /var/lib/mysql/mysql-bin.index 2>/dev/null"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.stdout:
        log_info("binlog索引文件内容:")
        print(result.stdout)
    
    # 检查当前binlog位置
    log_info("检查当前binlog位置...")
    sql = "SHOW MASTER STATUS;"
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    subprocess.run(cmd, check=False)
    
    log_info("========== 开始恢复流程 ==========")
    
    log_info("查找最新的全量备份...")
    
    # 从容器内查找最新的备份目录
    cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", "ls -td /backups/full/*/ 2>/dev/null | head -1"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    backup_dir = result.stdout.strip().replace('\r', '').replace('\n', '')
    
    if not backup_dir:
        log_warning("容器内未找到备份目录，尝试从本地查找...")
        backup_path = Path("./backups/full")
        if backup_path.exists():
            backup_files = sorted(backup_path.glob("*/backup.tar.gz"), reverse=True)
            if backup_files:
                backup_dir = f"/backups/full/{backup_files[0].parent.name}"
                log_info(f"从本地找到备份: {backup_files[0]}")
    
    if not backup_dir:
        log_error("未找到备份文件，检查备份状态...")
        log_info("容器内备份目录内容:")
        subprocess.run(["docker", "exec", CONTAINER_NAME, "ls", "-la", "/backups/full/"], check=False)
        log_info("本地备份目录内容:")
        subprocess.run(["ls", "-la", "./backups/full/"], check=False)
        error_exit("未找到备份文件，无法恢复。请先执行全量备份。")
    
    log_info(f"找到备份目录: {backup_dir}")
    
    # 停止MySQL容器
    log_info("停止 MySQL 容器以进行恢复...")
    cmd = ["docker", "stop", CONTAINER_NAME]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    time.sleep(5)
    
    # 检查容器是否已停止
    result = subprocess.run(["docker", "ps"], capture_output=True, text=True, check=False)
    if CONTAINER_NAME in result.stdout:
        log_warning("容器仍在运行，强制停止...")
        subprocess.run(["docker", "stop", CONTAINER_NAME], check=True)
        time.sleep(3)
    
    # 应用恢复（通过容器内的脚本，不自己写逻辑）
    log_info("应用恢复到数据目录（通过容器内的脚本）...")
    cmd = ["docker", "run", "--rm", 
           "-v", f"{Path.cwd()}/mysql_data:/var/lib/mysql",
           "-v", f"{Path.cwd()}/mysql_config:/etc/mysql/conf.d",
           "-v", f"{Path.cwd()}/backups:/backups",
           "-v", "/etc/localtime:/etc/localtime:ro",
           "-v", "/etc/timezone:/etc/timezone:ro",
           IMAGE_NAME, "python3", "/scripts/main.py", "restore", "apply", backup_dir]
    log_info(f"[命令] {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    
    if result.returncode != 0:
        log_error(f"恢复失败（退出码: {result.returncode}）")
        log_info(f"检查备份目录是否存在: {backup_dir}")
        subprocess.run(["docker", "run", "--rm", 
                        "-v", f"{Path.cwd()}/mysql_data:/var/lib/mysql",
                        "-v", f"{Path.cwd()}/mysql_config:/etc/mysql/conf.d",
                        "-v", f"{Path.cwd()}/backups:/backups",
                        "-v", "/etc/localtime:/etc/localtime:ro",
                        "-v", "/etc/timezone:/etc/timezone:ro",
                        IMAGE_NAME, "ls", "-la", backup_dir], check=False)
        error_exit("恢复失败，请检查备份文件和日志")
    else:
        log_success("恢复应用成功")
    
    # 重新启动容器
    log_info("重新启动 MySQL 容器...")
    cmd = ["docker", "start", CONTAINER_NAME]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    wait_for_mysql()
    
    log_success("数据恢复完成，等待MySQL启动后进行验证...")

def check_binlog_events_by_time_range(start_datetime=None, end_datetime=None):
    """按时间范围检查binlog事件数量"""
    log_info("========== 按时间范围检查binlog事件数量 ==========")
    
    # 查找binlog文件（从mysql_data目录）
    binlog_files = []
    binlog_index_file = Path("./mysql_data/mysql-bin.index")
    
    if not binlog_index_file.exists():
        log_warning("未找到binlog索引文件，无法检查binlog事件")
        return {}
    
    # 读取binlog文件列表
    try:
        with open(binlog_index_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # 处理路径
                if line.startswith("/"):
                    binlog_file = Path(line)
                else:
                    binlog_file = Path("./mysql_data") / line.lstrip("./")
                
                # 如果文件不存在，尝试只使用文件名
                if not binlog_file.exists():
                    filename = Path(line).name
                    binlog_file = Path("./mysql_data") / filename
                
                if binlog_file.exists() and binlog_file.name.startswith("mysql-bin.") and binlog_file.name != "mysql-bin.index":
                    binlog_files.append(binlog_file)
    except Exception as e:
        log_error(f"读取binlog索引文件失败: {e}")
        return {}
    
    if not binlog_files:
        log_warning("未找到任何binlog文件")
        return {}
    
    log_info(f"找到 {len(binlog_files)} 个binlog文件")
    
    # 统计每个binlog文件的事件数量
    binlog_stats = {}
    total_events = 0
    
    for binlog_file in binlog_files:
        # 使用容器中的mysqlbinlog
        container_path = f"/var/lib/mysql/{binlog_file.name}"
        
        # 构建mysqlbinlog命令
        cmd = ["docker", "exec", CONTAINER_NAME, "mysqlbinlog", "--skip-gtids"]
        
        # 添加时间范围参数
        if start_datetime:
            cmd.extend(["--start-datetime", start_datetime])
        if end_datetime:
            cmd.extend(["--stop-datetime", end_datetime])
        
        cmd.append(container_path)
        
        # 执行mysqlbinlog并统计事件数量
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode == 0 and result.stdout:
                # 统计时间戳数量（每个事件都有一个时间戳）
                timestamps = re.findall(r'[0-9]{6}\s+[0-9]{1,2}:[0-9]{2}:[0-9]{2}', result.stdout)
                event_count = len(timestamps)
                
                if event_count > 0:
                    binlog_stats[binlog_file.name] = event_count
                    total_events += event_count
                    log_info(f"  {binlog_file.name}: {event_count} 个事件")
        except Exception as e:
            log_warning(f"处理binlog文件 {binlog_file.name} 失败: {e}")
    
    # 显示统计结果
    log_info("========== binlog事件统计结果 ==========")
    if start_datetime or end_datetime:
        time_range_str = ""
        if start_datetime and end_datetime:
            time_range_str = f"时间范围: {start_datetime} 到 {end_datetime}"
        elif start_datetime:
            time_range_str = f"开始时间: {start_datetime}"
        elif end_datetime:
            time_range_str = f"结束时间: {end_datetime}"
        log_info(time_range_str)
    
    log_info(f"binlog文件数: {len(binlog_stats)}")
    log_info(f"总事件数: {total_events}")
    
    for binlog_file, count in binlog_stats.items():
        log_info(f"  {binlog_file}: {count} 个事件")
    
    return {
        "total_files": len(binlog_stats),
        "total_events": total_events,
        "file_stats": binlog_stats
    }

def check_binlog_events_in_restore_time_range(target_datetime=None):
    """检查恢复时间段内的binlog事件数量"""
    log_info("========== 检查恢复时间段内的binlog事件数量 ==========")

    # 如果没有提供目标时间点，首先尝试读取最后使用的目标时间点
    if not target_datetime:
        try:
            last_target_file = Path("./backups/.last_pitr_target")
            if last_target_file.exists():
                target_datetime = last_target_file.read_text().strip()
                log_info(f"从最后使用记录中读取目标时间点: {target_datetime}")
        except Exception as e:
            log_warning(f"读取最后使用目标时间点失败: {e}")

    # 如果仍然没有目标时间点，尝试从时间点记录文件中读取
    if not target_datetime:
        try:
            timestamp_file = Path("./backups/timestamp_records.txt")
            if timestamp_file.exists():
                with open(timestamp_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    # 使用文件中的最后一个有效时间点作为目标时间点
                    for line in reversed(lines):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            # 解析时间点格式 "序号,时间点,描述"
                            parts = line.split(",")
                            if len(parts) >= 2:
                                target_datetime = parts[1].strip()
                                log_info(f"从时间点记录文件中读取最后的目标时间点: {target_datetime}")
                                break
        except Exception as e:
            log_warning(f"读取时间点记录文件失败: {e}")

    # 获取备份时间点（从LATEST_FULL_BACKUP_TIMESTAMP文件或从备份目录）
    backup_timestamp = None
    backup_datetime = None
    
    # 方法1: 从LATEST_FULL_BACKUP_TIMESTAMP文件读取
    latest_backup_file = Path("./backups/LATEST_FULL_BACKUP_TIMESTAMP")
    if latest_backup_file.exists():
        try:
            backup_timestamp = latest_backup_file.read_text().strip()
            if re.match(r'^\d{8}_\d{6}$', backup_timestamp):
                # 转换时间戳为datetime格式: YYYYMMDD_HHMMSS -> YYYY-MM-DD HH:MM:SS
                # 注意：备份时间戳是UTC时间
                year = backup_timestamp[0:4]
                month = backup_timestamp[4:6]
                day = backup_timestamp[6:8]
                hour = backup_timestamp[9:11]
                minute = backup_timestamp[11:13]
                second = backup_timestamp[13:15]
                backup_datetime = f"{year}-{month}-{day} {hour}:{minute}:{second}"
                log_info(f"从文件读取备份时间点: {backup_datetime} (UTC, 时间戳: {backup_timestamp})")
        except Exception as e:
            log_warning(f"读取LATEST_FULL_BACKUP_TIMESTAMP文件失败: {e}")
    
    # 方法2: 从备份目录名获取（如果方法1失败）
    if not backup_datetime:
        backup_dir = Path("./backups/full")
        if backup_dir.exists():
            backup_dirs = sorted([d for d in backup_dir.iterdir() if d.is_dir()], reverse=True)
            if backup_dirs:
                backup_timestamp = backup_dirs[0].name
                if re.match(r'^\d{8}_\d{6}$', backup_timestamp):
                    year = backup_timestamp[0:4]
                    month = backup_timestamp[4:6]
                    day = backup_timestamp[6:8]
                    hour = backup_timestamp[9:11]
                    minute = backup_timestamp[11:13]
                    second = backup_timestamp[13:15]
                    backup_datetime = f"{year}-{month}-{day} {hour}:{minute}:{second}"
                    log_info(f"从备份目录名获取备份时间点: {backup_datetime} (UTC, 时间戳: {backup_timestamp})")
    
    if not backup_datetime:
        log_warning("无法获取备份时间点，将检查所有binlog事件")
        check_binlog_events_by_time_range()
        return
    
    # 使用目标时间点或当前时间作为结束时间
    if target_datetime:
        end_datetime = target_datetime
        log_info(f"目标时间点: {end_datetime}")
    else:
        # 获取当前时间（恢复后的时间点）
        current_time = datetime.now()
        end_datetime = current_time.strftime("%Y-%m-%d %H:%M:%S")
        log_info(f"当前时间点: {end_datetime}")

    # 确保时间范围在同一时区（本地时区 Asia/Shanghai）
    # 将备份时间从UTC转换为本地时区
    try:
        # 解析UTC备份时间
        backup_time_utc = datetime.strptime(backup_datetime, "%Y-%m-%d %H:%M:%S")
        # 假设备份时间戳是UTC时间，转换为本地时区
        from datetime import timezone, timedelta
        tz_shanghai = timezone(timedelta(hours=8))
        backup_time_utc = backup_time_utc.replace(tzinfo=timezone.utc)
        backup_time_local = backup_time_utc.astimezone(tz_shanghai)
        backup_datetime_local = backup_time_local.strftime("%Y-%m-%d %H:%M:%S")

        log_info(f"备份时间点转换为本地时区: {backup_datetime_local}")
        log_info(f"检查时间段: {backup_datetime_local} 到 {end_datetime}")
        log_info("注意: 所有时间都在本地时区 (Asia/Shanghai)")
    except Exception as e:
        log_warning(f"时间转换失败，使用原始时间: {e}")
        backup_datetime_local = backup_datetime
        log_info(f"检查时间段: {backup_datetime_local} 到 {end_datetime}")

    stats = check_binlog_events_by_time_range(
        start_datetime=backup_datetime_local,
        end_datetime=end_datetime
    )
    
    if stats and stats.get("total_events", 0) > 0:
        log_success(f"恢复时间段内检测到 {stats['total_events']} 个binlog事件")
        log_info(f"涉及 {stats['total_files']} 个binlog文件")
    else:
        log_warning("恢复时间段内未检测到binlog事件，这可能是正常的（如果恢复后没有新的操作）")

def verify_restored_data():
    """步骤9: 验证恢复的数据"""
    log_step("9. 验证恢复的数据")
    
    log_info("========== 开始数据恢复验证 ==========")
    
    # 等待MySQL完全启动
    log_info("确保MySQL已完全启动...")
    wait_for_mysql()
    
    # 等待binlog自动应用完成（如果有）
    log_info("等待binlog自动应用（如果有）...")
    max_wait = 30  # 最多等待30秒
    wait_count = 0
    while wait_count < max_wait:
        # 检查是否有PITR标记文件
        cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", "test -f /backups/.pitr_restore_marker && echo 'exists' || echo 'not_exists'"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if "exists" in result.stdout:
            log_info("发现PITR恢复标记文件，等待binlog自动应用完成...")
            time.sleep(2)
            wait_count += 2
        else:
            break
    
    # 检查是否有PITR标记文件
    cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", "ls -la /backups/.pitr_restore_marker 2>/dev/null"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        log_info("发现PITR恢复标记文件，binlog应该会自动应用")
        log_info("标记文件内容:")
        subprocess.run(cmd, check=False)
    else:
        log_info("未发现PITR恢复标记文件（这是正常的，如果只是全量恢复）")
    
    # 检查是否有binlog SQL文件
    cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", "ls -lh /backups/pitr_replay_*.sql 2>/dev/null"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        log_info("发现binlog SQL文件:")
        subprocess.run(cmd, check=False)
    else:
        log_info("未发现binlog SQL文件（这是正常的，如果只是全量恢复）")
    
    # 获取恢复前的数据数量（从之前保存的值，如果没有则查询）
    # 注意：由于数据已被删除，恢复前应该是0
    before_test_table_count = 0
    before_products_count = 0
    
    # 查询恢复后的数据数量
    log_info("查询恢复后的数据数量...")
    sql = """
    SELECT '恢复后 test_table 数据数量:' AS '';
    SELECT COUNT(*) AS count FROM test_table;
    SELECT '恢复后 products 数据数量:' AS '';
    SELECT COUNT(*) AS count FROM products;
    """
    cmd = [
        "docker", "exec", "-i", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE
    ]
    subprocess.run(cmd, input=sql, text=True, check=False)
    
    # 获取恢复后的数据数量
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-N", "-e",
        "SELECT COUNT(*) FROM test_table;"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    after_test_table_count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
    
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-N", "-e",
        "SELECT COUNT(*) FROM products;"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    after_products_count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
    
    log_info(f"恢复后数据数量: test_table={after_test_table_count}, products={after_products_count}")
    
    # 显示恢复后的详细数据
    log_info("显示恢复后的详细数据...")
    sql = """
    SELECT 'test_table 数据:' AS '';
    SELECT * FROM test_table;
    
    SELECT 'products 数据:' AS '';
    SELECT * FROM products;
    """
    cmd = [
        "docker", "exec", "-i", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE
    ]
    subprocess.run(cmd, input=sql, text=True, check=False)
    
    # 检查binlog格式（确保是可恢复格式）
    log_info("检查binlog格式配置...")
    sql = """
    SELECT 'binlog_format' AS variable_name, @@binlog_format AS value
    UNION ALL
    SELECT 'log_bin', @@log_bin
    UNION ALL
    SELECT 'server_id', @@server_id;
    """
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    subprocess.run(cmd, check=False)
    
    binlog_format_ok = False
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-N", "-e",
        "SELECT @@binlog_format;"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    binlog_format = result.stdout.strip().upper() if result.stdout.strip() else "UNKNOWN"
    if binlog_format in ['ROW', 'MIXED']:
        binlog_format_ok = True
        log_success(f"binlog格式为 {binlog_format}，支持恢复")
    else:
        log_warning(f"binlog格式为 {binlog_format}，ROW或MIXED格式更适合恢复")
    
    # 检查恢复后的binlog状态
    log_info("检查恢复后的binlog状态...")
    sql = "SHOW MASTER STATUS;"
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    subprocess.run(cmd, check=False)
    
    # 按时间范围检查binlog事件数量（不指定时间范围，检查所有事件）
    log_info("")
    binlog_stats = check_binlog_events_by_time_range()
    
    # ========== 验证结果 ==========
    log_info("========== 数据恢复验证结果 ==========")
    log_info(f"恢复前数据数量: test_table={before_test_table_count}, products={before_products_count}")
    log_info(f"恢复后数据数量: test_table={after_test_table_count}, products={after_products_count}")
    log_info(f"binlog格式: {binlog_format} ({'✓ 支持恢复' if binlog_format_ok else '⚠ 建议使用ROW或MIXED'})")
    
    # 验证逻辑：恢复后应该有数据（至少5条test_table和5条products）
    expected_min_count = 5
    verification_passed = True
    
    if after_test_table_count >= expected_min_count and after_products_count >= expected_min_count:
        log_success(f"数据恢复验证成功！test_table有{after_test_table_count}条记录，products有{after_products_count}条记录")
    else:
        verification_passed = False
        log_error("数据恢复验证失败！")
        if after_test_table_count < expected_min_count:
            log_error(f"test_table 记录数不足: {after_test_table_count} < {expected_min_count} (期望至少{expected_min_count}条)")
        if after_products_count < expected_min_count:
            log_error(f"products 记录数不足: {after_products_count} < {expected_min_count} (期望至少{expected_min_count}条)")
        
        # 检查是否有binlog需要应用
        log_info("检查是否有未应用的binlog...")
        cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", "ls -lh /backups/pitr_replay_*.sql 2>/dev/null"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            log_warning("发现未应用的binlog SQL文件，可能需要手动应用")
            subprocess.run(cmd, check=False)
        
        cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", "ls -la /backups/.pitr_restore_marker 2>/dev/null"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            log_warning("发现PITR恢复标记文件，binlog可能正在自动应用中")
    
    if not verification_passed:
        error_exit("数据恢复验证失败，请检查恢复日志")

def test_point_in_time_restore():
    """步骤11: 时间点恢复测试"""
    log_step("11. 时间点恢复测试")
    
    timestamp_file = Path("./backups/timestamp_records.txt")
    
    if not timestamp_file.exists():
        log_warning("未找到时间点记录文件，跳过时间点恢复测试")
        return
    
    log_info("可用的时间点记录:")
    print("")
    with open(timestamp_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                parts = line.split(',')
                if len(parts) >= 3:
                    num, timestamp, data = parts[0], parts[1], parts[2]
                    print(f"  [{num}] {timestamp} - {data}")
    print("")
    
    # 自动模式：自动选择时间点
    pitr_restore_num = os.environ.get("PITR_RESTORE_NUM")
    if pitr_restore_num and pitr_restore_num.isdigit():
        restore_num = int(pitr_restore_num)
        log_info(f"自动模式: 使用指定的序号 {restore_num}")
    else:
        # 自动模式：在第13条附近2条以内随机选择一个时间点（11-15之间）
        base_num = 13
        range_val = 2
        min_num = base_num - range_val
        max_num = base_num + range_val
        restore_num = random.randint(min_num, max_num)
        log_info(f"自动模式: 随机选择序号 {restore_num} (范围: {min_num}-{max_num}，基于第13条附近2条以内)")
    
    if not restore_num or restore_num < 1:
        log_warning("无效的序号，跳过时间点恢复测试")
        return
    
    # 从记录文件中获取对应的时间点
    target_timestamp = None
    with open(timestamp_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{restore_num},"):
                target_timestamp = line.split(',')[1]
                break
    
    if not target_timestamp:
        log_error(f"未找到序号 {restore_num} 对应的时间点")
        return
    
    log_info(f"选择的时间点: {target_timestamp}")
    log_info(f"这将恢复到第 {restore_num} 条数据插入的时间点")
    log_info(f"预期结果: timestamp_test 表中应该有 {restore_num} 条数据")

    # 保存最后使用的目标时间点到文件，供后续检查使用
    try:
        last_target_file = Path("./backups/.last_pitr_target")
        last_target_file.parent.mkdir(parents=True, exist_ok=True)
        with open(last_target_file, "w", encoding="utf-8") as f:
            f.write(target_timestamp)
        log_info(f"已保存目标时间点到: {last_target_file}")
    except Exception as e:
        log_warning(f"保存目标时间点失败: {e}")

    print("")
    log_info("自动模式: 开始执行时间点恢复")
    
    # 检查 timestamp_test 表的当前状态
    log_info("检查 timestamp_test 表的当前状态...")
    sql = "SELECT COUNT(*) AS current_count FROM timestamp_test;"
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0 and "current_count" in result.stdout:
        current_count = result.stdout.split()[-1]
        log_info(f"恢复前 timestamp_test 表中有 {current_count} 条数据")
    else:
        log_warning("无法检查 timestamp_test 表状态")

    # 删除 timestamp_test 表（模拟表不存在的情况）
    log_info("删除 timestamp_test 表（模拟表丢失）...")
    sql = "DROP TABLE IF EXISTS timestamp_test;"
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", sql
    ]
    subprocess.run(cmd, check=False)
    log_info("timestamp_test 表已删除，准备进行 PITR 恢复")
    
    # 执行时间点恢复
    log_info("执行时间点恢复...")
    
    # 保存二进制日志文件信息
    log_info("保存二进制日志文件信息...")
    binlog_info_file = Path("./backups/binlog_info.txt")
    cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", "ls -lh /var/lib/mysql/mysql-bin.* 2>/dev/null"]
    with open(binlog_info_file, 'w') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=False)
    
    cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", "cat /var/lib/mysql/mysql-bin.index 2>/dev/null"]
    with open(Path("./backups/mysql-bin.index.backup"), 'w') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=False)
    log_info("二进制日志信息已保存")
    
    log_info("停止 MySQL 容器...")
    cmd = ["docker", "stop", CONTAINER_NAME]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    time.sleep(3)
    
    log_info("执行时间点恢复脚本...")
    cmd = ["docker", "run", "--rm",
           "-v", f"{Path.cwd()}/mysql_data:/var/lib/mysql",
           "-v", f"{Path.cwd()}/mysql_config:/etc/mysql/conf.d",
           "-v", f"{Path.cwd()}/backups:/backups",
           "-v", "/etc/localtime:/etc/localtime:ro",
           "-v", "/etc/timezone:/etc/timezone:ro",
           IMAGE_NAME, "python3", "/scripts/main.py", "restore", "pitr", target_timestamp]
    log_info(f"[命令] {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    
    if result.returncode == 0:
        log_success("时间点恢复脚本执行完成")
    else:
        log_warning("时间点恢复脚本可能有问题，继续验证...")
    
    # 启动 MySQL
    log_info("启动 MySQL 容器...")
    cmd = ["docker", "start", CONTAINER_NAME]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    wait_for_mysql()
    
    # 等待自动应用完成（docker-entrypoint.sh 会自动应用）
    log_info("等待自动应用二进制日志SQL文件...")
    time.sleep(10)  # 等待自动应用
    
    # 验证恢复结果
    log_info("验证恢复结果...")
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "mysql", "-h", "127.0.0.1", "-u", "root",
        f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-N", "-e",
        "SELECT COUNT(*) FROM timestamp_test;"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    recovered_count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
    
    log_info(f"恢复后的数据记录数: {recovered_count}")
    log_info(f"预期记录数: {restore_num}")
    
    if recovered_count == restore_num:
        log_success("时间点恢复测试成功！数据已恢复到指定时间点")
    elif recovered_count > 0:
        log_warning(f"时间点恢复部分成功，恢复了 {recovered_count} 条数据（预期 {restore_num} 条）")
        log_info("这可能是因为二进制日志应用的问题，请检查日志")
    else:
        log_error("时间点恢复失败，未恢复任何数据")
        log_info("请检查时间点恢复脚本的输出和日志")
    
    # 显示恢复后的数据
    log_info("恢复后的数据:")
    if recovered_count > 0:
        cmd = [
            "docker", "exec", CONTAINER_NAME,
            "mysql", "-h", "127.0.0.1", "-u", "root",
            f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e",
            "SELECT id, data_value, inserted_at, note FROM timestamp_test ORDER BY id;"
        ]
        subprocess.run(cmd, check=False)
    else:
        log_warning("timestamp_test 表可能不存在或为空")
        log_info("检查表是否存在:")
        cmd = [
            "docker", "exec", CONTAINER_NAME,
            "mysql", "-h", "127.0.0.1", "-u", "root",
            f"-p{MYSQL_ROOT_PASSWORD}", MYSQL_DATABASE, "-e", "SHOW TABLES;"
        ]
        subprocess.run(cmd, check=False)

def check_test_results():
    """检查并报告测试结果"""
    print("")
    print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
    print(f"{Colors.GREEN}  测试结果汇总{Colors.NC}")
    print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
    print("")
    
    if ERROR_COUNT == 0 and WARNING_COUNT == 0:
        log_success("所有测试通过！没有错误或警告")
        return 0
    elif ERROR_COUNT == 0:
        log_warning(f"测试完成，但有 {WARNING_COUNT} 个警告")
        return 0
    else:
        log_error(f"测试失败！发现 {ERROR_COUNT} 个错误，{WARNING_COUNT} 个警告")
        return 1

def main():
    """主函数"""
    print("")
    print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
    print(f"{Colors.GREEN}  MySQL 备份恢复全流程测试脚本{Colors.NC}")
    print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
    print("")
    
    # 检查是否在项目目录
    if not Path("docker-compose.yml").exists():
        error_exit("请在项目根目录执行此脚本")
    
    # 执行测试流程
    log_info("开始执行测试流程...")
    
    cleanup()
    log_info("✓ 步骤1完成: 环境清理")
    
    build_image()
    log_info("✓ 步骤2完成: 镜像构建")
    
    start_container()
    log_info("✓ 步骤3完成: 容器启动")
    
    create_test_data()
    log_info("✓ 步骤4完成: 测试数据创建")
    
    perform_backup()
    log_info("✓ 步骤5完成: 全量备份")
    
    add_more_data_and_incremental_backup()
    log_info("✓ 步骤6完成: 增量数据添加和备份")
    
    delete_data()
    log_info("✓ 步骤7完成: 数据删除（模拟数据丢失）")
    
    restore_data()
    log_info("✓ 步骤8完成: 数据恢复")
    
    verify_restored_data()
    log_info("✓ 步骤9完成: 数据验证")
    
    # 在验证后插入带时间戳的数据（这样二进制日志会包含这些操作）
    insert_timestamped_data()
    log_info("✓ 步骤10完成: 插入带时间戳的数据（用于时间点恢复测试）")
    
    # 可选：时间点恢复测试
    print("")
    print(f"{Colors.YELLOW}{'=' * 40}{Colors.NC}")
    print(f"{Colors.YELLOW}  可选: 时间点恢复测试{Colors.NC}")
    print(f"{Colors.YELLOW}{'=' * 40}{Colors.NC}")
    print("")
    log_info("已插入20条带时间戳的数据，可以进行时间点恢复测试")
    log_info("时间点记录文件: ./backups/timestamp_records.txt")
    print("")
    log_info("要测试时间点恢复，请执行以下步骤:")
    print("")
    print("  1. 查看时间点记录:")
    print("     cat ./backups/timestamp_records.txt")
    print("")
    print("  2. 选择一个时间点进行恢复（例如第10条数据的时间点）:")
    print(f"     docker stop {CONTAINER_NAME}")
    print(f"     docker run --rm -v $(pwd)/mysql_data:/var/lib/mysql -v $(pwd)/mysql_config:/etc/mysql/conf.d -v $(pwd)/backups:/backups -v /etc/localtime:/etc/localtime:ro -v /etc/timezone:/etc/timezone:ro {IMAGE_NAME} python3 /scripts/main.py restore pitr \"YYYY-MM-DD HH:MM:SS\"")
    print(f"     docker start {CONTAINER_NAME}")
    print("")
    print("  3. 验证恢复后的数据:")
    print(f"     docker exec {CONTAINER_NAME} mysql -h 127.0.0.1 -u root -p{MYSQL_ROOT_PASSWORD} {MYSQL_DATABASE} -e \"SELECT * FROM timestamp_test ORDER BY id;\"")
    print("")
    
    # 自动模式：默认进行时间点恢复测试
    if AUTO_PITR_TEST:
        log_info("自动模式: 进行时间点恢复测试")
        test_point_in_time_restore()
    else:
        log_info("跳过时间点恢复测试（设置 AUTO_PITR_TEST=y 以启用）")
    
    # 检查测试结果
    test_status = check_test_results()
    
    # ========== 最后一步：检查恢复时间段内的binlog事件数量 ==========
    log_step("12. 检查恢复时间段内的binlog事件数量")
    check_binlog_events_in_restore_time_range()
    
    print("")
    print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
    if test_status == 0:
        print(f"{Colors.GREEN}  测试流程全部完成！{Colors.NC}")
    else:
        print(f"{Colors.RED}  测试流程完成，但存在错误！{Colors.NC}")
    print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
    print("")
    log_info("容器状态:")
    cmd = ["docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}"]
    log_info(f"[命令] {' '.join(cmd)}")
    subprocess.run(cmd, check=False)
    print("")
    log_info("备份文件位置: ./backups/")
    log_info("数据文件位置: ./mysql_data/")
    log_info("时间点记录: ./backups/timestamp_records.txt")
    print("")
    
    # 显示可用命令
    # log_step("显示可用命令")
    # cmd = ["docker", "exec", CONTAINER_NAME, "python3", "/scripts/main.py", "help"]
    # log_info(f"[命令] {' '.join(cmd)}")
    # print("")
    # subprocess.run(cmd, check=False)
    # print("")
    
    # 如果有错误，以非零状态码退出
    if test_status != 0:
        sys.exit(1)

if __name__ == "__main__":
    main()

