"""API views: databases."""

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

class DatabaseListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        snapshot = load_database_list_snapshot()
        if snapshot is None:
            collect_dashboard_snapshot_task.delay()
            return Response({"items": [], "summary": None, "collected_at": None, "mysql_error": ""})
        if snapshot.get("mysql_error") and not snapshot.get("items"):
            return _mysql_error_response(
                MySQLClientError(snapshot["mysql_error"]),
                "/api/databases/",
            )
        return Response(snapshot)

    def post(self, request):
        serializer = CreateDatabaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = create_database(
                name=data["name"],
                charset=data["charset"],
                collation=data.get("collation"),
                grant_user=data.get("grant_user"),
                grant_host=data.get("grant_host"),
                privilege_level=data.get("privilege_level", "all"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLClientError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AuditLog.objects.create(
            action="database_create",
            target=data["name"],
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        collect_dashboard_snapshot_task.delay()
        sync_schema_inventory_task.delay()
        return Response(result, status=status.HTTP_201_CREATED)


class DatabaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        refresh = request.query_params.get("refresh") in {"1", "true", "yes"}
        if refresh:
            collect_dashboard_snapshot_task.delay()
            sync_schema_inventory_task.delay()

        snapshot = load_database_detail_snapshot(name)
        if snapshot:
            return Response(snapshot)

        inventory = get_inventory_database_detail(name)
        if inventory:
            collect_dashboard_snapshot_task.delay()
            return Response(inventory)

        collect_dashboard_snapshot_task.delay()
        return Response(
            {
                "database": None,
                "tables": [],
                "table_total": 0,
                "collected_at": None,
                "detail": "资源快照尚未就绪，已触发后台采集",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class DatabaseTableStructureView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name, table):
        refresh = request.query_params.get("refresh") in {"1", "true", "yes"}
        if not refresh:
            inventory = get_inventory_table_structure(name, table)
            if inventory and inventory.get("columns"):
                return Response(inventory)
        try:
            data = get_table_structure(name, table)
            inventory = get_inventory_table_structure(name, table)
            if inventory:
                data["recent_changes"] = inventory.get("recent_changes") or []
                data["structure_hash"] = inventory.get("structure_hash") or ""
            else:
                data["recent_changes"] = []
            data["from_inventory"] = False
            return Response(data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLClientError as exc:
            return _mysql_error_response(exc, f"/api/databases/{name}/tables/{table}/")


class DatabaseSchemaChangesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        table = request.query_params.get("table") or None
        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        return Response(
            {
                "items": list_schema_changes(name, table=table, limit=limit),
                "database": name,
                "table": table,
            }
        )


class DatabaseMetricsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        hours = request.query_params.get("hours")
        try:
            hours_val = float(hours) if hours is not None else None
        except (TypeError, ValueError):
            hours_val = None
        return Response(get_database_metrics_history(name, hours=hours_val))


class DatabaseQueryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, name):
        serializer = DatabaseQuerySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = execute_readonly_query(name, data["sql"], limit=data.get("limit", 500))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLClientError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AuditLog.objects.create(
            action="database_query",
            target=f"{name}",
            detail=(data["sql"][:500]),
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(result)


class DatabaseExportView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, name):
        serializer = DatabaseExportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            if data.get("table"):
                sql = build_table_select_sql(name, data["table"], data.get("limit", 5000))
                default_name = f"{name}_{data['table']}.xlsx"
            else:
                sql = data["sql"]
                default_name = f"{name}_export.xlsx"
            filename = (data.get("filename") or default_name).strip()
            if not filename.lower().endswith(".xlsx"):
                filename += ".xlsx"

            response = export_query_to_excel_response(
                name,
                sql,
                limit=data.get("limit", 5000),
                filename=filename,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLClientError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AuditLog.objects.create(
            action="database_export",
            target=f"{name}",
            detail=sql[:500],
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return response


