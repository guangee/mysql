FROM mysql:8.0.44

# 设置环境变量，避免交互式安装
ENV DEBIAN_FRONTEND=noninteractive

# 安装必要的工具和依赖
RUN apt-get update && apt-get install -y \
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
    && rm -rf /var/lib/apt/lists/*

# 安装 Percona XtraBackup 8.0
# 下载并安装 Percona XtraBackup
RUN wget https://repo.percona.com/apt/percona-release_latest.generic_all.deb \
    && dpkg -i percona-release_latest.generic_all.deb \
    && apt-get update \
    && apt-get install -y percona-xtrabackup-80 \
    && rm -f percona-release_latest.generic_all.deb \
    && rm -rf /var/lib/apt/lists/*

# 安装 MinIO 客户端 (mc)，支持 S3 兼容的对象存储
RUN wget https://dl.min.io/client/mc/release/linux-amd64/mc -O /usr/local/bin/mc \
    && chmod +x /usr/local/bin/mc

# 创建备份脚本和备份目录
RUN mkdir -p /scripts /backups

# 复制备份脚本
COPY scripts/ /scripts/
RUN chmod +x /scripts/*.sh

# 复制自定义入口点脚本
COPY docker-entrypoint-with-backup.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint-with-backup.sh

# 备份原始的 docker-entrypoint.sh（如果存在）
RUN if [ -f /docker-entrypoint.sh ]; then \
        cp /docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh; \
    fi

WORKDIR /scripts

