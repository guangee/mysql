from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="DatabaseInventory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(db_index=True, max_length=64, unique=True, verbose_name="库名")),
                ("charset", models.CharField(blank=True, default="", max_length=64, verbose_name="字符集")),
                ("collation", models.CharField(blank=True, default="", max_length=64, verbose_name="排序规则")),
                ("table_count", models.PositiveIntegerField(default=0, verbose_name="表数")),
                ("row_count_est", models.BigIntegerField(default=0, verbose_name="行数(约)")),
                ("data_bytes", models.BigIntegerField(default=0, verbose_name="数据字节")),
                ("index_bytes", models.BigIntegerField(default=0, verbose_name="索引字节")),
                ("size_bytes", models.BigIntegerField(default=0, verbose_name="总字节")),
                ("connection_count", models.PositiveIntegerField(default=0, verbose_name="连接数")),
                ("memory_share_bytes", models.BigIntegerField(default=0, verbose_name="近似内存字节")),
                ("compute_share_percent", models.FloatField(default=0, verbose_name="近似算力份额%")),
                ("cpu_share_percent", models.FloatField(default=0, verbose_name="近似 CPU%")),
                ("primary_engine", models.CharField(blank=True, default="-", max_length=64, verbose_name="主引擎")),
                ("engines_json", models.JSONField(blank=True, default=list, verbose_name="引擎明细")),
                ("users_json", models.JSONField(blank=True, default=list, verbose_name="关联用户")),
                ("last_seen_at", models.DateTimeField(blank=True, null=True, verbose_name="最近采样")),
                ("schema_synced_at", models.DateTimeField(blank=True, null=True, verbose_name="结构同步时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "数据库库存",
                "verbose_name_plural": "数据库库存",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="TableInventory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(db_index=True, max_length=64, verbose_name="表名")),
                ("engine", models.CharField(blank=True, default="-", max_length=64, verbose_name="引擎")),
                ("row_count_est", models.BigIntegerField(default=0, verbose_name="行数(约)")),
                ("data_bytes", models.BigIntegerField(default=0, verbose_name="数据字节")),
                ("index_bytes", models.BigIntegerField(default=0, verbose_name="索引字节")),
                ("size_bytes", models.BigIntegerField(default=0, verbose_name="总字节")),
                ("comment", models.TextField(blank=True, default="", verbose_name="注释")),
                ("structure_hash", models.CharField(blank=True, default="", max_length=64, verbose_name="结构哈希")),
                ("create_sql", models.TextField(blank=True, default="", verbose_name="建表语句")),
                ("last_seen_at", models.DateTimeField(blank=True, null=True, verbose_name="最近采样")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "database",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tables",
                        to="databases.databaseinventory",
                        verbose_name="所属库",
                    ),
                ),
            ],
            options={
                "verbose_name": "表库存",
                "verbose_name_plural": "表库存",
                "ordering": ["-size_bytes", "name"],
            },
        ),
        migrations.CreateModel(
            name="SchemaChangeEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("database_name", models.CharField(db_index=True, max_length=64, verbose_name="库名")),
                ("table_name", models.CharField(blank=True, db_index=True, default="", max_length=64, verbose_name="表名")),
                (
                    "change_type",
                    models.CharField(
                        choices=[("created", "新建"), ("altered", "变更"), ("dropped", "删除")],
                        max_length=16,
                        verbose_name="类型",
                    ),
                ),
                ("structure_hash_before", models.CharField(blank=True, default="", max_length=64, verbose_name="变更前哈希")),
                ("structure_hash_after", models.CharField(blank=True, default="", max_length=64, verbose_name="变更后哈希")),
                ("create_sql_before", models.TextField(blank=True, default="", verbose_name="变更前 DDL")),
                ("create_sql_after", models.TextField(blank=True, default="", verbose_name="变更后 DDL")),
                ("unified_diff", models.TextField(blank=True, default="", verbose_name="Diff")),
                ("detected_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="发现时间")),
            ],
            options={
                "verbose_name": "结构变更",
                "verbose_name_plural": "结构变更",
                "ordering": ["-detected_at"],
            },
        ),
        migrations.CreateModel(
            name="IndexInventory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64, verbose_name="索引名")),
                ("unique", models.BooleanField(default=False, verbose_name="唯一")),
                ("index_type", models.CharField(blank=True, default="BTREE", max_length=32, verbose_name="类型")),
                ("columns_json", models.JSONField(blank=True, default=list, verbose_name="列")),
                (
                    "table",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="indexes",
                        to="databases.tableinventory",
                        verbose_name="所属表",
                    ),
                ),
            ],
            options={
                "verbose_name": "索引库存",
                "verbose_name_plural": "索引库存",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="ColumnInventory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64, verbose_name="字段名")),
                ("column_type", models.CharField(max_length=255, verbose_name="类型")),
                ("nullable", models.BooleanField(default=True, verbose_name="可空")),
                ("column_key", models.CharField(blank=True, default="", max_length=16, verbose_name="键")),
                ("column_default", models.TextField(blank=True, null=True, verbose_name="默认值")),
                ("extra", models.CharField(blank=True, default="", max_length=255, verbose_name="Extra")),
                ("comment", models.TextField(blank=True, default="", verbose_name="注释")),
                ("ordinal_position", models.PositiveIntegerField(default=0, verbose_name="序号")),
                (
                    "table",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="columns",
                        to="databases.tableinventory",
                        verbose_name="所属表",
                    ),
                ),
            ],
            options={
                "verbose_name": "列库存",
                "verbose_name_plural": "列库存",
                "ordering": ["ordinal_position"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="tableinventory",
            unique_together={("database", "name")},
        ),
        migrations.AddIndex(
            model_name="tableinventory",
            index=models.Index(fields=["database", "-size_bytes"], name="databases_t_databas_7f1a2b_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="indexinventory",
            unique_together={("table", "name")},
        ),
        migrations.AlterUniqueTogether(
            name="columninventory",
            unique_together={("table", "name")},
        ),
    ]
