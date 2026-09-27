from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("storages", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BackupRetentionPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("full_backup_schedule", models.CharField(default="0 2 * * 0", max_length=64)),
                ("incremental_backup_schedule", models.CharField(default="0 3 * * *", max_length=64)),
                ("full_backup_enabled", models.BooleanField(default=True)),
                ("incremental_backup_enabled", models.BooleanField(default=True)),
                ("full_retention_days", models.IntegerField(default=30)),
                ("incremental_retention_days", models.IntegerField(default=14)),
                ("cleanup_local_schedule", models.CharField(default="0 * * * *", max_length=64)),
                ("cleanup_s3_schedule", models.CharField(default="0 4 * * *", max_length=64)),
                ("cleanup_local_enabled", models.BooleanField(default=True)),
                ("cleanup_s3_enabled", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "备份策略", "verbose_name_plural": "备份策略"},
        ),
        migrations.CreateModel(
            name="BackupFileIndex",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payload", models.JSONField(blank=True, default=dict, verbose_name="目录")),
                ("file_count", models.IntegerField(default=0, verbose_name="文件数")),
                ("sync_status", models.CharField(default="idle", max_length=16, verbose_name="同步状态")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={"verbose_name": "备份文件索引", "verbose_name_plural": "备份文件索引"},
        ),
        migrations.CreateModel(
            name="BackupJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "等待中"), ("running", "进行中"), ("success", "成功"), ("failed", "失败")], default="pending", max_length=16, verbose_name="状态")),
                ("output_log", models.TextField(blank=True, default="", verbose_name="输出")),
                ("error_message", models.TextField(blank=True, default="", verbose_name="错误")),
                ("started_at", models.DateTimeField(blank=True, null=True, verbose_name="开始时间")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="结束时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("backup_type", models.CharField(choices=[("full", "全量"), ("incremental", "增量")], max_length=16, verbose_name="备份类型")),
                ("trigger", models.CharField(choices=[("manual", "手动"), ("schedule", "定时")], default="manual", max_length=16, verbose_name="触发方式")),
                ("storage_results", models.JSONField(blank=True, default=dict, verbose_name="存储结果")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="操作人")),
            ],
            options={"verbose_name": "备份任务", "verbose_name_plural": "备份任务", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BackupCleanupJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "等待中"), ("running", "进行中"), ("success", "成功"), ("failed", "失败")], default="pending", max_length=16, verbose_name="状态")),
                ("output_log", models.TextField(blank=True, default="", verbose_name="输出")),
                ("error_message", models.TextField(blank=True, default="", verbose_name="错误")),
                ("started_at", models.DateTimeField(blank=True, null=True, verbose_name="开始时间")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="结束时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("scope", models.CharField(choices=[("local", "本地"), ("s3", "对象存储"), ("all", "全部")], max_length=16, verbose_name="范围")),
                ("trigger", models.CharField(choices=[("manual", "手动"), ("schedule", "定时")], default="manual", max_length=16, verbose_name="触发方式")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="操作人")),
            ],
            options={"verbose_name": "清理任务", "verbose_name_plural": "清理任务", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BackupPitrJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "等待中"), ("running", "进行中"), ("success", "成功"), ("failed", "失败")], default="pending", max_length=16, verbose_name="状态")),
                ("output_log", models.TextField(blank=True, default="", verbose_name="输出")),
                ("error_message", models.TextField(blank=True, default="", verbose_name="错误")),
                ("started_at", models.DateTimeField(blank=True, null=True, verbose_name="开始时间")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="结束时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("target_time", models.CharField(max_length=32, verbose_name="目标时间")),
                ("full_backup_timestamp", models.CharField(blank=True, default="", max_length=32, verbose_name="全量备份时间戳")),
                ("plan", models.JSONField(blank=True, default=dict, verbose_name="恢复计划")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="操作人")),
            ],
            options={"verbose_name": "整实例恢复", "verbose_name_plural": "整实例恢复", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="DatabasePitrJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "等待中"), ("running", "进行中"), ("success", "成功"), ("failed", "失败")], default="pending", max_length=16, verbose_name="状态")),
                ("output_log", models.TextField(blank=True, default="", verbose_name="输出")),
                ("error_message", models.TextField(blank=True, default="", verbose_name="错误")),
                ("started_at", models.DateTimeField(blank=True, null=True, verbose_name="开始时间")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="结束时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("source_database", models.CharField(max_length=64, verbose_name="源库")),
                ("target_database", models.CharField(max_length=64, verbose_name="目标库")),
                ("write_mode", models.CharField(choices=[("overwrite", "覆盖"), ("new_database", "新库")], max_length=16, verbose_name="写入方式")),
                ("target_time", models.CharField(max_length=32, verbose_name="目标时间")),
                ("full_backup_timestamp", models.CharField(blank=True, default="", max_length=32, verbose_name="全量备份时间戳")),
                ("plan", models.JSONField(blank=True, default=dict, verbose_name="恢复计划")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="操作人")),
            ],
            options={"verbose_name": "单库恢复", "verbose_name_plural": "单库恢复", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BackupFullRestoreJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "等待中"), ("running", "进行中"), ("success", "成功"), ("failed", "失败")], default="pending", max_length=16, verbose_name="状态")),
                ("output_log", models.TextField(blank=True, default="", verbose_name="输出")),
                ("error_message", models.TextField(blank=True, default="", verbose_name="错误")),
                ("started_at", models.DateTimeField(blank=True, null=True, verbose_name="开始时间")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="结束时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("full_backup_timestamp", models.CharField(max_length=32, verbose_name="全量备份时间戳")),
                ("filename", models.CharField(max_length=128, verbose_name="文件名")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="操作人")),
            ],
            options={"verbose_name": "全量恢复", "verbose_name_plural": "全量恢复", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BackupArtifact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("s3_key", models.CharField(max_length=512, verbose_name="对象 Key")),
                ("size_bytes", models.BigIntegerField(default=0, verbose_name="大小")),
                ("sha256", models.CharField(blank=True, default="", max_length=64, verbose_name="SHA256")),
                ("status", models.CharField(choices=[("pending", "等待中"), ("running", "进行中"), ("success", "成功"), ("failed", "失败")], default="success", max_length=16, verbose_name="状态")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="artifacts", to="backups.backupjob", verbose_name="备份任务")),
                ("storage", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="artifacts", to="storages.storagebackend", verbose_name="存储")),
            ],
            options={"verbose_name": "备份产物", "verbose_name_plural": "备份产物", "ordering": ["-created_at"]},
        ),
    ]
