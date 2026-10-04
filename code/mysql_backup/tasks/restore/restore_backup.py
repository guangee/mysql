#!/usr/bin/env python3
"""
恢复备份脚本

从S3下载备份文件，解压并准备恢复
支持全量备份和增量备份

用法: restore_backup.py [恢复目录] <全量备份文件名或时间戳> [增量备份1] [增量备份2] ...
示例:
  restore_backup.py backup_20240101_020000.tar.gz
  restore_backup.py backup_20240101_020000
  restore_backup.py 20240101_020000
  restore_backup.py /backups/restore backup_20240101_020000.tar.gz
"""

import os
import sys
import subprocess
import shutil
import tarfile
from pathlib import Path
from typing import List, Optional

from mysql_backup.core.backup_naming import (
    extract_backup_timestamp,
    find_backup_archive,
    make_backup_filename,
    resolve_backup_dir,
    s3_backup_object_names_for_lookup,
)

# 配置变量
BACKUP_BASE_DIR = Path(os.environ.get("BACKUP_BASE_DIR", "/backups"))
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "mysql-backups")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_USE_SSL = os.environ.get("S3_USE_SSL", "true").lower() == "true"
S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "false").lower() == "true"
S3_ALIAS = os.environ.get("S3_ALIAS", "s3")

# 日志函数
def log(message: str):
    """记录日志"""
    timestamp = os.popen("date '+%Y-%m-%d %H:%M:%S'").read().strip()
    print(f"[{timestamp}] {message}")

def setup_s3():
    """配置 S3 客户端"""
    log("配置 S3 兼容对象存储客户端...")
    
    # 验证必要的配置
    if not S3_ENDPOINT or not S3_ACCESS_KEY or not S3_SECRET_KEY:
        log("错误: S3 配置不完整，请设置 S3_ENDPOINT, S3_ACCESS_KEY 和 S3_SECRET_KEY")
        sys.exit(1)
    
    # 构建 S3 URL
    if S3_USE_SSL:
        s3_url = f"https://{S3_ENDPOINT}"
    else:
        s3_url = f"http://{S3_ENDPOINT}"
    
    # 配置 S3 别名
    try:
        subprocess.run(
            ["mc", "alias", "set", S3_ALIAS, s3_url, S3_ACCESS_KEY, S3_SECRET_KEY, "--api", "s3v4"],
            check=False,
            capture_output=True
        )
    except Exception:
        pass  # 忽略错误
    
    log(f"S3 配置完成 (Endpoint: {S3_ENDPOINT}, Bucket: {S3_BUCKET})")

def _resolve_full_backup_candidates(backup_file: str) -> list[str]:
    text = (backup_file or "").strip()
    if text.endswith(".tar.gz"):
        return [Path(text).name]
    ts = extract_backup_timestamp(text) or text.replace("backup_", "")
    return s3_backup_object_names_for_lookup(ts, "full")


