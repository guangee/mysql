FROM ubuntu:22.04
USER root
ENV ANDROID_HOME=/opt/android
ENV MYSQL_VERSION=8.0.35
ENV LANG=C.UTF-8
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# 设置环境变量，避免交互式安装
ENV DEBIAN_FRONTEND=noninteractive

RUN echo "Asia/Shanghai" > /etc/timezone
# 修改欢迎命令
COPY .bashrc /root/.bashrc
ADD sources.list /etc/apt/sources.list
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

# 安装 MinIO 客户端 (mc)，支持 S3 兼容的对象存储
RUN wget https://dl.min.io/client/mc/release/linux-amd64/mc -O /usr/local/bin/mc \
    && chmod +x /usr/local/bin/mc

# 安装 MySQL 8.0.44（手动下载指定版本）
RUN set -eux; \
    # 下载 MySQL 8.0.44 的 deb bundle
    MYSQL_VERSION="${MYSQL_VERSION:-8.0.44}"; \
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

ADD sources.list /etc/apt/sources.list

# 创建备份脚本和备份目录
RUN mkdir -p /scripts /backups

# 复制备份脚本和 Python 脚本
COPY scripts/ /scripts/
RUN find /scripts -name "*.py" -type f -exec chmod +x {} \;

# 创建 MySQL 入口点脚本（类似官方 MySQL 镜像）
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 复制自定义入口点脚本
COPY docker-entrypoint-with-backup.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint-with-backup.sh

WORKDIR /scripts

# 暴露 MySQL 端口
EXPOSE 3306 33060

# 使用自定义入口点
ENTRYPOINT ["docker-entrypoint-with-backup.sh"]
CMD ["mysqld"]

