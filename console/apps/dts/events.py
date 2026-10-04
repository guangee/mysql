"""增量 SQL 明细：存在容器内 Redis，最近 N 条，重建后丢失。"""

from __future__ import annotations

import json
from datetime import datetime

from django.conf import settings
from django.utils import timezone
from redis import Redis

_SQL_MAX = 4000
_DEFAULT_LIMIT = 10000


def _client() -> Redis:
    return Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)


def _key(task_id: int) -> str:
    return f"mysql_console:dts:{task_id}:sql_events"


def _limit() -> int:
    return max(100, int(getattr(settings, "DTS_SQL_EVENT_LIMIT", _DEFAULT_LIMIT)))


def record_sql_events(task_id: int, items: list[dict]) -> None:
    if not items:
        return
    client = _client()
    key = _key(task_id)
    payload = [json.dumps(item, ensure_ascii=False, default=str) for item in items]
    pipe = client.pipeline()
    pipe.lpush(key, *reversed(payload))
    pipe.ltrim(key, 0, _limit() - 1)
    pipe.expire(key, 7 * 24 * 3600)
    pipe.execute()


def list_sql_events(task_id: int, limit: int = 200) -> dict:
    client = _client()
    key = _key(task_id)
    cap = _limit()
    take = max(1, min(int(limit or 200), cap))
    raw = client.lrange(key, 0, take - 1)
    items = []
    for text in raw:
        try:
            items.append(json.loads(text))
        except (TypeError, json.JSONDecodeError):
            continue
    return {"items": items, "total": int(client.llen(key) or 0), "limit": cap}


def format_event_time(value) -> str:
    if isinstance(value, datetime):
        local = timezone.localtime(value) if timezone.is_aware(value) else value
        return local.strftime("%Y-%m-%d %H:%M:%S")
    return timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")


def clip_sql(sql: str) -> str:
    text = (sql or "").strip()
    if len(text) <= _SQL_MAX:
        return text
    return text[:_SQL_MAX] + f"…(截断 {len(text) - _SQL_MAX} 字符)"


def event_kind(event) -> str:
    name = type(event).__name__
    mapping = {
        "WriteRowsEvent": "INSERT",
        "UpdateRowsEvent": "UPDATE",
        "DeleteRowsEvent": "DELETE",
        "QueryEvent": "DDL",
    }
    return mapping.get(name, name)
