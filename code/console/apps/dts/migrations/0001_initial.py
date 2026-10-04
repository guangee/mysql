from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="DtsTask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64, unique=True, verbose_name="名称")),
                ("target_host", models.CharField(max_length=255, verbose_name="目标地址")),
                ("target_port", models.PositiveIntegerField(default=3306, verbose_name="目标端口")),
                ("target_user", models.CharField(max_length=64, verbose_name="目标账号")),
                ("password_enc", models.TextField(blank=True, default="", verbose_name="目标密码")),
                ("databases", models.JSONField(blank=True, default=list, verbose_name="同步库")),
                ("status", models.CharField(choices=[("stopped", "已停止"), ("full_sync", "全量同步"), ("incremental", "增量同步"), ("paused", "已暂停"), ("error", "异常")], default="stopped", max_length=16, verbose_name="状态")),
                ("binlog_file", models.CharField(blank=True, default="", max_length=128, verbose_name="Binlog 文件")),
                ("binlog_pos", models.BigIntegerField(default=0, verbose_name="Binlog 位置")),
                ("events_applied", models.BigIntegerField(default=0, verbose_name="已应用事件")),
                ("last_event_at", models.DateTimeField(blank=True, null=True, verbose_name="最近事件时间")),
                ("last_sync_at", models.DateTimeField(blank=True, null=True, verbose_name="最近同步时间")),
                ("lag_seconds", models.IntegerField(blank=True, null=True, verbose_name="延迟秒数")),
                ("error_message", models.TextField(blank=True, default="", verbose_name="错误")),
                ("log_text", models.TextField(blank=True, default="", verbose_name="日志")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "DTS 同步任务",
                "verbose_name_plural": "DTS 同步任务",
                "ordering": ["-created_at"],
            },
        ),
    ]
