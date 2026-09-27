import logging
import traceback

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("apps.request")


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    request = context.get("request")
    path = getattr(request, "path", "?") if request else "?"
    method = getattr(request, "method", "?") if request else "?"

    if response is None:
        logger.error(
            "API 500 %s %s\n%s",
            method,
            path,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
        return Response(
            {"detail": str(exc) if settings.DEBUG else "服务器内部错误，请查看 logs/error.log"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if response.status_code >= 500:
        logger.error(
            "API %s %s %s\n%s",
            response.status_code,
            method,
            path,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
    elif response.status_code >= 400:
        logger.warning("API %s %s -> %s: %s", method, path, response.status_code, exc)

    return response
