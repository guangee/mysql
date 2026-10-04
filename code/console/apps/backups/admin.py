from django.contrib import admin

from apps.backups.models import (
    BackupCleanupJob,
    BackupFileIndex,
    BackupFullRestoreJob,
    BackupJob,
    BackupPitrJob,
    BackupRetentionPolicy,
    DatabasePitrJob,
)

admin.site.register(BackupJob)
admin.site.register(BackupCleanupJob)
admin.site.register(BackupPitrJob)
admin.site.register(DatabasePitrJob)
admin.site.register(BackupFullRestoreJob)
admin.site.register(BackupFileIndex)
admin.site.register(BackupRetentionPolicy)
