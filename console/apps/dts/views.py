from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import IsSuperUser, get_client_ip
from apps.core.models import AuditLog
from apps.dts.models import DtsTask
from apps.dts.serializers import DtsTableSyncSerializer, DtsTaskSerializer, DtsTaskWriteSerializer, DtsTestConnectionSerializer
from apps.dts.sync import (
    DtsError,
    assign_inventory,
    inspect_source_database,
    list_source_databases,
    replace_table_rows,
    test_target_connection,
)
from apps.dts.tasks import run_dts_full_sync


def _require_superuser(request):
    if not IsSuperUser().has_permission(request, None):
        return Response({"detail": "需要超级管理员权限"}, status=status.HTTP_403_FORBIDDEN)
    return None


class DtsDatabaseListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            return Response({"items": list_source_databases()})
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


class DtsTestConnectionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        denied = _require_superuser(request)
        if denied:
            return denied
        serializer = DtsTestConnectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = test_target_connection(
                data["target_host"],
                data["target_port"],
                data["target_user"],
                data.get("password") or "",
            )
        except DtsError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class DtsTaskListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tasks = list(DtsTask.objects.all())
        changed = []
        for task in tasks:
            if task.status in {"full_sync", "incremental"}:
                continue
            if task.table_total or task.bytes_total or task.rows_total:
                continue
            if not task.source_database_name():
                continue
            try:
                stats = inspect_source_database(task.source_database_name())
                assign_inventory(task, stats)
                if not task.table_syncs.exists():
                    replace_table_rows(task, stats)
            except Exception:
                continue
            changed.append(task)
        if changed:
            DtsTask.objects.bulk_update(
                changed,
                ["table_total", "table_done", "rows_total", "bytes_total", "bytes_done", "bytes_streamed", "progress_percent", "current_table", "full_phase", "structure_done", "updated_at"],
            )
        return Response({"items": DtsTaskSerializer(tasks, many=True).data})

    def post(self, request):
        denied = _require_superuser(request)
        if denied:
            return denied
        serializer = DtsTaskWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not data.get("password"):
            return Response({"detail": "请填写目标库密码"}, status=status.HTTP_400_BAD_REQUEST)
        if DtsTask.objects.filter(name=data["name"]).exists():
            return Response({"detail": "任务名称已存在"}, status=status.HTTP_400_BAD_REQUEST)
        task = DtsTask(
            name=data["name"],
            target_host=data["target_host"],
            target_port=data["target_port"],
            target_user=data["target_user"],
            databases=[data["source_database"]],
            target_database=data["target_database"],
        )
        task.set_password(data["password"])
        stats = None
        try:
            stats = inspect_source_database(data["source_database"])
            assign_inventory(task, stats)
        except Exception:
            stats = None
        task.append_log(
            f"任务已创建，待同步 {task.table_total} 张表，约 {task.rows_total} 行，{task.bytes_total} 字节"
        )
        task.save()
        if stats:
            replace_table_rows(task, stats)
        AuditLog.objects.create(
            action="dts_create",
            target=task.name,
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(DtsTaskSerializer(task).data, status=status.HTTP_201_CREATED)


class DtsTaskDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id: int):
        task = DtsTask.objects.filter(pk=task_id).first()
        if not task:
            return Response({"detail": "任务不存在"}, status=status.HTTP_404_NOT_FOUND)
        if (
            task.source_database_name()
            and not task.table_syncs.exists()
            and task.status in {"stopped", "paused", "error"}
        ):
            try:
                stats = inspect_source_database(task.source_database_name())
                assign_inventory(task, stats, reset_progress=True)
                task.save(update_fields=["table_total", "table_done", "rows_total", "bytes_total", "bytes_done", "bytes_streamed", "progress_percent", "current_table", "full_phase", "structure_done", "updated_at"])
                replace_table_rows(task, stats)
            except Exception:
                pass
        tables = task.table_syncs.all()
        return Response({
            "task": DtsTaskSerializer(task).data,
            "tables": DtsTableSyncSerializer(tables, many=True).data,
        })

    def patch(self, request, task_id: int):
        denied = _require_superuser(request)
        if denied:
            return denied
        task = DtsTask.objects.filter(pk=task_id).first()
        if not task:
            return Response({"detail": "任务不存在"}, status=status.HTTP_404_NOT_FOUND)
        if task.status in {"full_sync", "incremental"}:
            return Response({"detail": "任务运行中，不能修改"}, status=status.HTTP_409_CONFLICT)
        serializer = DtsTaskWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        task.name = data["name"]
        task.target_host = data["target_host"]
        task.target_port = data["target_port"]
        task.target_user = data["target_user"]
        task.databases = [data["source_database"]]
        task.target_database = data["target_database"]
        if data.get("password"):
            task.set_password(data["password"])
        task.append_log("任务配置已更新")
        task.save()
        return Response(DtsTaskSerializer(task).data)

    def delete(self, request, task_id: int):
        denied = _require_superuser(request)
        if denied:
            return denied
        task = DtsTask.objects.filter(pk=task_id).first()
        if not task:
            return Response({"detail": "任务不存在"}, status=status.HTTP_404_NOT_FOUND)
        if task.status in {"full_sync", "incremental"}:
            return Response({"detail": "请先停止任务"}, status=status.HTTP_409_CONFLICT)
        name = task.name
        task.delete()
        AuditLog.objects.create(
            action="dts_delete",
            target=name,
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class DtsTaskActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, task_id: int, action: str):
        denied = _require_superuser(request)
        if denied:
            return denied
        task = DtsTask.objects.filter(pk=task_id).first()
        if not task:
            return Response({"detail": "任务不存在"}, status=status.HTTP_404_NOT_FOUND)
        if action == "start":
            return self._start(request, task)
        if action == "pause":
            return self._pause(request, task)
        if action == "stop":
            return self._stop(request, task)
        return Response({"detail": "未知操作"}, status=status.HTTP_400_BAD_REQUEST)

    def _start(self, request, task: DtsTask):
        if task.status in {"full_sync", "incremental"}:
            return Response({"detail": "任务已在同步中"}, status=status.HTTP_409_CONFLICT)
        force_full = str(request.data.get("full", "")).lower() in {"1", "true", "yes"}
        if force_full or not task.binlog_file:
            task.status = "full_sync"
            task.error_message = ""
            task.append_log("提交全量同步")
            task.save()
            run_dts_full_sync.delay(task.id)
        else:
            task.status = "incremental"
            task.error_message = ""
            task.append_log(f"继续增量同步 {task.binlog_file}:{task.binlog_pos}")
            task.save()
        AuditLog.objects.create(
            action="dts_start",
            target=task.name,
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(DtsTaskSerializer(task).data)

    def _pause(self, request, task: DtsTask):
        if task.status not in {"full_sync", "incremental"}:
            return Response({"detail": "当前没有正在进行的同步"}, status=status.HTTP_400_BAD_REQUEST)
        task.status = "paused"
        task.append_log("已暂停")
        task.save(update_fields=["status", "log_text", "updated_at"])
        AuditLog.objects.create(
            action="dts_pause",
            target=task.name,
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(DtsTaskSerializer(task).data)

    def _stop(self, request, task: DtsTask):
        task.status = "stopped"
        task.append_log("已停止")
        task.save(update_fields=["status", "log_text", "updated_at"])
        AuditLog.objects.create(
            action="dts_stop",
            target=task.name,
            operator=request.user,
            ip_address=get_client_ip(request),
        )
        return Response(DtsTaskSerializer(task).data)
