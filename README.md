# MySQL 一体控制台（单镜像多进程）

一个 Docker 镜像同时提供 **MySQL 8.0**、Web 控制台、备份/恢复、DTS 近实时同步与参数管理。容器内多进程：`mysqld` + Redis + Celery + Gunicorn + Nginx。

## 功能特性

- **MySQL 8.0.46** + Percona XtraBackup
- **Web 控制台**：监控、库表、账号、参数、备份管理、时间点恢复、DTS
- **S3 兼容对象存储客户端**（控制台配置外部桶，本镜像不自带对象存储服务端）
- **自动/手动备份**、整实例/单库时间点恢复、钉钉通知
- **无需挂载 docker.sock**：运维与 PITR 均在容器内完成本地执行

## 资源建议

| 场景 | 建议内存 |
|------|----------|
| 试用/开发 | ≥ 4 GB |
| 小生产 | ≥ 8 GB（含 InnoDB buffer pool） |
| 含 DTS 大表同步 | ≥ 16 GB |

## 客户部署包（推荐）

推送到 `master` 或打 `v*` 标签时，GitHub Actions 会产出可直接执行的部署包：

- Artifact / Release 附件：`mysql-console-<version>.tar.gz`
- 内容：`docker-compose.yml`、`README.md`、`.env.example`、`data/` 配置目录

```bash
tar -xzf mysql-console-*.tar.gz
cd mysql-console-*
cp .env.example .env   # 修改密码后
docker compose pull && docker compose up -d
```

本地打包：`./code/tools/pack_deploy.sh ./dist`

部署包内的 compose **只拉取镜像、不本地 build**。日常备份、恢复、策略修改请用控制台。

## 源码构建

```bash
cp .env.example .env
# 至少修改 MYSQL_ROOT_PASSWORD、CONSOLE_ADMIN_PASSWORD、CONSOLE_SECRET_KEY

./run.sh                 # 构建一体镜像并 docker compose up -d
# 或：docker compose up -d --build
```

- MySQL：`localhost:${MYSQL_PORT}`（默认 3306）
- 控制台：`http://127.0.0.1:${CONSOLE_PORT}`（默认 8888）
- 健康检查：`GET /api/healthz/`
- 账号：`.env` 中 `CONSOLE_ADMIN_USER` / `CONSOLE_ADMIN_PASSWORD`

```bash
docker compose ps
docker compose logs -f mysql
curl -s http://127.0.0.1:8888/api/healthz/
```

仅构建数据库层（无控制台）：

```bash
docker build -f docker/Dockerfile --target mysql-only -t zziaguan/mysql:8.0.46 .
```

旧版「`mysql` + `console` 双容器 + docker.sock」升级：`docker compose down`（不要加 `-v`），换用当前 compose，保留 `./data/*` 后 `docker compose up -d --build`。

## 控制台操作（优先）

登录控制台后：

| 菜单 | 用途 |
|------|------|
| 监控中心 | 系统负载、容量、CPU/内存；MySQL 运行状态与累计统计 |
| 备份管理 → 备份任务 | 手动全量/增量，查看任务进度 |
| 备份管理 → 备份文件 | 备份策略（周期、保留天数、定时清理）、上传、清理、全量恢复 |
| 备份管理 → 时间点恢复 | 整实例 PITR、单库 PITR |
| 对象存储 | 配置外部 S3 兼容桶 |

调度与保留策略在 **备份文件 → 备份策略** 中修改并保存后会同步到容器内 crontab，不必改 `docker-compose.yml`。`.env` 中的 `FULL_BACKUP_SCHEDULE` 等仅作首次导入默认值。

## 命令行备份（可选）

一体容器内 MySQL 保持运行即可：

```bash
docker compose exec mysql python3 -m mysql_backup backup full
docker compose exec mysql python3 -m mysql_backup backup incremental
docker compose exec mysql python3 -m mysql_backup backup cleanup
docker compose exec mysql ls -lh /backups/full/$(date +%Y%m%d)/
docker compose exec mysql tail -n 100 /backups/backup.log
```

增量备份需要先有全量备份。未配置对象存储时，备份只写本地 `./data/backups/`。

## 命令行恢复（可选）

**推荐走控制台**。命令行恢复会在容器内停止 `mysqld`、覆盖数据目录后再拉起，不要对整个 compose 服务 `stop`（那样会连同控制台一起停掉）。

```bash
# 按全量时间戳恢复（YYYYMMDD_HHMMSS）
docker compose exec mysql python3 -m mysql_backup restore backup 20261004_020001

# 时间点恢复（东八区本地时间，可用 RESTORE_TZ 覆盖）
docker compose exec mysql python3 -m mysql_backup restore pitr "2026-10-04 14:00:00"
```

底层仍保留 `restore apply` 等步骤，一般由上述命令或控制台任务自动串联。时间格式详见 [doc/使用说明-时间格式.md](doc/使用说明-时间格式.md)。

## 环境变量

配置写在 `.env`（由 `.env.example` 复制），compose 通过 `env_file` 注入。不要把调度写进 `docker-compose.yml` 的 `environment:`。

### MySQL

| 参数 | 说明 |
|------|------|
| `MYSQL_ROOT_PASSWORD` | root 密码 |
| `MYSQL_DATABASE` / `MYSQL_USER` / `MYSQL_PASSWORD` | 初始化业务库与账号 |
| `MYSQL_BACKUP_USER` / `MYSQL_BACKUP_PASSWORD` | 可选备份专用账号；未设则用 `MYSQL_USER` 或 root |

