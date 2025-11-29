#!/usr/bin/env python3
"""分析test3.py测试失败的原因"""

import os
from pathlib import Path
from datetime import datetime

def analyze_snapshots():
    """分析快照文件"""
    print("=" * 80)
    print("快照对比分析")
    print("=" * 80)
    print()
    
    baseline_file = Path("./backups/test3_baseline_snapshot_content.txt")
    restored_file = Path("./backups/test3_restored_snapshot_content.txt")
    
    if not baseline_file.exists():
        print("错误: 基准快照文件不存在")
        return
    
    if not restored_file.exists():
        print("错误: 恢复后快照文件不存在")
        return
    
    # 读取基准快照
    with open(baseline_file, 'r') as f:
        baseline_content = f.read()
    
    # 读取恢复后快照
    with open(restored_file, 'r') as f:
        restored_content = f.read()
    
    # 提取MD5
    baseline_md5 = None
    restored_md5 = None
    for line in baseline_content.split('\n'):
        if line.startswith('MD5:'):
            baseline_md5 = line.split(':')[1].strip()
            break
    
    for line in restored_content.split('\n'):
        if line.startswith('MD5:'):
            restored_md5 = line.split(':')[1].strip()
            break
    
    print(f"基准快照MD5: {baseline_md5}")
    print(f"恢复后快照MD5: {restored_md5}")
    print()
    
    if baseline_md5 == restored_md5:
        print("✓ MD5一致，测试应该通过")
    else:
        print("✗ MD5不一致，测试失败")
    print()
    
    # 分析每个表的数据
    print("表数据对比:")
    print("-" * 80)
    
    tables = ["customers", "orders", "inventory", "audit_logs", "metrics"]
    
    for table in tables:
        print(f"\n表: {table}")
        
        # 提取基准快照中的表数据
        baseline_table_data = []
        in_table = False
        for line in baseline_content.split('\n'):
            if line == f"TABLE:{table}":
                in_table = True
                continue
            elif line.startswith("TABLE:"):
                in_table = False
                continue
            elif in_table and line.strip() and not line.startswith("MD5:") and not line.startswith("Timestamp:"):
                if line != "TABLE_NOT_EXISTS" and line != "TABLE_ERROR":
                    baseline_table_data.append(line)
        
        # 提取恢复后快照中的表数据
        restored_table_data = []
        in_table = False
        for line in restored_content.split('\n'):
            if line == f"TABLE:{table}":
                in_table = True
                continue
            elif line.startswith("TABLE:"):
                in_table = False
                continue
            elif in_table and line.strip() and not line.startswith("MD5:") and not line.startswith("Timestamp:"):
                if line != "TABLE_NOT_EXISTS" and line != "TABLE_ERROR":
                    restored_table_data.append(line)
        
        baseline_ids = set()
        restored_ids = set()
        
        for row in baseline_table_data:
            if '|' in row:
                parts = row.split('|')
                if parts[0].isdigit():
                    baseline_ids.add(int(parts[0]))
        
        for row in restored_table_data:
            if '|' in row:
                parts = row.split('|')
                if parts[0].isdigit():
                    restored_ids.add(int(parts[0]))
        
        print(f"  基准快照记录数: {len(baseline_table_data)} (ID: {sorted(baseline_ids)})")
        print(f"  恢复后快照记录数: {len(restored_table_data)} (ID: {sorted(restored_ids)})")
        
        if baseline_ids == restored_ids:
            print(f"  ✓ 记录ID一致")
        else:
            print(f"  ✗ 记录ID不一致")
            missing_in_restored = baseline_ids - restored_ids
            extra_in_restored = restored_ids - baseline_ids
            if missing_in_restored:
                print(f"    恢复后缺少的ID: {sorted(missing_in_restored)}")
            if extra_in_restored:
                print(f"    恢复后多出的ID: {sorted(extra_in_restored)}")

def analyze_backups():
    """分析备份文件"""
    print()
    print("=" * 80)
    print("备份文件分析")
    print("=" * 80)
    print()
    
    backups_dir = Path("./backups")
    
    # 检查全量备份
    full_dir = backups_dir / "full"
    if full_dir.exists():
        full_backups = sorted([d.name for d in full_dir.iterdir() if d.is_dir()])
        print(f"全量备份数量: {len(full_backups)}")
        if full_backups:
            print(f"  最新: {full_backups[-1]}")
            print(f"  最旧: {full_backups[0]}")
    print()
    
    # 检查增量备份
    inc_dir = backups_dir / "incremental"
    if inc_dir.exists():
        inc_backups = sorted([d.name for d in inc_dir.iterdir() if d.is_dir()])
        print(f"增量备份数量: {len(inc_backups)}")
        if inc_backups:
            for i, inc in enumerate(inc_backups, 1):
                print(f"  [{i}] {inc}")
    print()
    
    # 检查binlog文件
    mysql_data_dir = Path("./mysql_data")
    binlog_files = list(mysql_data_dir.glob("mysql-bin.[0-9]*")) if mysql_data_dir.exists() else []
    print(f"mysql_data目录中的binlog文件数量: {len(binlog_files)}")
    if binlog_files:
        for binlog in sorted(binlog_files):
            size = binlog.stat().st_size
            print(f"  {binlog.name}: {size} 字节")
    print()
    
    # 检查binlog备份目录
    binlog_backup_dirs = list(backups_dir.glob("binlog_backup_*")) if backups_dir.exists() else []
    print(f"binlog备份目录数量: {len(binlog_backup_dirs)}")
    if binlog_backup_dirs:
        for bdir in sorted(binlog_backup_dirs):
            binlogs = list(bdir.glob("mysql-bin.[0-9]*"))
            print(f"  {bdir.name}: {len(binlogs)} 个binlog文件")

def analyze_timeline():
    """分析时间线"""
    print()
    print("=" * 80)
    print("时间线分析")
    print("=" * 80)
    print()
    
    baseline_file = Path("./backups/test3_baseline_snapshot_content.txt")
    if baseline_file.exists():
        with open(baseline_file, 'r') as f:
            for line in f:
                if line.startswith('Timestamp:'):
                    baseline_time = line.split(':', 1)[1].strip()
                    print(f"基准快照时间: {baseline_time}")
                    break
    
    restored_file = Path("./backups/test3_restored_snapshot_content.txt")
    if restored_file.exists():
        with open(restored_file, 'r') as f:
            for line in f:
                if line.startswith('Timestamp:'):
                    restored_time = line.split(':', 1)[1].strip()
                    print(f"恢复后快照时间: {restored_time}")
                    break
    
    # 检查备份时间戳文件
    latest_full = Path("./backups/LATEST_FULL_BACKUP_TIMESTAMP")
    latest_inc = Path("./backups/LATEST_INCREMENTAL_BACKUP_TIMESTAMP")
    
    if latest_full.exists():
        full_ts = latest_full.read_text().strip()
        print(f"最新全量备份时间戳: {full_ts}")
    
    if latest_inc.exists():
        inc_ts = latest_inc.read_text().strip()
        print(f"最新增量备份时间戳: {inc_ts}")

def main():
    print("test3.py 测试失败分析")
    print()
    
    analyze_snapshots()
    analyze_backups()
    analyze_timeline()
    
    print()
    print("=" * 80)
    print("分析完成")
    print("=" * 80)

if __name__ == "__main__":
    main()

