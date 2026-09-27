#!/usr/bin/env python3
"""
应用恢复脚本

将准备好的备份应用到MySQL数据目录

用法: apply_restore.py [恢复目录] [选项]

参数:
  恢复目录: 准备好的备份目录路径（默认: /backups/restore）

环境变量:
  RESTORE_DIR: 恢复目录路径（默认: /backups/restore）
  MYSQL_DATA_DIR: MySQL 数据目录（默认: /var/lib/mysql）
  BACKUP_EXISTING_DATA: 是否备份现有数据（默认: true）
  USE_MOVE_BACK: 是否使用 --move-back（默认: false，使用 --copy-back）

示例:
  apply_restore.py /backups/restore
  RESTORE_DIR=/backups/restore USE_MOVE_BACK=true apply_restore.py

注意:
  1. 此脚本需要在 MySQL 停止的情况下运行
  2. 建议先备份现有数据
  3. 恢复后需要手动启动 MySQL
"""

import os
import sys
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

# 配置变量
BACKUP_BASE_DIR = Path(os.environ.get("BACKUP_BASE_DIR", "/backups"))
RESTORE_DIR = Path(os.environ.get("RESTORE_DIR", "/backups/restore"))
MYSQL_DATA_DIR = Path(os.environ.get("MYSQL_DATA_DIR", "/var/lib/mysql"))
BACKUP_EXISTING_DATA = os.environ.get("BACKUP_EXISTING_DATA", "true").lower() == "true"
USE_MOVE_BACK = os.environ.get("USE_MOVE_BACK", "false").lower() == "true"

def log(message: str):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def error_exit(message: str):
    """错误处理"""
    log(f"错误: {message}")
    sys.exit(1)

def check_restore_dir(restore_dir: Path):
    """检查恢复目录是否存在"""
    if not restore_dir.exists():
        error_exit(f"恢复目录不存在: {restore_dir}")
    
    # 检查是否是准备好的备份（应该有 backup-my.cnf 文件）
    if not (restore_dir / "backup-my.cnf").exists():
        error_exit(f"恢复目录 {restore_dir} 似乎不是准备好的备份（缺少 backup-my.cnf）")
    
    log(f"恢复目录检查通过: {restore_dir}")

def backup_existing_data():
    """备份现有数据"""
    if BACKUP_EXISTING_DATA and MYSQL_DATA_DIR.exists() and any(MYSQL_DATA_DIR.iterdir()):
        backup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_BASE_DIR / f"mysql_data_backup_{backup_timestamp}"
        
        log(f"备份现有数据到: {backup_path}")
        backup_path.mkdir(parents=True, exist_ok=True)
        
        try:
            for item in MYSQL_DATA_DIR.iterdir():
                if item.is_file():
                    shutil.copy2(item, backup_path / item.name)
                elif item.is_dir():
                    shutil.copytree(item, backup_path / item.name, dirs_exist_ok=True)
            log(f"现有数据已备份到: {backup_path}")
        except Exception as e:
            log(f"警告: 备份现有数据时出错: {e}")
    else:
        log(f"跳过现有数据备份（BACKUP_EXISTING_DATA={BACKUP_EXISTING_DATA} 或数据目录为空）")

# 全局变量：保存binlog文件的临时目录
BINLOG_TEMP_DIR = None

def clear_data_dir():
    """清空数据目录（临时保存binlog文件，xtrabackup要求目录完全为空）"""
    global BINLOG_TEMP_DIR
    log(f"清空数据目录: {MYSQL_DATA_DIR}")
    if MYSQL_DATA_DIR.exists():
        # 先保存binlog文件到临时目录（xtrabackup要求目录完全为空）
        binlog_files = []
        binlog_index = None
        for item in MYSQL_DATA_DIR.iterdir():
            if item.name.startswith("mysql-bin.") or item.name == "mysql-bin.index":
                binlog_files.append(item)
                if item.name == "mysql-bin.index":
                    binlog_index = item
        
        if binlog_files:
            # 创建临时目录保存binlog文件
            BINLOG_TEMP_DIR = BACKUP_BASE_DIR / f"binlog_temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            BINLOG_TEMP_DIR.mkdir(parents=True, exist_ok=True)
            log(f"临时保存binlog文件到: {BINLOG_TEMP_DIR}")
            
            for binlog_file in binlog_files:
                try:
                    log(f"保存binlog文件: {binlog_file.name}")
                    shutil.copy2(binlog_file, BINLOG_TEMP_DIR / binlog_file.name)
                except Exception as e:
                    log(f"警告: 保存binlog文件失败: {binlog_file.name}: {e}")
        
        # 现在可以完全清空数据目录（xtrabackup要求目录为空）
        try:
            for item in MYSQL_DATA_DIR.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            if binlog_files:
                log("数据目录已清空（binlog文件已临时保存）")
            else:
                log("数据目录已清空")
        except Exception as e:
            log(f"警告: 清空数据目录时出错: {e}")
    else:
        MYSQL_DATA_DIR.mkdir(parents=True, exist_ok=True)
        log(f"创建数据目录: {MYSQL_DATA_DIR}")

