#!/bin/bash

set -euo pipefail

# 配置变量
MYSQL_HOST=${MYSQL_HOST:-localhost}
MYSQL_PORT=${MYSQL_PORT:-3306}
MYSQL_USER=${MYSQL_USER:-root}
MYSQL_PASSWORD=${MYSQL_ROOT_PASSWORD:-${MYSQL_PASSWORD:-}}
BACKUP_BASE_DIR=${BACKUP_BASE_DIR:-/backups}
S3_BACKUP_ENABLED=${S3_BACKUP_ENABLED:-true}
S3_ENDPOINT=${S3_ENDPOINT}
S3_ACCESS_KEY=${S3_ACCESS_KEY}
S3_SECRET_KEY=${S3_SECRET_KEY}
S3_BUCKET=${S3_BUCKET:-mysql-backups}
S3_REGION=${S3_REGION:-us-east-1}
S3_USE_SSL=${S3_USE_SSL:-true}
S3_FORCE_PATH_STYLE=${S3_FORCE_PATH_STYLE:-false}
S3_ALIAS=${S3_ALIAS:-s3}
LOCAL_BACKUP_RETENTION_HOURS=${LOCAL_BACKUP_RETENTION_HOURS:-0}

# 备份目录
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FULL_BACKUP_DIR="${BACKUP_BASE_DIR}/full/${TIMESTAMP}"

# 日志函数
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a /backups/backup.log
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
    if [ "${S3_FORCE_PATH_STYLE}" = "true" ]; then
        mc alias set ${S3_ALIAS} ${S3_URL} ${S3_ACCESS_KEY} ${S3_SECRET_KEY} --api s3v4 || true
    else
        mc alias set ${S3_ALIAS} ${S3_URL} ${S3_ACCESS_KEY} ${S3_SECRET_KEY} --api s3v4 || true
    fi
    
    # 创建存储桶（如果不存在）
    mc mb ${S3_ALIAS}/${S3_BUCKET} || true
    
    log "S3 配置完成 (Endpoint: ${S3_ENDPOINT}, Bucket: ${S3_BUCKET})"
}

# 执行全量备份
perform_full_backup() {
    log "开始全量备份..."
    
    # 创建备份目录
    mkdir -p "${FULL_BACKUP_DIR}"
    
    # 执行 xtrabackup 全量备份
    log "执行 XtraBackup 全量备份到 ${FULL_BACKUP_DIR}..."
    
    xtrabackup \
        --host=${MYSQL_HOST} \
        --port=${MYSQL_PORT} \
        --user=${MYSQL_USER} \
        --password=${MYSQL_PASSWORD} \
        --backup \
        --target-dir=${FULL_BACKUP_DIR} \
        --compress \
        --compress-threads=4 \
        --parallel=4
    
    if [ $? -ne 0 ]; then
        log "错误: 全量备份失败"
        exit 1
    fi
    
    log "全量备份完成: ${FULL_BACKUP_DIR}"
    
    # 准备备份（应用日志）
    log "准备备份（应用日志）..."
    xtrabackup --decompress --target-dir=${FULL_BACKUP_DIR}
    xtrabackup --prepare --target-dir=${FULL_BACKUP_DIR}
    
    # 重新压缩
    log "重新压缩备份文件..."
    cd ${FULL_BACKUP_DIR}
    tar czf ${FULL_BACKUP_DIR}/backup.tar.gz . --remove-files
    
    # 保存最新的全量备份信息，供增量备份使用（无论是否启用 S3）
    echo "${FULL_BACKUP_DIR}" > ${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP
    echo "${TIMESTAMP}" > ${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP_TIMESTAMP
    echo "backup_${TIMESTAMP}.tar.gz" > ${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP_FILE
    
    # 清除增量备份标记（全量备份后，增量备份链重新开始）
    rm -f ${BACKUP_BASE_DIR}/LATEST_INCREMENTAL_BACKUP
    rm -f ${BACKUP_BASE_DIR}/LATEST_INCREMENTAL_BACKUP_TIMESTAMP
    rm -f ${BACKUP_BASE_DIR}/LATEST_INCREMENTAL_BACKUP_FILE
    
    # 如果启用了 S3 备份，上传到 S3
    if [ "${S3_BACKUP_ENABLED}" = "true" ]; then
        log "S3 备份已启用，开始上传备份到 S3..."
        mc cp ${FULL_BACKUP_DIR}/backup.tar.gz ${S3_ALIAS}/${S3_BUCKET}/full/backup_${TIMESTAMP}.tar.gz
        
        if [ $? -eq 0 ]; then
            log "备份成功上传到 S3: backup_${TIMESTAMP}.tar.gz"
            
            # 将元数据上传到 S3
            echo "${TIMESTAMP}" | mc pipe ${S3_ALIAS}/${S3_BUCKET}/.metadata/latest_full_backup_timestamp.txt
            
            # 处理本地备份文件保留策略（S3 上传成功后）
            if [ "${LOCAL_BACKUP_RETENTION_HOURS}" = "0" ]; then
                # 立即删除本地备份文件以节省空间
                rm -rf ${FULL_BACKUP_DIR}
                log "本地备份文件已清理（立即删除模式）"
            else
                # 记录删除时间，保留本地备份文件
                DELETE_TIME=$(date -d "+${LOCAL_BACKUP_RETENTION_HOURS} hours" +%s 2>/dev/null || echo $(($(date +%s) + ${LOCAL_BACKUP_RETENTION_HOURS} * 3600)))
                echo "${DELETE_TIME}" > ${FULL_BACKUP_DIR}/.delete_after
                log "本地备份文件将保留 ${LOCAL_BACKUP_RETENTION_HOURS} 小时，预计删除时间: $(date -d "@${DELETE_TIME}" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo 'N/A')"
            fi
        else
            log "错误: 上传到 S3 失败，保留本地备份"
            exit 1
        fi
    else
        log "S3 备份已禁用，仅保留本地备份"
        log "备份文件位置: ${FULL_BACKUP_DIR}/backup.tar.gz"
        log "注意: 本地备份将永久保留，不会自动删除"
    fi
    
    log "全量备份流程完成"
}

# 主函数
main() {
    log "========== 全量备份开始 =========="
    
    # 如果启用了 S3 备份，配置 S3 客户端
    if [ "${S3_BACKUP_ENABLED}" = "true" ]; then
        setup_s3
    else
        log "S3 备份已禁用，跳过 S3 配置"
    fi
    
    perform_full_backup
    log "========== 全量备份结束 =========="
}

main "$@"

