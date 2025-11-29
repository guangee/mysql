# MySQL 备份恢复测试流程详解

## 完整测试流程顺序

### 步骤1: 环境清理 (`cleanup`)
- **操作**: 停止并删除容器，清理数据目录、备份目录、配置目录
- **目的**: 确保每次测试从干净的环境开始
- **关键目录**:
  - `./mysql_data/` - MySQL数据目录（完全删除后重建）
  - `./backups/` - 备份目录（完全删除后重建）
  - `./mysql_config/` - 配置目录（完全删除后重建）

### 步骤2: 重新构建镜像 (`build_image`)
- **操作**: 执行 `docker-compose build`
- **目的**: 确保使用最新的Docker镜像

### 步骤3: 启动容器 (`start_container`)
- **操作**: 执行 `docker-compose up -d mysql`，等待MySQL启动
- **目的**: 启动MySQL服务，准备接收数据

### 步骤4: 创建测试数据 (`create_test_data`)
- **操作**: 
  - 创建 `test_table` 表，插入5条用户数据
  - 创建 `products` 表，插入5条产品数据
- **目的**: 创建基础测试数据
- **数据状态**: 
  - `test_table`: 5条记录
  - `products`: 5条记录

### 步骤5: 执行全量备份 (`perform_backup`)
- **操作**: 执行 `docker exec mysql8044 /scripts/full-backup.sh`
- **目的**: 创建全量备份（包含步骤4的所有数据）
- **备份内容**: 
  - `test_table`: 5条记录
  - `products`: 5条记录
- **备份位置**: `/backups/full/YYYYMMDD_HHMMSS/`

### 步骤6: 添加更多数据并执行增量备份 (`add_more_data_and_incremental_backup`)
- **操作**: 
  1. 向 `test_table` 添加2条新记录（总数变为7条）
  2. 向 `products` 添加2条新记录（总数变为7条）
  3. 执行 `docker exec mysql8044 /scripts/incremental-backup.sh`
- **目的**: 测试增量备份功能
- **数据状态**: 
  - `test_table`: 7条记录
  - `products`: 7条记录
- **备份内容**: 增量备份（只包含新增的2条记录）

### 步骤7: 删除数据 (`delete_data`)
- **操作**: 
  - `DELETE FROM test_table;`
  - `DELETE FROM products;`
- **目的**: 模拟数据丢失场景
- **数据状态**: 
  - `test_table`: 0条记录
  - `products`: 0条记录

### 步骤8: 恢复数据 (`restore_data`)
- **操作**: 
  1. 停止MySQL容器
  2. 查找最新的全量备份
  3. 准备备份（解压、decompress、prepare）
  4. 应用恢复到数据目录
  5. 重新启动MySQL容器
- **目的**: 从全量备份恢复数据
- **恢复后的数据状态**: 
  - `test_table`: 5条记录（恢复到步骤5的状态）
  - `products`: 5条记录（恢复到步骤5的状态）
- **注意**: 此时 `timestamp_test` 表**不存在**（因为它在全量备份之后创建）

### 步骤8.5: 插入带时间戳的数据 (`insert_timestamped_data`)
- **操作**: 
  1. 创建 `timestamp_test` 表
  2. 每秒插入1条数据，共插入20条
  3. 每条数据记录插入时间到 `./backups/timestamp_records.txt`
- **目的**: 为时间点恢复测试准备数据
- **数据状态**: 
  - `timestamp_test`: 20条记录（id=1到20）
  - 每条记录间隔1秒插入
- **时间点记录文件**: `./backups/timestamp_records.txt`
  - 格式: `序号,插入时间,数据内容`
  - 例如: `1,2025-11-27 11:17:40,测试数据_1`
- **二进制日志**: 这些插入操作会被记录到二进制日志中

### 步骤9: 验证恢复的数据 (`verify_restored_data`)
- **操作**: 检查 `test_table` 和 `products` 表的记录数
- **目的**: 验证步骤8的恢复是否成功
- **预期结果**: 
  - `test_table`: >= 5条记录
  - `products`: >= 5条记录

### 步骤10: 时间点恢复测试 (`test_point_in_time_restore`)

这是**最关键**的步骤，也是当前出现问题的地方。

#### 10.1 选择时间点
- **操作**: 自动在第13条附近2条以内随机选择（11-15之间）
- **示例**: 选择第11条，对应时间点 `2025-11-27 11:17:50`
- **预期**: 恢复到该时间点，应该有11条数据

#### 10.2 准备测试环境
- **操作**: 
  1. 创建 `timestamp_test` 表（如果不存在）
  2. 删除 `timestamp_test` 表中的所有数据（模拟数据丢失）
- **目的**: 模拟数据丢失，准备进行时间点恢复
- **数据状态**: `timestamp_test`: 0条记录

#### 10.3 保存二进制日志信息
- **操作**: 
  - 保存二进制日志文件列表到 `./backups/binlog_info.txt`
  - 保存二进制日志索引到 `./backups/mysql-bin.index.backup`
- **目的**: 记录二进制日志的位置，用于后续恢复

#### 10.4 停止MySQL并执行时间点恢复
- **操作**: 
  1. `docker-compose stop mysql`
  2. `docker-compose run --rm mysql /scripts/point-in-time-restore.sh "2025-11-27 11:17:50"`
