# MySQL 一体控制台（单镜像多进程）

一个 Docker 镜像同时提供 **MySQL 8.0**、Web 控制台、备份/恢复、DTS 近实时同步与参数管理（类似 GitLab 的一体交付）。容器内多进程：`mysqld` + Redis + Celery + Gunicorn + Nginx。

## 功能特性

- ✅ **MySQL 8.0.46** + Percona XtraBackup
- ✅ **Web 控制台**（库表、账号、参数、备份、PITR、DTS）
- ✅ **S3 兼容对象存储客户端**（控制台配置外部桶）
- ✅ **自动/手动备份**、时间点恢复、钉钉通知
- ✅ **无需挂载 docker.sock**（运维与 PITR 均在容器内完成本地执行）

## 资源建议

| 场景 | 建议内存 |
|------|----------|
| 试用/开发 | ≥ 4 GB |
| 小生产 | ≥ 8 GB（含 InnoDB buffer pool） |
| 含 DTS 大表同步 | ≥ 16 GB |

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 至少修改 MYSQL_ROOT_PASSWORD、CONSOLE_ADMIN_PASSWORD、CONSOLE_SECRET_KEY
```

对象存储在控制台「对象存储」配置；也可选填 `.env` 中的 `S3_*` 作首次导入。

### 2. 启动（仅一个业务容器）

```bash
docker compose up -d --build
```

- MySQL：`localhost:${MYSQL_PORT}`（默认 3306）
- 控制台：`http://127.0.0.1:${CONSOLE_PORT}`（默认 8888）
- 健康检查：`GET /api/healthz/`
- 账号：`.env` 中 `CONSOLE_ADMIN_USER` / `CONSOLE_ADMIN_PASSWORD`

数据卷与旧版兼容：`./data/mysql_data`、`./data/backups`、`./data/console_data`、`./shared` 等可直接沿用。

### 3. 查看状态

```bash
docker compose ps
docker compose logs -f mysql
curl -s http://127.0.0.1:8888/api/healthz/
docker compose exec mysql tail -f /backups/backup.log
```

### 从双容器升级到一体镜像

旧版是 `mysql` + `console` 两个服务，并可能挂载 `/var/run/docker.sock`。升级步骤：

1. 停掉旧栈：`docker compose down`（**不要**加 `-v`，保留数据卷）
2. 拉取/构建本仓库最新 `docker-compose.yml`（仅一个 `mysql` 服务，无 sock）
3. 按 `.env.example` 补齐密码与 `CONSOLE_PORT` 等变量（一体镜像内 MySQL/Redis 地址已内置，不必再配）
4. `docker compose up -d --build`
5. 用原路径挂载：`./data/mysql_data`、`./data/mysql_config`、`./data/backups`、`./data/console_data`、`./shared`、`./data/logs`
6. 验证：`curl http://127.0.0.1:${CONSOLE_PORT}/api/healthz/` 返回 `"ok": true`，控制台可登录

控制台元数据仍在 `console_data`（SQLite），业务数据仍在 `mysql_data`，一般无需导库。

### 仅构建数据库层（无控制台）

```bash
docker build -f docker/Dockerfile --target mysql-only -t zziaguan/mysql:8.0.46 .
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

**方式 A：使用统一入口（推荐）**

```bash
docker-compose exec mysql python3 -m mysql_backup backup full
```

### 方式三：手动执行增量备份

**方式 A：使用统一入口（推荐）**

```bash
docker-compose exec mysql python3 -m mysql_backup backup incremental
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
docker-compose run --rm mysql python3 -m mysql_backup restore backup 20251127_020000

# 恢复全量备份并应用增量备份
docker-compose run --rm mysql python3 -m mysql_backup restore backup 20251127_020000 backup_20251128_030000.tar.gz backup_20251129_030000.tar.gz
```

**说明**：
- 脚本会自动从 S3 下载备份（如果启用了 S3 备份）
- 支持从本地备份恢复（如果备份文件已存在）
- 支持恢复全量备份并应用多个增量备份

#### 3. 应用恢复

恢复脚本会下载并准备好备份，但不会自动应用到数据目录。需要手动应用：

```bash
# 使用统一入口
docker-compose run --rm mysql python3 -m mysql_backup restore apply /backups/restore

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
  mysql python3 -m mysql_backup restore pitr "2025-11-27 18:23:10"

# 指定全量备份时间戳
docker-compose run --rm \
  -e RESTORE_TZ="Asia/Shanghai" \
  mysql python3 -m mysql_backup restore pitr "2025-11-27 18:23:10" 20251127_020000

# 指定全量备份和增量备份
docker-compose run --rm \
  -e RESTORE_TZ="Asia/Shanghai" \
  mysql python3 -m mysql_backup restore pitr "2025-11-27 18:23:10" 20251127_020000 backup_20251127_030000.tar.gz
