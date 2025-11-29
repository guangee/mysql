# MySQL 8.0 备份恢复方案

基于 Docker Compose 的 MySQL 8.0 数据库备份恢复方案，使用 Percona-XtraBackup 进行全量和增量备份，支持 S3 兼容对象存储（如 MinIO），并提供时间点恢复（PITR）功能。

## 功能特性

- ✅ **MySQL 8.0.35** 数据库
- ✅ **Percona-XtraBackup 8.0** 全量和增量备份
- ✅ **S3 兼容对象存储**支持（MinIO、AWS S3 等）
- ✅ **自动定时备份**（Cron 调度）
- ✅ **手动备份**（全量/增量）
- ✅ **普通恢复**（恢复到备份时间点）
- ✅ **时间点恢复（PITR）**（恢复到任意指定时间点）
- ✅ **钉钉机器人通知**（备份成功/失败提醒）
- ✅ **自动清理旧备份**
- ✅ **完整的日志记录**

## 快速开始

### 1. 配置环境变量

创建 `.env` 文件或直接在 `docker-compose.yml` 中配置环境变量：

```yaml
services:
  mysql:
    environment:
      # MySQL 配置
      MYSQL_ROOT_PASSWORD: your_root_password
      MYSQL_DATABASE: your_database
      
      # S3 兼容对象存储配置（MinIO 示例）
      S3_BACKUP_ENABLED: true
      S3_ENDPOINT: minio.example.com:9000
      S3_ACCESS_KEY: your_access_key
      S3_SECRET_KEY: your_secret_key
      S3_BUCKET: mysql-backups
      S3_REGION: us-east-1
      S3_USE_SSL: false
      S3_FORCE_PATH_STYLE: true
      
      # 备份配置
      FULL_BACKUP_SCHEDULE: "0 2 * * 0"        # 每周日凌晨 2 点
      INCREMENTAL_BACKUP_SCHEDULE: "0 3 * * *"  # 每天凌晨 3 点
      BACKUP_RETENTION_DAYS: 30
      
      # 钉钉机器人通知配置（可选）
      DINGTALK_WEBHOOK_ENABLED: false
      # DINGTALK_WEBHOOK_URL: https://oapi.dingtalk.com/robot/send?access_token=your_token
```

### 2. 启动服务

```bash
docker-compose up -d
```

### 3. 查看服务状态

```bash
# 查看容器状态
docker-compose ps

# 查看日志
docker-compose logs -f mysql

# 查看备份日志
docker-compose exec mysql tail -f /backups/backup.log
```

## 主动备份

### 方式一：自动定时备份（推荐）

通过 Cron 定时任务自动执行备份，无需手动干预。

#### 配置备份计划

在 `docker-compose.yml` 中配置：

```yaml
environment:
  # 全量备份计划（Cron 格式：分钟 小时 日 月 星期）
  FULL_BACKUP_SCHEDULE: "0 2 * * 0"        # 每周日凌晨 2 点
  INCREMENTAL_BACKUP_SCHEDULE: "0 3 * * *"  # 每天凌晨 3 点
```

**Cron 格式说明**：`分钟 小时 日 月 星期`

常用示例：
- `0 2 * * 0` - 每周日凌晨 2 点
- `0 3 * * *` - 每天凌晨 3 点
- `0 */6 * * *` - 每 6 小时
- `0 2 1 * *` - 每月 1 日凌晨 2 点

#### 修改备份计划

修改 `docker-compose.yml` 后重启服务：

```bash
docker-compose restart mysql
```

### 方式二：手动执行全量备份

```bash
docker-compose exec mysql /scripts/full-backup.sh
```

### 方式三：手动执行增量备份

```bash
docker-compose exec mysql /scripts/incremental-backup.sh
```

**注意**：增量备份需要先有全量备份作为基础。

### 查看备份状态

