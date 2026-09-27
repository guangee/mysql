from django.contrib import admin

from apps.core.models import AuditLog, SystemCredential

admin.site.register(AuditLog)
admin.site.register(SystemCredential)