def restore_full_backup(backup_file: str, target_dir: Path) -> Path:
    """下载/复制并恢复全量备份（兼容新旧命名与本地目录）"""
    log(f"准备全量备份: {backup_file}")

    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 优先使用本地 full 目录中的压缩包
    ts = extract_backup_timestamp(backup_file) or backup_file.replace("backup_", "").replace(".tar.gz", "")
    from mysql_backup.core.backup_naming import resolve_backup_archive
    local_archive = resolve_backup_archive(BACKUP_BASE_DIR / "full", ts)
    backup_path = None
    if local_archive is not None:
        backup_path = target_dir / local_archive.name
        shutil.copy2(local_archive, backup_path)
        log(f"已从本地复制全量备份: {local_archive} -> {backup_path}")

    if backup_path is None:
        log(f"下载到目录: {target_dir}")
        last_error = None
        for candidate in _resolve_full_backup_candidates(backup_file):
            backup_path = target_dir / candidate
            try:
                result = subprocess.run(
                    ["mc", "cp", f"{S3_ALIAS}/{S3_BUCKET}/full/{candidate}", str(backup_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                if result.stdout:
                    for line in result.stdout.strip().split('\n'):
                        if line.strip():
                            log(f"mc: {line}")
                if backup_path.exists():
                    break
            except subprocess.CalledProcessError as e:
                last_error = e
                backup_path.unlink(missing_ok=True)
                backup_path = None
                continue
        if backup_path is None or not backup_path.exists():
            log(f"错误: 下载备份失败: {last_error}")
            sys.exit(1)

    if not backup_path.exists():
        backup_subdirs = list(target_dir.rglob("*.tar.gz"))
        if backup_subdirs:
            backup_path = backup_subdirs[0]
            target_dir = backup_path.parent
            log(f"检测到备份文件在子目录: {target_dir}")
        else:
            log("错误: 找不到备份压缩包")
            sys.exit(1)
    
    # 解压
    log("解压备份文件...")
    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            tar.extractall(path=target_dir)
        backup_path.unlink()  # 删除tar.gz文件
    except Exception as e:
        log(f"错误: 解压失败: {e}")
        sys.exit(1)
    
    log(f"全量备份已恢复到: {target_dir}")
    return target_dir.resolve()

def apply_incremental_backup(incremental_file: str, base_dir: Path):
    """应用增量备份"""
    log(f"应用增量备份: {incremental_file}")
    
    tmp_dir = base_dir / "tmp_incremental"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    
    # 下载/复制增量备份
    backup_tar = None
    ts = extract_backup_timestamp(incremental_file) or incremental_file.replace("backup_", "").replace(".tar.gz", "")
    from mysql_backup.core.backup_naming import resolve_backup_archive
    local_archive = resolve_backup_archive(BACKUP_BASE_DIR / "incremental", ts)
    if local_archive is not None:
        backup_tar = tmp_dir / local_archive.name
        shutil.copy2(local_archive, backup_tar)
        log(f"已从本地复制增量备份: {local_archive}")
    if backup_tar is None:
        last_error = None
        candidates = (
            [Path(incremental_file).name]
            if incremental_file.endswith(".tar.gz")
            else s3_backup_object_names_for_lookup(ts, "incr")
        )
        for candidate in candidates:
            backup_tar = tmp_dir / candidate
            try:
                subprocess.run(
                    ["mc", "cp", f"{S3_ALIAS}/{S3_BUCKET}/incremental/{candidate}", str(backup_tar)],
                    check=True,
                    capture_output=True,
                )
                break
            except subprocess.CalledProcessError as e:
                last_error = e
                backup_tar.unlink(missing_ok=True)
                backup_tar = None
        if backup_tar is None:
            log(f"错误: 下载增量备份失败: {last_error}")
            sys.exit(1)
    
    # 解压
    log("解压增量备份...")
    try:
        with tarfile.open(backup_tar, "r:gz") as tar:
            tar.extractall(path=tmp_dir)
        backup_tar.unlink()
    except Exception as e:
        log(f"错误: 解压增量备份失败: {e}")
        sys.exit(1)
    
    # 解压压缩的备份文件
    log("解压 XtraBackup 文件...")
    try:
        subprocess.run(
            ["xtrabackup", "--decompress", f"--target-dir={tmp_dir}"],
            check=True
        )
    except subprocess.CalledProcessError as e:
        log(f"错误: 解压 XtraBackup 文件失败: {e}")
        sys.exit(1)
    
    # 准备增量备份（合并到基础备份）
    log("合并增量备份...")
    try:
        subprocess.run(
            ["xtrabackup", "--prepare", f"--target-dir={base_dir}", f"--incremental-dir={tmp_dir}"],
            check=True
        )
    except subprocess.CalledProcessError as e:
        log(f"错误: 合并增量备份失败: {e}")
        sys.exit(1)
    
    # 清理临时文件
    shutil.rmtree(tmp_dir, ignore_errors=True)
    
    log("增量备份已应用")

def restore_backup(full_backup_input: str, incremental_backups: List[str], restore_target_dir: Optional[Path] = None):
    """恢复备份（全量 + 增量）"""
    setup_s3()
    
    # 处理备份文件名
    if full_backup_input.startswith("backup_") and full_backup_input.endswith(".tar.gz"):
        full_backup_file = full_backup_input
    elif full_backup_input.startswith("backup_"):
        full_backup_file = f"{full_backup_input}.tar.gz"
    else:
        full_backup_file = f"backup_{full_backup_input}.tar.gz"
    
    # 确定恢复目录
    if restore_target_dir:
        restore_dir = Path(restore_target_dir).resolve()
    else:
        restore_dir = BACKUP_BASE_DIR / "restore"
    
    log(f"恢复目标目录: {restore_dir}")
    
    # 恢复全量备份
    restore_dir = restore_full_backup(full_backup_file, restore_dir)
    
    # 检查实际解压位置（mc cp 可能创建了子目录）
    backup_name = full_backup_input.replace(".tar.gz", "").replace("backup_", "")
    possible_dirs = [
        restore_dir / f"backup_{backup_name}",
        restore_dir / backup_name,
    ]
    
    for possible_dir in possible_dirs:
        if possible_dir.exists() and possible_dir.is_dir():
            restore_dir = possible_dir.resolve()
            log(f"检测到备份在子目录: {restore_dir}")
            break
    
    # 确保 RESTORE_DIR 是绝对路径
    restore_dir = restore_dir.resolve()
    
    if not restore_dir.exists():
        log(f"错误: 恢复目录不存在: {restore_dir}")
        sys.exit(1)
    
    # 解压压缩的备份文件
    log("解压 XtraBackup 文件...")
    log(f"目标目录: {restore_dir}")
    try:
        subprocess.run(
            ["xtrabackup", "--decompress", f"--target-dir={restore_dir}"],
            check=True
        )
    except subprocess.CalledProcessError as e:
        log(f"错误: 解压 XtraBackup 文件失败: {e}")
        sys.exit(1)
    
    # 准备全量备份
    log("准备全量备份...")
    try:
        subprocess.run(
            ["xtrabackup", "--prepare", f"--target-dir={restore_dir}"],
            check=True
        )
    except subprocess.CalledProcessError as e:
        log(f"错误: 准备全量备份失败: {e}")
        sys.exit(1)
    
    # 按顺序应用所有增量备份
    for inc_backup in incremental_backups:
        if inc_backup:
            apply_incremental_backup(inc_backup, restore_dir)
    
    log("备份恢复完成！")
    log(f"恢复目录: {restore_dir}")
    log("")
    log("要应用恢复，请执行以下命令之一:")
    log("  1. 使用统一入口（推荐）:")
    log(f"     docker compose run --rm mysql python3 -m mysql_backup restore apply {restore_dir}")
    log("")
    log("  2. 控制台脚本:")
    log(f"     docker compose run --rm mysql mysql-backup restore apply {restore_dir}")
    log("")
    log("  3. 手动执行:")
    log(f"     xtrabackup --copy-back --target-dir={restore_dir} --datadir=/var/lib/mysql")
    log("     或")
    log(f"     xtrabackup --move-back --target-dir={restore_dir} --datadir=/var/lib/mysql")
    log("")
    log("注意: 应用恢复前需要停止 MySQL 服务")

def main():
    """主函数"""
    # 处理参数：如果第一个参数是目录路径，则作为目标目录
    restore_target_dir = None
    args = sys.argv[1:]
    
    if args and args[0].startswith("/"):
        restore_target_dir = Path(args[0])
        args = args[1:]
    
    if not args:
        print("用法: restore_backup.py [恢复目录] <全量备份文件名或时间戳> [增量备份1] [增量备份2] ...", file=sys.stderr)
        print("示例:", file=sys.stderr)
        print("  restore_backup.py backup_20240101_020000.tar.gz", file=sys.stderr)
        print("  restore_backup.py backup_20240101_020000", file=sys.stderr)
        print("  restore_backup.py 20240101_020000", file=sys.stderr)
        print("  restore_backup.py /backups/restore backup_20240101_020000.tar.gz", file=sys.stderr)
        print("", file=sys.stderr)
        print("可用的全量备份:", file=sys.stderr)
        try:
            setup_s3()
            result = subprocess.run(
                ["mc", "ls", f"{S3_ALIAS}/{S3_BUCKET}/full/"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 6:
                            print(f"  {parts[5]}", file=sys.stderr)
            else:
                print("  无法列出备份文件", file=sys.stderr)
        except Exception:
            print("  无法列出备份文件", file=sys.stderr)
        sys.exit(1)
    
    full_backup_input = args[0]
    incremental_list = args[1:] if len(args) > 1 else []
    
    restore_backup(full_backup_input, incremental_list, restore_target_dir)

if __name__ == "__main__":
    main()