```bash
# 查看本地备份文件
docker-compose exec mysql ls -lh /backups/full/
docker-compose exec mysql ls -lh /backups/incremental/

# 查看 S3 中的备份（如果启用了 S3）
docker-compose exec mysql mc ls s3/mysql-backups/full/
docker-compose exec mysql mc ls s3/mysql-backups/incremental/

# 查看备份日志
docker-compose exec mysql tail -n 100 /backups/backup.log
```

## 主动恢复

### 方式一：普通恢复（恢复到备份时间点）

恢复到指定备份的时间点状态。

#### 1. 停止 MySQL 服务

```bash
docker-compose stop mysql
```

#### 2. 执行恢复

**方式 A：使用统一入口（推荐）**

```bash
# 恢复指定时间戳的全量备份（自动从 S3 下载，如果启用）
docker-compose run --rm mysql python3 /scripts/main.py restore backup 20251127_020000

# 恢复全量备份并应用增量备份
docker-compose run --rm mysql python3 /scripts/main.py restore backup 20251127_020000 backup_20251128_030000.tar.gz backup_20251129_030000.tar.gz
```

**方式 B：直接调用 Python 脚本**

```bash
# 恢复指定时间戳的全量备份（自动从 S3 下载，如果启用）
docker-compose run --rm mysql python3 /scripts/tasks/restore/restore_backup.py 20251127_020000

# 恢复全量备份并应用增量备份
docker-compose run --rm mysql python3 /scripts/tasks/restore/restore_backup.py 20251127_020000 backup_20251128_030000.tar.gz backup_20251129_030000.tar.gz
```

**说明**：
- 脚本会自动从 S3 下载备份（如果启用了 S3 备份）
- 支持从本地备份恢复（如果备份文件已存在）
- 支持恢复全量备份并应用多个增量备份

#### 3. 应用恢复

恢复脚本会下载并准备好备份，但不会自动应用到数据目录。需要手动应用：

```bash
# 使用统一入口
docker-compose run --rm mysql python3 /scripts/main.py restore apply /backups/restore

# 或直接调用 Python 脚本
docker-compose run --rm mysql python3 /scripts/tasks/restore/apply_restore.py /backups/restore
```

**环境变量选项**：
- `USE_MOVE_BACK=true` - 使用 `--move-back`（恢复后删除恢复目录中的备份）
- `BACKUP_EXISTING_DATA=false` - 不备份现有数据

#### 4. 启动 MySQL 服务

```bash
docker-compose start mysql
```

### 方式二：时间点恢复（PITR - Point-in-Time Recovery）

恢复到任意指定的时间点，而不仅仅是备份的时间点。需要二进制日志（binlog）支持。

#### 1. 停止 MySQL 服务

```bash
docker-compose stop mysql
```

#### 2. 执行时间点恢复

**方式 A：使用统一入口（推荐）**

```bash
# 恢复到指定时间点（自动查找备份和二进制日志）
docker-compose run --rm \
  -e RESTORE_TZ="Asia/Shanghai" \
  mysql python3 /scripts/main.py restore pitr "2025-11-27 18:23:10"

# 指定全量备份时间戳
docker-compose run --rm \
  -e RESTORE_TZ="Asia/Shanghai" \
  mysql python3 /scripts/main.py restore pitr "2025-11-27 18:23:10" 20251127_020000

# 指定全量备份和增量备份
docker-compose run --rm \
  -e RESTORE_TZ="Asia/Shanghai" \
  mysql python3 /scripts/main.py restore pitr "2025-11-27 18:23:10" 20251127_020000 backup_20251127_030000.tar.gz
```

**方式 B：直接调用 Python 脚本**

```bash
# 恢复到指定时间点（自动查找备份和二进制日志）
docker-compose run --rm \
  -e RESTORE_TZ="Asia/Shanghai" \
  mysql python3 /scripts/tasks/restore/point_in_time_restore.py "2025-11-27 18:23:10"

# 指定全量备份时间戳
docker-compose run --rm \
  -e RESTORE_TZ="Asia/Shanghai" \
  mysql python3 /scripts/tasks/restore/point_in_time_restore.py "2025-11-27 18:23:10" 20251127_020000
```

