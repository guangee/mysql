# mysql_backup Python 包

正式包名：`mysql_backup`（发行名 `mysql-backup`）。

容器内可用：

```bash
python3 -m mysql_backup <category> <command>
mysql-backup <category> <command>
```

## 目录

```
code/mysql_backup/
├── __init__.py
├── __main__.py
├── cli.py
├── core/
└── tasks/               # backup / restore / binlog / notify / schedule
```

相关路径：
- 根目录 `pyproject.toml` — 包元数据（`where = ["code"]`）
- `code/tests/` — 主机侧集成测试
- `code/tools/` — 主机侧辅助脚本
- `docker/` — 镜像构建
- `doc/` — 补充文档

## 常用命令

```bash
docker compose exec mysql python3 -m mysql_backup backup full
docker compose exec mysql python3 -m mysql_backup backup incremental
docker compose exec mysql python3 -m mysql_backup restore pitr "2025-11-26 14:30:00"

# 宿主机集成测试
python code/tests/test.py
```