- **目的**: 执行时间点恢复脚本
- **恢复脚本操作**:
  1. 查找最新的全量备份
  2. 查找需要应用的增量备份（在目标时间点之前的）
  3. **保存二进制日志文件**（在清空数据目录之前！）
  4. 准备备份（apply log）
  5. 应用增量备份（如果有）
  6. 恢复数据到数据目录
  7. 提取二进制日志到目标时间点
  8. 生成SQL文件: `/backups/pitr_replay_*.sql`

#### 10.5 启动MySQL
- **操作**: `docker-compose up -d mysql`
- **目的**: 启动恢复后的MySQL服务

#### 10.6 应用二进制日志
- **操作**: 
  1. 查找二进制日志SQL文件: `./backups/pitr_replay_*.sql`
  2. 应用SQL文件到数据库: `mysql < pitr_replay_*.sql`
- **目的**: 应用从备份时间点到目标时间点之间的所有数据变更
- **预期**: 应该恢复11条数据

#### 10.7 验证恢复结果
- **操作**: 检查 `timestamp_test` 表的记录数
- **预期**: 应该有11条记录（对应选择的时间点）
- **当前问题**: 恢复后数据为0条 ❌

## 问题分析

根据测试输出，问题出现在**步骤10.6（应用二进制日志）**：

1. ✅ 二进制日志SQL文件已生成（23146字节）
2. ✅ 文件应用成功（没有错误）
3. ❌ 但数据记录数为0（预期11条）

### 可能的原因

1. **二进制日志格式问题**: 
   - MySQL使用ROW格式的二进制日志
   - ROW格式的二进制日志包含的是行级别的变更，不是SQL语句
   - 直接通过 `mysql < file` 可能无法正确应用

2. **Delete_rows事件问题**: 
   - 二进制日志可能包含了Delete_rows事件
   - 即使使用了 `--stop-datetime`，可能仍然包含了目标时间点之后的删除操作
   - 这会导致数据被删除

3. **时区问题**: 
   - 目标时间是东八区时间（`11:17:50`）
   - 二进制日志中的时间戳是UTC时间
   - `mysqlbinlog --stop-datetime` 可能有时区转换问题

4. **事务完整性问题**: 
   - 二进制日志可能包含不完整的事务
   - 或者事务的COMMIT时间晚于目标时间点

## 建议的检查点

1. **检查二进制日志SQL文件内容**:
   ```bash
   docker exec mysql8044 sh -c "head -100 /backups/pitr_replay_*.sql"
   docker exec mysql8044 sh -c "grep -E 'Write_rows|Delete_rows|COMMIT' /backups/pitr_replay_*.sql | tail -20"
   ```

2. **检查二进制日志文件**:
   ```bash
   docker exec mysql8044 sh -c "ls -lh /backups/binlog_backup_*/mysql-bin.*"
   docker exec mysql8044 sh -c "mysqlbinlog /backups/binlog_backup_*/mysql-bin.000004 | grep -E 'SET TIMESTAMP|Write_rows|Delete_rows' | tail -20"
   ```

3. **检查时间点记录**:
   ```bash
   cat ./backups/timestamp_records.txt
   ```

4. **手动测试二进制日志应用**:
   ```bash
   docker exec mysql8044 sh -c "mysqlbinlog /backups/binlog_backup_*/mysql-bin.000004 --stop-datetime='2025-11-27 11:17:51' | mysql -h 127.0.0.1 -u root -prootpassword testdb -vv"
   docker exec mysql8044 mysql -h 127.0.0.1 -u root -prootpassword testdb -e "SELECT COUNT(*) FROM timestamp_test;"
   ```

5. **检查表结构**:
   ```bash
   docker exec mysql8044 mysql -h 127.0.0.1 -u root -prootpassword testdb -e "DESCRIBE timestamp_test;"
   ```

## 关键时间线

```
步骤4: 创建基础数据 (test_table, products)
  ↓
步骤5: 全量备份 (备份时间点 T1)
  ↓
步骤6: 添加数据 + 增量备份
  ↓
步骤7: 删除数据
  ↓
步骤8: 恢复数据 (恢复到时间点 T1)
  ↓
步骤8.5: 插入20条带时间戳数据 (时间点 T1 到 T2)
  ↓
步骤10: 时间点恢复 (恢复到时间点 T1.5，在T1和T2之间)
  - 从全量备份恢复 (时间点 T1)
  - 应用二进制日志 (从 T1 到 T1.5)
```

## 数据状态变化

| 步骤 | test_table | products | timestamp_test |
|------|-----------|----------|----------------|
| 步骤4 | 5条 | 5条 | 不存在 |
| 步骤5 | 5条 | 5条 | 不存在 (备份) |
| 步骤6 | 7条 | 7条 | 不存在 |
| 步骤7 | 0条 | 0条 | 不存在 |
| 步骤8 | 5条 | 5条 | 不存在 (恢复后) |
| 步骤8.5 | 5条 | 5条 | 20条 |
| 步骤10 | 5条 | 5条 | 11条 (预期) / 0条 (实际) ❌ |

