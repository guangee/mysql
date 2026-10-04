from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dts", "0002_target_database"),
    ]

    operations = [
        migrations.AddField(
            model_name="dtstask",
            name="table_total",
            field=models.PositiveIntegerField(default=0, verbose_name="待同步表数"),
        ),
        migrations.AddField(
            model_name="dtstask",
            name="table_done",
            field=models.PositiveIntegerField(default=0, verbose_name="已同步表数"),
        ),
        migrations.AddField(
            model_name="dtstask",
            name="rows_total",
            field=models.BigIntegerField(default=0, verbose_name="待同步行数"),
        ),
        migrations.AddField(
            model_name="dtstask",
            name="bytes_total",
            field=models.BigIntegerField(default=0, verbose_name="待同步数据量"),
        ),
        migrations.AddField(
            model_name="dtstask",
            name="bytes_done",
            field=models.BigIntegerField(default=0, verbose_name="已同步数据量"),
        ),
        migrations.AddField(
            model_name="dtstask",
            name="bytes_streamed",
            field=models.BigIntegerField(default=0, verbose_name="已传输字节"),
        ),
        migrations.AddField(
            model_name="dtstask",
            name="progress_percent",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="进度"),
        ),
        migrations.AddField(
            model_name="dtstask",
            name="current_table",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="当前表"),
        ),
    ]
