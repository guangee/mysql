from django.contrib import admin

from apps.storages.models import StorageBackend

admin.site.register(StorageBackend)
