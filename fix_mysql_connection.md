# MySQL连接失败问题排查指南

## 常见问题和解决方案

### 1. 容器未运行
**检查方法：**
```bash
docker ps -a | grep mysql8035
```

**解决方法：**
```bash
# 启动容器
docker start mysql8035

# 或使用docker-compose
docker-compose up -d
```

### 2. MySQL服务未启动
**检查方法：**
```bash
docker exec mysql8035 ps aux | grep mysqld
```

**解决方法：**
```bash
# 查看容器日志
docker logs mysql8035

# 重启容器
docker restart mysql8035
```

### 3. 端口映射问题
**检查方法：**
```bash
# 检查端口是否监听
netstat -tlnp | grep 3307
# 或
ss -tlnp | grep 3307

# 检查容器端口映射
docker port mysql8035
```

**解决方法：**
- 确保docker-compose.yml中端口映射正确：`"3307:3306"`
- 检查是否有其他服务占用3307端口

### 4. 连接配置错误
**检查连接参数：**
- 主机：`127.0.0.1` 或 `localhost`
- 端口：`3307`（宿主机端口）
- 用户名：`root`
- 密码：`rootpassword`
- 数据库：`testdb`

### 5. 防火墙问题
**检查方法：**
```bash
# 检查防火墙规则
iptables -L -n | grep 3307
```

### 6. 数据目录权限问题
**检查方法：**
```bash
docker exec mysql8035 ls -la /var/lib/mysql
```

**解决方法：**
```bash
# 修复权限
sudo chown -R 999:999 ./mysql_data
```

### 7. MySQL初始化失败
**检查方法：**
```bash
docker logs mysql8035 | grep -i error
```

**解决方法：**
- 删除数据目录重新初始化
- 检查磁盘空间
- 检查内存是否足够

## 快速诊断命令

```bash
# 1. 检查容器状态
docker ps -a --filter name=mysql8035

# 2. 启动容器（如果未运行）
docker start mysql8035

# 3. 等待MySQL启动（最多60秒）
timeout 60 bash -c 'until docker exec mysql8035 mysqladmin ping -h 127.0.0.1 -u root -prootpassword --silent; do sleep 2; done'

# 4. 测试容器内连接
docker exec mysql8035 mysql -h 127.0.0.1 -u root -prootpassword -e "SELECT 1;"

# 5. 测试宿主机连接
mysql -h 127.0.0.1 -P 3307 -u root -prootpassword -e "SELECT 1;"
```

## 常见错误信息

### "Can't connect to MySQL server"
- 容器未运行
- MySQL服务未启动
- 端口映射错误

### "Access denied"
- 密码错误
- 用户权限问题

### "Connection refused"
- 端口未监听
- 防火墙阻止
- 绑定地址错误

