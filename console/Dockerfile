# Legacy：旧版「独立 console 容器」镜像。
# 官方交付请使用仓库根目录 docker/Dockerfile --target allinone（见 ./run.sh / docker compose）。
# ---------- 前端构建 ----------
FROM hub.coding-space.cn/library/node:20-alpine AS frontend-builder

WORKDIR /frontend

COPY frontend/.npmrc frontend/package.json ./
RUN npm install --registry=https://registry.npmmirror.com

COPY frontend/ .
RUN npm run build

# ---------- Console + Nginx ----------
FROM hub.coding-space.cn/library/python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_TRUSTED_HOST=mirrors.aliyun.com

COPY console/apt/debian.sources /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    curl \
    docker.io \
    nginx \
    redis-server \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

WORKDIR /app

COPY console/pip.conf /etc/pip.conf
COPY console/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY console/ .
RUN chmod +x entrypoint.sh

COPY console/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-builder /frontend/dist /usr/share/nginx/html

RUN mkdir -p /app/data /app/staticfiles /var/log/nginx /usr/share/nginx/html

EXPOSE 80

ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]