**时间格式说明**：
- 格式：`YYYY-MM-DD HH:MM:SS`
- 时区：东8区（Asia/Shanghai）本地时间（可通过 `RESTORE_TZ` 环境变量修改）
- 示例：`"2025-11-27 18:23:10"`

**详细说明请参考**：[时间格式使用说明](使用说明-时间格式.md)

**说明**：
- 脚本会自动查找目标时间之前的最新备份（全量或增量）
- 自动应用所有相关的增量备份
- 自动从备份时间点开始应用二进制日志到目标时间点
- 如果未指定备份，会自动从 S3 下载（如果启用了 S3 备份）

#### 3. 启动 MySQL 服务

```bash
docker-compose start mysql
```

#### 4. 验证恢复结果

```bash
# 连接数据库检查数据
docker-compose exec mysql mysql -u root -p"${MYSQL_ROOT_PASSWORD}" -e "SELECT COUNT(*) FROM your_table;"
```

## 参数配置

### MySQL 配置

| 参数 | 说明 | 示例 |
|------|------|------|
| `MYSQL_ROOT_PASSWORD` | MySQL root 用户密码 | `your_root_password` |
| `MYSQL_DATABASE` | 默认数据库名 | `your_database` |
| `MYSQL_USER` | 默认数据库用户 | `your_user` |
| `MYSQL_PASSWORD` | 默认数据库用户密码 | `your_password` |

### 备份用户配置（可选）

如果希望使用专门的备份用户（推荐），可以配置：

| 参数 | 说明 | 示例 |
|------|------|------|
| `MYSQL_BACKUP_USER` | 备份专用用户 | `backup_user` |
| `MYSQL_BACKUP_PASSWORD` | 备份专用用户密码 | `backup_password` |

**备份用户所需权限**：
- `RELOAD`
- `PROCESS`
- `LOCK TABLES`
- `REPLICATION CLIENT`
- `BACKUP_ADMIN`

如果未设置，将依次使用 `MYSQL_USER` 或 `root` 用户。

### S3 兼容对象存储配置（MinIO）

| 参数 | 说明 | 必填 | 示例 |
|------|------|------|------|
| `S3_BACKUP_ENABLED` | 是否启用 S3 备份 | 是 | `true` / `false` |
| `S3_ENDPOINT` | S3 服务端点地址 | 是 | `minio.example.com:9000` |
| `S3_ACCESS_KEY` | 访问密钥 ID | 是 | `your_access_key` |
| `S3_SECRET_KEY` | 访问密钥 | 是 | `your_secret_key` |
| `S3_BUCKET` | 存储桶名称 | 是 | `mysql-backups` |
| `S3_REGION` | 区域（MinIO 通常使用 `us-east-1`） | 是 | `us-east-1` |
| `S3_USE_SSL` | 是否使用 SSL/TLS | 否 | `true` / `false`（MinIO 通常为 `false`） |
| `S3_FORCE_PATH_STYLE` | 是否使用路径样式访问 | 否 | `true`（MinIO 需要设置为 `true`） |
| `S3_ALIAS` | S3 别名（用于 MinIO 客户端） | 否 | `s3`（默认值） |

#### MinIO 配置示例

```yaml
environment:
  S3_BACKUP_ENABLED: true
  S3_ENDPOINT: 192.168.1.100:9000
  S3_ACCESS_KEY: minioadmin
  S3_SECRET_KEY: minioadmin
  S3_BUCKET: mysql-backups
  S3_REGION: us-east-1
  S3_USE_SSL: false
  S3_FORCE_PATH_STYLE: true
  S3_ALIAS: s3
```

#### S3 备份开关

如果只需要本地备份，可以关闭 S3 备份：

```yaml
environment:
  S3_BACKUP_ENABLED: false
```

当 `S3_BACKUP_ENABLED=false` 时：
- ✅ 备份仍然会正常执行（全量和增量备份）
- ✅ 备份文件保存在本地目录 `./backups/`
- ❌ 不会上传到 S3 对象存储
- ❌ 增量备份只能使用本地的基础备份

### 备份调度配置

