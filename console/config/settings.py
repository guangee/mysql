import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# Docker 卷挂载在容器根路径（/backups、/mysql_data 等）；本地开发则为仓库根目录
PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", str(BASE_DIR.parent)))

load_dotenv(PROJECT_ROOT / ".env")

SECRET_KEY = os.environ.get(
    "CONSOLE_SECRET_KEY",
    "django-insecure-change-me-in-production",
)
DEBUG = os.environ.get("CONSOLE_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("CONSOLE_ALLOWED_HOSTS", "*").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "apps.core",
    "apps.accounts",
    "apps.databases",
    "apps.backups",
    "apps.storages",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestLoggingMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "data" / "console.db",
        "OPTIONS": {
            "timeout": 30,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = os.environ.get("TZ", "Asia/Shanghai")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "EXCEPTION_HANDLER": "apps.api.exceptions.api_exception_handler",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# CORS
_cors_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "")
if _cors_origins:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_origins.split(",") if o.strip()]
else:
    CORS_ALLOW_ALL_ORIGINS = DEBUG

CORS_ALLOW_CREDENTIALS = True

# MySQL connection (managed instance)
MYSQL_HOST = os.environ.get("CONSOLE_MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.environ.get("CONSOLE_MYSQL_PORT", os.environ.get("MYSQL_PORT", "3306")))
MYSQL_ROOT_PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD", "")
MYSQL_USER = os.environ.get("MYSQL_USER", "testuser")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_BACKUP_USER = os.environ.get("MYSQL_BACKUP_USER", "")
MYSQL_BACKUP_PASSWORD = os.environ.get("MYSQL_BACKUP_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "testdb")

# Docker integration
DOCKER_MYSQL_CONTAINER = os.environ.get(
    "DOCKER_MYSQL_CONTAINER",
    os.environ.get("MYSQL_CONTAINER_NAME", "mysql8046"),
)
BACKUP_BASE_DIR = Path(os.environ.get("BACKUP_BASE_DIR", str(PROJECT_ROOT / "backups")))
SHARED_STORAGES_FILE = Path(
    os.environ.get("STORAGES_CONFIG_FILE", "/shared/storages.json")
)
SHARED_BACKUP_POLICY_FILE = Path(
    os.environ.get("BACKUP_POLICY_FILE", "/shared/backup_policy.json")
)
# 备份文件名 YYYYMMDD_HHMMSS 使用的时区（与 MySQL 容器内备份脚本一致，默认 UTC）
BACKUP_TIMEZONE = os.environ.get("BACKUP_TIMEZONE", "UTC")
RESTORE_TIMEZONE = "Asia/Shanghai"
DOCKER_MYSQL_IMAGE = os.environ.get("MYSQL_IMAGE", "")
MYSQL_DATA_DIR = Path(os.environ.get("MYSQL_DATA_DIR", str(PROJECT_ROOT / "mysql_data")))
SCRIPTS_DIR = Path(os.environ.get("SCRIPTS_DIR", str(PROJECT_ROOT / "scripts")))
MYSQL_CONFIG_DIR = Path(os.environ.get("MYSQL_CONFIG_DIR", str(PROJECT_ROOT / "mysql_config")))
BACKUP_ENV_FILE = BACKUP_BASE_DIR / "backup.env"
# 浏览器直连对象存储时使用的外网 Endpoint（可选，如 s3.example.com）
S3_PUBLIC_ENDPOINT = os.environ.get("S3_PUBLIC_ENDPOINT", "")

# Encryption for stored secrets
CONSOLE_FERNET_KEY = os.environ.get("CONSOLE_FERNET_KEY", "")

# Celery
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/0")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 3600

# Backup lock
BACKUP_LOCK_KEY = "mysql_console:backup_lock"
BACKUP_LOCK_TIMEOUT = 3600
CLEANUP_LOCK_KEY = "mysql_console:cleanup_lock"
CLEANUP_LOCK_TIMEOUT = 1800
PITR_LOCK_KEY = "mysql_console:pitr_lock"
PITR_LOCK_TIMEOUT = 7200
BACKUP_INDEX_SYNC_LOCK_KEY = "mysql_console:backup_index_sync_lock"
BACKUP_INDEX_SYNC_LOCK_TIMEOUT = 600

HOST_METRICS_REDIS_KEY = "mysql_console:host_metrics"
HOST_METRICS_LAST_COLLECT_KEY = "mysql_console:host_metrics:last_collect"
HOST_METRICS_COLLECT_INTERVAL = int(os.environ.get("HOST_METRICS_COLLECT_INTERVAL", "60"))
HOST_METRICS_RETENTION_SECONDS = int(os.environ.get("HOST_METRICS_RETENTION_SECONDS", "86400"))
HOST_METRICS_MAX_POINTS = int(os.environ.get("HOST_METRICS_MAX_POINTS", "1440"))
HOST_METRICS_DEFAULT_HOURS = float(os.environ.get("HOST_METRICS_DEFAULT_HOURS", "6"))

CELERY_BEAT_SCHEDULE = {
    "collect-host-metrics": {
        "task": "apps.core.tasks.collect_host_metrics_task",
        "schedule": timedelta(seconds=HOST_METRICS_COLLECT_INTERVAL),
    },
}

SYSTEM_DB_NAMES = frozenset(
    {"information_schema", "performance_schema", "mysql", "sys"}
)

# Logging（按天轮转，backupCount=1 约保留 1 天历史）
LOG_DIR = Path(os.environ.get("CONSOLE_LOG_DIR", str(BASE_DIR / "logs")))
LOG_RETENTION_DAYS = int(os.environ.get("CONSOLE_LOG_RETENTION_DAYS", "1"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

_LOG_FORMAT = {
    "format": "[{asctime}] {levelname} {name} {module}:{lineno} {message}",
    "style": "{",
    "datefmt": "%Y-%m-%d %H:%M:%S",
}
_LOG_ROTATE = {
    "class": "logging.handlers.TimedRotatingFileHandler",
    "when": "midnight",
    "interval": 1,
    "backupCount": 1,
    "encoding": "utf-8",
    "formatter": "standard",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"standard": _LOG_FORMAT},
    "handlers": {
        "django_file": {**_LOG_ROTATE, "filename": str(LOG_DIR / "django.log"), "level": "INFO"},
        "error_file": {**_LOG_ROTATE, "filename": str(LOG_DIR / "error.log"), "level": "ERROR"},
        "celery_file": {**_LOG_ROTATE, "filename": str(LOG_DIR / "celery.log"), "level": "INFO"},
    },
    "loggers": {
        "django": {"handlers": ["django_file"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["django_file", "error_file"], "level": "ERROR", "propagate": False},
        "apps": {"handlers": ["django_file", "error_file"], "level": "DEBUG", "propagate": False},
        "apps.request": {"handlers": ["django_file", "error_file"], "level": "INFO", "propagate": False},
        "celery": {"handlers": ["celery_file"], "level": "INFO", "propagate": False},
        "celery.task": {"handlers": ["celery_file"], "level": "INFO", "propagate": False},
        "gunicorn.error": {"handlers": ["django_file", "error_file"], "level": "INFO", "propagate": False},
        "gunicorn.access": {"handlers": ["django_file"], "level": "INFO", "propagate": False},
    },
}
