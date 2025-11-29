#!/bin/bash
# MySQL连接诊断脚本

echo "=========================================="
echo "MySQL 连接诊断"
echo "=========================================="
echo ""

CONTAINER_NAME="mysql8035"
MYSQL_ROOT_PASSWORD="rootpassword"
MYSQL_PORT="3307"

echo "1. 检查容器状态..."
docker ps -a --filter name=$CONTAINER_NAME --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo ""

echo "2. 检查容器是否运行..."
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "✓ 容器正在运行"
else
    echo "✗ 容器未运行"
    echo "尝试启动容器..."
    docker start $CONTAINER_NAME
    sleep 3
fi
echo ""

echo "3. 检查容器内的MySQL进程..."
docker exec $CONTAINER_NAME ps aux | grep -E "mysql|mysqld" | grep -v grep || echo "未找到MySQL进程"
echo ""

echo "4. 检查容器内的MySQL端口..."
docker exec $CONTAINER_NAME netstat -tlnp 2>/dev/null | grep 3306 || docker exec $CONTAINER_NAME ss -tlnp 2>/dev/null | grep 3306 || echo "无法检查端口"
echo ""

echo "5. 检查宿主机端口映射..."
netstat -tlnp 2>/dev/null | grep $MYSQL_PORT || ss -tlnp 2>/dev/null | grep $MYSQL_PORT || echo "端口 $MYSQL_PORT 未监听"
echo ""

echo "6. 测试容器内MySQL连接..."
docker exec $CONTAINER_NAME mysqladmin ping -h 127.0.0.1 -u root -p$MYSQL_ROOT_PASSWORD 2>&1
echo ""

echo "7. 测试宿主机MySQL连接..."
mysql -h 127.0.0.1 -P $MYSQL_PORT -u root -p$MYSQL_ROOT_PASSWORD -e "SELECT 1 as test;" 2>&1
echo ""

echo "8. 检查容器日志（最后20行）..."
docker logs $CONTAINER_NAME --tail 20 2>&1
echo ""

echo "9. 检查MySQL错误日志..."
docker exec $CONTAINER_NAME cat /var/log/mysql/error.log 2>/dev/null | tail -20 || echo "无法读取错误日志"
echo ""

echo "=========================================="
echo "诊断完成"
echo "=========================================="

