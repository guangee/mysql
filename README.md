# MySQL 8.0 备份方案

本项目提供了一个基于 Docker Compose 的 MySQL 8.0 数据库备份方案，使用 Percona-XtraBackup 进行全量和增量备份，并将备份文件存储到 S3 兼容的对象存储中。

## 架构特点

**集成式设计**：所有功能（MySQL 数据库、Percona-XtraBackup、备份调度、S3 上传）都集成在一个基于 MySQL 8.0.44 的自定义镜像中，简化部署和管理。

## 功能特性

- ✅ MySQL 8.0.44 数据库
- ✅ Percona-XtraBackup 8.0 全量备份（内置在 MySQL 容器中）
- ✅ Percona-XtraBackup 8.0 增量备份（内置在 MySQL 容器中）
- ✅ S3 兼容对象存储支持（AWS S3、阿里云 OSS、腾讯云 COS、华为云 OBS、七牛云、MinIO 等）
- ✅ 可配置的备份周期（Cron 格式）
- ✅ 自动清理旧备份
- ✅ 完整的日志记录
- ✅ 单容器架构，资源占用更少

## 目录结构

```
mysql-doc/
├── docker-compose.yml              # Docker Compose 配置文件
├── Dockerfile                       # MySQL 集成备份功能的 Dockerfile
├── docker-entrypoint-with-backup.sh # 自定义入口点脚本
├── env.example                     # 环境变量示例文件
├── .env                            # 环境变量配置文件（需要创建）
├── start.sh                        # 快速启动脚本
├── README.md                       # 本文件
├── scripts/                        # 备份脚本目录
│   ├── full-backup.sh              # 全量备份脚本
│   ├── incremental-backup.sh       # 增量备份脚本
│   ├── start-backup.sh             # 启动和调度脚本
│   ├── cleanup-old-backups.sh      # 清理旧备份脚本
│   └── restore-backup.sh           # 备份恢复脚本
├── mysql_data/                     # MySQL 数据目录（自动创建）
├── mysql_config/                   # MySQL 配置文件目录（自动创建）
└── backups/                        # 备份文件目录（自动创建）
    ├── full/                       # 全量备份目录
    ├── incremental/                # 增量备份目录
    └── backup.log                  # 备份日志文件
```

## 快速开始

### 1. 配置环境变量

复制环境变量示例文件并修改配置：

```bash
cp env.example .env
```

编辑 `.env` 文件，配置以下参数：

- **MySQL 配置**: 数据库用户名、密码、端口等
- **S3 兼容对象存储配置**: S3 备份开关、endpoint、访问密钥、存储桶等
- **备份配置**: 全量备份和增量备份的 Cron 计划

### 2. 启动服务

**方式一：使用快速启动脚本（推荐）**

```bash
chmod +x start.sh
./start.sh
```

**方式二：使用 Docker Compose**

```bash
docker-compose up -d
```

这将启动以下服务：
- `mysql`: MySQL 8.0.44 数据库（集成备份功能，自动执行备份任务，可配置是否上传到 S3 兼容对象存储）

### 3. 访问服务

- **MySQL**: `localhost:3306` (默认端口，可在 .env 中修改)
- **S3 对象存储**: 通过配置的环境变量连接到外部 S3 兼容对象存储

### 4. 数据存储位置

所有数据都存储在项目目录下的本地文件夹中：

- **MySQL 数据**: `./mysql_data/` - MySQL 数据库文件
- **MySQL 配置**: `./mysql_config/` - MySQL 配置文件
- **备份文件**: `./backups/` - 本地备份文件（会根据配置定期清理）

> **注意**: 这些目录会在首次启动时自动创建，已添加到 `.gitignore` 中，不会被 Git 跟踪。

### 5. 查看备份日志

```bash
# 查看 MySQL 容器日志（包含备份日志）
docker-compose logs -f mysql

# 或直接查看备份日志文件（现在可以直接在本地查看）
tail -f ./backups/backup.log

# 或在容器内查看
docker exec mysql8044 tail -f /backups/backup.log
```

## 备份配置

### Cron 计划格式

备份计划使用标准 Cron 格式：`分钟 小时 日 月 星期`

