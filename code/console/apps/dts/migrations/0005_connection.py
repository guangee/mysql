from django.db import migrations, models
import django.db.models.deletion


def copy_tasks_to_connections(apps, schema_editor):
    DtsConnection = apps.get_model("dts", "DtsConnection")
    DtsTask = apps.get_model("dts", "DtsTask")
    seen = {}
    for task in DtsTask.objects.all().order_by("id"):
        key = (task.target_host or "", int(task.target_port or 3306), task.target_user or "")
        conn = seen.get(key)
        if conn is None:
            base = f"{key[0]}:{key[1]}" if key[0] else f"connection-{task.id}"
            name = base
            suffix = 1
            while DtsConnection.objects.filter(name=name).exists():
                suffix += 1
                name = f"{base}#{suffix}"
            conn = DtsConnection.objects.create(
                name=name[:64],
                host=task.target_host or "",
                port=task.target_port or 3306,
                user=task.target_user or "",
                password_enc=task.password_enc or "",
                remark="由同步任务导入",
            )
            seen[key] = conn
        task.connection_id = conn.id
        task.save(update_fields=["connection_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("dts", "0004_table_sync_detail"),
    ]

    operations = [
        migrations.CreateModel(
            name="DtsConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64, unique=True, verbose_name="名称")),
                ("host", models.CharField(max_length=255, verbose_name="地址")),
                ("port", models.PositiveIntegerField(default=3306, verbose_name="端口")),
                ("user", models.CharField(max_length=64, verbose_name="账号")),
                ("password_enc", models.TextField(blank=True, default="", verbose_name="密码")),
                ("remark", models.CharField(blank=True, default="", max_length=255, verbose_name="备注")),
                ("mysql_version", models.CharField(blank=True, default="", max_length=64, verbose_name="版本")),
                ("last_ok_at", models.DateTimeField(blank=True, null=True, verbose_name="最近连通")),
                ("last_error", models.CharField(blank=True, default="", max_length=255, verbose_name="最近错误")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "远程数据库连接",
                "verbose_name_plural": "远程数据库连接",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="dtstask",
            name="connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tasks",
                to="dts.dtsconnection",
                verbose_name="远程连接",
            ),
        ),
        migrations.RunPython(copy_tasks_to_connections, migrations.RunPython.noop),
    ]
