"""
日志模块
提供统一的日志功能
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

class Colors:
    """颜色输出类"""
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

class Logger:
    """日志类"""
    
    def __init__(self, log_file: Optional[Path] = None):
        """
        初始化日志器
        
        Args:
            log_file: 日志文件路径，如果为None则不写入文件
        """
        self.log_file = log_file
        self.error_count = 0
        self.warning_count = 0
    
    def _log(self, level: str, message: str, color: str = Colors.NC):
        """内部日志方法"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"{color}[{level}]{Colors.NC} {message}"
        print(log_message, flush=True)
        
        # 写入日志文件
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(f"[{timestamp}] [{level}] {message}\n")
            except Exception:
                pass  # 忽略日志写入错误
    
    def info(self, message: str):
        """信息日志"""
        self._log("INFO", message, Colors.BLUE)
    
    def success(self, message: str):
        """成功日志"""
        self._log("SUCCESS", message, Colors.GREEN)
    
    def warning(self, message: str):
        """警告日志"""
        self.warning_count += 1
        self._log("WARNING", message, Colors.YELLOW)
    
    def error(self, message: str):
        """错误日志"""
        self.error_count += 1
        self._log("ERROR", message, Colors.RED)
    
    def step(self, message: str):
        """步骤日志（带分隔线）"""
        print("")
        print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
        print(f"{Colors.GREEN}步骤: {message}{Colors.NC}")
        print(f"{Colors.GREEN}{'=' * 40}{Colors.NC}")
        print("")
    
    def log(self, message: str):
        """普通日志（不带级别前缀）"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        print(log_message, flush=True)
        
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(log_message + "\n")
            except Exception:
                pass

