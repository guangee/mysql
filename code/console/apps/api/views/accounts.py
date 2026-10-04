"""API views: accounts."""

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

class BusinessAccountListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            users = [u for u in list_mysql_users() if not u["is_system"]]
            return Response({"items": users})
        except MySQLClientError as exc:
            return _mysql_error_response(exc, "/api/accounts/business/")

    def post(self, request):
        serializer = CreateBusinessUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = create_business_user(
                user=data["user"],
                host=data["host"],
                password=data["password"],
                database=data.get("database"),
                privilege_level=data.get("privilege_level", "all"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLClientError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AuditLog.objects.create(
            action="user_create",
            target=f"{data['user']}@{data['host']}",
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class SystemAccountListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"items": list_system_accounts()})


class BusinessPasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, user, host):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            change_user_password(user, host, serializer.validated_data["new_password"])
            AuditLog.objects.create(
                action="password_change",
                target=f"{user}@{host}",
                operator=request.user,
                ip_address=get_client_ip(request),
            )
            return Response({"detail": f"已修改 {user}@{host} 的密码"})
        except MySQLClientError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class SystemPasswordChangeView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def post(self, request, role):
        accounts = {a["role"]: a for a in list_system_accounts()}
        account = accounts.get(role)
        if not account:
            return Response({"detail": "未知系统账号角色"}, status=status.HTTP_404_NOT_FOUND)

        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if role == "root" and not request.user.check_password(
            serializer.validated_data.get("admin_password", "")
        ):
            return Response({"detail": "登录密码验证失败"}, status=status.HTTP_403_FORBIDDEN)

        try:
            change_user_password(
                account["user"],
                account["host"],
                serializer.validated_data["new_password"],
            )
            upsert_system_credential(
                role, account["user"], serializer.validated_data["new_password"]
            )
            AuditLog.objects.create(
                action="password_change",
                target=f"system:{role} ({account['user']}@{account['host']})",
                operator=request.user,
                ip_address=get_client_ip(request),
            )
            AuditLog.objects.create(
                action="credential_sync",
                target=role,
                operator=request.user,
                ip_address=get_client_ip(request),
            )
            return Response({"detail": "密码已修改并同步凭证"})
        except MySQLClientError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


