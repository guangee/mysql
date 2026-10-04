"""
MySQL 可调整参数：读取、运行时应用与持久化到 mysql_config（经 docker exec 写入 MySQL 容器）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.core.ops_runner import read_file_in_mysql_container, write_file_in_mysql_container
from apps.core.mysql_client import MySQLClientError, mysql_cursor

CONFIG_FILENAME = "99-console-tuning.cnf"
MYSQL_TUNING_CONFIG_PATH = f"/etc/mysql/conf.d/{CONFIG_FILENAME}"
_CONFIG_LINE = re.compile(r"^([a-z0-9_]+)\s*=\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class TunableParam:
    key: str
    label: str
    category: str
    category_label: str
    unit: str  # count | bytes | seconds | enum
    min_value: float | int | None
    max_value: float | int | None
    dynamic: bool
    description: str
    step: float | int = 1
    risk: str = "low"  # low | medium | high
    value_type: str = "int"  # int | float | enum
    enum_options: tuple[str, ...] = ()
    recommend: str = ""


TUNABLE_PARAMS: tuple[TunableParam, ...] = (
    # ---------- 连接 ----------
    TunableParam(
        key="max_connections",
        label="最大连接数",
        category="connections",
        category_label="连接与会话",
        unit="count",
        min_value=10,
        max_value=100000,
        dynamic=True,
        description="允许同时建立的客户端连接上限。接近上限时新连接会失败（Too many connections）。",
        risk="medium",
        recommend="按并发与连接池规模设置，预留 10%~20% 余量。",
    ),
    TunableParam(
        key="max_user_connections",
        label="单用户最大连接",
        category="connections",
        category_label="连接与会话",
        unit="count",
        min_value=0,
        max_value=100000,
        dynamic=True,
        description="限制单个账号的最大并发连接数；0 表示不限制。",
        risk="medium",
        recommend="多租户/共享实例建议按业务账号限流。",
    ),
    TunableParam(
        key="max_connect_errors",
        label="最大连续连接错误",
        category="connections",
        category_label="连接与会话",
        unit="count",
        min_value=1,
        max_value=1000000000,
        dynamic=True,
        description="同一主机连续连接失败达到该次数后会被阻断，需 FLUSH HOSTS 解除。",
        risk="low",
    ),
    TunableParam(
        key="back_log",
        label="连接积压队列",
        category="connections",
        category_label="连接与会话",
        unit="count",
        min_value=1,
        max_value=65535,
        dynamic=False,
        description="短时间突发连接时，内核可挂起的连接请求队列长度。修改需重启。",
        risk="low",
    ),
    TunableParam(
        key="thread_cache_size",
        label="线程缓存",
        category="connections",
        category_label="连接与会话",
        unit="count",
        min_value=0,
        max_value=16384,
        dynamic=True,
        description="缓存空闲线程以便复用，降低频繁建连时的线程创建开销。",
        risk="low",
        recommend="可观察 Threads_created 与 Connections 比值后调优。",
    ),
    TunableParam(
        key="wait_timeout",
        label="非交互连接超时",
        category="connections",
        category_label="连接与会话",
        unit="seconds",
        min_value=1,
        max_value=31536000,
        dynamic=True,
        description="非交互连接空闲多久后自动断开（常见于应用连接池）。",
        risk="medium",
        recommend="应大于连接池空闲回收时间，避免池连接被服务端提前踢掉。",
    ),
    TunableParam(
        key="interactive_timeout",
        label="交互连接超时",
        category="connections",
        category_label="连接与会话",
        unit="seconds",
        min_value=1,
        max_value=31536000,
        dynamic=True,
        description="交互式会话（如 mysql 客户端）空闲超时时间。",
        risk="low",
    ),
    TunableParam(
        key="max_allowed_packet",
        label="最大数据包",
        category="connections",
        category_label="连接与会话",
        unit="bytes",
        min_value=1024,
        max_value=1073741824,
        dynamic=True,
        description="单次通信允许的最大包大小，影响大字段读写与大批量导入。",
        step=1048576,
        risk="medium",
        recommend="常见设置为 64M~256M，需与客户端同步。",
    ),
    # ---------- 内存 ----------
    TunableParam(
        key="innodb_buffer_pool_size",
        label="InnoDB 缓冲池",
        category="memory",
        category_label="内存",
        unit="bytes",
        min_value=134217728,
        max_value=None,
        dynamic=True,
        description="InnoDB 缓存表数据与索引的主内存池，通常是最关键的内存参数。",
        step=134217728,
        risk="high",
        recommend="专用实例常设为物理内存的 50%~70%，并确保与 chunk size 对齐。",
    ),
    TunableParam(
        key="innodb_buffer_pool_instances",
        label="缓冲池实例数",
        category="memory",
        category_label="内存",
        unit="count",
        min_value=1,
        max_value=64,
        dynamic=False,
        description="将缓冲池拆成多个实例以降低并发锁竞争。修改需重启。",
        risk="medium",
        recommend="缓冲池较大（>1G）时可设为 CPU 核数附近。",
    ),
    TunableParam(
        key="table_open_cache",
        label="表打开缓存",
        category="memory",
        category_label="内存",
        unit="count",
        min_value=1,
        max_value=524288,
        dynamic=True,
        description="缓存已打开表文件描述符的数量。表很多时过小会导致频繁开表。",
        risk="low",
    ),
    TunableParam(
        key="table_definition_cache",
        label="表定义缓存",
        category="memory",
        category_label="内存",
        unit="count",
        min_value=400,
        max_value=524288,
        dynamic=True,
        description="缓存表定义（.frm/元数据）的数量。",
        risk="low",
    ),
    TunableParam(
        key="tmp_table_size",
        label="临时表内存上限",
        category="memory",
        category_label="内存",
        unit="bytes",
        min_value=1024,
        max_value=1073741824,
        dynamic=True,
        description="内存临时表可达到的最大尺寸，超过会落到磁盘临时表。",
        step=1048576,
        risk="medium",
        recommend="通常与 max_heap_table_size 保持一致。",
    ),
    TunableParam(
        key="max_heap_table_size",
        label="MEMORY 表上限",
        category="memory",
        category_label="内存",
        unit="bytes",
        min_value=16384,
        max_value=1073741824,
        dynamic=True,
        description="用户创建的 MEMORY 表最大尺寸。",
        step=1048576,
        risk="medium",
    ),
    TunableParam(
        key="sort_buffer_size",
        label="排序缓冲",
        category="memory",
        category_label="内存",
        unit="bytes",
        min_value=32768,
        max_value=4294967295,
        dynamic=True,
        description="每个会话排序操作可使用的缓冲。过大容易在高并发下吃光内存。",
        step=65536,
        risk="high",
        recommend="保持适中（如 256K~2M），优先靠索引减少 filesort。",
    ),
    TunableParam(
        key="join_buffer_size",
        label="Join 缓冲",
        category="memory",
        category_label="内存",
        unit="bytes",
        min_value=128,
        max_value=4294967295,
        dynamic=True,
        description="无索引 join 时每个会话可用的缓冲。",
        step=128,
        risk="high",
        recommend="优先补索引，不要盲目调大。",
    ),
    TunableParam(
        key="read_buffer_size",
        label="顺序读缓冲",
        category="memory",
        category_label="内存",
        unit="bytes",
        min_value=8200,
        max_value=2147479552,
        dynamic=True,
        description="MyISAM 顺序扫描等场景使用的读缓冲。",
        step=4096,
        risk="medium",
    ),
    TunableParam(
        key="read_rnd_buffer_size",
        label="随机读缓冲",
        category="memory",
        category_label="内存",
        unit="bytes",
        min_value=8200,
        max_value=2147479552,
        dynamic=True,
        description="用于 MyISAM 乱序读等场景。",
        step=4096,
        risk="medium",
    ),
    # ---------- InnoDB ----------
    TunableParam(
        key="innodb_log_buffer_size",
        label="Redo 日志缓冲",
        category="innodb",
        category_label="InnoDB",
        unit="bytes",
        min_value=262144,
        max_value=4294967295,
        dynamic=True,
        description="事务写入 redo 前的内存缓冲。大事务频繁刷盘时可适当增大。",
        step=1048576,
        risk="medium",
    ),
    TunableParam(
        key="innodb_flush_log_at_trx_commit",
        label="事务提交刷盘策略",
        category="innodb",
        category_label="InnoDB",
        unit="enum",
        min_value=0,
        max_value=2,
        dynamic=True,
        description="0=每秒刷；1=每次提交都刷（最安全）；2=提交写 OS 缓存，每秒刷盘。",
        risk="high",
        value_type="enum",
        enum_options=("0", "1", "2"),
        recommend="金融/强一致场景保持 1；可容忍少量丢失时可用 2 提升性能。",
    ),
    TunableParam(
        key="innodb_io_capacity",
        label="InnoDB IO 能力",
        category="innodb",
        category_label="InnoDB",
        unit="count",
        min_value=100,
        max_value=2000000,
        dynamic=True,
        description="InnoDB 后台刷脏页等任务估算的磁盘 IOPS 能力。",
        risk="medium",
        recommend="SSD 常见 2000~20000，需结合压测调整。",
    ),
    TunableParam(
        key="innodb_io_capacity_max",
        label="InnoDB 最大 IO 能力",
        category="innodb",
        category_label="InnoDB",
        unit="count",
        min_value=100,
        max_value=2000000,
        dynamic=True,
        description="紧急刷脏时允许达到的最大 IOPS。",
        risk="medium",
    ),
    TunableParam(
        key="innodb_read_io_threads",
        label="读 IO 线程数",
        category="innodb",
        category_label="InnoDB",
        unit="count",
        min_value=1,
        max_value=64,
        dynamic=False,
        description="InnoDB 后台读 IO 线程数。修改需重启。",
        risk="medium",
    ),
    TunableParam(
        key="innodb_write_io_threads",
        label="写 IO 线程数",
        category="innodb",
        category_label="InnoDB",
        unit="count",
        min_value=1,
        max_value=64,
        dynamic=False,
        description="InnoDB 后台写 IO 线程数。修改需重启。",
        risk="medium",
    ),
    TunableParam(
        key="innodb_lock_wait_timeout",
        label="锁等待超时",
        category="innodb",
        category_label="InnoDB",
        unit="seconds",
        min_value=1,
        max_value=1073741824,
        dynamic=True,
        description="事务等待行锁的最长时间，超时后回滚当前语句。",
        risk="medium",
    ),
    TunableParam(
        key="innodb_deadlock_detect",
        label="死锁检测",
        category="innodb",
        category_label="InnoDB",
        unit="enum",
        min_value=None,
        max_value=None,
        dynamic=True,
        description="是否开启死锁检测。高并发极端场景可关闭并依赖锁等待超时。",
        risk="high",
        value_type="enum",
        enum_options=("ON", "OFF"),
        recommend="默认 ON；仅在明确评估后关闭。",
    ),
    # ---------- 日志与慢查询 ----------
    TunableParam(
        key="slow_query_log",
        label="慢查询日志",
        category="logging",
        category_label="日志与审计",
        unit="enum",
        min_value=None,
        max_value=None,
        dynamic=True,
        description="是否记录执行时间超过阈值的 SQL。",
        risk="low",
        value_type="enum",
        enum_options=("ON", "OFF"),
        recommend="排障/优化期间建议开启。",
    ),
    TunableParam(
        key="long_query_time",
        label="慢查询阈值",
        category="logging",
        category_label="日志与审计",
        unit="seconds",
        min_value=0,
        max_value=3600,
        dynamic=True,
        description="超过该秒数的语句记入慢查询日志，支持小数。",
        step=0.1,
        risk="low",
        value_type="float",
        recommend="在线业务常见 0.2~1 秒，排障时可临时调低。",
    ),
    TunableParam(
        key="log_queries_not_using_indexes",
        label="记录未走索引查询",
        category="logging",
        category_label="日志与审计",
        unit="enum",
        min_value=None,
        max_value=None,
        dynamic=True,
        description="即使未超过 long_query_time，未使用索引的查询也写入慢日志。",
        risk="medium",
        value_type="enum",
        enum_options=("ON", "OFF"),
        recommend="排障时可开，生产长期开启可能日志暴增。",
    ),
    TunableParam(
        key="sync_binlog",
        label="Binlog 同步策略",
        category="logging",
        category_label="日志与审计",
        unit="count",
        min_value=0,
        max_value=4294967295,
        dynamic=True,
        description="每写多少次 binlog 同步一次磁盘。1 最安全；0 由 OS 决定。",
        risk="high",
        recommend="要求 binlog 不丢通常设为 1。",
    ),
    TunableParam(
        key="binlog_expire_logs_seconds",
        label="Binlog 过期时间",
        category="logging",
        category_label="日志与审计",
        unit="seconds",
        min_value=0,
        max_value=4294967295,
        dynamic=True,
        description="自动清理二进制日志的保留秒数。0 表示不按时间自动过期。",
        risk="medium",
        recommend="需覆盖备份与 PITR 窗口，例如 7~30 天。",
    ),
)

CATEGORY_ORDER = ("connections", "memory", "innodb", "logging")

CATEGORY_META = {
    "connections": {
        "label": "连接与会话",
        "summary": "连接上限、超时、线程复用与网络包大小。",
    },
    "memory": {
        "label": "内存",
        "summary": "缓冲池与会话级缓冲，直接影响吞吐与 OOM 风险。",
    },
    "innodb": {
        "label": "InnoDB",
        "summary": "刷盘策略、IO 能力与锁相关参数。",
    },
    "logging": {
        "label": "日志与审计",
        "summary": "慢查询、Binlog 同步与保留策略。",
    },
}

# 按「分配给 MySQL 的可用内存」一键优化档位
MEMORY_PRESET_GBS = (1, 2, 4, 8, 16, 32, 64, 128)
_GB = 1024**3
_MB = 1024**2
_KB = 1024


def _align_bytes(value: int, chunk: int, minimum: int = 0) -> int:
    chunk = max(int(chunk or 1), 1)
    aligned = max(chunk, (int(value) // chunk) * chunk)
    return max(aligned, minimum)


def build_memory_preset(gb: int, chunk_size: int = 134217728) -> dict[str, Any]:
    """根据分配给 MySQL 的内存生成常见 OLTP 优化参数。"""
    if gb not in MEMORY_PRESET_GBS:
        raise ValueError(f"不支持的内存档位: {gb}G，可选: {', '.join(f'{x}G' for x in MEMORY_PRESET_GBS)}")

    # 专用实例缓冲池占比：小规格留更多给连接/OS，大规格可更高
    bp_ratio = {1: 0.45, 2: 0.50, 4: 0.55, 8: 0.62, 16: 0.65, 32: 0.68, 64: 0.70, 128: 0.72}[gb]
    buffer_pool = _align_bytes(int(gb * _GB * bp_ratio), chunk_size, minimum=128 * _MB)

    # MySQL 要求：buffer_pool_size 必须是 chunk_size * instances 的整数倍
    desired_instances = 1 if buffer_pool < _GB else min(64, max(2, min(gb, 16)))
    pool_instances = 1
    for n in range(desired_instances, 0, -1):
        unit = chunk_size * n
        if buffer_pool % unit == 0:
            pool_instances = n
            break
        # 向下对齐到可整除的大小
        aligned = (buffer_pool // unit) * unit
        if aligned >= 128 * _MB:
            buffer_pool = aligned
            pool_instances = n
            break
    if buffer_pool % (chunk_size * pool_instances) != 0:
        buffer_pool = _align_bytes(buffer_pool, chunk_size * pool_instances, minimum=128 * _MB)

    max_connections = {
        1: 100,
        2: 200,
        4: 400,
        8: 800,
        16: 1500,
        32: 2500,
        64: 4000,
        128: 6000,
    }[gb]
    table_open_cache = {
        1: 512,
        2: 1024,
        4: 2048,
        8: 4096,
        16: 8192,
        32: 16384,
        64: 32768,
        128: 65536,
    }[gb]
    table_definition_cache = max(400, table_open_cache // 2)
    tmp_table = {
        1: 32 * _MB,
        2: 64 * _MB,
        4: 64 * _MB,
        8: 128 * _MB,
        16: 256 * _MB,
        32: 256 * _MB,
        64: 512 * _MB,
        128: 512 * _MB,
    }[gb]
    sort_buffer = {
        1: 256 * _KB,
        2: 256 * _KB,
        4: 512 * _KB,
        8: 1 * _MB,
        16: 2 * _MB,
        32: 2 * _MB,
        64: 4 * _MB,
        128: 4 * _MB,
    }[gb]
    join_buffer = {
        1: 256 * _KB,
        2: 256 * _KB,
        4: 256 * _KB,
        8: 512 * _KB,
        16: 1 * _MB,
        32: 1 * _MB,
        64: 2 * _MB,
        128: 2 * _MB,
    }[gb]
    read_buffer = {
        1: 128 * _KB,
        2: 128 * _KB,
        4: 256 * _KB,
        8: 256 * _KB,
        16: 512 * _KB,
        32: 512 * _KB,
        64: 1 * _MB,
        128: 1 * _MB,
    }[gb]
    innodb_log_buffer = {
        1: 8 * _MB,
        2: 16 * _MB,
        4: 16 * _MB,
        8: 32 * _MB,
        16: 64 * _MB,
        32: 64 * _MB,
        64: 128 * _MB,
        128: 256 * _MB,
    }[gb]
    io_capacity = {
        1: 1000,
        2: 2000,
        4: 4000,
        8: 8000,
        16: 12000,
        32: 20000,
        64: 40000,
        128: 60000,
    }[gb]
    io_threads = {1: 2, 2: 2, 4: 4, 8: 4, 16: 8, 32: 8, 64: 16, 128: 16}[gb]
    thread_cache = min(max_connections // 4, 1024)
    back_log = min(max(max_connections // 2, 70), 65535)
    max_packet = 64 * _MB if gb <= 4 else (128 * _MB if gb <= 32 else 256 * _MB)

    return {
        "max_connections": max_connections,
        "max_user_connections": 0,
        "max_connect_errors": 100000,
        "back_log": back_log,
        "thread_cache_size": thread_cache,
        "wait_timeout": 28800,
        "interactive_timeout": 28800,
        "max_allowed_packet": max_packet,
        "innodb_buffer_pool_size": buffer_pool,
        "innodb_buffer_pool_instances": pool_instances,
        "table_open_cache": table_open_cache,
        "table_definition_cache": table_definition_cache,
        "tmp_table_size": tmp_table,
        "max_heap_table_size": tmp_table,
        "sort_buffer_size": sort_buffer,
        "join_buffer_size": join_buffer,
        "read_buffer_size": read_buffer,
        "read_rnd_buffer_size": read_buffer,
        "innodb_log_buffer_size": innodb_log_buffer,
        # 推荐值偏安全；大规格一键优化仍保持强一致，避免误伤生产
        "innodb_flush_log_at_trx_commit": 1,
        "innodb_io_capacity": io_capacity,
        "innodb_io_capacity_max": io_capacity * 2,
        "innodb_read_io_threads": io_threads,
        "innodb_write_io_threads": io_threads,
        "innodb_lock_wait_timeout": 50,
        "innodb_deadlock_detect": "ON",
        "slow_query_log": "ON",
        "long_query_time": 1.0 if gb <= 4 else 0.5,
        "log_queries_not_using_indexes": "OFF",
        "sync_binlog": 1,
        "binlog_expire_logs_seconds": 604800 if gb <= 8 else 2592000,
    }


def _detect_mysql_memory_bytes() -> tuple[int, str]:
    """返回 (可用内存字节, 来源说明)。优先容器限额，否则主机 MemTotal。"""
    try:
        from apps.core.ops_runner import get_mysql_host_stats

        stats = get_mysql_host_stats()
    except Exception:
        return 0, "unknown"

    container_limit = int(stats.get("memory_total_bytes") or 0)
    host_total = int(stats.get("host_memory_total_bytes") or 0)
    if container_limit > 0 and (host_total <= 0 or container_limit < host_total * 0.95):
        return container_limit, "container_limit"
    if host_total > 0:
        return host_total, "host_memtotal"
    if container_limit > 0:
        return container_limit, "container_limit"
    return 0, "unknown"


def _nearest_preset_gb(memory_bytes: int) -> int:
    if memory_bytes <= 0:
        return 8
    gb = memory_bytes / _GB
    # 并列时取较小档，避免建议值超出实际可用内存
    return min(MEMORY_PRESET_GBS, key=lambda x: (abs(x - gb), x))


def list_memory_presets(chunk_size: int = 134217728) -> list[dict[str, Any]]:
    presets = []
    for gb in MEMORY_PRESET_GBS:
        settings = build_memory_preset(gb, chunk_size)
        bp = settings["innodb_buffer_pool_size"]
        presets.append(
            {
                "gb": gb,
                "label": f"{gb}G",
                "summary": (
                    f"按 {gb}G 可用内存优化：缓冲池 {bp // _MB}MB，"
                    f"最大连接 {settings['max_connections']}，"
                    f"表缓存 {settings['table_open_cache']}"
                ),
                "settings": settings,
            }
        )
    return presets


def config_file_path() -> Path:
    return Path(MYSQL_TUNING_CONFIG_PATH)


def _parse_config_value(raw: str, value_type: str = "int"):
    raw = raw.strip().strip('"').strip("'")
    if value_type == "enum":
        return raw
    if value_type == "float":
        return float(raw)
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3}
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMG])?$", raw, re.IGNORECASE)
    if match:
        num = float(match.group(1))
        suffix = (match.group(2) or "").upper()
        return int(num * multipliers.get(suffix, 1))
    return int(float(raw))


def _format_config_value(param: TunableParam, value) -> str:
    if param.value_type == "enum":
        return str(value)
    if param.value_type == "float":
        return str(value)
    if param.unit == "bytes" and isinstance(value, int) and value >= 1024**2:
        if value % (1024**3) == 0:
            return f"{value // (1024**3)}G"
        if value % (1024**2) == 0:
            return f"{value // (1024**2)}M"
    return str(int(value))


def read_persisted_config() -> dict[str, Any]:
    text = read_file_in_mysql_container(MYSQL_TUNING_CONFIG_PATH)
    if not text.strip():
        return {}
    known = {p.key: p for p in TUNABLE_PARAMS}
    values: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        match = _CONFIG_LINE.match(line)
        if not match:
            continue
        key, raw = match.group(1).lower(), match.group(2)
        param = known.get(key)
        try:
            values[key] = _parse_config_value(raw, param.value_type if param else "int")
        except ValueError:
            continue
    return values


def write_persisted_config(values: dict[str, Any]) -> Path:
    lines = [
        "# 由 MySQL 控制台管理，重启 MySQL 后仍生效",
        "# Managed by MySQL Console",
        "[mysqld]",
    ]
    for param in TUNABLE_PARAMS:
        if param.key in values:
            lines.append(f"{param.key} = {_format_config_value(param, values[param.key])}")
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
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_runtime_value(param: TunableParam, raw: str):
    if param.value_type == "enum":
        text = str(raw)
        upper = text.upper()
        if upper in {"ON", "OFF", "1", "0", "TRUE", "FALSE"}:
            if upper in {"1", "TRUE"}:
                return "ON" if "ON" in param.enum_options else "1"
            if upper in {"0", "FALSE"}:
                return "OFF" if "OFF" in param.enum_options else "0"
        if text in param.enum_options:
            return text
        if upper in param.enum_options:
            return upper
        return text
    if param.value_type == "float":
        return _safe_float(raw)
    return _safe_int(raw)


def _validate_value(param: TunableParam, value: Any, variables: dict[str, str]) -> Any:
    if param.value_type == "enum":
        text = str(value)
        if param.enum_options and text not in param.enum_options:
            if _is_int_like(value) and str(int(value)) in param.enum_options:
                text = str(int(value))
            else:
                raise ValueError(f"{param.label} 只能是 {', '.join(param.enum_options)}")
        # MySQL 对纯数字枚举变量要求数值类型，不能传字符串 "1"
        if param.enum_options and all(opt.lstrip("-").isdigit() for opt in param.enum_options):
            return int(text)
        return text

    if param.value_type == "float":
        num = float(value)
    else:
        num = int(value)

    if param.min_value is not None and num < param.min_value:
        raise ValueError(f"{param.label} 不能小于 {param.min_value}")
    if param.max_value is not None and num > param.max_value:
        raise ValueError(f"{param.label} 不能大于 {param.max_value}")
    if param.key == "innodb_buffer_pool_size":
        chunk = _get_chunk_size(variables)
        if int(num) % chunk != 0:
            raise ValueError(f"{param.label} 必须是 {chunk // (1024**2)}MB 的整数倍")
    return num


def _is_int_like(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def _build_usage(param: TunableParam, runtime: Any, status: dict[str, str]) -> dict | None:
    if param.key == "max_connections":
        current = _safe_int(status.get("Threads_connected"))
        limit = _safe_int(runtime)
        return {
            "label": "当前连接",
            "current": current,
            "percent": round(current / limit * 100, 1) if limit else 0,
        }
    if param.key == "innodb_buffer_pool_size":
        used = _safe_int(status.get("Innodb_buffer_pool_bytes_data")) + _safe_int(
            status.get("Innodb_buffer_pool_bytes_dirty")
        )
        limit = _safe_int(runtime)
        return {
            "label": "缓冲池占用",
            "used_bytes": min(used, limit) if limit else used,
            "percent": round(used / limit * 100, 1) if limit else 0,
        }
    if param.key == "table_open_cache":
        opened = _safe_int(status.get("Open_tables"))
        limit = _safe_int(runtime)
        return {
            "label": "已打开表",
            "current": opened,
            "percent": round(opened / limit * 100, 1) if limit else 0,
        }
    if param.key == "thread_cache_size":
        return {
            "label": "缓存线程",
            "current": _safe_int(status.get("Threads_cached")),
            "created": _safe_int(status.get("Threads_created")),
            "percent": None,
        }
    return None


def get_tuning_overview(recommended_memory_gb: int | None = None) -> dict:
    keys = [p.key for p in TUNABLE_PARAMS]
    extra_keys = [
        "innodb_buffer_pool_chunk_size",
        "version",
        "datadir",
        "character_set_server",
        "collation_server",
        "innodb_buffer_pool_size",
    ]
    variables = _fetch_variables(list(dict.fromkeys(keys + extra_keys)))
    persisted = read_persisted_config()
    status = _fetch_status(
        [
            "Threads_connected",
            "Threads_running",
            "Threads_cached",
            "Threads_created",
            "Max_used_connections",
            "Open_tables",
            "Opened_tables",
            "Innodb_buffer_pool_bytes_data",
            "Innodb_buffer_pool_bytes_dirty",
            "Slow_queries",
            "Questions",
            "Uptime",
        ]
    )

    chunk_size = _get_chunk_size(variables)
    detected_bytes, detected_source = _detect_mysql_memory_bytes()
    suggested_gb = _nearest_preset_gb(detected_bytes)
    if recommended_memory_gb is None:
        recommended_gb = suggested_gb
    elif recommended_memory_gb in MEMORY_PRESET_GBS:
        recommended_gb = recommended_memory_gb
    else:
        raise ValueError(
            f"不支持的内存档位: {recommended_memory_gb}G，可选: {', '.join(f'{x}G' for x in MEMORY_PRESET_GBS)}"
        )

    suggested_settings = build_memory_preset(suggested_gb, chunk_size)
    recommended_settings = build_memory_preset(recommended_gb, chunk_size)
    memory_presets = list_memory_presets(chunk_size)

    groups_map: dict[str, dict] = {}
    flat_items: list[dict] = []
    restart_required = False
    modified_count = 0
    dynamic_count = 0

    for param in TUNABLE_PARAMS:
        runtime = _normalize_runtime_value(param, variables.get(param.key, "0"))
        persisted_val = persisted.get(param.key)
        dirty = persisted_val is not None and persisted_val != runtime
        needs_restart = (not param.dynamic) and dirty
        if needs_restart:
            restart_required = True
        if dirty:
            modified_count += 1
        if param.dynamic:
            dynamic_count += 1

        suggested_value = suggested_settings.get(param.key)
        recommended_value = recommended_settings.get(param.key)
        item = {
            "key": param.key,
            "label": param.label,
            "description": param.description,
            "recommend": param.recommend,
            "unit": param.unit,
            "min": param.min_value,
            "max": param.max_value,
            "step": param.step,
            "dynamic": param.dynamic,
            "risk": param.risk,
            "value_type": param.value_type,
            "enum_options": list(param.enum_options),
            "value": runtime,
            "suggested_value": suggested_value,
            "recommended_value": recommended_value,
            "persisted_value": persisted_val,
            "dirty": dirty,
            "needs_restart": needs_restart,
            "category": param.category,
            "category_label": param.category_label,
            "usage": _build_usage(param, runtime, status),
        }
        flat_items.append(item)

        group = groups_map.setdefault(
            param.category,
            {
                "key": param.category,
                "label": CATEGORY_META.get(param.category, {}).get("label", param.category_label),
                "summary": CATEGORY_META.get(param.category, {}).get("summary", ""),
                "items": [],
            },
        )
        group["items"].append(item)

    groups = [groups_map[key] for key in CATEGORY_ORDER if key in groups_map]
    uptime = _safe_int(status.get("Uptime"))
    return {
        "groups": groups,
        "items": flat_items,
        "categories": [
            {
                "key": key,
                "label": CATEGORY_META[key]["label"],
                "summary": CATEGORY_META[key]["summary"],
                "count": len(groups_map.get(key, {}).get("items", [])),
            }
            for key in CATEGORY_ORDER
            if key in groups_map
        ],
        "summary": {
            "total": len(flat_items),
            "dynamic": dynamic_count,
            "dirty": modified_count,
            "needs_restart": sum(1 for item in flat_items if item["needs_restart"]),
            "connections_current": _safe_int(status.get("Threads_connected")),
            "connections_running": _safe_int(status.get("Threads_running")),
            "connections_max_used": _safe_int(status.get("Max_used_connections")),
            "slow_queries": _safe_int(status.get("Slow_queries")),
            "uptime_seconds": uptime,
        },
        "instance": {
            "version": variables.get("version", ""),
            "datadir": variables.get("datadir", ""),
            "charset": variables.get("character_set_server", ""),
            "collation": variables.get("collation_server", ""),
        },
        "memory": {
            "detected_bytes": detected_bytes,
            "detected_source": detected_source,
            "suggested_gb": suggested_gb,
            "recommended_gb": recommended_gb,
            "presets": memory_presets,
        },
        "restart_required": restart_required,
        "config_file": str(config_file_path()),
        "chunk_size_bytes": chunk_size,
    }


def apply_tuning(changes: dict[str, Any], recommended_memory_gb: int | None = None) -> dict:
    known = {p.key: p for p in TUNABLE_PARAMS}
    unknown = set(changes) - set(known)
    if unknown:
        raise ValueError(f"未知参数: {', '.join(sorted(unknown))}")

    extra_keys = ["innodb_buffer_pool_chunk_size"]
    variables = _fetch_variables(list(changes.keys()) + extra_keys)

    normalized: dict[str, Any] = {}
    for key, value in changes.items():
        normalized[key] = _validate_value(known[key], value, variables)

    persisted = read_persisted_config()
    applied_runtime: list[str] = []
    pending_restart: list[str] = []
    errors: list[str] = []

    for key, value in normalized.items():
        param = known[key]
        try:
            if param.dynamic:
                with mysql_cursor() as cur:
                    cur.execute(f"SET GLOBAL `{key}` = %s", (value,))
                applied_runtime.append(key)
            else:
                pending_restart.append(key)
            # 持久化统一用可读字符串（数字枚举也写成数字）
            persisted[key] = value
        except MySQLClientError as exc:
            errors.append(f"{param.label}: {exc}")
        except Exception as exc:
            # mysql_cursor 对执行期 OperationalError 未封装，这里兜底转成可展示错误
            errors.append(f"{param.label}: {exc}")

    if errors:
        raise ValueError("; ".join(errors))

    write_persisted_config(persisted)

    return {
        "applied_runtime": applied_runtime,
        "pending_restart": pending_restart,
        "restart_required": bool(pending_restart),
        "overview": get_tuning_overview(recommended_memory_gb=recommended_memory_gb),
    }


def apply_memory_preset(gb: int) -> dict:
    variables = _fetch_variables(["innodb_buffer_pool_chunk_size"])
    chunk_size = _get_chunk_size(variables)
    settings = build_memory_preset(gb, chunk_size)
    result = apply_tuning(settings, recommended_memory_gb=gb)
    result["preset_memory_gb"] = gb
    return result
