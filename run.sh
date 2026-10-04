#!/usr/bin/env bash
# 构建一体镜像并启动 Compose（官方推荐入口）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MYSQL_IMAGE="${MYSQL_IMAGE:-zziaguan/mysql:8.0.46-allinone}"
BUILD=1
BUILD_ONLY=0
COMPOSE_ARGS=()

usage() {
  cat <<'EOF'
用法: ./run.sh [选项] [docker compose 参数…]

  默认构建一体镜像（docker/Dockerfile --target allinone），再执行 docker compose up -d。

选项:
  --no-build           跳过镜像构建（使用已有镜像）
  --build-only         仅构建镜像，不启动服务
  -h, --help           显示帮助

环境变量:
  MYSQL_IMAGE          一体镜像名（默认 zziaguan/mysql:8.0.46-allinone）

示例:
  ./run.sh
  ./run.sh --no-build
  MYSQL_IMAGE=registry.example.com/mysql:allinone ./run.sh

说明:
  旧版「单独构建 code/console/Dockerfile」已废弃；请使用本脚本或直接 docker compose。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build|--no-build-console)
      BUILD=0
      shift
      ;;
    --build-only)
      BUILD_ONLY=1
      BUILD=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      COMPOSE_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ "${BUILD}" -eq 1 ]]; then
  echo ">>> 构建一体镜像: ${MYSQL_IMAGE} (target=allinone)"
  docker build -f docker/Dockerfile --target allinone -t "${MYSQL_IMAGE}" .
  echo ">>> 一体镜像构建完成"
fi

if [[ "${BUILD_ONLY}" -eq 1 ]]; then
  echo ">>> 仅构建模式，未启动服务"
  exit 0
fi

echo ">>> 启动服务: docker compose up -d ${COMPOSE_ARGS[*]:-}"
docker compose up -d "${COMPOSE_ARGS[@]}"

echo ">>> 完成。控制台: http://127.0.0.1:${CONSOLE_PORT:-8888}"
echo ">>> 健康检查: curl -s http://127.0.0.1:${CONSOLE_PORT:-8888}/api/healthz/"
