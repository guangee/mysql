#!/bin/bash

set -euo pipefail

# 配置变量
S3_BACKUP_ENABLED=${S3_BACKUP_ENABLED:-true}
S3_ENDPOINT=${S3_ENDPOINT}
S3_ACCESS_KEY=${S3_ACCESS_KEY}
S3_SECRET_KEY=${S3_SECRET_KEY}
S3_BUCKET=${S3_BUCKET:-mysql-backups}
S3_REGION=${S3_REGION:-us-east-1}
S3_USE_SSL=${S3_USE_SSL:-true}
S3_FORCE_PATH_STYLE=${S3_FORCE_PATH_STYLE:-false}
S3_ALIAS=${S3_ALIAS:-s3}
BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-30}
BACKUP_BASE_DIR=${BACKUP_BASE_DIR:-/backups}

# 日志函数
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a ${BACKUP_BASE_DIR}/backup.log
}

# 配置 S3 客户端
setup_s3() {
    log "配置 S3 兼容对象存储客户端..."
    
    # 验证必要的配置
    if [ -z "${S3_ENDPOINT}" ] || [ -z "${S3_ACCESS_KEY}" ] || [ -z "${S3_SECRET_KEY}" ]; then
        log "错误: S3 配置不完整，请设置 S3_ENDPOINT, S3_ACCESS_KEY 和 S3_SECRET_KEY"
        exit 1
    fi
    
    # 构建 S3 URL
    if [ "${S3_USE_SSL}" = "true" ]; then
        S3_URL="https://${S3_ENDPOINT}"
    else
        S3_URL="http://${S3_ENDPOINT}"
    fi
    
    # 配置 S3 别名
    mc alias set ${S3_ALIAS} ${S3_URL} ${S3_ACCESS_KEY} ${S3_SECRET_KEY} --api s3v4 || true
    
    log "S3 配置完成 (Endpoint: ${S3_ENDPOINT}, Bucket: ${S3_BUCKET})"
}

# 清理本地过期的备份文件
cleanup_local_expired_backups() {
    log "开始清理本地过期备份文件..."
    
    CURRENT_TIME=$(date +%s)
    CLEANED_COUNT=0
    
    # 清理全量备份目录中的过期备份
    if [ -d "${BACKUP_BASE_DIR}/full" ]; then
        for BACKUP_DIR in ${BACKUP_BASE_DIR}/full/*/; do
            if [ -f "${BACKUP_DIR}/.delete_after" ]; then
                DELETE_TIME=$(cat "${BACKUP_DIR}/.delete_after" 2>/dev/null || echo "0")
                if [ "${DELETE_TIME}" != "0" ] && [ "${CURRENT_TIME}" -ge "${DELETE_TIME}" ]; then
                    log "删除过期本地备份: ${BACKUP_DIR}"
                    rm -rf "${BACKUP_DIR}"
                    CLEANED_COUNT=$((CLEANED_COUNT + 1))
                fi
            fi
        done
    fi
    
    # 清理增量备份目录中的过期备份
    if [ -d "${BACKUP_BASE_DIR}/incremental" ]; then
        for BACKUP_DIR in ${BACKUP_BASE_DIR}/incremental/*/; do
            if [ -f "${BACKUP_DIR}/.delete_after" ]; then
                DELETE_TIME=$(cat "${BACKUP_DIR}/.delete_after" 2>/dev/null || echo "0")
                if [ "${DELETE_TIME}" != "0" ] && [ "${CURRENT_TIME}" -ge "${DELETE_TIME}" ]; then
                    log "删除过期本地备份: ${BACKUP_DIR}"
                    rm -rf "${BACKUP_DIR}"
                    CLEANED_COUNT=$((CLEANED_COUNT + 1))
                fi
            fi
        done
    fi
    
    if [ "${CLEANED_COUNT}" -gt 0 ]; then
        log "已清理 ${CLEANED_COUNT} 个过期本地备份"
    else
        log "没有需要清理的过期本地备份"
    fi
}

# 清理 S3 上的旧备份
cleanup_s3_old_backups() {
    log "开始清理 S3 上 ${BACKUP_RETENTION_DAYS} 天前的备份..."
    
    setup_s3
    
    # 清理全量备份
    log "清理 S3 全量备份..."
    mc find ${S3_ALIAS}/${S3_BUCKET}/full/ --name "backup_*.tar.gz" --older-than "${BACKUP_RETENTION_DAYS}d" --exec "mc rm {}" || true
    
    # 清理增量备份
    log "清理 S3 增量备份..."
    mc find ${S3_ALIAS}/${S3_BUCKET}/incremental/ --name "backup_*.tar.gz" --older-than "${BACKUP_RETENTION_DAYS}d" --exec "mc rm {}" || true
    
    log "S3 清理完成"
}

# 清理旧备份（本地和 S3）
cleanup_old_backups() {
    # 清理本地过期备份
    cleanup_local_expired_backups
    
    # 如果启用了 S3 备份，清理 S3 上的旧备份
    if [ "${S3_BACKUP_ENABLED}" = "true" ]; then
        cleanup_s3_old_backups
    else
        log "S3 备份已禁用，跳过 S3 清理"
    fi
    
    log "备份清理完成"
}

# 主函数
main() {
    # 如果指定了参数 --local-only，只清理本地备份
    if [ "${1:-}" = "--local-only" ]; then
        cleanup_local_expired_backups
    else
        # 默认清理本地和 S3
        cleanup_old_backups
    fi
}

main "$@"

