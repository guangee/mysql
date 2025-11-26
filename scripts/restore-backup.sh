#!/bin/bash

set -euo pipefail

# 配置变量
BACKUP_BASE_DIR=${BACKUP_BASE_DIR:-/backups}
S3_ENDPOINT=${S3_ENDPOINT}
S3_ACCESS_KEY=${S3_ACCESS_KEY}
S3_SECRET_KEY=${S3_SECRET_KEY}
S3_BUCKET=${S3_BUCKET:-mysql-backups}
S3_REGION=${S3_REGION:-us-east-1}
S3_USE_SSL=${S3_USE_SSL:-true}
S3_FORCE_PATH_STYLE=${S3_FORCE_PATH_STYLE:-false}
S3_ALIAS=${S3_ALIAS:-s3}
RESTORE_TARGET_DIR=${1:-/backups/restore}

# 日志函数
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
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

# 下载并恢复全量备份
restore_full_backup() {
    local BACKUP_FILE=$1
    local TARGET_DIR=$2
    
    log "下载全量备份: ${BACKUP_FILE}"
    mkdir -p ${TARGET_DIR}
    
    # 下载备份文件
    mc cp ${S3_ALIAS}/${S3_BUCKET}/full/${BACKUP_FILE} ${TARGET_DIR}/backup.tar.gz
    
    # 解压
    log "解压备份文件..."
    cd ${TARGET_DIR}
    tar xzf backup.tar.gz
    rm backup.tar.gz
    
    log "全量备份已恢复到: ${TARGET_DIR}"
}

# 应用增量备份
apply_incremental_backup() {
    local INCREMENTAL_FILE=$1
    local BASE_DIR=$2
    local TMP_DIR="${BASE_DIR}/tmp_incremental"
    
    log "应用增量备份: ${INCREMENTAL_FILE}"
    mkdir -p ${TMP_DIR}
    
    # 下载增量备份
    mc cp ${S3_ALIAS}/${S3_BUCKET}/incremental/${INCREMENTAL_FILE} ${TMP_DIR}/backup.tar.gz
    
    # 解压
    log "解压增量备份..."
    cd ${TMP_DIR}
    tar xzf backup.tar.gz
    
    # 解压压缩的备份文件
    log "解压 XtraBackup 文件..."
    xtrabackup --decompress --target-dir=${TMP_DIR}
    
    # 准备增量备份（合并到基础备份）
    log "合并增量备份..."
    xtrabackup --prepare --target-dir=${BASE_DIR} --incremental-dir=${TMP_DIR}
    
    # 清理临时文件
    rm -rf ${TMP_DIR}
    
    log "增量备份已应用"
}

# 恢复备份（全量 + 增量）
restore_backup() {
    local FULL_BACKUP_TIMESTAMP=$1
    shift
    local INCREMENTAL_BACKUPS=("$@")
    
    setup_s3
    
    # 恢复全量备份
    local FULL_BACKUP_FILE="backup_${FULL_BACKUP_TIMESTAMP}.tar.gz"
    local RESTORE_DIR="${RESTORE_TARGET_DIR}"
    
    restore_full_backup ${FULL_BACKUP_FILE} ${RESTORE_DIR}
    
    # 解压压缩的备份文件
    log "解压 XtraBackup 文件..."
    xtrabackup --decompress --target-dir=${RESTORE_DIR}
    
    # 准备全量备份
    log "准备全量备份..."
    xtrabackup --prepare --target-dir=${RESTORE_DIR}
    
    # 按顺序应用所有增量备份
    for INC_BACKUP in "${INCREMENTAL_BACKUPS[@]}"; do
        if [ -n "${INC_BACKUP}" ]; then
            apply_incremental_backup ${INC_BACKUP} ${RESTORE_DIR}
        fi
    done
    
    log "备份恢复完成！"
    log "恢复目录: ${RESTORE_DIR}"
    log "要应用恢复，请执行:"
    log "  xtrabackup --copy-back --target-dir=${RESTORE_DIR}"
    log "或"
    log "  xtrabackup --move-back --target-dir=${RESTORE_DIR}"
}

# 主函数
main() {
    if [ $# -lt 1 ]; then
        echo "用法: $0 <全量备份时间戳> [增量备份1] [增量备份2] ..."
        echo "示例: $0 20240101_020000 backup_20240102_030000.tar.gz backup_20240103_030000.tar.gz"
        echo ""
        echo "可用的全量备份:"
        setup_s3
        mc ls ${S3_ALIAS}/${S3_BUCKET}/full/ | awk '{print "  "$6}'
        exit 1
    fi
    
    FULL_TIMESTAMP=$1
    shift
    INCREMENTAL_LIST=("$@")
    
    restore_backup ${FULL_TIMESTAMP} "${INCREMENTAL_LIST[@]}"
}

main "$@"