```

**时间格式说明**：
- 格式：`YYYY-MM-DD HH:MM:SS`
- 时区：东8区（Asia/Shanghai）本地时间（可通过 `RESTORE_TZ` 环境变量修改）
- 示例：`"2025-11-27 18:23:10"`

**详细说明请参考**：[时间格式使用说明](docs/使用说明-时间格式.md)

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

### S3 兼容对象存储（外部桶）

本项目只内置 S3 **客户端**（`mc`），不启动任何对象存储服务端。请在控制台「对象存储」页面填写外部桶信息并测试连通性；备份任务会按启用的存储同步上传。

也可在 `.env` 中配置以下变量作为空库时的首次导入回退（默认关闭）：

| 参数 | 说明 | 必填 | 示例 |
|------|------|------|------|
| `S3_BACKUP_ENABLED` | 是否从环境变量启用/导入 S3 | 否 | `false`（默认） |
| `S3_ENDPOINT` | S3 服务端点地址 | 启用时 | `s3.amazonaws.com` / `oss-cn-hangzhou.aliyuncs.com` |
| `S3_ACCESS_KEY` | 访问密钥 ID | 启用时 | `your_access_key` |
| `S3_SECRET_KEY` | 访问密钥 | 启用时 | `your_secret_key` |
| `S3_BUCKET` | 存储桶名称 | 启用时 | `mysql-backups` |
| `S3_REGION` | 区域 | 否 | `us-east-1` |
| `S3_USE_SSL` | 是否使用 SSL/TLS | 否 | `true` / `false` |
| `S3_FORCE_PATH_STYLE` | 是否使用路径样式访问 | 否 | 多数云厂商 `false`，部分兼容实现需 `true` |
| `S3_ALIAS` | `mc` 客户端别名 | 否 | `s3`（默认值） |

未配置对象存储时：
- ✅ 备份仍然会正常执行（全量和增量备份）
- ✅ 备份文件保存在本地目录 `./data/backups/`
- ❌ 不会上传到对象存储
- ❌ 增量备份只能使用本地的基础备份

### 备份调度配置

| 参数 | 说明 | 格式 | 默认值 |
|------|------|------|--------|
| `FULL_BACKUP_SCHEDULE` | 全量备份 Cron 计划 | `分钟 小时 日 月 星期` | `0 2 * * 0`（每周日凌晨 2 点） |
| `INCREMENTAL_BACKUP_SCHEDULE` | 增量备份 Cron 计划 | `分钟 小时 日 月 星期` | `0 3 * * *`（每天凌晨 3 点） |
| `BACKUP_RETENTION_DAYS` | 备份保留天数 | 数字 | `30` |
| `LOCAL_BACKUP_RETENTION_HOURS` | 本地备份保留时间（小时） | 数字 | `0`（上传到对象存储后立即删除） |

**注意**：`LOCAL_BACKUP_RETENTION_HOURS` 仅在已配置并启用对象存储上传时生效；未启用时本地备份将按保留策略保留。

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
├── docker-compose.yml               # Compose 编排（留在根目录）
├── run.sh                           # 官方入口：构建一体镜像并 compose up
├── .env.example                     # 环境变量模板（密码/端口/调度）
├── README.md                        # 主文档
├── console/                         # Django 控制台 API（打进一体镜像）
├── frontend/                        # Vue 控制台页面（构建产物打进一体镜像）
├── shared/                          # 容器间共享的存储与备份策略
├── docker/                          # 一体镜像构建
│   ├── Dockerfile                   # targets: mysql-only | allinone（默认）
│   ├── entrypoint-allinone.sh
│   ├── mysql-service.sh
│   ├── docker-entrypoint.sh
│   └── …
├── src/                             # Python 包源码（src layout）
│   └── mysql_backup/                # 包名；容器内: python -m mysql_backup
│       ├── cli.py
│       ├── core/
│       └── tasks/
├── pyproject.toml                   # 包元数据 / console script: mysql-backup
├── tools/                           # 主机侧辅助脚本（不进镜像）
├── tests/                           # 主机侧集成测试
├── docs/                            # 补充文档
└── data/                            # 运行时数据（已 gitignore）
    ├── mysql_data/                  # MySQL 数据目录
    ├── mysql_config/                # MySQL 配置
    └── backups/                     # 备份文件
        ├── full/
        ├── incremental/
        ├── binlog_backup_*/
        └── backup.log
```

## 备份存储结构

### 本地存储

```
./data/backups/
├── full/
│   └── 20251127_020000/            # 全量备份时间戳目录
│       └── backup.tar.gz            # 备份压缩文件
├── incremental/
│   └── 20251128_030000/            # 增量备份时间戳目录
│       └── backup.tar.gz            # 备份压缩文件
└── backup.log                      # 备份日志
```

### 对象存储结构（在控制台配置并启用后）

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
# 使用统一入口（推荐）
docker-compose exec mysql python3 -m mysql_backup backup cleanup

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
   # 使用统一入口（推荐）
   docker-compose exec mysql python3 -m mysql_backup backup full
   
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
   # 使用统一入口（推荐）
   docker-compose exec mysql python3 -m mysql_backup backup full
   
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

- [时间格式使用说明](docs/使用说明-时间格式.md) - 时间点恢复的时间格式说明
- [注意事项](docs/注意事项.md) - 测试过程中发现的问题和解决方案
- [PITR 说明](docs/README_PITR.md) - 时间点恢复详细文档
- [恢复说明](docs/README_RESTORE.md) - 普通恢复详细文档
- [测试流程](docs/TEST_FLOW.md) - 测试流程说明

## 许可证

本项目仅供学习和参考使用。
