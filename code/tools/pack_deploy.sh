#!/usr/bin/env bash
# 打包客户可直接执行的部署包（compose + README + 配置目录）
# 用法: ./code/tools/pack_deploy.sh [输出目录] [版本号]
# 输出: <outdir>/mysql-console-<version>.tar.gz

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUTDIR="${1:-${ROOT}/dist}"
VERSION="${2:-}"
PACKAGE_SRC="${ROOT}/deploy/package"

if [[ -z "${VERSION}" ]]; then
  if git -C "${ROOT}" describe --tags --exact-match HEAD >/dev/null 2>&1; then
    VERSION="$(git -C "${ROOT}" describe --tags --exact-match HEAD)"
  else
    SHORT_SHA="$(git -C "${ROOT}" rev-parse --short HEAD 2>/dev/null || echo local)"
    VERSION="$(date +%Y%m%d)-${SHORT_SHA}"
  fi
fi

# 去掉前导 v，目录名统一用 mysql-console-<version>
VERSION_NAME="${VERSION#v}"
BUNDLE_NAME="mysql-console-${VERSION_NAME}"
STAGE="${OUTDIR}/.pack-stage/${BUNDLE_NAME}"
ARCHIVE="${OUTDIR}/${BUNDLE_NAME}.tar.gz"

rm -rf "${STAGE}"
mkdir -p "${STAGE}" "${OUTDIR}"

cp -a "${PACKAGE_SRC}/." "${STAGE}/"

# 写入版本信息，便于客户核对
cat > "${STAGE}/VERSION" <<EOF
version=${VERSION}
built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
git_sha=$(git -C "${ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)
image=${MYSQL_IMAGE:-zziaguan/mysql:8.0.46-allinone}
EOF

# 若传入自定义镜像名，同步写进 .env.example 注释提示
if [[ -n "${MYSQL_IMAGE:-}" ]]; then
  sed -i "s|^MYSQL_IMAGE=.*|MYSQL_IMAGE=${MYSQL_IMAGE}|" "${STAGE}/.env.example" || true
fi

tar -czf "${ARCHIVE}" -C "${OUTDIR}/.pack-stage" "${BUNDLE_NAME}"
rm -rf "${OUTDIR}/.pack-stage"

echo "packed: ${ARCHIVE}"
ls -lh "${ARCHIVE}"
