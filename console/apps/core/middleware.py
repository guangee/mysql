import logging
import traceback

from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("apps.request")


class RequestLoggingMiddleware(MiddlewareMixin):
    """记录未捕获异常，便于开发排查 500 错误。"""

    def process_exception(self, request, exception):
        logger.error(
            "未捕获异常 %s %s\n%s",
            request.method,
            request.path,
            "".join(traceback.format_exception(type(exception), exception, exception.__traceback__)),
        )
        return None