| 参数 | 说明 | 格式 | 默认值 |
|------|------|------|--------|
| `FULL_BACKUP_SCHEDULE` | 全量备份 Cron 计划 | `分钟 小时 日 月 星期` | `0 2 * * 0`（每周日凌晨 2 点） |
| `INCREMENTAL_BACKUP_SCHEDULE` | 增量备份 Cron 计划 | `分钟 小时 日 月 星期` | `0 3 * * *`（每天凌晨 3 点） |
| `BACKUP_RETENTION_DAYS` | 备份保留天数 | 数字 | `30` |
| `LOCAL_BACKUP_RETENTION_HOURS` | 本地备份保留时间（小时） | 数字 | `0`（上传到 S3 后立即删除） |

**注意**：`LOCAL_BACKUP_RETENTION_HOURS` 仅在 `S3_BACKUP_ENABLED=true` 时生效。当 `S3_BACKUP_ENABLED=false` 时，本地备份将永久保留。

### 钉钉机器人通知配置

| 参数 | 说明 | 必填 | 示例 |
|------|------|------|------|
| `DINGTALK_WEBHOOK_ENABLED` | 是否启用钉钉通知 | 是 | `true` / `false` |
| `DINGTALK_WEBHOOK_URL` | 钉钉机器人 Webhook URL | 是（当启用时） | `https://oapi.dingtalk.com/robot/send?access_token=your_token` |

#### 配置示例

```yaml
environment:
  # 启用钉钉通知
  DINGTALK_WEBHOOK_ENABLED: true
  # 钉钉机器人 Webhook URL
  DINGTALK_WEBHOOK_URL: https://oapi.dingtalk.com/robot/send?access_token=your_access_token
```

#### 如何获取钉钉机器人 Webhook URL

1. 在钉钉群聊中，点击右上角设置 → **智能群助手**
2. 选择 **添加机器人** → **自定义**
3. 设置机器人名称和头像，选择 **加签** 或 **自定义关键词** 安全设置
4. 复制生成的 **Webhook 地址**
5. 将地址配置到 `DINGTALK_WEBHOOK_URL` 环境变量

#### 通知内容

启用钉钉通知后，备份成功或失败时都会自动发送通知：

**备份成功通知包含**：
- 备份类型（全量/增量）
- 备份时间戳
- 备份文件名
- 文件大小
- S3 上传状态

**备份失败通知包含**：
- 备份类型
- 错误时间
- 错误提示信息

### 其他配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `BACKUP_BASE_DIR` | 备份基础目录 | `/backups` |
| `RESTORE_TZ` | 恢复时使用的时区（PITR） | `Asia/Shanghai` |

## 目录结构

```
mysql/
├── docker-compose.yml              # Docker Compose 配置文件
├── Dockerfile                       # MySQL 镜像构建文件
├── docker-entrypoint.sh            # 容器入口点脚本
├── mysql_data/                     # MySQL 数据目录（自动创建）
├── mysql_config/                   # MySQL 配置文件目录（自动创建）
└── backups/                        # 备份文件目录（自动创建）
    ├── full/                       # 全量备份目录
    │   └── YYYYMMDD_HHMMSS/        # 按时间戳组织的备份
    ├── incremental/                # 增量备份目录
    │   └── YYYYMMDD_HHMMSS/        # 按时间戳组织的备份
    ├── binlog_backup_*/            # 二进制日志备份（PITR 使用）
    └── backup.log                  # 备份日志文件
```

## 备份存储结构

### 本地存储

```
./backups/
├── full/
│   └── 20251127_020000/            # 全量备份时间戳目录
│       └── backup.tar.gz            # 备份压缩文件
├── incremental/
│   └── 20251128_030000/            # 增量备份时间戳目录
│       └── backup.tar.gz            # 备份压缩文件
└── backup.log                      # 备份日志
```

### S3 存储结构（当 S3_BACKUP_ENABLED=true 时）

