#!/bin/sh
set -e

mkdir -p /var/log/nginx
RETENTION_DAYS="${NGINX_LOG_RETENTION_DAYS:-1}"
find /var/log/nginx -type f -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true

exec nginx -g 'daemon off;'