备份用户建议权限：`RELOAD`、`PROCESS`、`LOCK TABLES`、`REPLICATION CLIENT`、`BACKUP_ADMIN`。

### 控制台与端口

| 参数 | 说明 | 默认 |
|------|------|------|
| `MYSQL_IMAGE` | 一体镜像名 | `zziaguan/mysql:8.0.46-allinone` |
| `MYSQL_PORT` / `CONSOLE_PORT` | 宿主机映射端口 | `3306` / `8888` |
| `CONSOLE_ADMIN_USER` / `CONSOLE_ADMIN_PASSWORD` | 控制台登录 | |
| `CONSOLE_SECRET_KEY` | Django 密钥 | |

一体镜像内 MySQL / Redis / 控制台本机互通，无需再配 `CONSOLE_MYSQL_*`、`CELERY_*`。

### 对象存储（可选）

本项目只内置 `mc` 客户端。优先在控制台「对象存储」配置；也可在 `.env` 填以下变量作为空库首次导入：

`S3_BACKUP_ENABLED`、`S3_ENDPOINT`、`S3_ACCESS_KEY`、`S3_SECRET_KEY`、`S3_BUCKET`、`S3_REGION`、`S3_USE_SSL`、`S3_FORCE_PATH_STYLE`、`S3_ALIAS`。

### 备份调度与保留（首次默认）

| 参数 | 说明 | 默认 |
|------|------|------|
| `FULL_BACKUP_SCHEDULE` | 全量 Cron | `0 2 * * 0` |
| `INCREMENTAL_BACKUP_SCHEDULE` | 增量 Cron | `0 3 * * *` |
| `FULL_BACKUP_RETENTION_DAYS` | 全量保留天数 | `30` |
| `INCREMENTAL_BACKUP_RETENTION_DAYS` | 增量保留天数 | `14` |
| `BACKUP_RETENTION_DAYS` | 兼容旧字段 | `14` |
| `LOCAL_BACKUP_RETENTION_HOURS` | 本地额外保留小时；`0` 表示上传对象存储后按策略清理 | `0` |
| `CLEANUP_LOCAL_SCHEDULE` / `CLEANUP_S3_SCHEDULE` | 定时清理 | |

此后以控制台「备份策略」为准。

### 其他

| 参数 | 说明 |
|------|------|
| `DINGTALK_WEBHOOK_ENABLED` / `DINGTALK_WEBHOOK_URL` | 备份成败通知 |
| `TZ` / `RESTORE_TZ` / `BACKUP_TIMEZONE` | 默认 `Asia/Shanghai` |
| `BACKUP_BASE_DIR` | 容器内备份根目录，默认 `/backups` |

## 目录结构

```
mysql/
├── docker-compose.yml          # 源码侧编排（含 build）
├── run.sh                      # 构建一体镜像并 compose up
├── .env.example
├── deploy/package/             # 客户部署包模板（CI 打 tar）
├── code/                       # console / frontend / mysql_backup / tools
├── docker/                     # 一体镜像 Dockerfile
├── doc/
└── data/                       # 运行时卷（gitignore）
    ├── mysql_data/
    ├── mysql_config/
    ├── backups/
    ├── console_data/
    ├── logs/
    └── shared/
```

## 备份存储结构

按**日期目录**存放，一天可有多个压缩包，并带 `day.xml` 说明：

```
./data/backups/
├── full/
│   └── 20261004/
│       ├── 20261004_020001_full_v0.1.0.tar.gz
│       └── day.xml
├── incremental/
│   └── 20261004/
│       ├── 20261004_030001_incr_v0.1.0.tar.gz
│       └── day.xml
├── LATEST_FULL_BACKUP
└── backup.log
```

压缩包名：`{YYYYMMDD}_{HHMMSS}_{full|incr}_v{版本}.tar.gz`。  
`day.xml` 记录每个文件的可回滚时间、大小，以及库级元数据（表数、数据大小、近似行数）。兼容旧名 `backup_YYYYMMDD_HHMMSS.tar.gz` 与旧的时间戳子目录。

对象存储启用后，路径与本地类似：`full/YYYYMMDD/*.tar.gz`、`incremental/YYYYMMDD/*.tar.gz`。

## 故障排查

```bash
# MySQL
docker compose exec mysql mysql -h 127.0.0.1 -u root -p"${MYSQL_ROOT_PASSWORD}" -e "SELECT 1"

# 备份日志 / crontab
docker compose exec mysql tail -n 200 /backups/backup.log
docker compose exec mysql crontab -l

# 最近一次全量指针
docker compose exec mysql cat /backups/LATEST_FULL_BACKUP

# 增量找不到基线时，先做一次全量
docker compose exec mysql python3 -m mysql_backup backup full
```

对象存储连通性请在控制台「对象存储」页面测试，勿假设镜像内自带 MinIO。

## 安全建议

1. 生产环境修改全部默认密码，并限制控制台端口暴露范围
2. 可选独立备份账号，最小权限
3. 不要将 MySQL 端口暴露到公网
4. 对象存储使用桶策略与独立密钥，`.env` 勿提交到公开仓库
5. 定期在维护窗口验证恢复

## 相关文档

- [时间格式使用说明](doc/使用说明-时间格式.md)
- [注意事项](doc/注意事项.md)
- [PITR 说明](doc/README_PITR.md)
- [恢复说明](doc/README_RESTORE.md)
- [测试流程](doc/TEST_FLOW.md)

## 许可证

本项目仅供学习和参考使用。
