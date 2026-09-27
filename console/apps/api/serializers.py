from rest_framework import serializers

from apps.backups.models import BackupArtifact, BackupJob, BackupCleanupJob, BackupPitrJob, DatabasePitrJob, BackupFullRestoreJob
from apps.storages.models import StorageBackend


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class ChangePasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(min_length=8, write_only=True)
    confirm_password = serializers.CharField(min_length=8, write_only=True)
    admin_password = serializers.CharField(required=False, allow_blank=True, write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError("两次输入的密码不一致")
        return attrs


class CreateDatabaseSerializer(serializers.Serializer):
    name = serializers.RegexField(r"^[a-zA-Z0-9_]{1,64}$")
    charset = serializers.ChoiceField(choices=["utf8mb4", "utf8", "latin1"], default="utf8mb4")
    collation = serializers.CharField(required=False, allow_blank=True, max_length=64)
    grant_user = serializers.RegexField(r"^[a-zA-Z0-9_]{1,32}$", required=False, allow_blank=True)
    grant_host = serializers.CharField(required=False, allow_blank=True, max_length=255)
    privilege_level = serializers.ChoiceField(
        choices=["all", "read_write", "read_only"],
        default="all",
        required=False,
    )

    def validate(self, attrs):
        grant_user = (attrs.get("grant_user") or "").strip()
        grant_host = (attrs.get("grant_host") or "").strip()
        if grant_user and not grant_host:
            attrs["grant_host"] = "%"
        if grant_host and not grant_user:
            raise serializers.ValidationError("指定 Host 时必须填写授权用户")
        attrs["grant_user"] = grant_user or None
        attrs["grant_host"] = grant_host or None
        if attrs.get("collation") == "":
            attrs["collation"] = None
        return attrs


class CreateBusinessUserSerializer(serializers.Serializer):
    user = serializers.RegexField(r"^[a-zA-Z0-9_]{1,32}$")
    host = serializers.CharField(max_length=255, default="%")
    password = serializers.CharField(min_length=8, write_only=True)
    confirm_password = serializers.CharField(min_length=8, write_only=True)
    database = serializers.RegexField(r"^[a-zA-Z0-9_]{1,64}$", required=False, allow_blank=True)
    privilege_level = serializers.ChoiceField(
        choices=["all", "read_write", "read_only"],
        default="all",
        required=False,
    )

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError("两次输入的密码不一致")
        if attrs.get("host") == "":
            attrs["host"] = "%"
        if attrs.get("database") == "":
            attrs["database"] = None
        return attrs


class DatabaseQuerySerializer(serializers.Serializer):
    sql = serializers.CharField()
    limit = serializers.IntegerField(min_value=1, max_value=5000, default=500, required=False)


class DatabaseExportSerializer(serializers.Serializer):
    sql = serializers.CharField(required=False, allow_blank=True)
    table = serializers.RegexField(r"^[a-zA-Z0-9_]{1,64}$", required=False, allow_blank=True)
    limit = serializers.IntegerField(min_value=1, max_value=5000, default=5000, required=False)
    filename = serializers.CharField(required=False, allow_blank=True, max_length=128)

    def validate(self, attrs):
        sql = (attrs.get("sql") or "").strip()
        table = (attrs.get("table") or "").strip()
        if not sql and not table:
            raise serializers.ValidationError("请提供 SQL 或表名")
        if sql and table:
            raise serializers.ValidationError("SQL 与表名只能二选一")
        attrs["sql"] = sql or None
        attrs["table"] = table or None
        return attrs


class StorageBackendSerializer(serializers.ModelSerializer):
    access_key = serializers.CharField(required=False, allow_blank=True, write_only=True)
    secret_key = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = StorageBackend
        fields = [
            "id",
            "name",
            "alias",
            "endpoint",
            "bucket",
            "region",
            "use_ssl",
            "force_path_style",
            "enabled",
            "last_test_at",
            "last_test_ok",
            "last_test_message",
            "created_at",
            "updated_at",
            "access_key",
            "secret_key",
        ]
        read_only_fields = [
            "id",
            "last_test_at",
            "last_test_ok",
            "last_test_message",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        access_key = validated_data.pop("access_key", "")
        secret_key = validated_data.pop("secret_key", "")
        if not access_key or not secret_key:
            raise serializers.ValidationError("新建存储必须填写 Access Key 和 Secret Key")
        instance = StorageBackend(**validated_data)
        instance.set_access_key(access_key)
        instance.set_secret_key(secret_key)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        access_key = validated_data.pop("access_key", None)
        secret_key = validated_data.pop("secret_key", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if access_key:
            instance.set_access_key(access_key)
        if secret_key:
            instance.set_secret_key(secret_key)
        instance.save()
        return instance


class BackupJobSerializer(serializers.ModelSerializer):
    backup_type_display = serializers.CharField(source="get_backup_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    trigger_display = serializers.CharField(source="get_trigger_display", read_only=True)
    duration_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)

    class Meta:
        model = BackupJob
        fields = [
            "id",
            "backup_type",
            "backup_type_display",
            "status",
            "status_display",
            "trigger",
            "trigger_display",
            "storage_results",
            "output_log",
            "error_message",
            "started_at",
            "finished_at",
            "duration_seconds",
            "created_by_name",
            "created_at",
        ]


class BackupArtifactSerializer(serializers.ModelSerializer):
    storage_name = serializers.CharField(source="storage.name", read_only=True)

    class Meta:
        model = BackupArtifact
        fields = ["id", "storage", "storage_name", "s3_key", "size_bytes", "sha256", "status", "created_at"]


class BackupRetentionPolicySerializer(serializers.Serializer):
    full_backup_schedule = serializers.CharField(max_length=64, required=False)
    incremental_backup_schedule = serializers.CharField(max_length=64, required=False)
    full_backup_enabled = serializers.BooleanField(required=False)
    incremental_backup_enabled = serializers.BooleanField(required=False)
    full_retention_days = serializers.IntegerField(min_value=1, max_value=3650, required=False)
    incremental_retention_days = serializers.IntegerField(min_value=1, max_value=3650, required=False)
    cleanup_local_schedule = serializers.CharField(max_length=64, required=False)
    cleanup_s3_schedule = serializers.CharField(max_length=64, required=False)
    cleanup_local_enabled = serializers.BooleanField(required=False)
    cleanup_s3_enabled = serializers.BooleanField(required=False)
    updated_at = serializers.DateTimeField(read_only=True)


class BackupCleanupJobSerializer(serializers.ModelSerializer):
    scope_display = serializers.CharField(source="get_scope_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    trigger_display = serializers.CharField(source="get_trigger_display", read_only=True)
    duration_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)

    class Meta:
        model = BackupCleanupJob
        fields = [
            "id",
            "scope",
            "scope_display",
            "status",
            "status_display",
            "trigger",
            "trigger_display",
            "output_log",
            "error_message",
            "started_at",
            "finished_at",
            "duration_seconds",
            "created_by_name",
            "created_at",
        ]


class BackupCleanupTriggerSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=["local", "s3", "all"])


class BackupPitrJobSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    duration_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)

    class Meta:
        model = BackupPitrJob
        fields = [
            "id",
            "target_time",
            "full_backup_timestamp",
            "status",
            "status_display",
            "plan",
            "output_log",
            "error_message",
            "started_at",
            "finished_at",
            "duration_seconds",
            "created_by_name",
            "created_at",
        ]


class BackupPitrPreviewSerializer(serializers.Serializer):
    target_time = serializers.CharField(max_length=32)
    full_backup_timestamp = serializers.CharField(max_length=32, required=False, allow_blank=True)


class BackupPitrTriggerSerializer(serializers.Serializer):
    target_time = serializers.CharField(max_length=32)
    full_backup_timestamp = serializers.CharField(max_length=32, required=False, allow_blank=True)


class DatabasePitrJobSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    write_mode_display = serializers.CharField(source="get_write_mode_display", read_only=True)
    duration_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)

    class Meta:
        model = DatabasePitrJob
        fields = [
            "id",
            "source_database",
            "target_database",
            "write_mode",
            "write_mode_display",
            "target_time",
            "full_backup_timestamp",
            "status",
            "status_display",
            "plan",
            "output_log",
            "error_message",
            "started_at",
            "finished_at",
            "duration_seconds",
            "created_by_name",
            "created_at",
        ]


class DatabasePitrPreviewSerializer(serializers.Serializer):
    source_database = serializers.CharField(max_length=64)
    target_time = serializers.CharField(max_length=32)
    write_mode = serializers.ChoiceField(choices=["overwrite", "new_database"])
    target_database = serializers.CharField(max_length=64, required=False, allow_blank=True)
    full_backup_timestamp = serializers.CharField(max_length=32, required=False, allow_blank=True)


class DatabasePitrTriggerSerializer(DatabasePitrPreviewSerializer):
    pass


class BackupFullRestoreJobSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    duration_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)

    class Meta:
        model = BackupFullRestoreJob
        fields = [
            "id",
            "full_backup_timestamp",
            "filename",
            "status",
            "status_display",
            "output_log",
            "error_message",
            "started_at",
            "finished_at",
            "duration_seconds",
            "created_by_name",
            "created_at",
        ]


class BackupFullRestoreTriggerSerializer(serializers.Serializer):
    full_backup_timestamp = serializers.RegexField(r"^\d{8}_\d{6}$")


class MySQLTuningUpdateSerializer(serializers.Serializer):
    settings = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="参数名 -> 整数值",
    )
