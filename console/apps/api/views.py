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
from apps.core.mysql_tuning import apply_tuning, get_tuning_overview
from apps.core.docker_client import apply_backup_crontab, tail_backup_log
from apps.core.metrics import get_host_metrics_history, load_dashboard_snapshot, load_database_list_snapshot
from apps.core.tasks import collect_dashboard_snapshot_task
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
    get_database_overview,
    get_table_structure,
    list_database_tables,
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


def _mysql_error_response(exc: MySQLClientError, path: str = ""):
    logger.error("MySQL 不可用 %s: %s", path, exc)
    return Response(
        {"detail": "MySQL 暂不可用，请稍后重试", "error": str(exc)},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def _restore_task_running() -> bool:
    return (
        BackupPitrJob.objects.filter(status__in=["pending", "running"]).exists()
        or DatabasePitrJob.objects.filter(status__in=["pending", "running"]).exists()
        or BackupFullRestoreJob.objects.filter(status__in=["pending", "running"]).exists()
    )


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if not user:
            return Response({"detail": "用户名或密码错误"}, status=status.HTTP_401_UNAUTHORIZED)
        refresh = RefreshToken.for_user(user)
        AuditLog.objects.create(
            action="login",
            target=user.username,
            operator=user,
            ip_address=get_client_ip(request),
        )
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "is_superuser": user.is_superuser,
                },
            }
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response(
            {
                "id": user.id,
                "username": user.username,
                "is_superuser": user.is_superuser,
                "is_staff": user.is_staff,
            }
        )


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        snapshot = load_dashboard_snapshot()
        if snapshot is None:
            collect_dashboard_snapshot_task.delay()
            snapshot = {
                "mysql_status": {"connected": False, "version": "-", "uptime_seconds": 0},
                "mysql_stats": None,
                "host_stats": None,
                "mysql_error": "",
                "db_count": 0,
                "collected_at": None,
            }

        hours = request.query_params.get("hours")
        try:
            history_hours = float(hours) if hours else None
        except (TypeError, ValueError):
            history_hours = None
        host_metrics_history = get_host_metrics_history(history_hours)

        storages = StorageBackend.objects.filter(enabled=True)
        latest_jobs = BackupJob.objects.all()[:5]

        return Response(
            {
                "mysql_status": snapshot.get("mysql_status") or {"connected": False, "version": "-"},
                "mysql_stats": snapshot.get("mysql_stats"),
                "host_stats": snapshot.get("host_stats"),
                "host_metrics_history": host_metrics_history,
                "mysql_error": snapshot.get("mysql_error") or "",
                "db_count": snapshot.get("db_count") or 0,
                "collected_at": snapshot.get("collected_at"),
                "storage_count": storages.count(),
                "storage_ok_count": storages.filter(last_test_ok=True).count(),
                "latest_jobs": BackupJobSerializer(latest_jobs, many=True).data,
            }
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
        return Response(result, status=status.HTTP_201_CREATED)


class DatabaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        try:
            overview = get_database_overview(name)
            tables = list_database_tables(name)
            for t in tables:
                t["size_display"] = _format_size(t["size_bytes"])
                t["data_size_display"] = _format_size(t["data_bytes"])
                t["index_size_display"] = _format_size(t["index_bytes"])
            overview["size_display"] = _format_size(overview["size_bytes"])
            return Response({"database": overview, "tables": tables})
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLClientError as exc:
            return _mysql_error_response(exc, f"/api/databases/{name}/")


class DatabaseTableStructureView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name, table):
        try:
            return Response(get_table_structure(name, table))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLClientError as exc:
            return _mysql_error_response(exc, f"/api/databases/{name}/tables/{table}/")


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


class BackupJobViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BackupJobSerializer
    queryset = BackupJob.objects.all()

    def list(self, request, *args, **kwargs):
        limit = int(request.query_params.get("limit", 50))
        jobs = BackupJob.objects.all()[:limit]
        running = BackupJob.objects.filter(status__in=["pending", "running"]).exists()
        serializer = self.get_serializer(jobs, many=True)
        return Response({"items": serializer.data, "running": running})

    @action(detail=False, methods=["post"], url_path="trigger/(?P<backup_type>full|incremental)")
    def trigger(self, request, backup_type=None):
        if BackupJob.objects.filter(status__in=["pending", "running"]).exists():
            return Response({"detail": "已有备份任务在进行中"}, status=status.HTTP_409_CONFLICT)

        job = BackupJob.objects.create(
            backup_type=backup_type,
            status="pending",
            trigger="manual",
            created_by=request.user,
        )
        run_backup_task.delay(job.id)
        AuditLog.objects.create(
            action="backup_trigger",
            target=f"{backup_type} #{job.id}",
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(BackupJobSerializer(job).data, status=status.HTTP_201_CREATED)


class BackupFileListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        policy = get_retention_policy()
        catalog = get_backup_file_catalog()
        force_refresh = request.query_params.get("refresh") in {"1", "true", "yes"}

        if force_refresh:
            try:
                sync_backup_file_index_with_retry(force=True)
            except Exception:
                schedule_backup_file_index_refresh_if_stale()
        elif not BackupFileIndex.objects.exists():
            schedule_backup_file_index_refresh_if_stale()
        elif is_index_stale(catalog):
            schedule_backup_file_index_refresh_if_stale()

        files = aggregate_backup_files()
        full_items = [f for f in files if f.get("backup_type") == "full"]
        incremental_items = [f for f in files if f.get("backup_type") != "full"]

        for f in files:
            for s in f.get("storages", []):
                if s.get("last_modified"):
                    s["last_modified"] = s["last_modified"].isoformat()

        annotate_backup_expiry(full_items, policy.full_retention_days)
        annotate_backup_expiry(incremental_items, policy.incremental_retention_days)

        return Response(
            {
                "full": full_items,
                "incremental": incremental_items,
                "retention": retention_to_dict(policy),
                "catalog": catalog_to_dict(get_backup_file_catalog()),
            }
        )


class BackupUploadView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "请选择备份文件"}, status=status.HTTP_400_BAD_REQUEST)

        storage_id = request.data.get("storage_id")
        backup_type = (request.data.get("backup_type") or "full").strip()
        if backup_type not in {"full", "incremental"}:
            return Response({"detail": "backup_type 须为 full 或 incremental"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            storage = StorageBackend.objects.get(pk=storage_id, enabled=True)
        except StorageBackend.DoesNotExist:
            return Response({"detail": "存储不存在或未启用"}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = upload_backup_file(storage, upload, backup_type)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({"detail": str(exc)[:500]}, status=status.HTTP_400_BAD_REQUEST)

        sync_backup_file_index_task.delay(force=True)
        AuditLog.objects.create(
            action="backup_upload",
            target=f"{result['filename']} -> {storage.name}",
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class BackupFullRestoreTriggerView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def post(self, request):
        if _restore_task_running():
            return Response({"detail": "已有恢复任务在进行中"}, status=status.HTTP_409_CONFLICT)
        if BackupJob.objects.filter(status__in=["pending", "running"]).exists():
            return Response({"detail": "已有备份任务在进行中，请稍后再试"}, status=status.HTTP_409_CONFLICT)

        serializer = BackupFullRestoreTriggerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ts = serializer.validated_data["full_backup_timestamp"]
        filename = f"backup_{ts}.tar.gz"

        job = BackupFullRestoreJob.objects.create(
            full_backup_timestamp=ts,
            filename=filename,
            status="pending",
            created_by=request.user,
        )
        run_full_backup_restore_task.delay(job.id)
        AuditLog.objects.create(
            action="backup_full_restore_trigger",
            target=f"{filename} #{job.id}",
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(BackupFullRestoreJobSerializer(job).data, status=status.HTTP_201_CREATED)


class BackupFullRestoreJobListView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def get(self, request):
        limit = int(request.query_params.get("limit", 20))
        jobs = BackupFullRestoreJob.objects.all()[:limit]
        running = BackupFullRestoreJob.objects.filter(status__in=["pending", "running"]).exists()
        return Response(
            {
                "items": BackupFullRestoreJobSerializer(jobs, many=True).data,
                "running": running,
            }
        )


class BackupRetentionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(retention_to_dict())

    def patch(self, request):
        serializer = BackupRetentionPolicySerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        for field in (
            "full_backup_schedule",
            "incremental_backup_schedule",
            "cleanup_local_schedule",
            "cleanup_s3_schedule",
        ):
            if field in data:
                try:
                    data[field] = validate_cron_schedule(data[field])
                except ValueError as exc:
                    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        policy = get_retention_policy()
        for field, value in data.items():
            setattr(policy, field, value)
        policy.save()
        export_retention_policy()

        crontab_result = apply_backup_crontab()
        crontab_applied = crontab_result.returncode == 0

        AuditLog.objects.create(
            action="backup_retention_update",
            target=(
                f"全量备份 {policy.full_backup_schedule}"
                f" / 增量备份 {policy.incremental_backup_schedule}"
                f"；全量保留 {policy.full_retention_days} 天 / 增量保留 {policy.incremental_retention_days} 天"
                f"；本地清理 {policy.cleanup_local_schedule}"
                f"；对象存储清理 {policy.cleanup_s3_schedule}"
            ),
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        payload = retention_to_dict(policy)
        payload["crontab_applied"] = crontab_applied
        if not crontab_applied:
            payload["crontab_message"] = (
                (crontab_result.stderr or crontab_result.stdout or "更新 crontab 失败").strip()[-500:]
            )
        return Response(payload)


class BackupCleanupJobListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = int(request.query_params.get("limit", 20))
        jobs = BackupCleanupJob.objects.all()[:limit]
        running = BackupCleanupJob.objects.filter(status__in=["pending", "running"]).exists()
        return Response(
            {
                "items": BackupCleanupJobSerializer(jobs, many=True).data,
                "running": running,
            }
        )


class BackupCleanupTriggerView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if BackupCleanupJob.objects.filter(status__in=["pending", "running"]).exists():
            return Response({"detail": "已有清理任务在进行中"}, status=status.HTTP_409_CONFLICT)

        serializer = BackupCleanupTriggerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scope = serializer.validated_data["scope"]

        job = BackupCleanupJob.objects.create(
            scope=scope,
            status="pending",
            trigger="manual",
            created_by=request.user,
        )
        run_cleanup_task.delay(job.id)
        AuditLog.objects.create(
            action="backup_cleanup_trigger",
            target=f"{scope} #{job.id}",
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(BackupCleanupJobSerializer(job).data, status=status.HTTP_201_CREATED)


class BackupPitrOptionsView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def get(self, request):
        force_refresh = request.query_params.get("refresh") in {"1", "true", "yes"}
        if force_refresh:
            try:
                sync_backup_file_index_with_retry(force=True)
            except Exception:
                schedule_backup_file_index_refresh_if_stale()
        elif not BackupFileIndex.objects.exists() or is_index_stale():
            schedule_backup_file_index_refresh_if_stale()
        return Response(build_pitr_options())


class BackupPitrPreviewView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def post(self, request):
        serializer = BackupPitrPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(
            preview_pitr_target(
                data["target_time"],
                data.get("full_backup_timestamp") or None,
            )
        )


class BackupPitrJobListView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def get(self, request):
        limit = int(request.query_params.get("limit", 20))
        jobs = BackupPitrJob.objects.all()[:limit]
        running = BackupPitrJob.objects.filter(status__in=["pending", "running"]).exists()
        return Response(
            {
                "items": BackupPitrJobSerializer(jobs, many=True).data,
                "running": running,
            }
        )


class BackupPitrTriggerView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def post(self, request):
        if _restore_task_running():
            return Response({"detail": "已有恢复任务在进行中"}, status=status.HTTP_409_CONFLICT)
        if BackupJob.objects.filter(status__in=["pending", "running"]).exists():
            return Response({"detail": "已有备份任务在进行中，请稍后再试"}, status=status.HTTP_409_CONFLICT)

        serializer = BackupPitrTriggerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        preview = preview_pitr_target(
            data["target_time"],
            data.get("full_backup_timestamp") or None,
        )
        if not preview.get("valid"):
            return Response({"detail": preview.get("reason") or "目标时间不可恢复"}, status=status.HTTP_400_BAD_REQUEST)

        job = BackupPitrJob.objects.create(
            target_time=data["target_time"],
            full_backup_timestamp=data.get("full_backup_timestamp") or "",
            status="pending",
            plan=preview.get("plan") or {},
            created_by=request.user,
        )
        run_pitr_task.delay(job.id)
        AuditLog.objects.create(
            action="backup_pitr_trigger",
            target=f"{data['target_time']} #{job.id}",
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(BackupPitrJobSerializer(job).data, status=status.HTTP_201_CREATED)


class DatabasePitrPreviewView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def post(self, request):
        serializer = DatabasePitrPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            preview = preview_database_pitr(
                data["source_database"],
                data["target_time"],
                data["write_mode"],
                data.get("target_database") or "",
                data.get("full_backup_timestamp") or None,
            )
        except ValueError as exc:
            return Response({"valid": False, "reason": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(preview)


class DatabasePitrJobListView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def get(self, request):
        limit = int(request.query_params.get("limit", 20))
        jobs = DatabasePitrJob.objects.all()[:limit]
        running = DatabasePitrJob.objects.filter(status__in=["pending", "running"]).exists()
        return Response(
            {
                "items": DatabasePitrJobSerializer(jobs, many=True).data,
                "running": running,
            }
        )


class DatabasePitrTriggerView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def post(self, request):
        if _restore_task_running():
            return Response({"detail": "已有恢复任务在进行中"}, status=status.HTTP_409_CONFLICT)
        if BackupJob.objects.filter(status__in=["pending", "running"]).exists():
            return Response({"detail": "已有备份任务在进行中，请稍后再试"}, status=status.HTTP_409_CONFLICT)

        serializer = DatabasePitrTriggerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            preview = preview_database_pitr(
                data["source_database"],
                data["target_time"],
                data["write_mode"],
                data.get("target_database") or "",
                data.get("full_backup_timestamp") or None,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not preview.get("valid"):
            return Response({"detail": preview.get("reason") or "目标时间不可恢复"}, status=status.HTTP_400_BAD_REQUEST)

        job = DatabasePitrJob.objects.create(
            source_database=data["source_database"],
            target_database=preview["target_database"],
            write_mode=data["write_mode"],
            target_time=data["target_time"],
            full_backup_timestamp=data.get("full_backup_timestamp") or "",
            status="pending",
            plan=preview.get("plan") or {},
            created_by=request.user,
        )
        run_database_pitr_task.delay(job.id)
        AuditLog.objects.create(
            action="backup_database_pitr_trigger",
            target=f"{data['source_database']} -> {preview['target_database']} @ {data['target_time']} #{job.id}",
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(DatabasePitrJobSerializer(job).data, status=status.HTTP_201_CREATED)


class BackupDownloadView(APIView):
    """返回下载方式：API 代理 / 对象存储直连预签名 URL"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        storage_id = request.query_params.get("storage_id")
        key = request.query_params.get("key")
        if not storage_id or not key:
            return Response({"detail": "缺少参数 storage_id 或 key"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            storage = StorageBackend.objects.get(pk=storage_id)
            options = build_backup_download_options(storage, key)
            return Response(options)
        except StorageBackend.DoesNotExist:
            return Response({"detail": "存储不存在"}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class BackupDownloadProxyView(APIView):
    """经控制台 API 代理流式下载（适用于对象存储仅内网可达的场景）"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        storage_id = request.query_params.get("storage_id")
        key = request.query_params.get("key")
        if not storage_id or not key:
            return Response({"detail": "缺少参数 storage_id 或 key"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            storage = StorageBackend.objects.get(pk=storage_id)
            return stream_storage_object(storage, key)
        except StorageBackend.DoesNotExist:
            return Response({"detail": "存储不存在"}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class BackupLogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        lines = int(request.query_params.get("lines", 300))
        return Response({"content": tail_backup_log(lines)})


class MySQLSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            return Response(get_tuning_overview())
        except MySQLClientError as exc:
            return _mysql_error_response(exc, "/api/mysql/settings/")

    def patch(self, request):
        if not IsSuperUser().has_permission(request, self):
            return Response({"detail": "需要超级管理员权限"}, status=status.HTTP_403_FORBIDDEN)

        serializer = MySQLTuningUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = apply_tuning(serializer.validated_data["settings"])
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except MySQLClientError as exc:
            return _mysql_error_response(exc, "/api/mysql/settings/")

        AuditLog.objects.create(
            action="mysql_settings_update",
            target=", ".join(f"{k}={v}" for k, v in serializer.validated_data["settings"].items()),
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(result)


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
