from django.urls import path

from apps.dts.views import (
    DtsConnectionDatabaseView,
    DtsConnectionDetailView,
    DtsConnectionListCreateView,
    DtsConnectionTestView,
    DtsDatabaseListView,
    DtsTaskActionView,
    DtsTaskDetailView,
    DtsTaskListCreateView,
    DtsTaskSqlEventsView,
    DtsTestConnectionView,
)

urlpatterns = [
    path("databases/", DtsDatabaseListView.as_view(), name="api-dts-databases"),
    path("test-connection/", DtsTestConnectionView.as_view(), name="api-dts-test"),
    path("connections/", DtsConnectionListCreateView.as_view(), name="api-dts-connections"),
    path("connections/<int:connection_id>/", DtsConnectionDetailView.as_view(), name="api-dts-connection"),
    path("connections/<int:connection_id>/test/", DtsConnectionTestView.as_view(), name="api-dts-connection-test"),
    path("connections/<int:connection_id>/databases/", DtsConnectionDatabaseView.as_view(), name="api-dts-connection-databases"),
    path("tasks/", DtsTaskListCreateView.as_view(), name="api-dts-tasks"),
    path("tasks/<int:task_id>/sql-events/", DtsTaskSqlEventsView.as_view(), name="api-dts-task-sql-events"),
    path("tasks/<int:task_id>/", DtsTaskDetailView.as_view(), name="api-dts-task"),
    path("tasks/<int:task_id>/<str:action>/", DtsTaskActionView.as_view(), name="api-dts-task-action"),
]
