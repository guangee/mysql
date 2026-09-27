import logging
from pathlib import Path

from celery import shared_task
from django.db import close_old_connections
from django.utils import timezone

from apps.backups.inventory import is_index_stale, list_backup_files, sync_backup_file_index_with_retry
from apps.backups.models import (
    BackupCleanupJob,
    BackupFullRestoreJob,
    BackupJob,
    BackupPitrJob,
    DatabasePitrJob,
)
from apps.core.docker_client import (
    DockerClientError,
    cleanup_pitr_resources,
    dump_database_from_container,
    import_database_to_production,
    prepare_production_database,
    run_backup_command,
    run_cleanup_command,
    run_full_backup_restore,
    run_pitr_on_docker_volume,
    run_pitr_restore,
    start_temp_mysql_container,
    wait_mysql_container_ready,
)

logger = logging.getLogger("apps.backups")


def aggregate_backup_files() -> list[dict]:
    return list_backup_files()


def _finish(job, result=None, error: str = ""):
    job.finished_at = timezone.now()
    if result is not None:
        job.output_log = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else ""))[-50000:]
        if result.returncode == 0 and not error:
            job.status = "success"
            job.error_message = ""
        else:
            job.status = "failed"
            job.error_message = (error or result.stderr or result.stdout or f"退出码 {result.returncode}")[-4000:]
    else:
        job.status = "failed"
        job.error_message = (error or "任务失败")[-4000:]
    job.save()


def _begin(job):
    job.status = "running"
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])


@shared_task
def sync_backup_file_index_task(force: bool = False):
    close_old_connections()
    return sync_backup_file_index_with_retry(force=force)


def schedule_backup_file_index_refresh_if_stale():
    if is_index_stale():
        sync_backup_file_index_task.delay()


@shared_task
def run_backup_task(job_id: int):
    close_old_connections()
    job = BackupJob.objects.get(pk=job_id)
    _begin(job)
    try:
        result = run_backup_command(job.backup_type)
        _finish(job, result)
        if job.status == "success":
            sync_backup_file_index_with_retry(force=True)
    except Exception as exc:
        logger.exception("backup job %s failed", job_id)
        _finish(job, error=str(exc))


@shared_task
def run_cleanup_task(job_id: int):
    close_old_connections()
    job = BackupCleanupJob.objects.get(pk=job_id)
    _begin(job)
    try:
        result = run_cleanup_command(job.scope)
        _finish(job, result)
        if job.status == "success":
            sync_backup_file_index_with_retry(force=True)
    except Exception as exc:
        logger.exception("cleanup job %s failed", job_id)
        _finish(job, error=str(exc))


@shared_task
def run_full_backup_restore_task(job_id: int):
    close_old_connections()
    job = BackupFullRestoreJob.objects.get(pk=job_id)
    _begin(job)
    try:
        result = run_full_backup_restore(job.full_backup_timestamp)
        _finish(job, result)
    except Exception as exc:
        logger.exception("full restore %s failed", job_id)
        _finish(job, error=str(exc))


@shared_task
def run_pitr_task(job_id: int):
    close_old_connections()
    job = BackupPitrJob.objects.get(pk=job_id)
    _begin(job)
    try:
        result = run_pitr_restore(job.target_time, job.full_backup_timestamp or "")
        _finish(job, result)
    except Exception as exc:
        logger.exception("pitr %s failed", job_id)
        _finish(job, error=str(exc))


@shared_task
def run_database_pitr_task(job_id: int):
    close_old_connections()
    job = DatabasePitrJob.objects.get(pk=job_id)
    _begin(job)
    volume = f"mysql-db-pitr-{job.id}"
    container = f"mysql-db-pitr-{job.id}"
    dump_path = Path("/app/data/pitr") / f"{job.id}.sql"
    logs = []
    try:
        result = run_pitr_on_docker_volume(volume, job.target_time, job.full_backup_timestamp or "")
        logs.append(result.stdout or "")
        logs.append(result.stderr or "")
        if result.returncode != 0:
            _finish(job, result)
            return
        start_temp_mysql_container(container, volume)
        wait_mysql_container_ready(container)
        found = dump_database_from_container(container, job.source_database, dump_path)
        if not found:
            _finish(job, error=f"目标时间点不存在数据库 {job.source_database}")
            return
        prepare_production_database(job.target_database, job.write_mode)
        import_database_to_production(job.target_database, dump_path)
        job.output_log = "\n".join(logs)[-50000:]
        job.status = "success"
        job.error_message = ""
        job.finished_at = timezone.now()
        job.save()
    except DockerClientError as exc:
        _finish(job, error=str(exc))
    except Exception as exc:
        logger.exception("database pitr %s failed", job_id)
        _finish(job, error=str(exc))
    finally:
        cleanup_pitr_resources(container, volume)
        if dump_path.exists():
            dump_path.unlink(missing_ok=True)
