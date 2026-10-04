#!/usr/bin/env python3
"""MySQL连接诊断脚本"""

import subprocess
import sys
import time

CONTAINER_NAME = "mysql8035"
MYSQL_ROOT_PASSWORD = "rootpassword"
MYSQL_PORT = "3307"

def run_cmd(cmd, check=False):
    """执行命令"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def main():
    print("=" * 50)
    print("MySQL 连接诊断")
    print("=" * 50)
    print()
    
    # 1. 检查容器状态
    print("1. 检查容器状态...")
    success, stdout, stderr = run_cmd(["docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"])
    if stdout:
        print(stdout)
    else:
        print("容器不存在或无法查询")
    print()
    
    # 2. 检查容器是否运行
    print("2. 检查容器是否运行...")
    success, stdout, stderr = run_cmd(["docker", "ps", "--format", "{{.Names}}"])
    if CONTAINER_NAME in stdout:
        print(f"✓ 容器 {CONTAINER_NAME} 正在运行")
    else:
        print(f"✗ 容器 {CONTAINER_NAME} 未运行")
        print("尝试启动容器...")
        success, stdout, stderr = run_cmd(["docker", "start", CONTAINER_NAME])
        if success:
            print("容器启动命令执行成功，等待5秒...")
            time.sleep(5)
        else:
            print(f"容器启动失败: {stderr}")
    print()
    
    # 3. 检查容器内的MySQL进程
    print("3. 检查容器内的MySQL进程...")
    success, stdout, stderr = run_cmd(["docker", "exec", CONTAINER_NAME, "ps", "aux"])
    if "mysqld" in stdout or "mysql" in stdout:
        print("✓ 找到MySQL进程")
        for line in stdout.split('\n'):
            if 'mysqld' in line or 'mysql' in line:
                print(f"  {line[:100]}")
    else:
        print("✗ 未找到MySQL进程")
        print(f"错误: {stderr}")
    print()
    
    # 4. 测试容器内连接
    print("4. 测试容器内MySQL连接...")
    success, stdout, stderr = run_cmd([
        "docker", "exec", CONTAINER_NAME,
        "mysqladmin", "ping", "-h", "127.0.0.1", "-u", "root", f"-p{MYSQL_ROOT_PASSWORD}", "--silent"
    ])
    if success:
        print("✓ 容器内连接成功")
    else:
        print(f"✗ 容器内连接失败")
        print(f"错误: {stderr}")
    print()
    
    # 5. 测试宿主机连接
    print("5. 测试宿主机MySQL连接...")
    success, stdout, stderr = run_cmd([
        "mysql", "-h", "127.0.0.1", "-P", MYSQL_PORT, "-u", "root", f"-p{MYSQL_ROOT_PASSWORD}", "-e", "SELECT 1;"
    ])
    if success:
        print("✓ 宿主机连接成功")
        print(stdout)
    else:
        print(f"✗ 宿主机连接失败")
        print(f"错误: {stderr}")
        print("提示: 可能需要安装mysql-client")
    print()
    
    # 6. 检查端口映射
    print("6. 检查端口映射...")
    success, stdout, stderr = run_cmd(["docker", "port", CONTAINER_NAME])
    if stdout:
        print(stdout)
    else:
        print(f"无法获取端口映射: {stderr}")
    print()
    
    # 7. 检查容器日志
    print("7. 检查容器日志（最后20行）...")
    success, stdout, stderr = run_cmd(["docker", "logs", CONTAINER_NAME, "--tail", "20"])
    if stdout:
        print(stdout)
    print()
    
    print("=" * 50)
    print("诊断完成")
    print("=" * 50)

if __name__ == "__main__":
    main()

