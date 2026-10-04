import re

from apps.backups.pitr import preview_pitr_target

_DB_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def preview_database_pitr(
    source_database: str,
    target_time: str,
    write_mode: str,
    target_database: str = "",
    full_backup_timestamp: str | None = None,
) -> dict:
    source = (source_database or "").strip()
    if not _DB_NAME.match(source):
        raise ValueError("源库名无效")
    if write_mode not in {"overwrite", "new_database"}:
        raise ValueError("写入方式无效")

    target_name = (target_database or "").strip()
    if write_mode == "overwrite":
        target_name = source
    elif not target_name:
        compact = re.sub(r"[-: ]", "", target_time)[:12]
        target_name = f"{source}_pitr_{compact}"[:64]
    if not _DB_NAME.match(target_name):
        raise ValueError("目标库名无效")

    preview = preview_pitr_target(target_time, full_backup_timestamp)
    preview.update(
        {
            "source_database": source,
            "target_database": target_name,
            "write_mode": write_mode,
        }
    )
    if preview.get("valid"):
        preview["workflow"] = [
            "在独立数据卷上按目标时间做时间点恢复",
            "从临时实例导出源库",
            "覆盖" if write_mode == "overwrite" else "创建新库" + f"并导入 {target_name}",
            "删除临时实例和数据卷",
        ]
        if write_mode == "overwrite":
            preview["workflow"][2] = f"停止源库 {source} 的连接并覆盖导入"
    return preview
