# mysql_backup Python 包

正式包名：`mysql_backup`（PyPI/发行名 `mysql-backup`）。

镜像构建时通过 `pip install` 安装，容器内可用：

```bash
python3 -m mysql_backup <category> <command>
mysql-backup <category> <command>
```

## 目录结构

```
src/
└── mysql_backup/
    ├── __init__.py
    ├── __main__.py          # python -m mysql_backup
    ├── cli.py               # CLI 实现 / console_scripts 入口
    ├── core/                # 配置、日志、存储、Docker/MySQL 工具
    └── tasks/               # backup / restore / binlog / notify / schedule
```

相关目录：
- 根目录 `pyproject.toml` — 包元数据与入口
- `tests/` — 主机侧集成测试
- `tools/` — 主机侧辅助脚本
- `docker/` — 镜像构建与入口脚本
- `docs/` — 补充文档

## 常用命令

```bash
# 容器内
docker compose exec mysql python3 -m mysql_backup backup full
docker compose exec mysql python3 -m mysql_backup backup incremental
docker compose exec mysql python3 -m mysql_backup backup cleanup --local-only
docker compose exec mysql python3 -m mysql_backup restore pitr "2025-11-26 14:30:00"
docker compose exec mysql mysql-backup --help

# 宿主机开发（editable）
pip install -e .
python -m mysql_backup --help
```

## 运行测试

```bash
python tests/test.py
python tests/test2.py
python tests/test3.py
```

## 核心模块

| 模块 | 说明 |
|------|------|
| `core.config` | 环境变量与路径配置 |
| `core.logger` | 日志 |
| `core.backup_storage` | 本地 / S3 备份存储 |
| `core.docker_utils` / `core.mysql_utils` | 容器与 MySQL 辅助 |
| `tasks.*` | 具体备份、恢复、binlog、通知、调度任务 |
