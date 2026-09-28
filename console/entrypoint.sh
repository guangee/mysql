#!/bin/bash
set -e

mkdir -p /app/data /app/logs /var/log/nginx

start_redis() {
  redis-server /app/redis.conf
  for _ in $(seq 1 30); do
    if redis-cli ping >/dev/null 2>&1; then
      echo "Redis started (127.0.0.1:6379)"
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

is_production_mode() {
  [[ $# -eq 0 ]] || [[ "$1" == "gunicorn" ]] || [[ "$1" == "--production" ]]
}

run_production() {
  celery -A config worker -B \
    --loglevel=info \
    --logfile=/app/logs/celery-worker.log \
    --concurrency=1 &
  CELERY_PID=$!
  echo "Celery worker started (pid=${CELERY_PID})"

  gunicorn -c /app/gunicorn.conf.py config.wsgi:application &
  GUNICORN_PID=$!
  echo "Gunicorn started (pid=${GUNICORN_PID})"

  nginx -g 'daemon off;' &
  NGINX_PID=$!
  echo "Nginx started (pid=${NGINX_PID})"

  term_handler() {
    kill -TERM "${NGINX_PID}" 2>/dev/null || true
    kill -TERM "${GUNICORN_PID}" 2>/dev/null || true
    kill -TERM "${CELERY_PID}" 2>/dev/null || true
    stop_redis
  }
  trap term_handler TERM INT

  wait "${NGINX_PID}"
  term_handler
  wait "${GUNICORN_PID}" 2>/dev/null || true
  wait "${CELERY_PID}" 2>/dev/null || true
}

# 清理超过保留天数的日志文件
RETENTION_DAYS="${CONSOLE_LOG_RETENTION_DAYS:-1}"
find /app/logs -type f \( -name "*.log" -o -name "*.log.*" \) -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true
find /var/log/nginx -type f -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true

python manage.py migrate --noinput
python manage.py collectstatic --noinput 2>/dev/null || true

if [ -n "${CONSOLE_ADMIN_USER}" ] && [ -n "${CONSOLE_ADMIN_PASSWORD}" ]; then
  python manage.py shell <<EOF
from django.contrib.auth import get_user_model
User = get_user_model()
username = "${CONSOLE_ADMIN_USER}"
password = "${CONSOLE_ADMIN_PASSWORD}"
user = User.objects.filter(username=username).first()
if user is None:
    User.objects.create_superuser(username, "", password)
    print(f"Superuser {username} created")
else:
    user.set_password(password)
    user.is_staff = True
    user.is_superuser = True
    user.save()
    print(f"Superuser {username} password synced from environment")
EOF
fi

python manage.py shell <<'EOF'
from apps.storages.services import import_from_env, export_storages_config
import_from_env()
export_storages_config()
print("Storage config initialized")
EOF

python manage.py shell <<'EOF'
from apps.backups.services import init_retention_policy_from_env
init_retention_policy_from_env()
print("Backup retention policy initialized")
EOF

start_redis || exit 1

python manage.py shell <<'EOF'
from apps.backups.tasks import sync_backup_file_index_task
sync_backup_file_index_task.delay()
print("Backup file index sync scheduled")
EOF

python manage.py shell <<'EOF'
from apps.core.tasks import collect_dashboard_snapshot_task, sync_schema_inventory_task
collect_dashboard_snapshot_task.delay()
sync_schema_inventory_task.delay()
print("Dashboard snapshot and schema inventory sync scheduled")
EOF

# MySQL 由 API / Celery 按需连接；未就绪时首页显示未连接，无需阻塞启动

if is_production_mode "$@"; then
  run_production
  exit 0
fi

exec "$@"
