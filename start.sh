#!/bin/bash

set -e

echo "======================================"
echo "MySQL 8.0 备份系统启动脚本"
echo "======================================"
echo ""

# 检查 .env 文件
if [ ! -f .env ]; then
    echo "未找到 .env 文件，从 env.example 创建..."
    cp env.example .env
    echo "已创建 .env 文件，请根据需要修改配置"
    echo ""
fi

# 启动服务
echo "启动 Docker Compose 服务..."
docker-compose up -d

echo ""
echo "等待服务启动..."
sleep 10

echo ""
echo "======================================"
echo "服务状态:"
echo "======================================"
docker-compose ps

echo ""
echo "======================================"
echo "访问信息:"
echo "======================================"
echo "MySQL: localhost:$(grep MYSQL_PORT .env | cut -d '=' -f2 | tr -d ' ' || echo '3306')"
echo ""
echo "S3 对象存储: $(grep S3_ENDPOINT .env | cut -d '=' -f2 | tr -d ' ' || echo '未配置')"
echo "S3 存储桶: $(grep S3_BUCKET .env | cut -d '=' -f2 | tr -d ' ' || echo 'mysql-backups')"
echo ""
echo "提示: 请确保已在 .env 文件中正确配置 S3 兼容对象存储的连接信息"
echo ""
echo "查看备份日志: docker-compose logs -f mysql"
echo "手动执行全量备份: docker exec mysql8044 /scripts/full-backup.sh"
echo "手动执行增量备份: docker exec mysql8044 /scripts/incremental-backup.sh"
echo ""
echo "======================================"

