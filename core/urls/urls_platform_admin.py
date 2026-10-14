from django.urls import path
from core.working_views import views_platform_admin

urlpatterns = [
    path('', views_platform_admin.platform_admin_dashboard_view, name='platform-admin-dashboard'),
]
