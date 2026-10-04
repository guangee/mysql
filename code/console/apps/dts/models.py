from django.conf import settings
from django.db import models

from apps.core.crypto import decrypt_value, encrypt_value

STATUS_CHOICES = [
    ("stopped", "已停止"),
    ("full_sync", "全量同步"),
    ("incremental", "增量同步"),
    ("paused", "已暂停"),
    ("error", "异常"),
]


class DtsConnection(models.Model):
    name = models.CharField("名称", max_length=64, unique=True)
    host = models.CharField("地址", max_length=255)
    port = models.PositiveIntegerField("端口", default=3306)
    user = models.CharField("账号", max_length=64)
    password_enc = models.TextField("密码", blank=True, default="")
    remark = models.CharField("备注", max_length=255, blank=True, default="")
    mysql_version = models.CharField("版本", max_length=64, blank=True, default="")
    last_ok_at = models.DateTimeField("最近连通", null=True, blank=True)
    last_error = models.CharField("最近错误", max_length=255, blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "远程数据库连接"
        verbose_name_plural = "远程数据库连接"
        ordering = ["-created_at"]

    def set_password(self, raw: str) -> None:
        self.password_enc = encrypt_value(raw or "")

    def get_password(self) -> str:
        return decrypt_value(self.password_enc)

    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"


class DtsTask(models.Model):
    name = models.CharField("名称", max_length=64, unique=True)
    connection = models.ForeignKey(
        DtsConnection,
        verbose_name="远程连接",
        related_name="tasks",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    target_host = models.CharField("目标地址", max_length=255)
    target_port = models.PositiveIntegerField("目标端口", default=3306)
    target_user = models.CharField("目标账号", max_length=64)
    password_enc = models.TextField("目标密码", blank=True, default="")
    databases = models.JSONField("本地库", default=list, blank=True)
    target_database = models.CharField("目标库", max_length=64, blank=True, default="")
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default="stopped")
    binlog_file = models.CharField("Binlog 文件", max_length=128, blank=True, default="")
    binlog_pos = models.BigIntegerField("Binlog 位置", default=0)
    events_applied = models.BigIntegerField("已应用事件", default=0)
    last_event_at = models.DateTimeField("最近事件时间", null=True, blank=True)
    last_sync_at = models.DateTimeField("最近同步时间", null=True, blank=True)
    lag_seconds = models.IntegerField("延迟秒数", null=True, blank=True)
    table_total = models.PositiveIntegerField("待同步表数", default=0)
    table_done = models.PositiveIntegerField("已同步表数", default=0)
    rows_total = models.BigIntegerField("待同步行数", default=0)
    bytes_total = models.BigIntegerField("待同步数据量", default=0)
    bytes_done = models.BigIntegerField("已同步数据量", default=0)
    bytes_streamed = models.BigIntegerField("已传输字节", default=0)
    progress_percent = models.PositiveSmallIntegerField("进度", default=0)
    current_table = models.CharField("当前表", max_length=64, blank=True, default="")
    full_phase = models.CharField("全量阶段", max_length=16, blank=True, default="")
    structure_done = models.PositiveIntegerField("结构已同步表数", default=0)
    error_message = models.TextField("错误", blank=True, default="")
    log_text = models.TextField("日志", blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "DTS 同步任务"
        verbose_name_plural = "DTS 同步任务"
        ordering = ["-created_at"]

    def set_password(self, raw: str) -> None:
        self.password_enc = encrypt_value(raw or "")

    def get_password(self) -> str:
        return decrypt_value(self.password_enc)

    def apply_connection(self, connection: DtsConnection | None = None) -> None:
        conn = connection or self.connection
        if not conn:
            return
        self.connection = conn
        self.target_host = conn.host
        self.target_port = conn.port
        self.target_user = conn.user
        self.password_enc = conn.password_enc

    def target_endpoint(self) -> tuple[str, int, str, str]:
        if self.connection_id:
            conn = self.connection
            return conn.host, conn.port, conn.user, conn.get_password()
        return self.target_host, self.target_port, self.target_user, self.get_password()

    def source_database_name(self) -> str:
        names = self.allowed_databases()
        return names[0] if names else ""

    def allowed_databases(self) -> list[str]:
        names = []
        for name in self.databases or []:
            text = str(name).strip()
            if text and text not in settings.SYSTEM_DB_NAMES and text not in names:
                names.append(text)
        return names

    def append_log(self, message: str) -> None:
        from django.utils import timezone

        stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {message}"
        text = (self.log_text + "\n" + line).strip()
        self.log_text = text[-20000:]


class DtsTableSync(models.Model):
    PHASE_CHOICES = [
        ("pending", "等待"),
        ("structure", "结构已同步"),
        ("copying", "同步数据"),
        ("done", "已完成"),
        ("error", "失败"),
    ]

    task = models.ForeignKey(DtsTask, verbose_name="任务", related_name="table_syncs", on_delete=models.CASCADE)
    name = models.CharField("表名", max_length=64)
    rows_total = models.BigIntegerField("预估行数", default=0)
    rows_copied = models.BigIntegerField("已同步行数", default=0)
    bytes_total = models.BigIntegerField("数据量", default=0)
    bytes_copied = models.BigIntegerField("已传输", default=0)
    phase = models.CharField("阶段", max_length=16, choices=PHASE_CHOICES, default="pending")
    error_message = models.CharField("错误", max_length=255, blank=True, default="")
    sort_order = models.PositiveIntegerField("排序", default=0)

    class Meta:
        verbose_name = "DTS 表进度"
        verbose_name_plural = "DTS 表进度"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["task", "name"], name="dts_table_sync_task_name"),
        ]
