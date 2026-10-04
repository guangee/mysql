import os

bind = "127.0.0.1:8000"
workers = int(os.environ.get("CONSOLE_GUNICORN_WORKERS", "2"))
timeout = int(os.environ.get("CONSOLE_GUNICORN_TIMEOUT", "120"))
accesslog = "/app/logs/gunicorn-access.log"
errorlog = "/app/logs/gunicorn-error.log"
capture_output = True
enable_stdio_inheritance = True
