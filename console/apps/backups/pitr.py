from django.utils import timezone

from apps.backups.inventory import list_backup_files
from apps.backups.services import parse_backup_timestamp


def _display(timestamp: str) -> str:
    moment = parse_backup_timestamp(timestamp)
    if not moment:
        return timestamp
    return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M:%S")


def _iso(moment) -> str | None:
    if not moment:
        return None
    return moment.isoformat()


def build_pitr_options() -> dict:
    files = list_backup_files()
    full_items = []
    incremental_items = []
    for item in files:
        record = {
            "timestamp": item["timestamp"],
            "display_time": _display(item["timestamp"]),
            "filename": item["filename"],
            "backed_up_at": _iso(parse_backup_timestamp(item["timestamp"])),
        }
        if item["backup_type"] == "full":
            full_items.append(record)
        else:
            incremental_items.append(record)

    full_items.sort(key=lambda item: item["timestamp"])
    incremental_items.sort(key=lambda item: item["timestamp"])
    timeline = [
        {**item, "backup_type": "full"} for item in full_items
    ] + [
        {**item, "backup_type": "incremental"} for item in incremental_items
    ]
    timeline.sort(key=lambda item: item["timestamp"])
    now = timezone.now()
    windows = []
    for index, item in enumerate(timeline):
        start = parse_backup_timestamp(item["timestamp"])
        end = now
        if index + 1 < len(timeline):
            end = parse_backup_timestamp(timeline[index + 1]["timestamp"]) or now
        windows.append(
            {
                "backup_type": item["backup_type"],
                "timestamp": item["timestamp"],
                "display_time": item["display_time"],
                "window_start": _iso(start),
                "window_end": _iso(end),
            }
        )

    recoverable = bool(full_items)
    earliest = parse_backup_timestamp(full_items[0]["timestamp"]) if full_items else None
    return {
        "recoverable": recoverable,
        "reason": "" if recoverable else "还没有可用的全量备份",
        "full_backups": list(reversed(full_items)),
        "incremental_backups": list(reversed(incremental_items)),
        "windows": windows,
        "earliest_recoverable_at": _iso(earliest),
        "latest_recoverable_at": _iso(now) if recoverable else None,
        "notes": [
            "整实例恢复会停止 MySQL 并覆盖当前数据目录。",
            "单库恢复在临时卷上还原后再导入生产库，不影响其他库。",
        ],
    }


def _select_base(files: list[dict], target, full_backup_timestamp: str | None):
    full_items = [item for item in files if item["backup_type"] == "full"]
    if full_backup_timestamp:
        chosen = next((item for item in full_items if item["timestamp"] == full_backup_timestamp), None)
        if not chosen:
            return None, "指定的全量备份不存在"
        if parse_backup_timestamp(chosen["timestamp"]) > target:
            return None, "指定的全量备份晚于目标时间"
        base_full = chosen
    else:
        eligible = [item for item in full_items if parse_backup_timestamp(item["timestamp"]) and parse_backup_timestamp(item["timestamp"]) <= target]
        if not eligible:
            return None, "目标时间早于最早的全量备份"
        base_full = max(eligible, key=lambda item: item["timestamp"])

    incrementals = [
        item
        for item in files
        if item["backup_type"] != "full"
        and item["timestamp"] > base_full["timestamp"]
        and parse_backup_timestamp(item["timestamp"])
        and parse_backup_timestamp(item["timestamp"]) <= target
    ]
    incrementals.sort(key=lambda item: item["timestamp"])
    base = incrementals[-1] if incrementals else base_full
    return {
        "base_backup_type": "incremental" if base is not base_full else "full",
        "base_timestamp": base["timestamp"],
        "base_display_time": _display(base["timestamp"]),
        "full_backup_timestamp": base_full["timestamp"],
        "incremental_backups": [item["timestamp"] for item in incrementals],
    }, ""


def preview_pitr_target(target_time: str, full_backup_timestamp: str | None = None) -> dict:
    target = _parse_target(target_time)
    if target is None:
        return {"valid": False, "reason": "目标时间格式应为 YYYY-MM-DD HH:MM:SS", "target_time": target_time}
    if target > timezone.now():
        return {"valid": False, "reason": "目标时间不能晚于当前时间", "target_time": target_time}
    plan, reason = _select_base(list_backup_files(), target, full_backup_timestamp)
    if not plan:
        return {"valid": False, "reason": reason, "target_time": target_time}
    return {"valid": True, "reason": "", "target_time": target_time, "plan": plan}


def _parse_target(target_time: str):
    from datetime import datetime

    from apps.backups.services import _zone

    text = (target_time or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(text, fmt)
            return timezone.make_aware(naive, _zone())
        except ValueError:
            continue
    return None
