from django.db import models


class DatabaseInventory(models.Model):
    name = models.CharField("库名", max_length=64, unique=True, db_index=True)
    charset = models.CharField("字符集", max_length=64, blank=True, default="")
    collation = models.CharField("排序规则", max_length=64, blank=True, default="")
    table_count = models.PositiveIntegerField("表数", default=0)
    row_count_est = models.BigIntegerField("行数(约)", default=0)
    data_bytes = models.BigIntegerField("数据字节", default=0)
    index_bytes = models.BigIntegerField("索引字节", default=0)
    size_bytes = models.BigIntegerField("总字节", default=0)
    connection_count = models.PositiveIntegerField("连接数", default=0)
    memory_share_bytes = models.BigIntegerField("近似内存字节", default=0)
    compute_share_percent = models.FloatField("近似算力份额%", default=0)
    cpu_share_percent = models.FloatField("近似 CPU%", default=0)
    primary_engine = models.CharField("主引擎", max_length=64, blank=True, default="-")
    engines_json = models.JSONField("引擎明细", default=list, blank=True)
    users_json = models.JSONField("关联用户", default=list, blank=True)
    last_seen_at = models.DateTimeField("最近采样", null=True, blank=True)
    schema_synced_at = models.DateTimeField("结构同步时间", null=True, blank=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "数据库库存"
        verbose_name_plural = "数据库库存"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class TableInventory(models.Model):
    database = models.ForeignKey(
        DatabaseInventory,
        on_delete=models.CASCADE,
        related_name="tables",
        verbose_name="所属库",
    )
    name = models.CharField("表名", max_length=64, db_index=True)
    engine = models.CharField("引擎", max_length=64, blank=True, default="-")
    row_count_est = models.BigIntegerField("行数(约)", default=0)
    data_bytes = models.BigIntegerField("数据字节", default=0)
    index_bytes = models.BigIntegerField("索引字节", default=0)
    size_bytes = models.BigIntegerField("总字节", default=0)
    comment = models.TextField("注释", blank=True, default="")
    structure_hash = models.CharField("结构哈希", max_length=64, blank=True, default="")
    create_sql = models.TextField("建表语句", blank=True, default="")
    last_seen_at = models.DateTimeField("最近采样", null=True, blank=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "表库存"
        verbose_name_plural = "表库存"
        ordering = ["-size_bytes", "name"]
        unique_together = [("database", "name")]
        indexes = [
            models.Index(fields=["database", "-size_bytes"]),
        ]

    def __str__(self) -> str:
        return f"{self.database.name}.{self.name}"


class ColumnInventory(models.Model):
    table = models.ForeignKey(
        TableInventory,
        on_delete=models.CASCADE,
        related_name="columns",
        verbose_name="所属表",
    )
    name = models.CharField("字段名", max_length=64)
    column_type = models.CharField("类型", max_length=255)
    nullable = models.BooleanField("可空", default=True)
    column_key = models.CharField("键", max_length=16, blank=True, default="")
    column_default = models.TextField("默认值", null=True, blank=True)
    extra = models.CharField("Extra", max_length=255, blank=True, default="")
    comment = models.TextField("注释", blank=True, default="")
    ordinal_position = models.PositiveIntegerField("序号", default=0)

    class Meta:
        verbose_name = "列库存"
        verbose_name_plural = "列库存"
        ordering = ["ordinal_position"]
        unique_together = [("table", "name")]

    def __str__(self) -> str:
        return f"{self.table}.{self.name}"


class IndexInventory(models.Model):
    table = models.ForeignKey(
        TableInventory,
        on_delete=models.CASCADE,
        related_name="indexes",
        verbose_name="所属表",
    )
    name = models.CharField("索引名", max_length=64)
    unique = models.BooleanField("唯一", default=False)
    index_type = models.CharField("类型", max_length=32, blank=True, default="BTREE")
    columns_json = models.JSONField("列", default=list, blank=True)

    class Meta:
        verbose_name = "索引库存"
        verbose_name_plural = "索引库存"
        ordering = ["name"]
        unique_together = [("table", "name")]

    def __str__(self) -> str:
        return f"{self.table}.{self.name}"


class SchemaChangeEvent(models.Model):
    CHANGE_CREATED = "created"
    CHANGE_ALTERED = "altered"
    CHANGE_DROPPED = "dropped"
    CHANGE_CHOICES = [
        (CHANGE_CREATED, "新建"),
        (CHANGE_ALTERED, "变更"),
        (CHANGE_DROPPED, "删除"),
    ]

    database_name = models.CharField("库名", max_length=64, db_index=True)
    table_name = models.CharField("表名", max_length=64, blank=True, default="", db_index=True)
    change_type = models.CharField("类型", max_length=16, choices=CHANGE_CHOICES)
    structure_hash_before = models.CharField("变更前哈希", max_length=64, blank=True, default="")
    structure_hash_after = models.CharField("变更后哈希", max_length=64, blank=True, default="")
    create_sql_before = models.TextField("变更前 DDL", blank=True, default="")
    create_sql_after = models.TextField("变更后 DDL", blank=True, default="")
    unified_diff = models.TextField("Diff", blank=True, default="")
    detected_at = models.DateTimeField("发现时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "结构变更"
        verbose_name_plural = "结构变更"
        ordering = ["-detected_at"]

    def __str__(self) -> str:
        target = f"{self.database_name}.{self.table_name}" if self.table_name else self.database_name
        return f"{self.get_change_type_display()} {target}"
