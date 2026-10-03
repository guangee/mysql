from django.urls import path

from apps.dts.views import (
    DtsDatabaseListView,
    DtsTaskActionView,
    DtsTaskDetailView,
    DtsTaskListCreateView,
    DtsTestConnectionView,
)

urlpatterns = [
    path("databases/", DtsDatabaseListView.as_view(), name="api-dts-databases"),
    path("test-connection/", DtsTestConnectionView.as_view(), name="api-dts-test"),
    path("tasks/", DtsTaskListCreateView.as_view(), name="api-dts-tasks"),
    path("tasks/<int:task_id>/", DtsTaskDetailView.as_view(), name="api-dts-task"),
    path("tasks/<int:task_id>/<str:action>/", DtsTaskActionView.as_view(), name="api-dts-task-action"),
]
