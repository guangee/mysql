from django.db import models

from apps.core.crypto import decrypt_value, encrypt_value


class StorageBackend(models.Model):
    name = models.CharField("名称", max_length=128, unique=True)
    alias = models.CharField("MC 别名", max_length=64, default="s3")
    endpoint = models.CharField("Endpoint", max_length=255)
    access_key_enc = models.TextField("Access Key（加密）")
    secret_key_enc = models.TextField("Secret Key（加密）")
    bucket = models.CharField("Bucket", max_length=128, default="mysql")
    region = models.CharField("Region", max_length=64, default="us-east-1")
    use_ssl = models.BooleanField("使用 SSL", default=False)
    force_path_style = models.BooleanField("Path Style", default=True)
    enabled = models.BooleanField("启用", default=True)
    last_test_at = models.DateTimeField("上次测试时间", null=True, blank=True)
    last_test_ok = models.BooleanField("上次测试成功", default=False)
    last_test_message = models.TextField("测试消息", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "对象存储"
        verbose_name_plural = "对象存储"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def access_key(self) -> str:
        return decrypt_value(self.access_key_enc)

    @property
    def secret_key(self) -> str:
        return decrypt_value(self.secret_key_enc)

    def set_access_key(self, value: str) -> None:
        self.access_key_enc = encrypt_value(value)

    def set_secret_key(self, value: str) -> None:
        self.secret_key_enc = encrypt_value(value)
