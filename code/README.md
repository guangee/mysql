# code — 业务代码

| 目录 | 说明 |
|------|------|
| `console/` | Django 管理端 API（打进一体镜像 `/app`） |
| `frontend/` | Vue 管理端（构建产物打进 Nginx） |
| `mysql_backup/` | 备份/恢复 CLI（`python3 -m mysql_backup`） |
| `tests/` | 宿主机集成测试 |
| `tools/` | 主机侧脚本（镜像构建推送等） |

部署与镜像构建见仓库根目录 `docker/`、`docker-compose.yml`、`./run.sh`。  
文档见 `doc/`，运行时卷见 `data/`。
