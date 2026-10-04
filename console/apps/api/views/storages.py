"""API views: storages."""

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
import logging

from apps.accounts.services import upsert_system_credential
from apps.api.permissions import IsSuperUser, get_client_ip
from apps.api.serializers import (
    BackupCleanupJobSerializer,
    BackupCleanupTriggerSerializer,
    BackupJobSerializer,
    BackupPitrJobSerializer,
    BackupPitrPreviewSerializer,
    BackupPitrTriggerSerializer,
    BackupFullRestoreJobSerializer,
    BackupFullRestoreTriggerSerializer,
    DatabasePitrJobSerializer,
    DatabasePitrPreviewSerializer,
    DatabasePitrTriggerSerializer,
    BackupRetentionPolicySerializer,
    ChangePasswordSerializer,
    CreateBusinessUserSerializer,
    CreateDatabaseSerializer,
    DatabaseExportSerializer,
    DatabaseQuerySerializer,
    LoginSerializer,
    MySQLServiceActionSerializer,
    MySQLTuningUpdateSerializer,
    StorageBackendSerializer,
)
from apps.backups.models import BackupCleanupJob, BackupJob, BackupFileIndex, BackupPitrJob, DatabasePitrJob, BackupFullRestoreJob
from apps.backups.upload import upload_backup_file
from apps.backups.pitr import build_pitr_options, preview_pitr_target
from apps.backups.db_pitr_runner import preview_database_pitr
from apps.backups.tasks import (
    aggregate_backup_files,
    run_backup_task,
    run_cleanup_task,
    run_database_pitr_task,
    run_full_backup_restore_task,
    run_pitr_task,
    schedule_backup_file_index_refresh_if_stale,
    sync_backup_file_index_task,
)
from apps.backups.services import (
    annotate_backup_expiry,
    export_retention_policy,
    get_retention_policy,
    retention_to_dict,
    validate_cron_schedule,
)
from apps.backups.inventory import (
    catalog_to_dict,
    get_backup_file_catalog,
    is_index_stale,
    sync_backup_file_index_with_retry,
)
from apps.core.mysql_tuning import apply_memory_preset, apply_tuning, get_tuning_overview
from apps.core.ops_runner import (
    MySQLOpsError,
    apply_backup_crontab,
    control_mysql_service,
    get_mysql_service_status,
    tail_backup_log,
)
from apps.core.metrics import (
    get_database_metrics_history,
    get_host_metrics_history,
    load_dashboard_snapshot,
    load_database_detail_snapshot,
    load_database_list_snapshot,
)
from apps.core.tasks import collect_dashboard_snapshot_task, sync_schema_inventory_task
from apps.core.models import AuditLog
from apps.core.mysql_client import (
    MySQLClientError,
    change_user_password,
    create_business_user,
    create_database,
    list_mysql_users,
    list_system_accounts,
)
from apps.core.mysql_explorer import (
    build_table_select_sql,
    execute_readonly_query,
    export_query_to_excel_response,
    get_table_structure,
)
from apps.databases.services import (
    get_inventory_database_detail,
    get_inventory_table_structure,
    list_schema_changes,
)
from apps.storages.models import StorageBackend
from apps.storages.services import (
    build_backup_download_options,
    export_storages_config,
    import_from_env,
    stream_storage_object,
    test_storage_connection,
)

logger = logging.getLogger("apps.api")

from apps.api.views.common import (
    _format_size,
    _mysql_error_response,
    _restore_task_running,
)

class StorageBackendViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = StorageBackendSerializer
    queryset = StorageBackend.objects.all()

    def list(self, request, *args, **kwargs):
        if not StorageBackend.objects.exists():
            import_from_env()
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        storage = serializer.save()
        export_storages_config()
        sync_backup_file_index_task.delay()
        AuditLog.objects.create(
            action="storage_create",
            target=storage.name,
            operator=self.request.user,
            ip_address=get_client_ip(self.request),
        )

    def perform_update(self, serializer):
        storage = serializer.save()
        export_storages_config()
        sync_backup_file_index_task.delay()
        AuditLog.objects.create(
            action="storage_update",
            target=storage.name,
            operator=self.request.user,
            ip_address=get_client_ip(self.request),
        )

    def perform_destroy(self, instance):
        name = instance.name
        instance.delete()
        export_storages_config()
        sync_backup_file_index_task.delay()
        AuditLog.objects.create(
            action="storage_delete",
            target=name,
            operator=self.request.user,
            ip_address=get_client_ip(self.request),
        )

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        storage = self.get_object()
        ok, message = test_storage_connection(storage)
        storage.last_test_at = timezone.now()
        storage.last_test_ok = ok
        storage.last_test_message = message
        storage.save(update_fields=["last_test_at", "last_test_ok", "last_test_message"])
        AuditLog.objects.create(
            action="storage_test",
            target=storage.name,
            detail=message,
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(
            {
                "ok": ok,
                "message": message,
                "last_test_at": storage.last_test_at,
                "last_test_ok": storage.last_test_ok,
            }
        )

    @action(detail=False, methods=["post"], url_path="import-env")
    def import_env(self, request):
        storage = import_from_env()
        if storage:
            return Response({"detail": f"已从 .env 导入存储「{storage.name}」", "id": storage.id})
        return Response({"detail": "未导入：存储已存在或 .env 中无 S3 配置"})

    @action(detail=False, methods=["get"], url_path="export-status")
    def export_status(self, request):
        return Response({"exported_count": export_storages_config()})
