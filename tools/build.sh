#!/bin/bash
# 从仓库根目录构建并推送镜像
# 用法: ./tools/build.sh <image:tag> [context_path]
# 示例: ./tools/build.sh mysql-s3:8.0.46 ./

set -euo pipefail

image=${1:?usage: $0 <image:tag> [context_path]}
context=${2:-./}
dockerfile="${context%/}/docker/Dockerfile"

# 若 context 本身就是 docker/，回退为同目录 Dockerfile
if [ ! -f "$dockerfile" ] && [ -f "${context%/}/Dockerfile" ]; then
  dockerfile="${context%/}/Dockerfile"
fi

docker build -f "$dockerfile" -t "${docker_hub_username}/${image}" "$context"
docker push "${docker_hub_username}/${image}"
