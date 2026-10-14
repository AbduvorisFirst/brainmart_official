from django.urls import path
from core.working_views import views_pos

app_name = 'pos'

urlpatterns = [
    path('', views_pos.pos_home, name='pos_home'),
    path('login/', views_pos.pos_login, name='pos_login'),
    path('logout/', views_pos.pos_logout, name='pos_logout'),
    path('terminal/', views_pos.pos_terminal, name='pos_terminal'),
    
    # API endpoints for POS JS
    path('api/search/', views_pos.api_search_products, name='api_search_products'),
    path('api/checkout/', views_pos.api_checkout, name='api_checkout'),
    path('api/shift/open/', views_pos.api_shift_open, name='api_shift_open'),
    path('api/shift/close/', views_pos.api_shift_close, name='api_shift_close'),
]
