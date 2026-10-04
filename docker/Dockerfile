# syntax=docker/dockerfile:1
# targets: mysql-only（仅数据库）| allinone（默认，MySQL+控制台）
FROM hub.coding-space.cn/library/ubuntu:22.04 AS mysql-only
USER root
ENV ANDROID_HOME=/opt/android
ENV MYSQL_VERSION=8.0.46
ENV LANG=C.UTF-8
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# 设置环境变量，避免交互式安装
ENV DEBIAN_FRONTEND=noninteractive

RUN echo "Asia/Shanghai" > /etc/timezone
# 修改欢迎命令
COPY docker/bashrc /root/.bashrc
ADD docker/sources.list /etc/apt/sources.list
# 安装必要的工具和依赖
RUN apt update && apt install -y \
    wget \
    curl \
    cron \
    tar \
    gzip \
    procps \
    libnuma1 \
    libaio1 \
    libncurses5 \
    libncurses6 \
    libnss3 \
    libc6 \
    gnupg \
    lsb-release \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 Percona XtraBackup 8.0
# 下载并安装 Percona XtraBackup
RUN wget https://repo.percona.com/apt/percona-release_latest.generic_all.deb \
    && DEBIAN_FRONTEND=noninteractive dpkg -i percona-release_latest.generic_all.deb \
    && percona-release setup pdps8.0 \
    && apt-get update \
    && apt-get install -y percona-xtrabackup-80 \
    && rm -f percona-release_latest.generic_all.deb \
    && rm -rf /var/lib/apt/lists/*

# 安装 MinIO 客户端 (mc)
# 优先使用仓库内 docker/bin/mc（dl.min.io 的 latest 链接可能 410）；
# 若构建上下文无无该文件，可先下载：
#   mkdir -p docker/bin && curl -L -o docker/bin/mc \
#     https://ghfast.top/https://github.com/minio/mc/releases/download/RELEASE.2025-08-13T08-35-41Z/mc.linux-amd64.RELEASE.2025-08-13T08-35-41Z
COPY docker/bin/mc /usr/local/bin/mc
RUN chmod +x /usr/local/bin/mc && mc --version

# 安装 MySQL 8.0.46（手动下载指定版本）
RUN set -eux; \
    # 下载 MySQL 8.0.46 的 deb bundle
    MYSQL_VERSION="${MYSQL_VERSION:-8.0.46}"; \
    MYSQL_DEB_BUNDLE="mysql-server_${MYSQL_VERSION}-1ubuntu22.04_amd64.deb-bundle.tar"; \
    MYSQL_DOWNLOAD_URL="https://dev.mysql.com/get/Downloads/MySQL-8.0/${MYSQL_DEB_BUNDLE}"; \
    echo "下载 MySQL ${MYSQL_VERSION} 安装包..."; \
    wget -q "${MYSQL_DOWNLOAD_URL}" -O /tmp/${MYSQL_DEB_BUNDLE} || \
    wget -q "https://cdn.mysql.com/Downloads/MySQL-8.0/${MYSQL_DEB_BUNDLE}" -O /tmp/${MYSQL_DEB_BUNDLE} || \
    (echo "尝试备用下载地址..." && \
     wget -q "https://downloads.mysql.com/archives/get/p/23/file/${MYSQL_DEB_BUNDLE}" -O /tmp/${MYSQL_DEB_BUNDLE}); \
    # 解压 bundle
    cd /tmp && tar -xf ${MYSQL_DEB_BUNDLE}; \
    # 安装依赖
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libmecab2 \
        libnuma1 \
        libaio1 \
        libncurses5 \
        libncurses6 \
        psmisc \
        libc6 \
        libstdc++6 \
        libgcc-s1 \
        zlib1g; \
    # 按顺序安装 MySQL 组件
    DEBIAN_FRONTEND=noninteractive dpkg -i mysql-common_${MYSQL_VERSION}-1ubuntu22.04_amd64.deb || true; \
    DEBIAN_FRONTEND=noninteractive dpkg -i mysql-community-client-plugins_${MYSQL_VERSION}-1ubuntu22.04_amd64.deb || true; \
    DEBIAN_FRONTEND=noninteractive dpkg -i mysql-community-client-core_${MYSQL_VERSION}-1ubuntu22.04_amd64.deb || true; \
    DEBIAN_FRONTEND=noninteractive dpkg -i mysql-community-client_${MYSQL_VERSION}-1ubuntu22.04_amd64.deb || true; \
    # 创建 mysql-client 符号链接（mysql-community-server 需要 mysql-client）
    if [ ! -f /usr/bin/mysql-client ]; then \
        ln -s /usr/bin/mysql /usr/bin/mysql-client || true; \
    fi; \
    DEBIAN_FRONTEND=noninteractive dpkg -i mysql-community-server-core_${MYSQL_VERSION}-1ubuntu22.04_amd64.deb || true; \
    DEBIAN_FRONTEND=noninteractive dpkg -i mysql-community-server_${MYSQL_VERSION}-1ubuntu22.04_amd64.deb || true; \
    # 修复依赖关系
    DEBIAN_FRONTEND=noninteractive apt-get install -f -y || true; \
    # 确保所有包都已正确安装
    dpkg -l | grep mysql-community || true; \
    # 验证 MySQL 安装版本
    echo "验证 MySQL 安装版本:"; \
    mysqld --version || (echo "错误: mysqld 未正确安装" && exit 1); \
    mysql --version || (echo "错误: mysql 客户端未正确安装" && exit 1); \
    # 清理临时文件
    rm -rf /tmp/mysql-*.deb /tmp/${MYSQL_DEB_BUNDLE}; \
    rm -rf /var/lib/apt/lists/*

ADD docker/sources.list /etc/apt/sources.list

# Python 运行时（备份恢复工具）
# 使用 PYTHONPATH 部署，避免构建时访问 PyPI（内网/超时场景更稳）
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 \
        tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo Asia/Shanghai > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai

RUN mkdir -p /backups /opt/mysql-backup
COPY src /opt/mysql-backup/src
ENV PYTHONPATH=/opt/mysql-backup/src
RUN printf '%s\n' '#!/bin/sh' 'exec python3 -m mysql_backup "$@"' > /usr/local/bin/mysql-backup \
    && chmod +x /usr/local/bin/mysql-backup \
    && python3 -m mysql_backup --help >/dev/null \
    && mysql-backup --help >/dev/null

# 创建 MySQL 入口点脚本（类似官方 MySQL 镜像）
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 复制自定义入口点脚本
COPY docker/docker-entrypoint-with-backup.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint-with-backup.sh

WORKDIR /backups

# 暴露 MySQL 端口
EXPOSE 3306 33060

# 使用自定义入口点
ENTRYPOINT ["docker-entrypoint-with-backup.sh"]
CMD ["mysqld"]

# ---------- 前端构建 ----------
FROM hub.coding-space.cn/library/node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/.npmrc frontend/package.json ./
RUN npm install --registry=https://registry.npmmirror.com
COPY frontend/ .
RUN npm run build

# ---------- 一体镜像：MySQL + Console + Frontend ----------
FROM mysql-only AS allinone

# 一体镜像内置本机连接与路径，用户侧 .env 只需密码/端口/调度等业务变量
ENV RUNTIME_MODE=allinone \
    CONSOLE_MYSQL_HOST=127.0.0.1 \
    CONSOLE_MYSQL_PORT=3306 \
    MYSQL_DATA_DIR=/var/lib/mysql \
    MYSQL_CONFIG_DIR=/etc/mysql/conf.d \
    BACKUP_BASE_DIR=/backups \
    STORAGES_CONFIG_FILE=/shared/storages.json \
    BACKUP_POLICY_FILE=/shared/backup_policy.json \
    PROJECT_ROOT=/app \
    CELERY_BROKER_URL=redis://127.0.0.1:6379/0 \
    CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0 \
    MYSQLD_EXTRA_ARGS="--default-authentication-plugin=mysql_native_password --log-bin=mysql-bin --binlog-format=ROW --binlog-row-image=FULL --binlog-row-metadata=FULL --server-id=1 --character-set-server=utf8mb4 --collation-server=utf8mb4_general_ci --explicit_defaults_for_timestamp=true --bind-address=0.0.0.0 --default-time-zone=+08:00 --binlog-expire-logs-seconds=2592000" \
    DJANGO_SETTINGS_MODULE=config.settings \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      python3 \
      python3-pip \
      python3-venv \
      python3-dev \
      gcc \
      pkg-config \
      libssl-dev \
      libmysqlclient-dev \
      nginx \
      redis-server \
      curl \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && ln -sf /usr/bin/python3 /usr/local/bin/python3

WORKDIR /app
COPY console/pip.conf /etc/pip.conf
COPY console/requirements.txt /app/requirements.txt
# Ubuntu 上编译 mysqlclient 易失败；一体镜像改用 PyMySQL（见 config/__init__.py）
RUN grep -v '^mysqlclient' /app/requirements.txt > /tmp/requirements-allinone.txt \
    && (pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements-allinone.txt \
        || pip3 install --no-cache-dir -r /tmp/requirements-allinone.txt)

COPY console/ /app/
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /app/data /app/staticfiles /app/logs /var/log/nginx /usr/share/nginx/html /shared /backups/pitr-work

COPY console/nginx.conf /etc/nginx/conf.d/default.conf
COPY console/redis.conf /app/redis.conf
COPY --from=frontend-builder /frontend/dist /usr/share/nginx/html

COPY docker/mysql-service.sh /usr/local/bin/mysql-service
COPY docker/entrypoint-allinone.sh /usr/local/bin/entrypoint-allinone.sh
RUN chmod +x /usr/local/bin/mysql-service /usr/local/bin/entrypoint-allinone.sh

EXPOSE 3306 33060 80

ENTRYPOINT ["/usr/local/bin/entrypoint-allinone.sh"]
CMD []
