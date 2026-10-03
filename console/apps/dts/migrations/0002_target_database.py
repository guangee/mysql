from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="dtstask",
            name="target_database",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="目标库"),
        ),
        migrations.AlterField(
            model_name="dtstask",
            name="databases",
            field=models.JSONField(blank=True, default=list, verbose_name="本地库"),
        ),
    ]
