"""API views: mysql_admin."""

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

class MySQLSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        recommended_gb = request.query_params.get("recommended_memory_gb")
        try:
            recommended = int(recommended_gb) if recommended_gb not in (None, "") else None
        except (TypeError, ValueError):
            return Response({"detail": "recommended_memory_gb 必须是整数"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            return Response(get_tuning_overview(recommended_memory_gb=recommended))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLClientError as exc:
            return _mysql_error_response(exc, "/api/mysql/settings/")

    def patch(self, request):
        if not IsSuperUser().has_permission(request, self):
            return Response({"detail": "需要超级管理员权限"}, status=status.HTTP_403_FORBIDDEN)

        serializer = MySQLTuningUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            if data.get("preset_memory_gb") is not None:
                result = apply_memory_preset(int(data["preset_memory_gb"]))
                target = f"preset={data['preset_memory_gb']}G"
            else:
                result = apply_tuning(
                    data.get("settings") or {},
                    recommended_memory_gb=data.get("recommended_memory_gb"),
                )
                target = ", ".join(f"{k}={v}" for k, v in (data.get("settings") or {}).items())
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLClientError as exc:
            return _mysql_error_response(exc, "/api/mysql/settings/")

        AuditLog.objects.create(
            action="mysql_settings_update",
            target=target[:500],
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(result)


class MySQLServiceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(get_mysql_service_status())

    def post(self, request):
        if not IsSuperUser().has_permission(request, self):
            return Response({"detail": "需要超级管理员权限"}, status=status.HTTP_403_FORBIDDEN)
        if _restore_task_running():
            return Response(
                {"detail": "当前有恢复任务进行中，请完成后再操作 MySQL 启停"},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = MySQLServiceActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action_name = serializer.validated_data["action"]
        wait_ready = serializer.validated_data.get("wait_ready", True)
        try:
            result = control_mysql_service(action_name, wait_ready=wait_ready)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLOpsError as exc:
            logger.error("MySQL 服务控制失败 action=%s: %s", action_name, exc)
            return Response({"detail": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        AuditLog.objects.create(
            action=f"mysql_service_{action_name}",
            target=result.get("container") or "mysql",
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(result)