def restore_binlog_files():
    """恢复之前保存的binlog文件"""
    global BINLOG_TEMP_DIR
    if BINLOG_TEMP_DIR and BINLOG_TEMP_DIR.exists():
        log(f"恢复binlog文件从: {BINLOG_TEMP_DIR}")
        try:
            for binlog_file in BINLOG_TEMP_DIR.iterdir():
                if binlog_file.is_file():
                    target_file = MYSQL_DATA_DIR / binlog_file.name
                    log(f"恢复binlog文件: {binlog_file.name}")
                    shutil.copy2(binlog_file, target_file)
            log("binlog文件已恢复")
            
            # 清理临时目录
            try:
                shutil.rmtree(BINLOG_TEMP_DIR)
                log(f"已清理临时binlog目录: {BINLOG_TEMP_DIR}")
            except Exception as e:
                log(f"警告: 清理临时binlog目录失败: {e}")
        except Exception as e:
            log(f"警告: 恢复binlog文件时出错: {e}")
    else:
        log("没有需要恢复的binlog文件")

def apply_restore(restore_dir: Path):
    """应用恢复"""
    log("开始应用恢复...")
    log(f"恢复目录: {restore_dir}")
    log(f"目标数据目录: {MYSQL_DATA_DIR}")
    
    # 确保恢复目录是绝对路径
    restore_dir = restore_dir.resolve()
    
    # 确保数据目录是绝对路径
    mysql_data_dir = MYSQL_DATA_DIR.resolve()
    
    # 检查 xtrabackup 是否可用
    if not shutil.which("xtrabackup"):
        error_exit("xtrabackup 命令不可用")
    
    # 执行恢复
    if USE_MOVE_BACK:
        log("使用 --move-back（恢复后删除恢复目录中的备份）")
        cmd = ["xtrabackup", "--move-back", f"--target-dir={restore_dir}", f"--datadir={mysql_data_dir}"]
    else:
        log("使用 --copy-back（保留恢复目录中的备份）")
        cmd = ["xtrabackup", "--copy-back", f"--target-dir={restore_dir}", f"--datadir={mysql_data_dir}"]
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )
        if result.stdout:
            for line in result.stdout.split('\n'):
                if line.strip():
                    log(f"xtrabackup: {line}")
        log("恢复应用成功！")
        
        # 检查并删除从备份中恢复的binlog文件（如果备份中包含binlog，会被xtrabackup恢复，需要删除）
        log("检查从备份中恢复的binlog文件...")
        restored_binlog_files = list(mysql_data_dir.glob("mysql-bin.*"))
        if restored_binlog_files:
            log(f"发现 {len(restored_binlog_files)} 个从备份恢复的binlog文件，这些是备份时的旧binlog，需要删除")
            for binlog_file in restored_binlog_files:
                try:
                    log(f"删除从备份恢复的旧binlog文件: {binlog_file.name}")
                    binlog_file.unlink()
                except Exception as e:
                    log(f"警告: 删除binlog文件失败: {binlog_file}: {e}")
            
            # 删除从备份恢复的binlog索引文件（如果有）
            restored_binlog_index = mysql_data_dir / "mysql-bin.index"
            if restored_binlog_index.exists():
                try:
                    log("删除从备份恢复的旧binlog索引文件")
                    restored_binlog_index.unlink()
                except Exception as e:
                    log(f"警告: 删除binlog索引文件失败: {e}")
        
        # 恢复之前保存的binlog文件
        restore_binlog_files()
        
    except subprocess.CalledProcessError as e:
        if e.stderr:
            for line in e.stderr.split('\n'):
                if line.strip():
                    log(f"xtrabackup: {line}")
        error_exit("恢复应用失败")

def fix_permissions():
    """修复权限"""
    log("修复数据目录权限...")
    try:
        subprocess.run(
            ["chown", "-R", "mysql:mysql", str(MYSQL_DATA_DIR)],
            check=False,
            capture_output=True
        )
        subprocess.run(
            ["chmod", "700", str(MYSQL_DATA_DIR)],
            check=False,
            capture_output=True
        )
        log("权限修复完成")
    except Exception as e:
        log(f"警告: 无法使用 chown，可能需要手动修复权限: {e}")

def main():
    """主函数"""
    # 检查是否需要显示帮助
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--help", "-h"):
            print(__doc__)
            sys.exit(0)
    
    # 如果提供了参数，使用它作为恢复目录
    global RESTORE_DIR
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.startswith("/") or not arg.startswith("-"):
            RESTORE_DIR = Path(arg)
    
    RESTORE_DIR = RESTORE_DIR.resolve()
    
    log("========== 开始应用恢复 ==========")
    log(f"恢复目录: {RESTORE_DIR}")
    log(f"数据目录: {MYSQL_DATA_DIR}")
    log(f"备份现有数据: {BACKUP_EXISTING_DATA}")
    log(f"使用 move-back: {USE_MOVE_BACK}")
    
    check_restore_dir(RESTORE_DIR)
    backup_existing_data()
    clear_data_dir()
    apply_restore(RESTORE_DIR)
    fix_permissions()
    # restore_binlog_files() 已在 apply_restore() 中调用
    
    log("========== 恢复应用完成 ==========")
    log(f"恢复目录: {RESTORE_DIR}")
    log(f"数据目录: {MYSQL_DATA_DIR}")
    log("")
    log("下一步:")
    log("  1. 检查数据目录权限是否正确")
    log("  2. 启动 MySQL 服务")
    log("  3. 验证数据库是否正常")

if __name__ == "__main__":
    main()

