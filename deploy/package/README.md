# MySQL 一体控制台 — 部署包

本包用于**直接拉起**一体镜像（MySQL 8 + Web 控制台 + 备份/恢复），无需本地编译源码。

镜像默认：`zziaguan/mysql:8.0.46-allinone`

## 环境要求

- Docker 20+ 与 Docker Compose v2
- 建议内存：试用 ≥ 4GB，生产 ≥ 8GB
- 开放端口：MySQL（默认 3306）、控制台（默认 8888）

## 快速开始

### 1. 解压

```bash
tar -xzf mysql-console-*.tar.gz
cd mysql-console-*
```

### 2. 配置

```bash
cp .env.example .env
# 至少修改：
#   MYSQL_ROOT_PASSWORD
#   CONSOLE_ADMIN_PASSWORD
#   CONSOLE_SECRET_KEY
```

可选：修改 `MYSQL_PORT`、`CONSOLE_PORT`、`MYSQL_IMAGE`。

### 3. 启动

```bash
docker compose pull
docker compose up -d
```

### 4. 验证

```bash
docker compose ps
curl -s http://127.0.0.1:8888/api/healthz/
```

浏览器打开：`http://<主机IP>:8888`  
默认账号见 `.env` 中的 `CONSOLE_ADMIN_USER` / `CONSOLE_ADMIN_PASSWORD`。

## 目录说明

| 路径 | 用途 |
|------|------|
| `docker-compose.yml` | 单容器编排（仅拉取镜像，不本地 build） |
| `.env.example` | 环境变量模板，复制为 `.env` 后修改 |
| `data/mysql_data/` | MySQL 数据文件 |
| `data/mysql_config/` | MySQL 自定义配置（`*.cnf`） |
| `data/backups/` | 本地备份与日志 |
| `data/console_data/` | 控制台元数据（SQLite 等） |
| `data/shared/` | 容器内共享临时目录 |
| `data/logs/` | 控制台/应用日志 |

首次启动会自动创建上述数据目录内容；请定期备份 `data/`。

## 常用运维

```bash
# 查看日志
docker compose logs -f mysql

# 停止 / 启动
docker compose stop
docker compose start

# 更新到新镜像版本（先改 .env 中 MYSQL_IMAGE，或重新解压新部署包）
docker compose pull
docker compose up -d

# 手动全量 / 增量备份
docker compose exec mysql python3 -m mysql_backup backup full
docker compose exec mysql python3 -m mysql_backup backup incremental
```

更多能力（PITR、对象存储、DTS、参数调优）请在 Web 控制台中操作。

## 注意事项

1. **不要**删除 `data/mysql_data`，否则业务数据会丢失。
2. `.env` 含密码，请妥善保管，勿提交到公开仓库。
3. 生产环境请修改全部默认密码，并限制控制台端口暴露范围。
4. 若本机 3306/8888 已被占用，请在 `.env` 中改端口后重启。
