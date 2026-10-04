#!/bin/bash
# 一体镜像内管理本机 mysqld（供控制台启停/重启）
set -euo pipefail

PID_FILE="${MYSQL_SERVICE_PID_FILE:-/var/run/mysqld/mysqld.service.pid}"
SOCK_FILE="${MYSQL_UNIX_PORT:-/var/run/mysqld/mysqld.sock}"
MYSQLD_BIN="${MYSQLD_BIN:-mysqld}"
ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-root}"

mkdir -p /var/run/mysqld /var/log/mysql /var/lib/mysql-files
chown mysql:mysql /var/run/mysqld /var/lib/mysql-files 2>/dev/null || true
chmod 750 /var/lib/mysql-files 2>/dev/null || true

is_running() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  pgrep -x mysqld >/dev/null 2>&1
}

do_start() {
  if is_running; then
    echo "mysqld already running"
    return 0
  fi
  # 优先走官方 entrypoint 初始化逻辑一次后，由我们后台拉起
  if [[ ! -d /var/lib/mysql/mysql ]]; then
    echo "Initializing MySQL datadir..."
    /usr/local/bin/docker-entrypoint.sh mysqld --version >/dev/null 2>&1 || true
  fi
  # shellcheck disable=SC2086
  set -- "$MYSQLD_BIN" --user=mysql \
    --datadir=/var/lib/mysql \
    --pid-file=/var/run/mysqld/mysqld.pid \
    --socket="$SOCK_FILE" \
    --port=3306
  # MYSQLD_EXTRA_ARGS 为空格分隔的 mysqld 参数
  # shellcheck disable=SC2206
  extra=( ${MYSQLD_EXTRA_ARGS:-} )
  "$@" "${extra[@]}" &
  echo $! >"$PID_FILE"
  for _ in $(seq 1 240); do
    if mysqladmin ping -h127.0.0.1 -uroot -p"$ROOT_PASSWORD" --silent 2>/dev/null \
      || mysqladmin ping -h127.0.0.1 -uroot --silent 2>/dev/null \
      || mysqladmin ping -h127.0.0.1 --silent 2>/dev/null \
      || mysqladmin ping --socket="$SOCK_FILE" -uroot -p"$ROOT_PASSWORD" --silent 2>/dev/null \
      || mysqladmin ping --socket="$SOCK_FILE" -uroot --silent 2>/dev/null; then
      echo "mysqld started"
      return 0
    fi
    # 进程已退出则不必空等
    if ! is_running; then
      echo "mysqld exited during startup" >&2
      return 1
    fi
    sleep 1
  done
  echo "mysqld start timeout" >&2
  return 1
}

do_stop() {
  if mysqladmin ping -h127.0.0.1 -uroot -p"$ROOT_PASSWORD" --silent 2>/dev/null; then
    mysqladmin shutdown -h127.0.0.1 -uroot -p"$ROOT_PASSWORD" 2>/dev/null || true
  fi
  for _ in $(seq 1 30); do
    if ! is_running; then
      rm -f "$PID_FILE"
      echo "mysqld stopped"
      return 0
    fi
    sleep 1
  done
  pkill -x mysqld 2>/dev/null || true
  sleep 1
  pkill -9 -x mysqld 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "mysqld force stopped"
}

case "${1:-}" in
  start) do_start ;;
  stop) do_stop ;;
  restart) do_stop; do_start ;;
  status)
    if is_running; then echo "running"; exit 0; else echo "stopped"; exit 1; fi
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
