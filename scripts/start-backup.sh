#!/bin/bash

set -euo pipefail

# 配置变量
FULL_BACKUP_SCHEDULE=${FULL_BACKUP_SCHEDULE:-"0 2 * * 0"}
INCREMENTAL_BACKUP_SCHEDULE=${INCREMENTAL_BACKUP_SCHEDULE:-"0 3 * * *"}
BACKUP_BASE_DIR=${BACKUP_BASE_DIR:-/backups}

# 日志函数
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a ${BACKUP_BASE_DIR}/backup.log
}

# 创建必要的目录
mkdir -p ${BACKUP_BASE_DIR}/full ${BACKUP_BASE_DIR}/incremental

# 配置 cron
log "配置备份计划任务..."

# 清除现有的 cron 任务
crontab -l 2>/dev/null | grep -v "full-backup.sh\|incremental-backup.sh" | crontab - || true

# 添加全量备份任务
(crontab -l 2>/dev/null; echo "${FULL_BACKUP_SCHEDULE} /scripts/full-backup.sh >> ${BACKUP_BASE_DIR}/backup.log 2>&1") | crontab -

# 添加增量备份任务
(crontab -l 2>/dev/null; echo "${INCREMENTAL_BACKUP_SCHEDULE} /scripts/incremental-backup.sh >> ${BACKUP_BASE_DIR}/backup.log 2>&1") | crontab -

# 添加本地过期备份清理任务（每小时执行一次，只清理本地）
(crontab -l 2>/dev/null; echo "0 * * * * /scripts/cleanup-old-backups.sh --local-only >> ${BACKUP_BASE_DIR}/backup.log 2>&1") | crontab -

log "备份计划任务已配置:"
log "  全量备份: ${FULL_BACKUP_SCHEDULE}"
log "  增量备份: ${INCREMENTAL_BACKUP_SCHEDULE}"
log "  本地过期备份清理: 每小时执行一次"

# 显示 cron 任务
log "当前 cron 任务:"
crontab -l | grep -E "full-backup|incremental-backup" || true

# 启动 cron 服务
log "启动 cron 服务..."
service cron start

# 执行一次全量备份（如果还没有基础备份）
if [ ! -f "${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP" ]; then
    log "未找到基础备份，执行首次全量备份..."
    /scripts/full-backup.sh
fi

# 备份服务已在后台运行
log "备份调度服务已启动，等待计划任务执行..."
log "查看日志: tail -f ${BACKUP_BASE_DIR}/backup.log"

# 保持脚本运行（但不阻塞 MySQL 主进程）
# 使用无限循环等待，但定期检查 MySQL 进程
while true; do
    sleep 60
    # 检查 MySQL 进程是否还在运行
    if ! pgrep -x mysqld > /dev/null; then
        log "检测到 MySQL 进程已停止，备份服务将退出"
        break
    fi
done

