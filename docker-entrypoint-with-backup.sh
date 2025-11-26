#!/bin/bash
set -e

# 保存原始 MySQL 入口点脚本路径
ORIGINAL_ENTRYPOINT="/usr/local/bin/docker-entrypoint.sh"

# 检查原始入口点是否存在，如果不存在则使用 /docker-entrypoint.sh
if [ ! -f "$ORIGINAL_ENTRYPOINT" ]; then
    ORIGINAL_ENTRYPOINT="/docker-entrypoint.sh"
fi

# 启动备份服务的函数
start_backup_service() {
    echo "=========================================="
    echo "启动备份调度服务..."
    echo "=========================================="
    
    # 等待 MySQL 完全启动（最多等待 5 分钟）
    echo "等待 MySQL 完全启动..."
    MAX_WAIT=300
    WAIT_COUNT=0
    
    while [ $WAIT_COUNT -lt $MAX_WAIT ]; do
        # 尝试连接 MySQL，如果成功则退出循环
        if mysqladmin ping -h localhost -u root -p"${MYSQL_ROOT_PASSWORD:-rootpassword}" --silent 2>/dev/null; then
            echo "MySQL 已启动"
            break
        fi
        sleep 2
        WAIT_COUNT=$((WAIT_COUNT + 2))
    done
    
    if [ $WAIT_COUNT -ge $MAX_WAIT ]; then
        echo "警告: MySQL 启动超时，但将继续启动备份服务"
    fi
    
    # 创建备份目录
    mkdir -p /backups/full /backups/incremental
    
    # 启动备份调度服务（在后台运行）
    /scripts/start-backup.sh &
    
    echo "备份调度服务已启动"
    echo "=========================================="
}

# 如果命令是 mysqld，则启动 MySQL 和备份服务
if [ "$1" = 'mysqld' ]; then
    # 在后台启动备份服务
    start_backup_service &
    
    # 执行原始的 MySQL 入口点脚本
    exec "$ORIGINAL_ENTRYPOINT" "$@"
else
    # 对于其他命令，直接执行原始入口点脚本
    exec "$ORIGINAL_ENTRYPOINT" "$@"
fi

