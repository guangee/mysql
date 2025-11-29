#!/bin/bash
set -e

# 如果第一个参数不是 mysqld，且不是以 -- 开头（MySQL 参数），则直接执行
# 如果第一个参数以 -- 开头，说明是 MySQL 参数，应该执行 mysqld
if [ "$1" != 'mysqld' ] && [ "${1#--}" = "$1" ]; then
    exec "$@"
fi

# 如果第一个参数是 mysqld，移除它（因为后面会重新执行 mysqld）
if [ "$1" = 'mysqld' ]; then
    shift
fi

# 查找 mysqld 的完整路径
MYSQLD=$(which mysqld 2>/dev/null || find /usr -name mysqld 2>/dev/null | head -1)
if [ -z "$MYSQLD" ]; then
    echo >&2 "错误: 找不到 mysqld 命令"
    exit 1
fi

# MySQL 数据目录
DATADIR="/var/lib/mysql"

# 确保 socket 目录存在
mkdir -p /var/run/mysqld
chown -R mysql:mysql /var/run/mysqld
chmod 1777 /var/run/mysqld

# 如果数据目录为空，初始化 MySQL
if [ ! -d "$DATADIR/mysql" ]; then
    echo "初始化 MySQL 数据目录..."
    
    # 创建数据目录
    mkdir -p "$DATADIR"
    
    # 如果数据目录不为空但不是有效的MySQL数据目录，清空它
    if [ -n "$(ls -A $DATADIR 2>/dev/null)" ]; then
        echo "警告: 数据目录不为空，但未找到有效的MySQL数据，清空目录..."
        # 清空目录但保留目录本身
        find "$DATADIR" -mindepth 1 -delete 2>/dev/null || {
            rm -rf "$DATADIR"/* "$DATADIR"/.[!.]* "$DATADIR"/..?* 2>/dev/null || true
        }
        echo "数据目录已清空"
    fi
    
    # 立即设置权限，确保可以访问
    chown -R mysql:mysql "$DATADIR"
    chmod 700 "$DATADIR"
    # 再次确保权限正确（处理可能的竞态条件）
    sleep 0.5
    chown -R mysql:mysql "$DATADIR" 2>/dev/null || true
    chmod 700 "$DATADIR" 2>/dev/null || true
    
    # 初始化 MySQL 8.0
    echo "执行 MySQL 初始化..."
    echo "使用 mysqld: $MYSQLD"
    "$MYSQLD" --initialize-insecure --datadir="$DATADIR" --user=mysql 2>&1 | tee /tmp/mysql-init.log || true
    
    echo "MySQL 初始化完成"
    
    # 初始化完成后立即修复权限
    echo "修复初始化后的权限..."
    chown -R mysql:mysql "$DATADIR" 2>/dev/null || true
    chmod 700 "$DATADIR" 2>/dev/null || true
    find "$DATADIR" -type f -exec chmod 600 {} \; 2>/dev/null || true
    find "$DATADIR" -type d -exec chmod 700 {} \; 2>/dev/null || true
    find "$DATADIR" -exec chown mysql:mysql {} \; 2>/dev/null || true
    echo "权限修复完成"
    
    # 创建 MySQL secure-file-priv 目录（MySQL 8.0 需要，必须在启动前创建）
    echo "创建 MySQL secure-file-priv 目录..."
    mkdir -p /var/lib/mysql-files
    chown -R mysql:mysql /var/lib/mysql-files
    chmod 750 /var/lib/mysql-files
    echo "secure-file-priv 目录创建完成"
    
    # 启动 MySQL 服务器（临时，用于初始化）
    echo "启动临时 MySQL 服务器进行初始化..."
    "$MYSQLD" --skip-networking --socket=/var/run/mysqld/mysqld.sock --user=mysql &
    pid="$!"
    
    # 等待 MySQL 启动
    echo "等待 MySQL 启动..."
    for i in {60..0}; do
        if echo 'SELECT 1' | mysql --protocol=socket -uroot -hlocalhost --socket=/var/run/mysqld/mysqld.sock &> /dev/null; then
            echo "MySQL 已启动"
            break
        fi
        if [ $i -eq 0 ]; then
            echo >&2 "MySQL 启动失败"
            exit 1
        fi
        sleep 1
    done
    
    # 设置 root 密码
    if [ -z "$MYSQL_ROOT_PASSWORD" ]; then
        echo >&2 "错误: MYSQL_ROOT_PASSWORD 环境变量未设置"
        exit 1
    fi
    
    echo "设置 root 密码..."
    mysql --protocol=socket -uroot -hlocalhost --socket=/var/run/mysqld/mysqld.sock <<-EOSQL
        ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}' ;
        -- 创建 root 用户允许远程连接
        CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}' ;
        GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION ;
        FLUSH PRIVILEGES ;
EOSQL
    
    # 创建数据库（如果指定）
    if [ "$MYSQL_DATABASE" ]; then
        echo "创建数据库: $MYSQL_DATABASE"
        mysql --protocol=socket -uroot -hlocalhost --socket=/var/run/mysqld/mysqld.sock -p"${MYSQL_ROOT_PASSWORD}" <<-EOSQL
            CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\` ;
EOSQL
        echo "数据库 $MYSQL_DATABASE 创建完成"
    fi
    
    # 创建用户（如果指定）
    if [ "$MYSQL_USER" ] && [ "$MYSQL_PASSWORD" ]; then
        echo "创建用户: $MYSQL_USER"
        mysql --protocol=socket -uroot -hlocalhost --socket=/var/run/mysqld/mysqld.sock -p"${MYSQL_ROOT_PASSWORD}" <<-EOSQL
            CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'%' IDENTIFIED BY '${MYSQL_PASSWORD}' ;
            CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'localhost' IDENTIFIED BY '${MYSQL_PASSWORD}' ;
            GRANT ALL ON \`${MYSQL_DATABASE:-*}\`.* TO '${MYSQL_USER}'@'%' ;
            GRANT ALL ON \`${MYSQL_DATABASE:-*}\`.* TO '${MYSQL_USER}'@'localhost' ;
            -- XtraBackup 所需的权限
            GRANT RELOAD, PROCESS, LOCK TABLES, REPLICATION CLIENT, BACKUP_ADMIN ON *.* TO '${MYSQL_USER}'@'localhost' ;
            GRANT RELOAD, PROCESS, LOCK TABLES, REPLICATION CLIENT, BACKUP_ADMIN ON *.* TO '${MYSQL_USER}'@'%' ;
            -- performance_schema 权限（用于 XtraBackup 查询）
            GRANT SELECT ON performance_schema.* TO '${MYSQL_USER}'@'localhost' ;
            GRANT SELECT ON performance_schema.* TO '${MYSQL_USER}'@'%' ;
            FLUSH PRIVILEGES ;
EOSQL
        echo "用户 $MYSQL_USER 创建完成（已授权 % 和 localhost，包含 XtraBackup 所需权限）"
    fi
    
    # 停止临时 MySQL 服务器
    echo "停止临时 MySQL 服务器..."
    if ! kill -s TERM "$pid" || ! wait "$pid"; then
        echo >&2 "MySQL 初始化过程失败"
        exit 1
    fi
    
    echo "MySQL 初始化完成，准备启动服务器..."
else
    echo "MySQL 数据目录已存在，跳过初始化..."
    
    # 即使数据目录已存在，也需要确保 root@'%' 用户存在（允许远程访问）
    # 这将在 MySQL 启动后通过后台任务完成
    echo "将在 MySQL 启动后确保 root@'%' 用户存在..."
fi

# 确保权限正确
# 强制修复权限，即使数据目录已存在
echo "修复 MySQL 数据目录权限..."
# 确保目录存在
mkdir -p "$DATADIR"
# 设置目录权限
chown -R mysql:mysql "$DATADIR" 2>/dev/null || true
chmod 700 "$DATADIR" 2>/dev/null || true
# 修复数据目录下所有文件的权限
find "$DATADIR" -type f -exec chmod 600 {} \; 2>/dev/null || true
find "$DATADIR" -type d -exec chmod 700 {} \; 2>/dev/null || true
find "$DATADIR" -exec chown mysql:mysql {} \; 2>/dev/null || true
# 再次验证权限（确保权限已正确设置）
if [ -d "$DATADIR" ]; then
    CURRENT_OWNER=$(stat -c '%U:%G' "$DATADIR" 2>/dev/null || echo "")
    if [ "$CURRENT_OWNER" != "mysql:mysql" ]; then
        echo "警告: 数据目录所有者不正确 ($CURRENT_OWNER)，尝试修复..."
        chown -R mysql:mysql "$DATADIR" 2>/dev/null || true
    fi
fi
echo "权限修复完成"

# 确保 socket 目录权限正确
mkdir -p /var/run/mysqld
chown -R mysql:mysql /var/run/mysqld
chmod 1777 /var/run/mysqld

# 创建 MySQL secure-file-priv 目录（MySQL 8.0 需要）
mkdir -p /var/lib/mysql-files
chown -R mysql:mysql /var/lib/mysql-files
chmod 750 /var/lib/mysql-files

# 创建函数：确保 root@'%' 用户存在（用于远程访问）
ensure_root_remote_access() {
    # 等待 MySQL 启动
    for i in {60..0}; do
        if mysqladmin ping -h localhost -u root -p"${MYSQL_ROOT_PASSWORD}" --silent 2>/dev/null; then
            break
        fi
        if [ $i -eq 0 ]; then
            echo "警告: 无法连接到 MySQL，跳过 root@'%' 用户检查"
            return 1
        fi
        sleep 1
    done
    
    # 确保 root@'%' 用户存在
    mysql -h localhost -u root -p"${MYSQL_ROOT_PASSWORD}" <<-EOSQL 2>/dev/null || true
        CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}' ;
        GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION ;
        FLUSH PRIVILEGES ;
EOSQL
    echo "已确保 root@'%' 用户存在（允许远程访问）"
}

# 应用 PITR 二进制日志 SQL（如果存在）
apply_pitr_binlog() {
    # 使用 Python 脚本处理 PITR 二进制日志应用
    # Python 脚本提供更好的错误处理和日志记录，并且会应用所有 binlog（包括 DDL）
    if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then
        local python_cmd=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
        echo "使用 Python 脚本应用 PITR 二进制日志..." >&2
        $python_cmd /scripts/tasks/binlog/apply_pitr_binlog.py
        return $?
    fi
    
    # 后备方案：使用 shell 脚本（如果 Python 不可用）
    # 注意：后备方案也会应用所有 binlog，不再过滤 DDL
    # 使用 set +e 避免命令失败导致函数提前退出
    set +e
    local pitr_marker="/backups/.pitr_restore_marker"
    echo "[DEBUG] 检查 PITR 标记文件: $pitr_marker" >&2
    if [ -f "$pitr_marker" ]; then
        echo "[DEBUG] PITR 标记文件存在" >&2
        local sql_file=$(cat "$pitr_marker" 2>/dev/null | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        echo "[DEBUG] 从标记文件读取 SQL 文件路径: '$sql_file'" >&2
        if [ -n "$sql_file" ] && [ -f "$sql_file" ]; then
            echo "[DEBUG] SQL 文件存在: $sql_file" >&2
            local sql_file_size=$(stat -c%s "$sql_file" 2>/dev/null || echo "未知")
            echo "[DEBUG] SQL 文件大小: $sql_file_size 字节" >&2
            echo "发现 PITR 恢复标记文件，准备应用二进制日志 SQL..."
            echo "SQL 文件: $sql_file"
            
            # 等待 MySQL 完全启动
            local max_wait=60
            local wait_count=0
            echo "[DEBUG] 等待 MySQL 启动（最多 $max_wait 秒）..." >&2
            while [ $wait_count -lt $max_wait ]; do
                if mysql -h localhost -u root -p"${MYSQL_ROOT_PASSWORD}" -e "SELECT 1" >/dev/null 2>&1; then
                    echo "[DEBUG] MySQL 连接成功（等待了 $wait_count 秒）" >&2
                    echo "MySQL 已启动，开始应用二进制日志 SQL..."
                    
                    # 注意：不再过滤 DDL 语句，因为 binlog 提取时已经根据备份时间点和目标时间点进行了精确的时间范围过滤
                    # 应该应用所有内容，包括 DDL 语句（CREATE TABLE、DROP TABLE、ALTER TABLE 等）
                    # 创建临时文件（用于直接应用，不过滤）
                    local filtered_sql=$(mktemp) || {
                        echo "[ERROR] 无法创建临时文件" >&2
                        return 1
                    }
                    echo "[DEBUG] 创建临时文件: $filtered_sql" >&2
                    echo "应用所有 binlog 内容（包括 DDL 和 DML）..." >&2
                    echo "原始 SQL 文件: $sql_file" >&2
                    
                    # 直接复制 SQL 文件，不过滤 DDL
                    cp "$sql_file" "$filtered_sql" 2>&1 || {
                        echo "[DEBUG] 复制 SQL 文件失败，使用原始文件" >&2
                        filtered_sql="$sql_file"
                    }
                    
                    # 检查 SQL 文件大小
                    local sql_size=$(stat -c%s "$filtered_sql" 2>/dev/null || echo "0")
                    local sql_lines=$(wc -l < "$filtered_sql" 2>/dev/null || echo "0")
                    echo "[DEBUG] SQL 文件大小: $sql_size 字节 ($sql_lines 行)" >&2
                    
                    # 应用 SQL，使用 --force 忽略表已存在的错误和重复键错误
                    echo "[DEBUG] 准备应用 SQL 文件: $filtered_sql" >&2
                    echo "应用 SQL（忽略表已存在和重复键错误）..." >&2
                    local apply_start_time=$(date +%s)
                    # 注意：对于 ROW 格式的 binlog，需要确保在正确的数据库上下文中应用
                    # 先尝试应用到 testdb 数据库
                    local apply_output=$(mysql -h localhost -u root -p"${MYSQL_ROOT_PASSWORD}" --force testdb < "$filtered_sql" 2>&1)
                    local exit_code=$?
                    if [ "$exit_code" -ne 0 ]; then
                        echo "[DEBUG] 应用到 testdb 失败，尝试应用到默认数据库..." >&2
                        # 如果失败，尝试应用到默认数据库
                        apply_output=$(mysql -h localhost -u root -p"${MYSQL_ROOT_PASSWORD}" --force < "$filtered_sql" 2>&1)
                        exit_code=$?
                    fi
                    local exit_code=$?
                    local apply_end_time=$(date +%s)
                    local apply_duration=$((apply_end_time - apply_start_time))
                    echo "[DEBUG] SQL 应用完成，耗时: ${apply_duration} 秒，退出码: $exit_code" >&2
                    
                    # 统计错误数量
                    local total_errors=$(echo "$apply_output" | grep -c "ERROR" 2>/dev/null || echo "0")
                    local error_1050=$(echo "$apply_output" | grep -c "ERROR 1050" 2>/dev/null || echo "0")
                    local error_1062=$(echo "$apply_output" | grep -c "ERROR 1062" 2>/dev/null || echo "0")
                    local error_1032=$(echo "$apply_output" | grep -c "ERROR 1032" 2>/dev/null || echo "0")
                    echo "[DEBUG] 错误统计: 总计=$total_errors, ERROR 1050=$error_1050, ERROR 1062=$error_1062, ERROR 1032=$error_1032" >&2
                    
                    # 检查输出，过滤掉表已存在的错误、重复键错误和记录不存在错误
                    # ERROR 1050: Table already exists
                    # ERROR 1062: Duplicate entry (重复键错误，在表已存在场景中可能发生)
                    # ERROR 1032: Can't find record (记录不存在错误，在二进制日志应用时可能发生，因为某些 UPDATE/DELETE 操作可能针对不存在的记录)
                    local critical_errors=$(echo "$apply_output" | grep -v "ERROR 1050\|ERROR 1062\|ERROR 1032\|Table.*already exists\|Duplicate entry\|Can't find record\|Using a password" | grep -c "ERROR" 2>/dev/null || echo "0")
                    # 确保 critical_errors 是数字
                    critical_errors=${critical_errors:-0}
                    echo "[DEBUG] 关键错误数量: $critical_errors" >&2
                    
                    # 如果退出码为 0，说明 SQL 应用成功（即使有重复键等非致命错误）
                    if [ "$exit_code" -eq 0 ]; then
                        echo "[DEBUG] 退出码为 0，视为成功" >&2
                        echo "✓ 二进制日志 SQL 应用成功！（退出码: 0，已忽略非致命错误）" >&2
                        # 删除标记文件和临时文件
                        echo "[DEBUG] 删除标记文件: $pitr_marker" >&2
                        rm -f "$pitr_marker" "$filtered_sql" || echo "[DEBUG] 警告: 删除文件失败" >&2
                        if [ ! -f "$pitr_marker" ]; then
                            echo "[DEBUG] 标记文件已成功删除" >&2
                            echo "已删除 PITR 标记文件" >&2
                        else
                            echo "[DEBUG] 警告: 标记文件仍然存在" >&2
                        fi
                    else
                        # 退出码不为 0，检查是否有致命错误
                        echo "⚠ 警告: 二进制日志 SQL 应用时遇到错误（退出码: $exit_code）" >&2
                        echo "[DEBUG] 关键错误数量: $critical_errors" >&2
                        echo "[DEBUG] 错误统计: 总计=$total_errors, ERROR 1050=$error_1050, ERROR 1062=$error_1062, ERROR 1032=$error_1032" >&2
                        echo "错误信息:" >&2
                        local other_errors=$(echo "$apply_output" | grep -v "ERROR 1050\|ERROR 1062\|ERROR 1032\|Duplicate entry\|Can't find record\|Using a password" | grep "ERROR" | head -10)
                        if [ -n "$other_errors" ]; then
                            echo "$other_errors" >&2
                        else
                            echo "（无其他错误）" >&2
                        fi
                        if [ "$critical_errors" -eq 0 ]; then
                            echo "[DEBUG] critical_errors 为 0，视为成功" >&2
                            echo "注意: 所有错误都是非致命的（重复键或记录不存在），视为成功" >&2
                            echo "✓ 二进制日志 SQL 应用成功！（已忽略非致命错误）" >&2
                            rm -f "$pitr_marker" "$filtered_sql" || echo "[DEBUG] 警告: 删除文件失败" >&2
                            if [ ! -f "$pitr_marker" ]; then
                                echo "[DEBUG] 标记文件已成功删除" >&2
                                echo "已删除 PITR 标记文件" >&2
                            else
                                echo "[DEBUG] 警告: 标记文件仍然存在" >&2
                            fi
                        else
                            echo "[DEBUG] critical_errors 不为 0 ($critical_errors)，视为失败" >&2
                            echo "⚠ 警告: 二进制日志 SQL 应用失败，请检查日志" >&2
                            echo "过滤后的 SQL 文件保存在: $filtered_sql" >&2
                            echo "可以手动检查并执行: mysql -h localhost -u root -p${MYSQL_ROOT_PASSWORD} < $filtered_sql" >&2
                        fi
                    fi
                    return 0
                fi
                sleep 1
                wait_count=$((wait_count + 1))
            done
            echo "⚠ 警告: 等待 MySQL 启动超时，无法自动应用二进制日志 SQL"
            echo "请手动执行: mysql -h localhost -u root -p${MYSQL_ROOT_PASSWORD} < $sql_file"
        else
            echo "⚠ 警告: PITR 标记文件存在，但 SQL 文件不存在或无效: $sql_file"
            rm -f "$pitr_marker"
        fi
    fi
}

# 如果数据目录已存在（非首次启动），在后台确保 root@'%' 用户存在，并应用 PITR SQL
if [ -d "$DATADIR/mysql" ]; then
    (sleep 5 && ensure_root_remote_access && apply_pitr_binlog) &
fi

# 执行实际的 mysqld 命令
# 输出调试信息
echo "准备启动 MySQL 服务器..."
echo "mysqld 路径: $MYSQLD"
echo "参数数量: $#"
if [ $# -gt 0 ]; then
    echo "参数列表:"
    for arg in "$@"; do
        echo "  - $arg"
    done
else
    echo "警告: 没有传递任何参数给 mysqld"
fi
echo "开始启动 MySQL..."

# 检查 MySQL 错误日志目录
ERROR_LOG_DIR="/var/log/mysql"
mkdir -p "$ERROR_LOG_DIR"
chown -R mysql:mysql "$ERROR_LOG_DIR"

# 在前台运行 mysqld，确保容器不会退出
# 使用 --user=mysql 确保以正确用户运行
echo "启动 MySQL 服务器（前台模式）..."
echo "执行命令: $MYSQLD --user=mysql $@"

# 先测试一下 mysqld 是否能正常启动（不实际启动，只检查配置）
if ! "$MYSQLD" --version > /dev/null 2>&1; then
    echo >&2 "错误: mysqld 无法执行"
    exit 1
fi

# 创建错误日志文件
ERROR_LOG="/var/log/mysql/error.log"
mkdir -p "$(dirname "$ERROR_LOG")"
touch "$ERROR_LOG"
chown mysql:mysql "$ERROR_LOG"

# 创建 PID 文件目录
PID_FILE_DIR="/var/run/mysqld"
mkdir -p "$PID_FILE_DIR"
chown mysql:mysql "$PID_FILE_DIR"

# 在前台运行 mysqld，将错误输出到 stderr 和日志文件
# 添加必要的参数确保 MySQL 在前台运行
echo "开始启动 MySQL（所有输出将显示在日志中）..."
echo "注意: MySQL 将在前台运行，所有日志将输出到控制台"

# 检查是否有 mysqld_safe（更安全的启动方式）
MYSQLD_SAFE=$(which mysqld_safe 2>/dev/null || find /usr -name mysqld_safe 2>/dev/null | head -1)

if [ -n "$MYSQLD_SAFE" ]; then
    echo "使用 mysqld_safe 启动 MySQL..."
    exec "$MYSQLD_SAFE" \
        --user=mysql \
        --datadir="$DATADIR" \
        --pid-file="$PID_FILE_DIR/mysqld.pid" \
        --socket="$PID_FILE_DIR/mysqld.sock" \
        --log-error="$ERROR_LOG" \
        "$@" 2>&1
else
    echo "使用 mysqld 直接启动 MySQL..."
    # 使用 exec 替换当前进程，确保容器不会退出
    # 添加 --pid-file 参数和 --skip-networking 用于测试
    exec "$MYSQLD" \
        --user=mysql \
        --datadir="$DATADIR" \
        --pid-file="$PID_FILE_DIR/mysqld.pid" \
        --socket="$PID_FILE_DIR/mysqld.sock" \
        --log-error="$ERROR_LOG" \
        --console \
        "$@" 2>&1
fi
