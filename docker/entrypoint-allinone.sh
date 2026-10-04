#!/bin/bash
# 单镜像多进程入口：MySQL + Redis + Celery + Gunicorn + Nginx + 备份 cron
set -euo pipefail

export RUNTIME_MODE="${RUNTIME_MODE:-allinone}"
export CONSOLE_MYSQL_HOST="${CONSOLE_MYSQL_HOST:-127.0.0.1}"
export CONSOLE_MYSQL_PORT="${CONSOLE_MYSQL_PORT:-3306}"
export MYSQL_DATA_DIR="${MYSQL_DATA_DIR:-/var/lib/mysql}"
export MYSQL_CONFIG_DIR="${MYSQL_CONFIG_DIR:-/etc/mysql/conf.d}"
export BACKUP_BASE_DIR="${BACKUP_BASE_DIR:-/backups}"
export STORAGES_CONFIG_FILE="${STORAGES_CONFIG_FILE:-/shared/storages.json}"
export BACKUP_POLICY_FILE="${BACKUP_POLICY_FILE:-/shared/backup_policy.json}"
export PYTHONPATH="${PYTHONPATH:-/opt/mysql-backup/src}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"
export CELERY_BROKER_URL="${CELERY_BROKER_URL:-redis://127.0.0.1:6379/0}"
export CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND:-redis://127.0.0.1:6379/0}"

mkdir -p /app/data /app/logs /var/log/nginx /backups/full /backups/incremental /shared \
  /var/run/mysqld /var/log/mysql /backups/pitr-work /var/lib/mysql-files
chown mysql:mysql /var/run/mysqld /var/lib/mysql-files 2>/dev/null || true
chmod 750 /var/lib/mysql-files 2>/dev/null || true

MYSQLD_EXTRA_ARGS="${MYSQLD_EXTRA_ARGS:---default-authentication-plugin=mysql_native_password --log-bin=mysql-bin --binlog-format=ROW --binlog-row-image=FULL --binlog-row-metadata=FULL --server-id=1 --character-set-server=utf8mb4 --collation-server=utf8mb4_general_ci --explicit_defaults_for_timestamp=true --bind-address=0.0.0.0 --default-time-zone=+08:00 --binlog-expire-logs-seconds=2592000}"
export MYSQLD_EXTRA_ARGS

start_redis() {
  redis-server /app/redis.conf
  for _ in $(seq 1 30); do
    if redis-cli ping >/dev/null 2>&1; then
      echo "Redis started"
      return 0
    fi
    sleep 0.2
  done
  echo "Redis failed to start" >&2
  return 1
}

stop_redis() {
  redis-cli shutdown nosave 2>/dev/null || true
}

init_mysql_datadir() {
  if [[ ! -d /var/lib/mysql/mysql ]]; then
    echo "Initializing MySQL data directory..."
    # 使用官方 entrypoint 做首次初始化（前台短暂拉起再关掉）
    mysqld --initialize-insecure --user=mysql --datadir=/var/lib/mysql || true
    chown -R mysql:mysql /var/lib/mysql
  fi
}

bootstrap_mysql_users() {
  # 首次启动后设置 root 密码与业务库（若尚无）
  local root_pass="${MYSQL_ROOT_PASSWORD:-root}"
  if mysqladmin ping -h127.0.0.1 -uroot --silent 2>/dev/null; then
    mysql -h127.0.0.1 -uroot -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '${root_pass}'; FLUSH PRIVILEGES;" 2>/dev/null || true
  fi
  if mysqladmin ping -h127.0.0.1 -uroot -p"${root_pass}" --silent 2>/dev/null; then
    mysql -h127.0.0.1 -uroot -p"${root_pass}" -e "ALTER USER 'root'@'%' IDENTIFIED WITH mysql_native_password BY '${root_pass}'; CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH mysql_native_password BY '${root_pass}'; GRANT ALL ON *.* TO 'root'@'%' WITH GRANT OPTION; FLUSH PRIVILEGES;" 2>/dev/null || true
    if [[ -n "${MYSQL_DATABASE:-}" ]]; then
      mysql -h127.0.0.1 -uroot -p"${root_pass}" -e "CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null || true
    fi
    if [[ -n "${MYSQL_USER:-}" && -n "${MYSQL_PASSWORD:-}" ]]; then
      mysql -h127.0.0.1 -uroot -p"${root_pass}" -e "CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '${MYSQL_PASSWORD}'; GRANT ALL ON \`${MYSQL_DATABASE:-*}\`.* TO '${MYSQL_USER}'@'%'; FLUSH PRIVILEGES;" 2>/dev/null || true
    fi
  fi
}

