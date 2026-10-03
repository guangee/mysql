import logging

from celery import shared_task
from django.conf import settings
from django.db import close_old_connections
from redis import Redis

from apps.dts.models import DtsTask
from apps.dts.sync import DtsError, advance_incremental, run_full_sync

logger = logging.getLogger("apps.dts")


def _lock(task_id: int, timeout: int = 300):
    client = Redis.from_url(settings.CELERY_BROKER_URL)
    return client.lock(f"mysql_console:dts:{task_id}", timeout=timeout)


@shared_task(time_limit=48 * 3600, soft_time_limit=48 * 3600 - 60)
def run_dts_full_sync(task_id: int):
    close_old_connections()
    lock = _lock(task_id, timeout=48 * 3600)
    if not lock.acquire(blocking=False):
        return
    try:
        run_full_sync(task_id)
    except DtsError as exc:
        _mark_failed(task_id, str(exc))
    except Exception as exc:
        logger.exception("dts full sync %s failed", task_id)
        _mark_failed(task_id, str(exc))
    finally:
        lock.release()


@shared_task
def advance_dts_incremental():
    close_old_connections()
    task_ids = list(DtsTask.objects.filter(status="incremental").values_list("id", flat=True))
    for task_id in task_ids:
        lock = _lock(task_id)
        if not lock.acquire(blocking=False):
            continue
        try:
            advance_incremental(task_id)
        except DtsError:
            logger.warning("dts incremental %s stopped: see task error", task_id)
        except Exception:
            logger.exception("dts incremental %s failed", task_id)
            _mark_failed(task_id, "增量同步异常，请查看任务日志")
        finally:
            lock.release()


def _mark_failed(task_id: int, message: str) -> None:
    task = DtsTask.objects.filter(pk=task_id).first()
    if not task or task.status not in {"full_sync", "incremental"}:
        return
    task.status = "error"
    task.error_message = (message or "同步失败")[:2000]
    task.append_log(task.error_message)
    task.save(update_fields=["status", "error_message", "log_text", "updated_at"])
