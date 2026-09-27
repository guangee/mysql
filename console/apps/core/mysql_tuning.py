"""
MySQL 可调整参数：读取、运行时应用与持久化到 mysql_config（经 docker exec 写入 MySQL 容器）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.core.docker_client import read_file_in_mysql_container, write_file_in_mysql_container
from apps.core.mysql_client import MySQLClientError, mysql_cursor

CONFIG_FILENAME = "99-console-tuning.cnf"
MYSQL_TUNING_CONFIG_PATH = f"/etc/mysql/conf.d/{CONFIG_FILENAME}"
_CONFIG_LINE = re.compile(r"^([a-z_]+)\s*=\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class TunableParam:
    key: str
    label: str
    category: str
    category_label: str
    unit: str  # count | bytes | seconds
    min_value: int
    max_value: int | None
    dynamic: bool
    description: str
    step: int = 1


TUNABLE_PARAMS: tuple[TunableParam, ...] = (
    TunableParam(
        key="max_connections",
        label="最大连接数",
        category="connections",
        category_label="连接",
        unit="count",
        min_value=1,
        max_value=10000,
        dynamic=True,
        description="允许同时建立的客户端连接上限。当前连接数接近该值时应适当调高。",
    ),
    TunableParam(
        key="thread_cache_size",
        label="线程缓存",
        category="connections",
        category_label="连接",
        unit="count",
        min_value=0,
        max_value=16384,
        dynamic=True,
        description="缓存空闲线程以复用，减少频繁创建/销毁线程的开销。",
    ),
    TunableParam(
        key="wait_timeout",
        label="非交互连接超时",
        category="connections",
        category_label="连接",
        unit="seconds",
        min_value=1,
        max_value=86400,
        dynamic=True,
        description="非交互式连接空闲多少秒后自动断开（如应用连接池）。",
    ),
    TunableParam(
        key="interactive_timeout",
        label="交互连接超时",
        category="connections",
        category_label="连接",
        unit="seconds",
        min_value=1,
        max_value=86400,
        dynamic=True,
        description="交互式会话（如 mysql 客户端）空闲超时时间。",
    ),
    TunableParam(
        key="innodb_buffer_pool_size",
        label="InnoDB 缓冲池",
        category="memory",
        category_label="内存 / 缓冲池",
        unit="bytes",
        min_value=134217728,
        max_value=None,
        dynamic=True,
        description="InnoDB 缓存表与索引数据的内存池，通常是 MySQL 最重要的内存参数。",
        step=134217728,
    ),
    TunableParam(
        key="innodb_buffer_pool_instances",
        label="缓冲池实例数",
        category="memory",
        category_label="内存 / 缓冲池",
        unit="count",
        min_value=1,
        max_value=64,
        dynamic=False,
        description="将缓冲池划分为多个实例以减少锁竞争；修改后需重启 MySQL 生效。",
    ),
    TunableParam(
        key="table_open_cache",
        label="表缓存",
        category="memory",
        category_label="内存 / 缓冲池",
        unit="count",
        min_value=1,
        max_value=524288,
        dynamic=True,
        description="缓存已打开表句柄的数量，表较多时可适当增大。",
    ),
)

CATEGORY_ORDER = ("connections", "memory")


def config_file_path() -> Path:
    return Path(MYSQL_TUNING_CONFIG_PATH)


def _parse_config_value(raw: str) -> int:
    raw = raw.strip().strip('"').strip("'")
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3}
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMG])?$", raw, re.IGNORECASE)
    if match:
        num = float(match.group(1))
        suffix = (match.group(2) or "").upper()
        return int(num * multipliers.get(suffix, 1))
    return int(raw)


def _format_config_value(key: str, value: int) -> str:
    if key == "innodb_buffer_pool_size" and value >= 1024**2:
        if value % (1024**3) == 0:
            return f"{value // (1024**3)}G"
        return f"{value // (1024**2)}M"
    return str(value)


def read_persisted_config() -> dict[str, int]:
    text = read_file_in_mysql_container(MYSQL_TUNING_CONFIG_PATH)
    if not text.strip():
        return {}
    values: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        match = _CONFIG_LINE.match(line)
        if match:
            key, raw = match.group(1).lower(), match.group(2)
            try:
                values[key] = _parse_config_value(raw)
            except ValueError:
                continue
    return values


def write_persisted_config(values: dict[str, int]) -> Path:
    lines = [
        "# 由 MySQL 控制台管理，重启 MySQL 后仍生效",
        "# Managed by MySQL Console",
        "[mysqld]",
    ]
    for param in TUNABLE_PARAMS:
        if param.key in values:
            lines.append(f"{param.key} = {_format_config_value(param.key, values[param.key])}")
    content = "\n".join(lines) + "\n"
    write_file_in_mysql_container(MYSQL_TUNING_CONFIG_PATH, content)
    return config_file_path()


def _fetch_variables(keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    with mysql_cursor() as cur:
        cur.execute(
            f"SHOW GLOBAL VARIABLES WHERE Variable_name IN ({','.join(['%s'] * len(keys))})",
            keys,
        )
        return {name: val for name, val in cur.fetchall()}


def _fetch_status(keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    with mysql_cursor() as cur:
        cur.execute(
            f"SHOW GLOBAL STATUS WHERE Variable_name IN ({','.join(['%s'] * len(keys))})",
            keys,
        )
        return {name: val for name, val in cur.fetchall()}


def _get_chunk_size(variables: dict[str, str]) -> int:
    return max(_safe_int(variables.get("innodb_buffer_pool_chunk_size"), 134217728), 1048576)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _validate_value(param: TunableParam, value: int, variables: dict[str, str]) -> None:
    if value < param.min_value:
        raise ValueError(f"{param.label} 不能小于 {param.min_value}")
    if param.max_value is not None and value > param.max_value:
        raise ValueError(f"{param.label} 不能大于 {param.max_value}")
    if param.key == "innodb_buffer_pool_size":
        chunk = _get_chunk_size(variables)
        if value % chunk != 0:
            raise ValueError(f"{param.label} 必须是 {chunk // (1024**2)}MB 的整数倍")


def get_tuning_overview() -> dict:
    keys = [p.key for p in TUNABLE_PARAMS]
    extra_keys = ["innodb_buffer_pool_chunk_size"]
    variables = _fetch_variables(keys + extra_keys)
    persisted = read_persisted_config()
    status = _fetch_status(
        [
            "Threads_connected",
            "Innodb_buffer_pool_bytes_data",
            "Innodb_buffer_pool_bytes_dirty",
        ]
    )

    groups_map: dict[str, dict] = {}
    restart_required = False

    for param in TUNABLE_PARAMS:
        runtime = _safe_int(variables.get(param.key))
        persisted_val = persisted.get(param.key)
        item = {
            "key": param.key,
            "label": param.label,
            "description": param.description,
            "unit": param.unit,
            "min": param.min_value,
            "max": param.max_value,
            "step": param.step,
            "dynamic": param.dynamic,
            "value": runtime,
            "persisted_value": persisted_val,
            "needs_restart": not param.dynamic and persisted_val is not None and persisted_val != runtime,
        }
        if item["needs_restart"]:
            restart_required = True

        if param.key == "max_connections":
            current = _safe_int(status.get("Threads_connected"))
            item["usage"] = {
                "current": current,
                "percent": round(current / runtime * 100, 1) if runtime else 0,
            }
        elif param.key == "innodb_buffer_pool_size":
            used = _safe_int(status.get("Innodb_buffer_pool_bytes_data")) + _safe_int(
                status.get("Innodb_buffer_pool_bytes_dirty")
            )
            item["usage"] = {
                "used_bytes": min(used, runtime),
                "percent": round(used / runtime * 100, 1) if runtime else 0,
            }

        group = groups_map.setdefault(
            param.category,
            {"key": param.category, "label": param.category_label, "items": []},
        )
        group["items"].append(item)

    groups = [groups_map[key] for key in CATEGORY_ORDER if key in groups_map]
    return {
        "groups": groups,
        "restart_required": restart_required,
        "config_file": str(config_file_path()),
        "chunk_size_bytes": _get_chunk_size(variables),
    }


def apply_tuning(changes: dict[str, int]) -> dict:
    known = {p.key: p for p in TUNABLE_PARAMS}
    unknown = set(changes) - set(known)
    if unknown:
        raise ValueError(f"未知参数: {', '.join(sorted(unknown))}")

    extra_keys = ["innodb_buffer_pool_chunk_size"]
    variables = _fetch_variables(list(changes.keys()) + extra_keys)

    for key, value in changes.items():
        _validate_value(known[key], int(value), variables)

    persisted = read_persisted_config()
    applied_runtime: list[str] = []
    pending_restart: list[str] = []
    errors: list[str] = []

    for key, value in changes.items():
        param = known[key]
        int_value = int(value)
        try:
            if param.dynamic:
                with mysql_cursor() as cur:
                    cur.execute(f"SET GLOBAL `{key}` = %s", (int_value,))
                applied_runtime.append(key)
            else:
                pending_restart.append(key)
            persisted[key] = int_value
        except MySQLClientError as exc:
            errors.append(f"{param.label}: {exc}")

    if errors:
        raise ValueError("; ".join(errors))

    write_persisted_config(persisted)

    return {
        "applied_runtime": applied_runtime,
        "pending_restart": pending_restart,
        "restart_required": bool(pending_restart),
        "overview": get_tuning_overview(),
    }
