#!/usr/bin/env bash
# 构建 console 成品镜像并启动 Compose 服务
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

CONSOLE_IMAGE="${CONSOLE_IMAGE:-mysql-console:latest}"
BUILD_CONSOLE=1
BUILD_ONLY=0
COMPOSE_ARGS=()

usage() {
  cat <<'EOF'
用法: ./run.sh [选项] [docker compose 参数…]

  默认会先构建 console 镜像，再执行 docker compose up -d。

选项:
  --no-build-console   跳过 console 镜像构建（使用已有镜像）
  --build-only         仅构建 console 镜像，不启动服务
  -h, --help           显示帮助

环境变量:
  CONSOLE_IMAGE        console 镜像名（默认 mysql-console:latest）

示例:
  ./run.sh
  ./run.sh --no-build-console
  CONSOLE_IMAGE=registry.example.com/mysql-console:v1 ./run.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build-console)
      BUILD_CONSOLE=0
      shift
      ;;
    --build-only)
      BUILD_ONLY=1
      BUILD_CONSOLE=1
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

if [[ "${BUILD_CONSOLE}" -eq 1 ]]; then
  echo ">>> 构建 console 镜像: ${CONSOLE_IMAGE}"
  docker build -f console/Dockerfile -t "${CONSOLE_IMAGE}" .
  echo ">>> console 镜像构建完成"
fi

if [[ "${BUILD_ONLY}" -eq 1 ]]; then
  echo ">>> 仅构建模式，未启动服务"
  exit 0
fi

echo ">>> 启动服务: docker compose up -d ${COMPOSE_ARGS[*]:-}"
docker compose up -d "${COMPOSE_ARGS[@]}"

echo ">>> 完成。控制台: http://127.0.0.1:${CONSOLE_PORT:-8888}"
