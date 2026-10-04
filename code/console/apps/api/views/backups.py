"""API views: backups."""

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

class BackupJobViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BackupJobSerializer
    queryset = BackupJob.objects.all()

    def list(self, request, *args, **kwargs):
        try:
            page = int(request.query_params.get("page", 1))
        except (TypeError, ValueError):
            page = 1
        try:
            # 兼容旧 limit 参数：无 page_size 时把 limit 当作 page_size
            raw_size = request.query_params.get("page_size", request.query_params.get("limit", 10))
            page_size = int(raw_size)
        except (TypeError, ValueError):
            page_size = 10
        page = max(1, page)
        page_size = max(1, min(page_size, 100))

        qs = BackupJob.objects.all()
        total = qs.count()
        offset = (page - 1) * page_size
        jobs = qs[offset : offset + page_size]
        running = BackupJob.objects.filter(status__in=["pending", "running"]).exists()
        serializer = self.get_serializer(jobs, many=True)
        return Response(
            {
                "items": serializer.data,
                "running": running,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )

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
        try:
            from pathlib import Path

            from apps.backups.naming import extract_backup_timestamp, find_backup_archive
            from django.conf import settings as dj_settings

            full_root = Path(dj_settings.BACKUP_BASE_DIR) / "full"
            if full_root.is_dir():
                for child in full_root.iterdir():
                    if child.is_dir() and extract_backup_timestamp(child.name) == ts:
                        archive = find_backup_archive(child)
                        if archive is not None:
                            filename = archive.name
                            break
        except Exception:
            pass

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
        return Response(preview_pitr_target(data["target_time"]))


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
        preview = preview_pitr_target(data["target_time"])
        if not preview.get("valid"):
            return Response({"detail": preview.get("reason") or "目标时间不可恢复"}, status=status.HTTP_400_BAD_REQUEST)

        plan = preview.get("plan") or {}
        job = BackupPitrJob.objects.create(
            target_time=data["target_time"],
            full_backup_timestamp=plan.get("full_backup_timestamp") or "",
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
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not preview.get("valid"):
            return Response({"detail": preview.get("reason") or "目标时间不可恢复"}, status=status.HTTP_400_BAD_REQUEST)

        plan = preview.get("plan") or {}
        job = DatabasePitrJob.objects.create(
            source_database=data["source_database"],
            target_database=preview["target_database"],
            write_mode=data["write_mode"],
            target_time=data["target_time"],
            full_backup_timestamp=plan.get("full_backup_timestamp") or "",
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


