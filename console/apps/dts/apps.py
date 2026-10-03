from django.apps import AppConfig


class DtsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dts"
    verbose_name = "数据同步"
