"""
配置模块
统一管理所有配置变量
"""

import os
from pathlib import Path
from typing import Optional

class Config:
    """配置类"""
    
    # Docker 配置
    CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "mysql8044")
    IMAGE_NAME = os.environ.get("IMAGE_NAME", "zziaguan/mysql:8.0.44")
    
    # MySQL 配置
    MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
    MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
    MYSQL_ROOT_PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD", "rootpassword")
    MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "testdb")
    MYSQL_USER = os.environ.get("MYSQL_USER", "testuser")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "testpass")
    
    # 备份用户配置（优先使用）
    MYSQL_BACKUP_USER = os.environ.get("MYSQL_BACKUP_USER")
    MYSQL_BACKUP_PASSWORD = os.environ.get("MYSQL_BACKUP_PASSWORD")
    
    # 备份配置
    BACKUP_BASE_DIR = Path(os.environ.get("BACKUP_BASE_DIR", "/backups"))
    MYSQL_DATA_DIR = Path(os.environ.get("MYSQL_DATA_DIR", "/var/lib/mysql"))
    LOCAL_BACKUP_RETENTION_HOURS = int(os.environ.get("LOCAL_BACKUP_RETENTION_HOURS", "0"))
    
    # S3 配置
    S3_BACKUP_ENABLED = os.environ.get("S3_BACKUP_ENABLED", "false").lower() == "true"
    S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
    S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
    S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
    S3_BUCKET = os.environ.get("S3_BUCKET", "mysql-backups")
    S3_REGION = os.environ.get("S3_REGION", "us-east-1")
    S3_USE_SSL = os.environ.get("S3_USE_SSL", "true").lower() == "true"
    S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "false").lower() == "true"
    S3_ALIAS = os.environ.get("S3_ALIAS", "s3")
    
    # 时区配置
    TZ_REGION = os.environ.get("TZ_REGION", "Asia/Shanghai")
    RESTORE_TZ = os.environ.get("RESTORE_TZ", "Asia/Shanghai")
    
    # 测试配置
    AUTO_PITR_TEST = os.environ.get("AUTO_PITR_TEST", "y").lower() == "y"
    PITR_RESTORE_NUM = os.environ.get("PITR_RESTORE_NUM")
    
    # 获取 MySQL 用户（优先使用备份用户）
    @classmethod
    def get_mysql_user(cls) -> str:
        """获取 MySQL 用户"""
        return cls.MYSQL_BACKUP_USER or cls.MYSQL_USER or "root"
    
    # 获取 MySQL 密码
    @classmethod
    def get_mysql_password(cls) -> str:
        """获取 MySQL 密码"""
        if cls.MYSQL_BACKUP_PASSWORD:
            return cls.MYSQL_BACKUP_PASSWORD
        user = cls.get_mysql_user()
        if user == "root":
            return cls.MYSQL_ROOT_PASSWORD or cls.MYSQL_PASSWORD or ""
        return cls.MYSQL_PASSWORD or ""
    
    # 获取 MySQL 连接选项字符串
    @classmethod
    def get_mysql_opts(cls) -> str:
        """获取 MySQL 连接选项字符串"""
        user = cls.get_mysql_user()
        password = cls.get_mysql_password()
        return f"-h {cls.MYSQL_HOST} -P {cls.MYSQL_PORT} -u {user} -p{password}"

