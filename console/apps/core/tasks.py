from __future__ import annotations

from celery import shared_task

from apps.core.docker_client import get_mysql_host_stats
from apps.core.metrics import record_host_metrics


@shared_task(bind=True, ignore_result=True)
def collect_host_metrics_task(self):
    stats = get_mysql_host_stats()
    record_host_metrics(stats)
