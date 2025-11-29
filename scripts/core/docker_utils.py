"""
Docker 工具模块
提供 Docker 相关的工具函数
"""

import subprocess
import shutil
from pathlib import Path
from typing import Optional
from .config import Config
from .logger import Logger

class DockerUtils:
    """Docker 工具类"""
    
    def __init__(self, logger: Optional[Logger] = None):
        """
        初始化 Docker 工具
        
        Args:
            logger: 日志器实例
        """
        self.logger = logger or Logger()
        self.container_name = Config.CONTAINER_NAME
        self.image_name = Config.IMAGE_NAME
    
    def exec(self, command: list, check: bool = True, capture_output: bool = False, input_data: Optional[str] = None) -> subprocess.CompletedProcess:
        """
        在容器内执行命令
        
        Args:
            command: 要执行的命令列表
            check: 是否检查返回码
            capture_output: 是否捕获输出
            input_data: 输入数据（用于管道）
        
        Returns:
            subprocess.CompletedProcess
        """
        cmd = ["docker", "exec", self.container_name] + command
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture_output,
            text=True,
            input=input_data
        )
    
    def compose_up(self, service: Optional[str] = None, detach: bool = True) -> bool:
        """
        启动 Docker Compose 服务
        
        Args:
            service: 服务名称，如果为None则启动所有服务
            detach: 是否在后台运行
        
        Returns:
            是否成功
        """
        cmd = ["docker-compose", "up"]
        if detach:
            cmd.append("-d")
        if service:
            cmd.append(service)
        
        result = subprocess.run(cmd, check=False)
        return result.returncode == 0
    
    def compose_down(self, volumes: bool = False) -> bool:
        """
        停止 Docker Compose 服务
        
        Args:
            volumes: 是否删除卷
        
        Returns:
            是否成功
        """
        cmd = ["docker-compose", "down"]
        if volumes:
            cmd.append("-v")
        
        result = subprocess.run(cmd, check=False, capture_output=True)
        return result.returncode == 0
    
    def compose_stop(self, service: Optional[str] = None) -> bool:
        """
        停止 Docker Compose 服务（不删除容器）
        
        Args:
            service: 服务名称，如果为None则停止所有服务
        
        Returns:
            是否成功
        """
        cmd = ["docker-compose", "stop"]
        if service:
            cmd.append(service)
        
        result = subprocess.run(cmd, check=False)
        return result.returncode == 0
    
    def compose_run(self, service: str, command: list, remove: bool = True, env: Optional[dict] = None) -> subprocess.CompletedProcess:
        """
        运行一次性 Docker Compose 服务
        
        Args:
            service: 服务名称
            command: 要执行的命令列表
            remove: 运行后是否删除容器
            env: 环境变量字典
        
        Returns:
            subprocess.CompletedProcess
        """
        cmd = ["docker-compose", "run"]
        if remove:
            cmd.append("--rm")
        
        if env:
            for key, value in env.items():
                cmd.extend(["-e", f"{key}={value}"])
        
        cmd.append(service)
        cmd.extend(command)
        
        return subprocess.run(cmd, check=False)
    
    def build_image(self, tag: Optional[str] = None, context: str = ".") -> bool:
        """
        构建 Docker 镜像
        
        Args:
            tag: 镜像标签，如果为None则使用配置中的镜像名
            context: 构建上下文路径
        
        Returns:
            是否成功
        """
        tag = tag or self.image_name
        result = subprocess.run(["docker", "build", "-t", tag, context], check=False)
        return result.returncode == 0
    
    def stop_container(self) -> bool:
        """停止容器"""
        result = subprocess.run(["docker", "stop", self.container_name], check=False, capture_output=True)
        return result.returncode == 0
    
    def remove_container(self) -> bool:
        """删除容器"""
        result = subprocess.run(["docker", "rm", self.container_name], check=False, capture_output=True)
        return result.returncode == 0
    
    def cleanup_directories(self, directories: list[Path], keep_structure: bool = True):
        """
        清理目录内容
        
        Args:
            directories: 要清理的目录列表
            keep_structure: 是否保留目录结构
        """
        for directory in directories:
            if directory.exists():
                self.logger.info(f"清理{directory.name}目录内容（保留目录）...")
                for item in directory.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                self.logger.success(f"{directory.name}目录内容已清理")
            else:
                directory.mkdir(parents=True, exist_ok=True)
                self.logger.success(f"已创建 {directory.name} 目录")

