from django.contrib.auth.models import User
from django.db import models


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ("password_change", "修改密码"),
        ("backup_trigger", "触发备份"),
        ("storage_create", "创建存储"),
        ("storage_update", "更新存储"),
        ("storage_delete", "删除存储"),
        ("storage_test", "测试存储"),
        ("credential_sync", "同步凭证"),
        ("login", "登录"),
        ("database_query", "SQL 查询"),
        ("database_export", "数据导出"),
        ("backup_pitr_trigger", "时间点恢复"),
        ("backup_database_pitr_trigger", "单库时间点恢复"),
        ("backup_upload", "上传备份"),
        ("backup_full_restore_trigger", "全量备份恢复"),
    ]

    action = models.CharField("操作", max_length=64, choices=ACTION_CHOICES)
    target = models.CharField("目标", max_length=255, blank=True)
    detail = models.TextField("详情", blank=True)
    operator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="操作者",
    )
    ip_address = models.GenericIPAddressField("IP", null=True, blank=True)
    created_at = models.DateTimeField("时间", auto_now_add=True)

    class Meta:
        verbose_name = "审计日志"
        verbose_name_plural = "审计日志"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_action_display()} - {self.target}"


class SystemCredential(models.Model):
    ROLE_CHOICES = [
        ("root", "Root"),
        ("backup", "备份账号"),
        ("default_user", "默认业务用户"),
    ]

    role = models.CharField("角色", max_length=32, choices=ROLE_CHOICES, unique=True)
    username = models.CharField("用户名", max_length=128)
    password_enc = models.TextField("密码（加密）", blank=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "系统凭证"
        verbose_name_plural = "系统凭证"

    def __str__(self) -> str:
        return f"{self.get_role_display()} ({self.username})"

    @property
    def password(self) -> str:
        from apps.core.crypto import decrypt_value

        return decrypt_value(self.password_enc)

    def set_password(self, raw_password: str) -> None:
        from apps.core.crypto import encrypt_value

        self.password_enc = encrypt_value(raw_password)
