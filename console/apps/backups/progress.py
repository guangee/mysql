"""从备份日志提炼进度、文件大小和云存储上传结果。"""

from __future__ import annotations

import re

_TS = re.compile(r"^\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\]\s*")
_SIZE_HUMAN = re.compile(r"压缩完成，文件大小:\s*([0-9.]+)\s*(KB|MB|GB|B)", re.IGNORECASE)
_SIZE_BYTES = re.compile(r"文件大小校验通过:\s*(\d+)\s*字节")
_STORAGE = re.compile(r"\[存储:([^\]]+)\]\s*(成功|失败)(?:\s*-\s*(.*))?")
_FILENAME = re.compile(r"backup_(\d{8}_\d{6})\.tar\.gz")
_DATABASES = re.compile(r"已备份的数据库:\s*(.+)")

_STAGES = (
    ("done", 100, "备份完成", r"全量备份流程完成|增量备份流程完成"),
    ("uploaded", 96, "已上传并校验", r"备份成功上传到 S3"),
    ("upload_failed", 90, "上传失败", r"上传校验失败|错误: 上传到 S3 失败"),
    ("uploading", 88, "正在上传到云存储", r"开始上传备份|上传文件:"),
    ("packed", 80, "压缩完成", r"压缩完成，文件大小"),
    ("packing", 72, "正在打包压缩", r"打包备份|打包增量备份"),
    ("prepare", 62, "正在准备备份", r"准备备份|应用 redo|解压备份文件"),
    ("copied", 58, "数据复制完成", r"全量备份完成:|增量备份完成:"),
    ("copying", 36, "正在复制数据", r"执行 XtraBackup|xtrabackup:"),
    ("start", 8, "开始备份", r"开始全量备份|开始增量备份|========== .*备份开始"),
)


def _plain(line: str) -> str:
    return _TS.sub("", line).strip()


def _human_to_bytes(amount: str, unit: str) -> int:
    value = float(amount)
    factor = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}[unit.upper()]
    return int(value * factor)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.1f} MB"
    return f"{size_bytes / 1024**3:.2f} GB"


def summarize_backup_log(text: str, status: str = "", storage_results: dict | None = None) -> dict:
    lines = [_plain(line) for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    joined = "\n".join(lines)

    stage = "pending"
    percent = 0
    stage_label = "等待开始"
    if status == "pending":
        stage, percent, stage_label = "pending", 0, "等待开始"
    elif status == "running" and not lines:
        stage, percent, stage_label = "running", 3, "任务已启动"
    else:
        for key, pct, label, pattern in _STAGES:
            if re.search(pattern, joined):
                stage, percent, stage_label = key, pct, label
                break
        if status == "success":
            stage, percent, stage_label = "done", 100, "备份完成"
        elif status == "failed" and stage not in {"upload_failed"}:
            stage_label = "备份失败"
            percent = max(percent, 100)

    xb_lines = sum(1 for line in lines if line.startswith("xtrabackup:"))
    if status == "running" and stage == "copying":
        percent = min(55, 18 + min(xb_lines, 37))

    size_bytes = None
    size_display = ""
    byte_match = None
    for match in _SIZE_BYTES.finditer(joined):
        byte_match = match
    if byte_match:
        size_bytes = int(byte_match.group(1))
        size_display = _format_size(size_bytes)
    else:
        human = None
        for match in _SIZE_HUMAN.finditer(joined):
            human = match
        if human:
            size_bytes = _human_to_bytes(human.group(1), human.group(2))
            size_display = _format_size(size_bytes)

    filename = ""
    name_match = None
    for match in _FILENAME.finditer(joined):
        name_match = match
    if name_match:
        filename = f"backup_{name_match.group(1)}.tar.gz"

    databases = ""
    db_match = None
    for match in _DATABASES.finditer(joined):
        db_match = match
    if db_match:
        databases = db_match.group(1).strip()

    storages: dict[str, dict] = {}
    for match in _STORAGE.finditer(joined):
        name, result, detail = match.group(1), match.group(2), (match.group(3) or "").strip()
        storages[name] = {
            "ok": result == "成功",
            "message": detail or ("已上传并校验" if result == "成功" else "上传或校验失败"),
        }
    if isinstance(storage_results, dict):
        for name, result in storage_results.items():
            if isinstance(result, dict) and "ok" in result and name not in storages:
                storages[name] = result

    s3_disabled = "S3 备份已禁用" in joined
    if s3_disabled and not storages:
        upload_status = "skipped"
        upload_label = "未启用云存储"
    elif storages and all(item.get("ok") for item in storages.values()):
        upload_status = "uploaded"
        upload_label = "已上传"
    elif storages and any(item.get("ok") for item in storages.values()):
        upload_status = "partial"
        upload_label = "部分上传"
    elif stage in {"upload_failed"} or (storages and not any(item.get("ok") for item in storages.values())):
        upload_status = "failed"
        upload_label = "上传失败"
    elif stage == "uploading" or (status == "running" and "开始上传备份" in joined):
        upload_status = "uploading"
        upload_label = "上传中"
    elif status == "success" and "备份成功上传到 S3" in joined:
        upload_status = "uploaded"
        upload_label = "已上传"
    else:
        upload_status = "pending"
        upload_label = "尚未上传"

    detail = ""
    for line in reversed(lines):
        if line.startswith("xtrabackup:"):
            detail = line.removeprefix("xtrabackup: ").strip()
            if len(detail) > 120:
                detail = detail[:117] + "..."
            break
    if not detail and lines:
        detail = lines[-1][:120]

    recent = lines[-6:]
    return {
        "stage": stage,
        "percent": 100 if status == "success" else percent,
        "stage_label": "备份完成" if status == "success" else stage_label,
        "detail": detail,
        "filename": filename,
        "size_bytes": size_bytes,
        "size_display": size_display or "-",
        "databases": databases,
        "upload_status": upload_status,
        "upload_label": upload_label,
        "storages": storages,
        "recent_lines": recent,
    }
