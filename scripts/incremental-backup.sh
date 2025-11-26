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
INCREMENTAL_BACKUP_DIR="${BACKUP_BASE_DIR}/incremental/${TIMESTAMP}"

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

# 获取基础备份路径（始终基于最新的全量备份）
get_base_backup() {
    # 检查本地是否有最新的全量备份目录
    if [ -f "${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP" ]; then
        BASE_BACKUP=$(cat ${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP)
        
        # 检查备份目录是否存在
        if [ -d "${BASE_BACKUP}" ]; then
            log "使用本地全量备份作为基础: ${BASE_BACKUP}"
            echo "${BASE_BACKUP}"
            return 0
        fi
    fi
    
    # 本地没有，如果启用了 S3 备份，尝试从 S3 下载
    if [ "${S3_BACKUP_ENABLED}" = "true" ]; then
        log "本地未找到全量备份，从 S3 下载最新的全量备份..."
        download_latest_full_backup
        
        if [ -f "${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP" ]; then
            BASE_BACKUP=$(cat ${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP)
            if [ -d "${BASE_BACKUP}" ]; then
                log "已下载并准备全量备份作为基础: ${BASE_BACKUP}"
                echo "${BASE_BACKUP}"
                return 0
            fi
        fi
    else
        log "S3 备份已禁用，无法从 S3 下载基础备份"
    fi
    
    log "错误: 无法找到或准备基础备份，请先执行全量备份"
    exit 1
}

# 从 S3 下载最新的全量备份
download_latest_full_backup() {
    log "从 S3 下载最新的全量备份..."
    
    # 尝试从元数据获取最新的全量备份时间戳
    LATEST_TIMESTAMP=$(mc cat ${S3_ALIAS}/${S3_BUCKET}/.metadata/latest_full_backup_timestamp.txt 2>/dev/null || echo "")
    
    if [ -z "${LATEST_TIMESTAMP}" ]; then
        # 如果没有元数据，从文件列表获取最新的全量备份文件
        LATEST_BACKUP=$(mc ls ${S3_ALIAS}/${S3_BUCKET}/full/ | sort -r | head -n 1 | awk '{print $6}')
        
        if [ -z "${LATEST_BACKUP}" ]; then
            log "错误: S3 中未找到全量备份"
            return 1
        fi
        
        # 提取时间戳
        LATEST_TIMESTAMP=$(echo ${LATEST_BACKUP} | sed 's/backup_\(.*\)\.tar\.gz/\1/')
    fi
    
    log "找到最新的全量备份时间戳: ${LATEST_TIMESTAMP}"
    
    LATEST_BACKUP="backup_${LATEST_TIMESTAMP}.tar.gz"
    RESTORE_DIR="${BACKUP_BASE_DIR}/full/${LATEST_TIMESTAMP}"
    mkdir -p ${RESTORE_DIR}
    
    # 下载并解压
    mc cp ${S3_ALIAS}/${S3_BUCKET}/full/${LATEST_BACKUP} ${RESTORE_DIR}/backup.tar.gz
    cd ${RESTORE_DIR}
    tar xzf backup.tar.gz
    rm backup.tar.gz
    
    # 更新标记文件
    echo "${RESTORE_DIR}" > ${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP
    echo "${LATEST_TIMESTAMP}" > ${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP_TIMESTAMP
    echo "${LATEST_BACKUP}" > ${BACKUP_BASE_DIR}/LATEST_FULL_BACKUP_FILE
    
    log "全量备份已恢复到: ${RESTORE_DIR}"
}

# 从 S3 恢复基础备份
restore_base_backup_from_s3() {
    download_latest_full_backup
}

# 执行增量备份
perform_incremental_backup() {
    log "开始增量备份..."
    
    # 获取基础备份
    BASE_BACKUP=$(get_base_backup)
    log "基础备份路径: ${BASE_BACKUP}"
    
    # 创建增量备份目录
    mkdir -p "${INCREMENTAL_BACKUP_DIR}"
    
    # 执行 xtrabackup 增量备份
    log "执行 XtraBackup 增量备份到 ${INCREMENTAL_BACKUP_DIR}..."
    
    xtrabackup \
        --host=${MYSQL_HOST} \
        --port=${MYSQL_PORT} \
        --user=${MYSQL_USER} \
        --password=${MYSQL_PASSWORD} \
        --backup \
        --target-dir=${INCREMENTAL_BACKUP_DIR} \
        --incremental-basedir=${BASE_BACKUP} \
        --compress \
        --compress-threads=2 \
        --parallel=2
    
    if [ $? -ne 0 ]; then
        log "错误: 增量备份失败"
        exit 1
    fi
    
    log "增量备份完成: ${INCREMENTAL_BACKUP_DIR}"
    
    # 解压备份文件（用于验证和后续处理）
    log "解压备份文件..."
    xtrabackup --decompress --target-dir=${INCREMENTAL_BACKUP_DIR}
    
    # 压缩备份（不进行 prepare，prepare 应该在恢复时进行）
    log "压缩备份文件..."
    cd ${INCREMENTAL_BACKUP_DIR}
    tar czf ${INCREMENTAL_BACKUP_DIR}/backup.tar.gz . --remove-files
    
    # 保存最新的增量备份信息（无论是否启用 S3）
    echo "${TIMESTAMP}" > ${BACKUP_BASE_DIR}/LATEST_INCREMENTAL_BACKUP_TIMESTAMP
    echo "backup_${TIMESTAMP}.tar.gz" > ${BACKUP_BASE_DIR}/LATEST_INCREMENTAL_BACKUP_FILE
    
    # 如果启用了 S3 备份，上传到 S3
    if [ "${S3_BACKUP_ENABLED}" = "true" ]; then
        log "S3 备份已启用，开始上传备份到 S3..."
        mc cp ${INCREMENTAL_BACKUP_DIR}/backup.tar.gz ${S3_ALIAS}/${S3_BUCKET}/incremental/backup_${TIMESTAMP}.tar.gz
        
        if [ $? -eq 0 ]; then
            log "备份成功上传到 S3: backup_${TIMESTAMP}.tar.gz"
            
            # 将元数据上传到 S3
            echo "${TIMESTAMP}" | mc pipe ${S3_ALIAS}/${S3_BUCKET}/.metadata/latest_incremental_backup_timestamp.txt
            
            # 处理本地备份文件保留策略（S3 上传成功后）
            if [ "${LOCAL_BACKUP_RETENTION_HOURS}" = "0" ]; then
                # 立即删除本地备份文件
                rm -rf ${INCREMENTAL_BACKUP_DIR}
                log "本地备份文件已清理（立即删除模式）"
            else
                # 记录删除时间，保留本地备份文件
                DELETE_TIME=$(date -d "+${LOCAL_BACKUP_RETENTION_HOURS} hours" +%s 2>/dev/null || echo $(($(date +%s) + ${LOCAL_BACKUP_RETENTION_HOURS} * 3600)))
                echo "${DELETE_TIME}" > ${INCREMENTAL_BACKUP_DIR}/.delete_after
                log "本地备份文件将保留 ${LOCAL_BACKUP_RETENTION_HOURS} 小时，预计删除时间: $(date -d "@${DELETE_TIME}" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo 'N/A')"
            fi
        else
            log "错误: 上传到 S3 失败，保留本地备份"
            exit 1
        fi
    else
        log "S3 备份已禁用，仅保留本地备份"
        log "备份文件位置: ${INCREMENTAL_BACKUP_DIR}/backup.tar.gz"
        log "注意: 本地备份将永久保留，不会自动删除"
    fi
    
    log "增量备份流程完成"
}

# 主函数
main() {
    log "========== 增量备份开始 =========="
    
    # 如果启用了 S3 备份，配置 S3 客户端
    if [ "${S3_BACKUP_ENABLED}" = "true" ]; then
        setup_s3
    else
        log "S3 备份已禁用，跳过 S3 配置"
    fi
    
    perform_incremental_backup
    log "========== 增量备份结束 =========="
}

main "$@"

