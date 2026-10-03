from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("dts", "0003_sync_progress"),
    ]

    operations = [
        migrations.AddField(
            model_name="dtstask",
            name="full_phase",
            field=models.CharField(blank=True, default="", max_length=16, verbose_name="全量阶段"),
        ),
        migrations.AddField(
            model_name="dtstask",
            name="structure_done",
            field=models.PositiveIntegerField(default=0, verbose_name="结构已同步表数"),
        ),
        migrations.CreateModel(
            name="DtsTableSync",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64, verbose_name="表名")),
                ("rows_total", models.BigIntegerField(default=0, verbose_name="预估行数")),
                ("rows_copied", models.BigIntegerField(default=0, verbose_name="已同步行数")),
                ("bytes_total", models.BigIntegerField(default=0, verbose_name="数据量")),
                ("bytes_copied", models.BigIntegerField(default=0, verbose_name="已传输")),
                ("phase", models.CharField(choices=[("pending", "等待"), ("structure", "结构已同步"), ("copying", "同步数据"), ("done", "已完成"), ("error", "失败")], default="pending", max_length=16, verbose_name="阶段")),
                ("error_message", models.CharField(blank=True, default="", max_length=255, verbose_name="错误")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="排序")),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="table_syncs", to="dts.dtstask", verbose_name="任务")),
            ],
            options={
                "verbose_name": "DTS 表进度",
                "verbose_name_plural": "DTS 表进度",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="dtstablesync",
            constraint=models.UniqueConstraint(fields=("task", "name"), name="dts_table_sync_task_name"),
        ),
    ]
