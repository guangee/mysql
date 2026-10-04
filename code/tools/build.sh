#!/bin/bash
# 从仓库根目录构建并推送一体镜像
# 用法: ./tools/build.sh <image:tag> [context_path]
# 示例: ./tools/build.sh mysql:8.0.46-allinone ./

set -euo pipefail

image=${1:?usage: $0 <image:tag> [context_path]}
context=${2:-./}
dockerfile="${context%/}/docker/Dockerfile"
target="${DOCKER_BUILD_TARGET:-allinone}"

# 若 context 本身就是 docker/，回退为同目录 Dockerfile
if [ ! -f "$dockerfile" ] && [ -f "${context%/}/Dockerfile" ]; then
  dockerfile="${context%/}/Dockerfile"
fi

full_tag="${docker_hub_username}/${image}"
echo ">>> docker build -f ${dockerfile} --target ${target} -t ${full_tag} ${context}"
docker build -f "$dockerfile" --target "$target" -t "${full_tag}" "$context"
docker push "${full_tag}"
