from rest_framework import serializers

from apps.dts.models import DtsConnection, DtsTask, DtsTableSync
from apps.dts.sync import DtsError, validate_database_name


class DtsConnectionSerializer(serializers.ModelSerializer):
    task_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = DtsConnection
        fields = [
            "id",
            "name",
            "host",
            "port",
            "user",
            "remark",
            "mysql_version",
            "last_ok_at",
            "last_error",
            "task_count",
            "created_at",
            "updated_at",
        ]


class DtsConnectionWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64)
    host = serializers.CharField(max_length=255)
    port = serializers.IntegerField(min_value=1, max_value=65535, default=3306)
    user = serializers.CharField(max_length=64)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)
    remark = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate_host(self, value):
        host = value.strip()
        if not host or any(ch.isspace() for ch in host):
            raise serializers.ValidationError("地址不合法")
        return host


class DtsCreateDatabaseSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64)
    charset = serializers.ChoiceField(
        choices=["utf8mb4", "utf8", "latin1"],
        default="utf8mb4",
        required=False,
    )

    def validate_name(self, value):
        try:
            return validate_database_name(value)
        except DtsError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class DtsTaskSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    position = serializers.SerializerMethodField()
    source_database = serializers.SerializerMethodField()
    connection = DtsConnectionSerializer(read_only=True)
    direction = serializers.SerializerMethodField()

    class Meta:
        model = DtsTask
        fields = [
            "id",
            "name",
            "connection",
            "target_host",
            "target_port",
            "target_user",
            "databases",
            "source_database",
            "target_database",
            "direction",
            "status",
            "status_display",
            "binlog_file",
            "binlog_pos",
            "position",
            "events_applied",
            "last_event_at",
            "last_sync_at",
            "lag_seconds",
            "table_total",
            "table_done",
            "rows_total",
            "bytes_total",
            "bytes_done",
            "bytes_streamed",
            "progress_percent",
            "current_table",
            "full_phase",
            "structure_done",
            "error_message",
            "log_text",
            "created_at",
            "updated_at",
        ]

    def get_position(self, obj):
        if not obj.binlog_file:
            return ""
        return f"{obj.binlog_file}:{obj.binlog_pos}"

    def get_source_database(self, obj):
        return obj.source_database_name()

    def get_direction(self, obj):
        return "local_to_remote"


class DtsTableSyncSerializer(serializers.ModelSerializer):
    phase_display = serializers.CharField(source="get_phase_display", read_only=True)
    percent = serializers.SerializerMethodField()

    class Meta:
        model = DtsTableSync
        fields = [
            "id",
            "name",
            "rows_total",
            "rows_copied",
            "bytes_total",
            "bytes_copied",
            "phase",
            "phase_display",
            "percent",
            "error_message",
        ]

    def get_percent(self, obj):
        if obj.phase == "done":
            return 100
        if obj.phase != "copying":
            return 0
        if not obj.bytes_total:
            return 50
        return min(99, int(obj.bytes_copied * 100 / obj.bytes_total))


class DtsTaskWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64)
    connection_id = serializers.IntegerField()
    source_database = serializers.CharField(max_length=64)
    target_database = serializers.CharField(max_length=64)

    def validate_source_database(self, value):
        return self._validate_database(value)

    def validate_target_database(self, value):
        return self._validate_database(value)

    def _validate_database(self, value):
        try:
            return validate_database_name(value)
        except DtsError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class DtsTestConnectionSerializer(serializers.Serializer):
    target_host = serializers.CharField(max_length=255)
    target_port = serializers.IntegerField(min_value=1, max_value=65535, default=3306)
    target_user = serializers.CharField(max_length=64)
    password = serializers.CharField(allow_blank=True)