示例：
- `0 2 * * 0` - 每周日凌晨 2 点
- `0 3 * * *` - 每天凌晨 3 点
- `0 */6 * * *` - 每 6 小时
- `0 2 1 * *` - 每月 1 日凌晨 2 点

### 默认配置

- **全量备份**: 每周日凌晨 2 点 (`0 2 * * 0`)
- **增量备份**: 每天凌晨 3 点 (`0 3 * * *`)
- **备份保留**: 30 天

### 修改备份计划

编辑 `.env` 文件中的以下变量：

```bash
# 全量备份计划（每周日凌晨 2 点）
FULL_BACKUP_SCHEDULE=0 2 * * 0

# 增量备份计划（每天凌晨 3 点）
INCREMENTAL_BACKUP_SCHEDULE=0 3 * * *
```

修改后重启服务：

```bash
docker-compose restart mysql
```

## 手动执行备份

### 执行全量备份

```bash
docker exec mysql8044 /scripts/full-backup.sh
```

### 执行增量备份

```bash
docker exec mysql8044 /scripts/incremental-backup.sh
```

### 清理旧备份

```bash
docker exec mysql8044 /scripts/cleanup-old-backups.sh
```

### 恢复备份

使用恢复脚本可以帮助您准备备份文件用于恢复：

```bash
# 恢复指定时间戳的全量备份
docker exec mysql8044 /scripts/restore-backup.sh 20240101_020000

# 恢复全量备份并应用增量备份
docker exec mysql8044 /scripts/restore-backup.sh 20240101_020000 backup_20240102_030000.tar.gz backup_20240103_030000.tar.gz
```

## 备份存储结构

### S3 存储结构（当 S3_BACKUP_ENABLED=true 时）

备份文件存储在 S3 兼容对象存储的以下路径：

```
mysql-backups/
├── full/                    # 全量备份目录
│   ├── backup_20240101_020000.tar.gz
│   ├── backup_20240108_020000.tar.gz
│   └── ...
├── incremental/            # 增量备份目录
│   ├── backup_20240102_030000.tar.gz
│   ├── backup_20240103_030000.tar.gz
│   └── ...
└── .metadata/              # 元数据目录
    ├── latest_full_backup_timestamp.txt
    └── latest_incremental_backup_timestamp.txt
```

### 本地存储结构

无论是否启用 S3 备份，本地备份文件都存储在：

```
./backups/
├── full/                    # 全量备份目录
│   └── 20240101_020000/     # 备份时间戳目录
│       └── backup.tar.gz    # 备份压缩文件
├── incremental/            # 增量备份目录
│   └── 20240102_030000/     # 备份时间戳目录
│       └── backup.tar.gz    # 备份压缩文件
└── backup.log              # 备份日志文件
```

当 `S3_BACKUP_ENABLED=false` 时，备份文件只存储在本地，不会上传到 S3。

## S3 备份开关

可以通过环境变量 `S3_BACKUP_ENABLED` 控制是否启用 S3 备份：

- **`S3_BACKUP_ENABLED=true`** (默认): 启用 S3 备份，备份文件会上传到 S3 兼容对象存储
- **`S3_BACKUP_ENABLED=false`**: 禁用 S3 备份，只进行本地备份，不会上传到 S3

### 关闭 S3 备份

如果只需要本地备份，可以在 `.env` 文件中设置：

```bash
S3_BACKUP_ENABLED=false
```

当 S3 备份关闭时：
- ✅ 备份仍然会正常执行（全量和增量备份）
- ✅ 备份文件保存在本地目录 `./backups/`
- ❌ 不会上传到 S3 对象存储
- ❌ 增量备份只能使用本地的基础备份（无法从 S3 下载）

### 启用 S3 备份

在 `.env` 文件中设置：

```bash
S3_BACKUP_ENABLED=true
```

并配置相应的 S3 连接信息。

## S3 兼容对象存储配置示例

### AWS S3

```bash
S3_ENDPOINT=s3.amazonaws.com
S3_ACCESS_KEY=your_aws_access_key
S3_SECRET_KEY=your_aws_secret_key
S3_BUCKET=mysql-backups
S3_REGION=us-east-1
S3_USE_SSL=true
S3_FORCE_PATH_STYLE=false
```

