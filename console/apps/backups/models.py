from django.conf import settings
from django.db import models


STATUS_CHOICES = [
    ("pending", "等待中"),
    ("running", "进行中"),
    ("success", "成功"),
    ("failed", "失败"),
]
TRIGGER_CHOICES = [
    ("manual", "手动"),
    ("schedule", "定时"),
]


class _JobBase(models.Model):
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default="pending")
    output_log = models.TextField("输出", blank=True, default="")
    error_message = models.TextField("错误", blank=True, default="")
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="操作人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    @property
    def duration_seconds(self):
        if self.started_at and self.finished_at:
            return max(0, int((self.finished_at - self.started_at).total_seconds()))
        return None


class BackupJob(_JobBase):
    backup_type = models.CharField(
        "备份类型",
        max_length=16,
        choices=[("full", "全量"), ("incremental", "增量")],
    )
    trigger = models.CharField("触发方式", max_length=16, choices=TRIGGER_CHOICES, default="manual")
    storage_results = models.JSONField("存储结果", default=dict, blank=True)

    class Meta(_JobBase.Meta):
        verbose_name = "备份任务"
        verbose_name_plural = "备份任务"


class BackupArtifact(models.Model):
    job = models.ForeignKey(
        BackupJob,
        verbose_name="备份任务",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="artifacts",
    )
    storage = models.ForeignKey(
        "storages.StorageBackend",
        verbose_name="存储",
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    s3_key = models.CharField("对象 Key", max_length=512)
    size_bytes = models.BigIntegerField("大小", default=0)
    sha256 = models.CharField("SHA256", max_length=64, blank=True, default="")
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default="success")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "备份产物"
        verbose_name_plural = "备份产物"
        ordering = ["-created_at"]


class BackupCleanupJob(_JobBase):
    scope = models.CharField(
        "范围",
        max_length=16,
        choices=[("local", "本地"), ("s3", "对象存储"), ("all", "全部")],
    )
    trigger = models.CharField("触发方式", max_length=16, choices=TRIGGER_CHOICES, default="manual")

    class Meta(_JobBase.Meta):
        verbose_name = "清理任务"
        verbose_name_plural = "清理任务"


class BackupPitrJob(_JobBase):
    target_time = models.CharField("目标时间", max_length=32)
    full_backup_timestamp = models.CharField("全量备份时间戳", max_length=32, blank=True, default="")
    plan = models.JSONField("恢复计划", default=dict, blank=True)

    class Meta(_JobBase.Meta):
        verbose_name = "整实例恢复"
        verbose_name_plural = "整实例恢复"


class DatabasePitrJob(_JobBase):
    source_database = models.CharField("源库", max_length=64)
    target_database = models.CharField("目标库", max_length=64)
    write_mode = models.CharField(
        "写入方式",
        max_length=16,
        choices=[("overwrite", "覆盖"), ("new_database", "新库")],
    )
    target_time = models.CharField("目标时间", max_length=32)
    full_backup_timestamp = models.CharField("全量备份时间戳", max_length=32, blank=True, default="")
    plan = models.JSONField("恢复计划", default=dict, blank=True)

    class Meta(_JobBase.Meta):
        verbose_name = "单库恢复"
        verbose_name_plural = "单库恢复"


class BackupFullRestoreJob(_JobBase):
    full_backup_timestamp = models.CharField("全量备份时间戳", max_length=32)
    filename = models.CharField("文件名", max_length=128)

    class Meta(_JobBase.Meta):
        verbose_name = "全量恢复"
        verbose_name_plural = "全量恢复"


class BackupFileIndex(models.Model):
    payload = models.JSONField("目录", default=dict, blank=True)
    file_count = models.IntegerField("文件数", default=0)
    sync_status = models.CharField("同步状态", max_length=16, default="idle")
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "备份文件索引"
        verbose_name_plural = "备份文件索引"


class BackupRetentionPolicy(models.Model):
    full_backup_schedule = models.CharField(max_length=64, default="0 2 * * 0")
    incremental_backup_schedule = models.CharField(max_length=64, default="0 3 * * *")
    full_backup_enabled = models.BooleanField(default=True)
    incremental_backup_enabled = models.BooleanField(default=True)
    full_retention_days = models.IntegerField(default=30)
    incremental_retention_days = models.IntegerField(default=14)
    cleanup_local_schedule = models.CharField(max_length=64, default="0 * * * *")
    cleanup_s3_schedule = models.CharField(max_length=64, default="0 4 * * *")
    cleanup_local_enabled = models.BooleanField(default=True)
    cleanup_s3_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "备份策略"
        verbose_name_plural = "备份策略"
