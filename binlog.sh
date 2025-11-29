#!/bin/bash
# binlog分析脚本（简化版）
# 只显示目录和事件数量

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUPS_DIR="${SCRIPT_DIR}/backups"
MYSQL_DATA_DIR="${SCRIPT_DIR}/mysql_data"

# 检查mysqlbinlog是否可用（优先使用本地，如果没有则使用容器中的）
USE_DOCKER=false
if command -v mysqlbinlog &> /dev/null; then
    MYSQLBINLOG_CMD="mysqlbinlog"
elif docker ps --format '{{.Names}}' | grep -q "^mysql8035$"; then
    USE_DOCKER=true
    MYSQLBINLOG_CMD="mysqlbinlog"
else
    echo "错误: mysqlbinlog 命令不可用，且容器 mysql8035 未运行" >&2
    exit 1
fi

# 查找所有binlog文件
BINLOG_FILES=()

# 1. 查找backups目录下的binlog文件
while IFS= read -r -d '' file; do
    if [[ "$file" =~ mysql-bin\.[0-9]+$ ]] && [[ ! "$file" =~ \.(zst|gz|zip)$ ]]; then
        BINLOG_FILES+=("$file")
    fi
done < <(find "$BACKUPS_DIR" -name "mysql-bin.*" -type f -print0 2>/dev/null)

# 2. 查找binlog_backup_*目录下的binlog文件
while IFS= read -r -d '' file; do
    if [[ "$file" =~ mysql-bin\.[0-9]+$ ]] && [[ ! "$file" =~ \.(zst|gz|zip)$ ]]; then
        BINLOG_FILES+=("$file")
    fi
done < <(find "$BACKUPS_DIR" -path "*/binlog_backup_*/*" -name "mysql-bin.*" -type f -print0 2>/dev/null)

# 3. 查找mysql_data目录下的binlog文件
if [ -d "$MYSQL_DATA_DIR" ]; then
    while IFS= read -r -d '' file; do
        if [[ "$file" =~ mysql-bin\.[0-9]+$ ]] && [[ ! "$file" =~ \.(zst|gz|zip)$ ]]; then
            BINLOG_FILES+=("$file")
        fi
    done < <(find "$MYSQL_DATA_DIR" -name "mysql-bin.*" -type f -print0 2>/dev/null)
fi

# 去重
IFS=$'\n' BINLOG_FILES=($(printf '%s\n' "${BINLOG_FILES[@]}" | sort -u))

if [ ${#BINLOG_FILES[@]} -eq 0 ]; then
    echo "未找到任何 binlog 文件"
    exit 0
fi

# 按目录分组并统计事件数量
declare -A dir_events

for binlog_file in "${BINLOG_FILES[@]}"; do
    dir=$(dirname "$binlog_file")
    
    # 初始化目录的事件计数
    if [ -z "${dir_events[$dir]}" ]; then
        dir_events[$dir]=0
    fi
    
    # 使用mysqlbinlog提取事件数量
    temp_output=$(mktemp)
    
    if [ "$USE_DOCKER" = true ]; then
        # 转换路径
        container_path="$binlog_file"
        if [[ "$binlog_file" == *"/backups/"* ]]; then
            relative_path="${binlog_file#*/backups}"
            container_path="/backups${relative_path}"
        elif [[ "$binlog_file" == *"/mysql_data/"* ]]; then
            relative_path="${binlog_file#*/mysql_data}"
            container_path="/var/lib/mysql${relative_path}"
        fi
        docker exec mysql8035 mysqlbinlog "$container_path" > "$temp_output" 2>/dev/null || true
    else
        $MYSQLBINLOG_CMD "$binlog_file" 2>/dev/null > "$temp_output" || true
    fi
    
    # 统计事件数量（通过时间戳数量）
    if [ -s "$temp_output" ]; then
        event_count=$(grep -oE "[0-9]{6}\s+[0-9]{1,2}:[0-9]{2}:[0-9]{2}" "$temp_output" 2>/dev/null | wc -l | tr -d ' ')
        dir_events[$dir]=$((${dir_events[$dir]} + event_count))
    fi
    
    rm -f "$temp_output"
done

# 输出结果
for dir in "${!dir_events[@]}"; do
    echo "$dir: ${dir_events[$dir]} 个事件"
done

