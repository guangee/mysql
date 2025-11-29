# MySQL 时间点恢复（Point-in-Time Recovery, PITR）指南

## 概述

时间点恢复允许您将数据库恢复到任意指定的时间点，而不仅仅是备份的时间点。这需要：

1. **全量备份** - 作为恢复的基础
2. **增量备份** - 应用到全量备份之后
3. **二进制日志（binlog）** - 从最后一个备份到目标时间点之间的所有变更

## 前提条件

1. MySQL 必须启用二进制日志（binlog）
   - 在 `docker-compose.yml` 中已配置：`--log-bin=mysql-bin`

2. 需要有可用的备份：
   - 至少一个全量备份
   - 可选：相关的增量备份

3. 需要有可用的二进制日志文件：
   - 二进制日志文件必须包含从备份时间到目标时间点的所有变更

## 使用方法

### 基本用法

```bash
# 恢复到指定时间点（自动查找最新的全量备份和相关增量备份）
docker-compose run --rm mysql /scripts/point-in-time-restore.sh "2025-11-26 14:30:00"
```

### 指定全量备份

```bash
# 使用指定的全量备份
docker-compose run --rm mysql /scripts/point-in-time-restore.sh "2025-11-26 14:30:00" 20251126_020000
```

### 指定全量备份和增量备份

```bash
# 使用指定的全量备份和增量备份
docker-compose run --rm mysql /scripts/point-in-time-restore.sh \
  "2025-11-26 14:30:00" \
  20251126_020000 \
  backup_20251126_030000.tar.gz \
  backup_20251126_040000.tar.gz
```

## 完整恢复流程

### 1. 停止 MySQL

```bash
docker-compose stop mysql
```

### 2. 执行时间点恢复

```bash
# 恢复到 2025-11-26 14:30:00
docker-compose run --rm mysql /scripts/point-in-time-restore.sh "2025-11-26 14:30:00"
```

### 3. 启动 MySQL

```bash
docker-compose start mysql
```

### 4. 应用二进制日志（如果需要）

如果脚本提示需要手动应用二进制日志，执行：

```bash
# 查找生成的 SQL 文件
docker exec mysql8044 ls -lh /tmp/pitr_replay_*.sql

# 应用二进制日志
docker exec -i mysql8044 mysql -h 127.0.0.1 -u root -prootpassword < /tmp/pitr_replay_*.sql
```

或者从容器内执行：

```bash
docker exec mysql8044 bash -c "mysql -h 127.0.0.1 -u root -prootpassword < /tmp/pitr_replay_*.sql"
```

### 5. 验证恢复

```bash
# 检查数据
docker exec mysql8044 mysql -h 127.0.0.1 -u root -prootpassword testdb -e "SELECT * FROM test_table;"

# 检查时间点
docker exec mysql8044 mysql -h 127.0.0.1 -u root -prootpassword -e "SELECT NOW();"
```

## 时间点恢复的工作原理

1. **恢复全量备份**
   - 从指定的全量备份开始恢复
   - 使用 `xtrabackup --prepare` 准备备份

2. **应用增量备份**
   - 按时间顺序应用所有相关的增量备份
   - 使用 `xtrabackup --prepare --incremental-dir` 合并增量备份

3. **应用二进制日志**
   - 使用 `mysqlbinlog` 提取从最后一个备份到目标时间点的所有 SQL 语句
   - 应用这些 SQL 语句以恢复到精确的时间点

## 时间格式

时间点格式：`YYYY-MM-DD HH:MM:SS`

示例：
- `2025-11-26 14:30:00`
- `2025-11-26 14:30:00`
- `2025-11-26 23:59:59`

## 注意事项

1. **备份现有数据**
   - 脚本会自动备份现有数据到 `/backups/existing_data_backup_*`
   - 但建议在执行恢复前手动备份重要数据

2. **二进制日志保留**
   - 确保二进制日志文件没有被删除
   - 目标时间点必须在二进制日志的保留范围内

3. **时间点限制**
   - 目标时间点必须在全量备份时间之后
   - 如果目标时间点在最后一个增量备份之前，只会恢复到最后一个增量备份的时间点

4. **性能考虑**
   - 时间点恢复可能需要较长时间，特别是如果有很多二进制日志需要应用
   - 建议在低峰期执行

5. **验证恢复**
   - 恢复后务必验证数据是否正确
   - 检查关键表的数据和时间戳

## 故障排查

### 找不到二进制日志文件

如果提示找不到二进制日志文件：

1. 检查二进制日志是否启用：
   ```bash
   docker exec mysql8044 mysql -h 127.0.0.1 -u root -prootpassword -e "SHOW VARIABLES LIKE 'log_bin';"
   ```

2. 检查二进制日志文件位置：
   ```bash
   docker exec mysql8044 ls -lh /var/lib/mysql/mysql-bin.*
   ```

### 二进制日志时间范围不足

如果目标时间点超出了二进制日志的保留范围：

1. 检查二进制日志保留设置：
   ```bash
   docker exec mysql8044 mysql -h 127.0.0.1 -u root -prootpassword -e "SHOW VARIABLES LIKE 'binlog_expire_logs_seconds';"
   ```

2. 考虑增加二进制日志保留时间或更频繁的备份

### 恢复后数据不正确

1. 检查恢复的时间点是否正确
2. 验证是否应用了所有必要的增量备份
3. 确认二进制日志是否完整应用

## 示例场景

### 场景1：恢复到1小时前

```bash
# 计算1小时前的时间
TARGET_TIME=$(date -d "1 hour ago" "+%Y-%m-%d %H:%M:%S")

# 执行恢复
docker-compose stop mysql
docker-compose run --rm mysql /scripts/point-in-time-restore.sh "$TARGET_TIME"
docker-compose start mysql
```

### 场景2：恢复到特定事务之前

如果知道某个事务的时间，可以恢复到该时间之前：

```bash
# 恢复到 2025-11-26 14:30:00（某个事务执行之前）
docker-compose stop mysql
docker-compose run --rm mysql /scripts/point-in-time-restore.sh "2025-11-26 14:29:59"
docker-compose start mysql
```

### 场景3：恢复到昨天的某个时间点

```bash
# 恢复到昨天 14:30:00
YESTERDAY=$(date -d "yesterday" "+%Y-%m-%d")
docker-compose stop mysql
docker-compose run --rm mysql /scripts/point-in-time-restore.sh "$YESTERDAY 14:30:00"
docker-compose start mysql
```

## 相关脚本

- `point-in-time-restore.sh` - 时间点恢复脚本
- `restore-backup.sh` - 普通备份恢复脚本
- `apply-restore.sh` - 应用恢复脚本
- `full-backup.sh` - 全量备份脚本
- `incremental-backup.sh` - 增量备份脚本

