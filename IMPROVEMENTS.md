# 数据恢复功能通用性改进方案

## 当前实现的限制

当前的数据恢复功能主要针对 `timestamp_test` 表进行了硬编码，存在以下限制：

### 1. 表名硬编码
- 只处理 `timestamp_test` 表
- 其他表的数据无法恢复

### 2. 列名和顺序硬编码
- 固定列名：`(id, data_value, inserted_at, note)`
- 固定列数：`if(length(arr)>=4)`
- 无法适应不同表结构

### 3. 时间戳处理硬编码
- 假设第3列是时间戳：`FROM_UNIXTIME(arr[3])`
- 其他表的时间戳列无法正确处理

### 4. CREATE TABLE 硬编码
- 只处理 `timestamp_test` 表的创建语句

## 改进方案

### 方案1：通用binlog转换脚本（推荐）

创建一个通用的脚本，能够：

1. **自动检测表名**：从binlog的 `INSERT INTO` 语句中提取表名
2. **自动获取列结构**：从MySQL的 `information_schema` 查询表结构
3. **智能类型处理**：根据列类型自动处理时间戳、字符串等
4. **支持所有表**：不限制特定表

#### 实现要点：

```bash
# 1. 从binlog提取表名
/^### INSERT INTO `([^`]+)`\.`([^`]+)`/ {
    db = extract_db_name()
    table = extract_table_name()
}

# 2. 查询表结构
mysql -N -e "
    SELECT COLUMN_NAME, DATA_TYPE 
    FROM information_schema.COLUMNS 
    WHERE TABLE_SCHEMA = '$db' AND TABLE_NAME = '$table'
    ORDER BY ORDINAL_POSITION;
"

# 3. 根据列类型处理值
if (col_type == "timestamp" || col_type == "datetime") {
    if (value ~ /^[0-9]+$/) {
        value = "FROM_UNIXTIME(" value ")"
    }
}

# 4. 生成通用INSERT语句
INSERT INTO `db`.`table` (col1, col2, ...) VALUES (val1, val2, ...)
```

### 方案2：使用VALUES语法（简化版）

如果表结构已经存在，可以使用 `VALUES` 语法，让MySQL自动匹配列：

```sql
INSERT INTO `db`.`table` VALUES (val1, val2, val3, ...)
```

**优点**：
- 简单，不需要查询表结构
- 自动匹配列顺序

**缺点**：
- 需要表结构已存在
- 列顺序必须与binlog中的顺序一致
- 时间戳需要手动转换

### 方案3：混合方案（最佳实践）

结合两种方案：

1. **DDL语句**：从binlog提取所有 `CREATE TABLE` 语句并应用
2. **DML语句**：使用通用脚本转换所有 `INSERT` 语句
3. **类型检测**：查询表结构，智能处理时间戳等特殊类型

## 实施建议

### 阶段1：基础通用化（当前可实施）

1. 移除表名硬编码，从binlog自动提取
2. 移除列名硬编码，使用 `VALUES` 语法
3. 支持处理多个表

### 阶段2：智能类型处理（推荐实施）

1. 查询表结构获取列信息
2. 根据列类型自动转换时间戳
3. 处理其他特殊类型（JSON、BLOB等）

### 阶段3：完整通用化（长期目标）

1. 支持所有DDL语句（CREATE、ALTER、DROP等）
2. 支持所有DML语句（INSERT、UPDATE、DELETE）
3. 支持事务边界处理
4. 支持多数据库恢复

## 当前代码位置

- **测试脚本**：`test.py` (Python 版本)
- **恢复脚本**：`scripts/tasks/restore/point_in_time_restore.py` (Python 版本)
- **通用脚本**：`scripts/tasks/binlog/apply_binlog_universal.py` (Python 版本)

## 使用示例

### 当前方式（硬编码）：
```bash
# 只恢复 timestamp_test 表
awk '/^### INSERT INTO.*timestamp_test/ { ... }'
```

### 改进后（通用）：
```bash
# 恢复所有表
docker-compose exec mysql python3 /scripts/tasks/binlog/apply_binlog_universal.py \
    /backups/binlog_backup_*/mysql-bin.000004 \
    '2025-11-27 16:15:54' \
    testdb
```

## 注意事项

1. **表结构必须存在**：恢复前需要先应用DDL语句创建表
2. **列顺序一致**：binlog中的列顺序必须与表结构一致
3. **时间戳处理**：需要根据实际列类型判断是否需要转换
4. **性能考虑**：查询表结构会增加开销，可以缓存结果

## 测试建议

1. 创建多个不同结构的表进行测试
2. 测试不同数据类型（INT、VARCHAR、TIMESTAMP、DATETIME等）
3. 测试多表恢复场景
4. 验证时间戳转换的正确性