### 阿里云 OSS

```bash
S3_ENDPOINT=oss-cn-beijing.aliyuncs.com
S3_ACCESS_KEY=your_oss_access_key
S3_SECRET_KEY=your_oss_secret_key
S3_BUCKET=mysql-backups
S3_REGION=cn-beijing
S3_USE_SSL=true
S3_FORCE_PATH_STYLE=false
```

### 腾讯云 COS

```bash
S3_ENDPOINT=cos.ap-beijing.myqcloud.com
S3_ACCESS_KEY=your_cos_secret_id
S3_SECRET_KEY=your_cos_secret_key
S3_BUCKET=mysql-backups
S3_REGION=ap-beijing
S3_USE_SSL=true
S3_FORCE_PATH_STYLE=false
```

### MinIO (自建)

```bash
S3_ENDPOINT=minio.example.com:9000
S3_ACCESS_KEY=your_minio_access_key
S3_SECRET_KEY=your_minio_secret_key
S3_BUCKET=mysql-backups
S3_REGION=us-east-1
S3_USE_SSL=false
S3_FORCE_PATH_STYLE=true
```

## 监控和维护

### 查看备份状态

```bash
# 查看所有服务状态
docker-compose ps

# 查看备份服务日志
docker-compose logs backup

# 查看最近的备份日志
docker exec mysql8044 tail -n 100 /backups/backup.log
```

### 检查 S3 中的备份

使用 mc 命令检查备份文件：

```bash
# 列出全量备份
docker exec mysql8044 mc ls s3/mysql-backups/full/

# 列出增量备份
docker exec mysql8044 mc ls s3/mysql-backups/incremental/
```

### 验证备份完整性

```bash
# 列出所有备份文件
docker exec mysql8044 mc ls -r s3/mysql-backups/

# 检查备份文件大小
docker exec mysql8044 mc ls -r s3/mysql-backups/ | awk '{print $3, $6}'
```

## 故障排查

### 备份失败

1. 检查 MySQL 连接：
   ```bash
   docker exec mysql8044 mysql -h localhost -u root -p${MYSQL_ROOT_PASSWORD} -e "SELECT 1"
   ```

2. 检查 S3 连接：
   ```bash
   docker exec mysql8044 mc alias list
   docker exec mysql8044 mc ls s3/mysql-backups/
   ```

3. 查看详细日志：
   ```bash
   docker-compose logs backup
   ```

### 增量备份找不到基础备份

如果增量备份提示找不到基础备份：

1. 手动执行一次全量备份
2. 检查 `/backups/LATEST_FULL_BACKUP` 文件是否存在

### Cron 任务未执行

1. 检查 cron 服务是否运行：
   ```bash
   docker exec mysql8044 service cron status
   ```

2. 查看 cron 任务列表：
   ```bash
   docker exec mysql8044 crontab -l
   ```

3. 手动测试备份脚本：
   ```bash
   docker exec mysql8044 /scripts/full-backup.sh
   ```

## 安全建议

1. **修改默认密码**: 在生产环境中，务必修改 `.env` 文件中的所有默认密码和访问密钥
2. **网络安全**: 考虑使用 Docker 内部网络，不要将 MySQL 端口暴露到公网
3. **备份加密**: 考虑对备份文件进行加密后再上传到 S3
4. **访问控制**: 配置 S3 存储桶的访问策略，限制备份存储桶的访问权限，使用最小权限原则
5. **定期测试恢复**: 定期测试备份恢复流程，确保备份可用
6. **密钥管理**: 使用密钥管理服务（如 AWS Secrets Manager、阿里云 KMS）管理访问密钥，不要硬编码在配置文件中

## 性能优化

1. **并行备份**: 已在脚本中配置并行压缩（`--parallel` 和 `--compress-threads`）
2. **网络优化**: 如果 S3 在同一区域，可以减少网络延迟
3. **存储优化**: 定期清理旧备份，避免存储空间不足
4. **压缩优化**: 根据网络带宽和 CPU 性能调整压缩级别

## 许可证

本项目仅供学习和参考使用。

