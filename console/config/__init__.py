try:
    import MySQLdb  # noqa: F401
except ImportError:  # 一体镜像等环境可无 mysqlclient，用 PyMySQL 顶替
    import pymysql

    pymysql.install_as_MySQLdb()

from .celery import app as celery_app

__all__ = ("celery_app",)
