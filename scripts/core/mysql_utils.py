"""
MySQL 工具模块
提供 MySQL 相关的工具函数
"""

import subprocess
import time
from typing import Optional
from .config import Config
from .logger import Logger
from .docker_utils import DockerUtils

class MySQLUtils:
    """MySQL 工具类"""
    
    def __init__(self, logger: Optional[Logger] = None, docker_utils: Optional[DockerUtils] = None):
        """
        初始化 MySQL 工具
        
        Args:
            logger: 日志器实例
            docker_utils: Docker工具实例
        """
        self.logger = logger or Logger()
        self.docker = docker_utils or DockerUtils(logger)
        self.host = Config.MYSQL_HOST
        self.port = Config.MYSQL_PORT
        self.user = Config.get_mysql_user()
        self.password = Config.get_mysql_password()
        self.database = Config.MYSQL_DATABASE
    
    def wait_for_mysql(self, max_attempts: int = 60, wait_seconds: int = 2) -> bool:
        """
        等待 MySQL 启动
        
        Args:
            max_attempts: 最大尝试次数
            wait_seconds: 每次尝试之间的等待秒数
        
        Returns:
            是否成功
        """
        self.logger.info("等待 MySQL 启动...")
        
        for attempt in range(max_attempts):
            cmd = [
                "mysqladmin", "ping", "-h", "127.0.0.1", "-u", "root",
                f"-p{Config.MYSQL_ROOT_PASSWORD}", "--silent"
            ]
            result = self.docker.exec(cmd, check=False, capture_output=True)
            
            if result.returncode == 0:
                self.logger.success("MySQL 已启动")
                time.sleep(3)  # 再等待3秒确保完全就绪
                return True
            
            time.sleep(wait_seconds)
        
        self.logger.error("MySQL 启动超时")
        return False
    
    def execute_sql(self, sql: str, database: Optional[str] = None, check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess:
        """
        执行 SQL 命令
        
        Args:
            sql: SQL 语句
            database: 数据库名，如果为None则使用配置中的数据库
            check: 是否检查返回码
            capture_output: 是否捕获输出
        
        Returns:
            subprocess.CompletedProcess
        """
        database = database or self.database
        cmd = [
            "mysql", "-h", "127.0.0.1", "-u", "root",
            f"-p{Config.MYSQL_ROOT_PASSWORD}", database, "-e", sql
        ]
        return self.docker.exec(cmd, check=check, capture_output=capture_output)
    
    def execute_sql_file(self, sql: str, database: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
        """
        执行 SQL 文件内容（通过管道）
        
        Args:
            sql: SQL 内容
            database: 数据库名，如果为None则使用配置中的数据库
            check: 是否检查返回码
        
        Returns:
            subprocess.CompletedProcess
        """
        database = database or self.database
        cmd = [
            "mysql", "-h", "127.0.0.1", "-u", "root",
            f"-p{Config.MYSQL_ROOT_PASSWORD}", database
        ]
        return self.docker.exec(cmd, check=check, input_data=sql)
    
    def get_count(self, table: str, database: Optional[str] = None) -> int:
        """
        获取表的记录数
        
        Args:
            table: 表名
            database: 数据库名，如果为None则使用配置中的数据库
        
        Returns:
            记录数
        """
        database = database or self.database
        sql = f"SELECT COUNT(*) FROM {table};"
        result = self.execute_sql(sql, database, check=False, capture_output=True)
        
        if result.returncode == 0 and result.stdout:
            try:
                return int(result.stdout.strip())
            except ValueError:
                return 0
        return 0

