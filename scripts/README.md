# MySQL 备份恢复工具

## 目录结构

```
scripts/
├── main.py                 # 统一入口
├── core/                   # 核心模块
│   ├── __init__.py
│   ├── config.py          # 配置管理
│   ├── logger.py          # 日志功能
│   ├── docker_utils.py    # Docker 工具
│   └── mysql_utils.py     # MySQL 工具
└── tasks/                  # 任务模块
    ├── backup/            # 备份相关
    │   ├── full_backup.py
    │   ├── incremental_backup.py
    │   └── cleanup_old_backups.py
    ├── restore/           # 恢复相关
    │   ├── restore_backup.py
    │   ├── apply_restore.py
    │   └── point_in_time_restore.py
    ├── binlog/            # binlog 相关
    │   ├── convert_binlog_to_sql.py
    │   ├── convert_binlog_to_insert.py
    │   ├── apply_binlog_generic.py
    │   ├── apply_binlog_universal.py
    │   └── apply_pitr_binlog.py
    ├── notify/            # 通知相关
    │   └── dingtalk_notify.py
    └── schedule/          # 调度相关
        └── start_backup.py
```

**注意：测试脚本位于项目根目录** (`/root/mysql/`)，不在 scripts 目录内，因为：
- 测试脚本需要在主机上运行，不依赖容器
- 容器重启不会影响测试脚本的可用性

## 使用方法

### 统一入口（推荐）

使用 `main.py` 作为统一入口：

```bash
cd /root/mysql/scripts

# 备份
python main.py backup full
python main.py backup incremental
python main.py backup cleanup

# 恢复
python main.py restore backup
python main.py restore apply
python main.py restore pitr "2025-11-26 14:30:00"

# binlog
python main.py binlog to-sql
python main.py binlog to-insert
python main.py binlog apply-generic

# 通知
python main.py notify dingtalk success "备份成功"

# 调度
python main.py schedule start
```

### 直接调用（兼容旧方式）

也可以直接调用各个模块：

```bash
# 在容器内执行
docker exec mysql8044 python /scripts/tasks/backup/full_backup.py
docker exec mysql8044 python /scripts/tasks/restore/point_in_time_restore.py "2025-11-26 14:30:00"
```

### 运行测试

测试脚本在项目根目录：

```bash
cd /root/mysql

# 完整流程测试
python test.py

# 时间点恢复测试
python test2.py

# 两次增量备份之间的 PITR 测试
python test3.py
```

## 核心模块说明

### Config (core/config.py)
统一管理所有配置变量，包括：
- Docker 配置
- MySQL 配置
- 备份配置
- S3 配置
- 时区配置

### Logger (core/logger.py)
提供统一的日志功能：
- `info()` - 信息日志
- `success()` - 成功日志
- `warning()` - 警告日志
- `error()` - 错误日志
- `step()` - 步骤日志（带分隔线）

### DockerUtils (core/docker_utils.py)
提供 Docker 相关工具：
- `exec()` - 在容器内执行命令
- `compose_up()` - 启动服务
- `compose_down()` - 停止服务
- `build_image()` - 构建镜像
- `cleanup_directories()` - 清理目录

### MySQLUtils (core/mysql_utils.py)
提供 MySQL 相关工具：
- `wait_for_mysql()` - 等待 MySQL 启动
- `execute_sql()` - 执行 SQL 命令
- `execute_sql_file()` - 执行 SQL 文件
- `get_count()` - 获取表记录数

## 迁移说明

所有脚本已从 Shell 转换为 Python，并按照功能模块化组织：
- 备份脚本 → `tasks/backup/`
- 恢复脚本 → `tasks/restore/`
- binlog 脚本 → `tasks/binlog/`
- 通知脚本 → `tasks/notify/`
- 调度脚本 → `tasks/schedule/`
- 测试脚本 → 项目根目录（`/root/mysql/`）

所有 Shell 脚本已删除，请使用 Python 版本。

## 环境变量

主要环境变量（详见 `core/config.py`）：
- `CONTAINER_NAME` - 容器名称
- `MYSQL_ROOT_PASSWORD` - MySQL root 密码
- `BACKUP_BASE_DIR` - 备份基础目录
- `S3_BACKUP_ENABLED` - 是否启用 S3 备份
- `S3_ENDPOINT` - S3 端点
- `S3_ACCESS_KEY` - S3 访问密钥
- `S3_SECRET_KEY` - S3 密钥
- `S3_BUCKET` - S3 存储桶
