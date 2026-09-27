# MySQL 备份恢复指南

## 恢复流程

### 1. 从 S3 下载并准备备份

**方式 A：使用统一入口（推荐）**

```bash
# 恢复备份（下载、解压、准备）
docker-compose run --rm mysql python3 /scripts/main.py restore backup backup_20251126_061546

# 或者使用完整文件名
docker-compose run --rm mysql python3 /scripts/main.py restore backup backup_20251126_061546.tar.gz

# 或者只使用时间戳
docker-compose run --rm mysql python3 /scripts/main.py restore backup 20251126_061546
```

**方式 B：直接调用 Python 脚本**

```bash
# 恢复备份（下载、解压、准备）
docker-compose run --rm mysql python3 /scripts/tasks/restore/restore_backup.py backup_20251126_061546

# 或者使用完整文件名
docker-compose run --rm mysql python3 /scripts/tasks/restore/restore_backup.py backup_20251126_061546.tar.gz

# 或者只使用时间戳
docker-compose run --rm mysql python3 /scripts/tasks/restore/restore_backup.py 20251126_061546
```

### 2. 停止 MySQL 服务

```bash
docker-compose stop mysql
```

### 3. 应用恢复（将备份应用到数据目录）

**方式 A：使用统一入口（推荐）**

```bash
# 使用默认设置（备份现有数据，使用 copy-back）
docker-compose run --rm mysql python3 /scripts/main.py restore apply /backups/restore

# 使用 move-back（恢复后删除恢复目录中的备份）
USE_MOVE_BACK=true docker-compose run --rm mysql python3 /scripts/main.py restore apply /backups/restore

# 不备份现有数据
BACKUP_EXISTING_DATA=false docker-compose run --rm mysql python3 /scripts/main.py restore apply /backups/restore
```

**方式 B：直接调用 Python 脚本**

```bash
# 使用默认设置（备份现有数据，使用 copy-back）
docker-compose run --rm mysql python3 /scripts/tasks/restore/apply_restore.py /backups/restore

# 使用 move-back（恢复后删除恢复目录中的备份）
USE_MOVE_BACK=true docker-compose run --rm mysql python3 /scripts/tasks/restore/apply_restore.py /backups/restore

# 不备份现有数据
BACKUP_EXISTING_DATA=false docker-compose run --rm mysql python3 /scripts/tasks/restore/apply_restore.py /backups/restore
```

### 4. 启动 MySQL 服务

```bash
docker-compose start mysql
```

### 5. 验证恢复

```bash
# 检查 MySQL 是否正常启动
docker-compose logs mysql | tail -20

# 连接数据库验证
docker-compose exec mysql mysql -uroot -prootpassword -e "SHOW DATABASES;"
```

## 完整恢复示例

```bash
# 1. 停止 MySQL
docker-compose stop mysql

# 2. 从 S3 恢复备份（使用统一入口）
docker-compose run --rm mysql python3 /scripts/main.py restore backup backup_20251126_061546

# 或直接调用 Python 脚本
# docker-compose run --rm mysql python3 /scripts/tasks/restore/restore_backup.py backup_20251126_061546

# 3. 应用恢复（使用统一入口）
docker-compose run --rm mysql python3 /scripts/main.py restore apply /backups/restore

# 或直接调用 Python 脚本
# docker-compose run --rm mysql python3 /scripts/tasks/restore/apply_restore.py /backups/restore

# 4. 启动 MySQL
docker-compose start mysql

# 5. 验证
docker-compose exec mysql mysql -uroot -prootpassword -e "SHOW DATABASES;"
```

## 环境变量说明

### apply_restore.py 环境变量

- `RESTORE_DIR`: 恢复目录路径（默认: `/backups/restore`）
- `MYSQL_DATA_DIR`: MySQL 数据目录（默认: `/var/lib/mysql`）
- `BACKUP_EXISTING_DATA`: 是否备份现有数据（默认: `true`）
- `USE_MOVE_BACK`: 是否使用 `--move-back`（默认: `false`，使用 `--copy-back`）

### restore_backup.py 环境变量

- `BACKUP_BASE_DIR`: 备份基础目录（默认: `/backups`）
- `S3_BACKUP_ENABLED`: 是否启用 S3 备份（默认: `false`）
- `S3_ENDPOINT`: S3 服务端点地址
- `S3_ACCESS_KEY`: S3 访问密钥 ID
- `S3_SECRET_KEY`: S3 访问密钥
- `S3_BUCKET`: S3 存储桶名称（默认: `mysql-backups`）

## 注意事项

1. **停止 MySQL**: 应用恢复前必须停止 MySQL 服务
2. **备份现有数据**: 默认会备份现有数据到 `/backups/mysql_data_backup_YYYYMMDD_HHMMSS/`
3. **权限**: 恢复后会自动修复数据目录权限
4. **验证**: 恢复后务必验证数据库是否正常

## 故障排除

### 恢复目录不存在

```bash
# 检查恢复目录
docker-compose exec mysql ls -la /backups/restore/
```

### 权限问题

```bash
# 手动修复权限
docker-compose run --rm mysql chown -R mysql:mysql /var/lib/mysql
```

### 查看恢复日志

```bash
# 查看容器日志
docker-compose logs mysql | grep -i restore
```