init_console() {
  cd /app
  RETENTION_DAYS="${CONSOLE_LOG_RETENTION_DAYS:-1}"
  find /app/logs -type f \( -name "*.log" -o -name "*.log.*" \) -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true

  python3 manage.py migrate --noinput
  python3 manage.py collectstatic --noinput 2>/dev/null || true

  if [[ -n "${CONSOLE_ADMIN_USER:-}" && -n "${CONSOLE_ADMIN_PASSWORD:-}" ]]; then
    python3 manage.py shell <<EOF
from django.contrib.auth import get_user_model
User = get_user_model()
username = "${CONSOLE_ADMIN_USER}"
password = "${CONSOLE_ADMIN_PASSWORD}"
user = User.objects.filter(username=username).first()
if user is None:
    User.objects.create_superuser(username, "", password)
else:
    user.set_password(password)
    user.is_staff = True
    user.is_superuser = True
    user.save()
print("console admin ready")
EOF
  fi

  python3 manage.py shell <<'EOF'
from apps.storages.services import import_from_env, export_storages_config
from apps.backups.services import init_retention_policy_from_env
import_from_env()
export_storages_config()
init_retention_policy_from_env()
print("storage/policy ready")
EOF
}

start_console_stack() {
  cd /app
  celery -A config worker -B --loglevel=info --logfile=/app/logs/celery-worker.log -Q celery --concurrency=1 &
  CELERY_PID=$!
  celery -A config worker --loglevel=info --logfile=/app/logs/celery-dts.log -Q dts --concurrency=1 -n dts@%h &
  DTS_PID=$!
  gunicorn -c /app/gunicorn.conf.py config.wsgi:application &
  GUNICORN_PID=$!
  nginx -g 'daemon off;' &
  NGINX_PID=$!
  echo "console stack: celery=${CELERY_PID} dts=${DTS_PID} gunicorn=${GUNICORN_PID} nginx=${NGINX_PID}"
}

start_backup_cron() {
  python3 -m mysql_backup schedule start &
  BACKUP_SCHED_PID=$!
  echo "backup scheduler pid=${BACKUP_SCHED_PID}"
}

schedule_boot_tasks() {
  cd /app
  python3 manage.py shell <<'EOF'
from apps.backups.tasks import sync_backup_file_index_task
from apps.core.tasks import collect_dashboard_snapshot_task, sync_schema_inventory_task
sync_backup_file_index_task.delay()
collect_dashboard_snapshot_task.delay()
sync_schema_inventory_task.delay()
print("boot tasks queued")
EOF
}

term_handler() {
  echo "shutting down..."
  kill -TERM "${NGINX_PID:-}" 2>/dev/null || true
  kill -TERM "${GUNICORN_PID:-}" 2>/dev/null || true
  kill -TERM "${CELERY_PID:-}" 2>/dev/null || true
  kill -TERM "${DTS_PID:-}" 2>/dev/null || true
  kill -TERM "${BACKUP_SCHED_PID:-}" 2>/dev/null || true
  stop_redis
  /usr/local/bin/mysql-service stop || true
}

# --- main ---
init_mysql_datadir
/usr/local/bin/mysql-service start
bootstrap_mysql_users
start_redis
init_console
start_console_stack
start_backup_cron
schedule_boot_tasks

trap term_handler TERM INT
wait "${NGINX_PID}"
term_handler
wait "${GUNICORN_PID}" 2>/dev/null || true
wait "${CELERY_PID}" 2>/dev/null || true
wait "${DTS_PID}" 2>/dev/null || true