```
s3://mysql-backups/
├── full/
│   ├── backup_20251127_020000.tar.gz
│   └── backup_20251128_020000.tar.gz
├── incremental/
│   ├── backup_20251128_030000.tar.gz
│   └── backup_20251129_030000.tar.gz
└── .metadata/
    ├── latest_full_backup_timestamp.txt
    └── latest_incremental_backup_timestamp.txt
```

## 监控和维护

### 查看备份状态

```bash
# 查看容器状态
docker-compose ps

# 查看备份日志
docker-compose exec mysql tail -n 100 /backups/backup.log

# 查看最近的备份
docker-compose exec mysql ls -lht /backups/full/ | head -5
docker-compose exec mysql ls -lht /backups/incremental/ | head -5
```

### 检查 S3 中的备份

```bash
# 列出全量备份
docker-compose exec mysql mc ls s3/mysql-backups/full/

# 列出增量备份
docker-compose exec mysql mc ls s3/mysql-backups/incremental/

# 检查备份文件大小
docker-compose exec mysql mc ls -lh s3/mysql-backups/full/
```

### 清理旧备份

```bash
# 手动清理旧备份（根据 BACKUP_RETENTION_DAYS 配置）
docker-compose exec mysql /scripts/cleanup-old-backups.sh
```

## 故障排查

### 备份失败

1. **检查 MySQL 连接**：
   ```bash
   docker-compose exec mysql mysql -h 127.0.0.1 -u root -p"${MYSQL_ROOT_PASSWORD}" -e "SELECT 1"
   ```

2. **检查 S3 连接**（如果启用了 S3）：
   ```bash
   docker-compose exec mysql mc alias list
   docker-compose exec mysql mc ls s3/mysql-backups/
   ```

3. **查看详细日志**：
   ```bash
   docker-compose logs mysql | grep -i backup
   docker-compose exec mysql tail -n 200 /backups/backup.log
   ```

### 增量备份找不到基础备份

如果增量备份提示找不到基础备份：

1. **手动执行一次全量备份**：
   ```bash
   docker-compose exec mysql /scripts/full-backup.sh
   ```

2. **检查基础备份文件**：
   ```bash
   docker-compose exec mysql cat /backups/LATEST_FULL_BACKUP
   ```

### Cron 任务未执行

1. **检查 cron 服务状态**：
   ```bash
   docker-compose exec mysql service cron status
   ```

2. **查看 cron 任务列表**：
   ```bash
   docker-compose exec mysql crontab -l
   ```

3. **手动测试备份脚本**：
   ```bash
   docker-compose exec mysql /scripts/full-backup.sh
   ```

### 恢复失败

1. **检查备份文件是否存在**：
   ```bash
   docker-compose exec mysql ls -lh /backups/full/
   ```

2. **检查 MySQL 是否已停止**（恢复前必须停止）：
   ```bash
   docker-compose ps mysql
   ```

3. **查看恢复日志**：
   ```bash
   docker-compose logs mysql | grep -i restore
   ```

## 安全建议

1. **修改默认密码**：在生产环境中，务必修改所有默认密码
2. **使用备份专用用户**：配置 `MYSQL_BACKUP_USER` 和 `MYSQL_BACKUP_PASSWORD`，使用最小权限原则
3. **网络安全**：不要将 MySQL 端口暴露到公网
4. **S3 访问控制**：配置 MinIO 存储桶的访问策略，限制访问权限
5. **定期测试恢复**：定期测试备份恢复流程，确保备份可用
6. **密钥管理**：使用密钥管理服务管理访问密钥，不要硬编码在配置文件中

## 性能优化

1. **并行备份**：脚本已配置并行压缩，充分利用 CPU
2. **网络优化**：如果 MinIO 在同一网络，可以减少网络延迟
3. **存储优化**：定期清理旧备份，避免存储空间不足
4. **压缩优化**：根据网络带宽和 CPU 性能调整压缩级别

## 相关文档

- [时间格式使用说明](使用说明-时间格式.md) - 时间点恢复的时间格式说明
- [注意事项](注意事项.md) - 测试过程中发现的问题和解决方案

## 许可证

本项目仅供学习和参考使用。
