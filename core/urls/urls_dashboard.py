"""
core/urls_dashboard.py
------------------------
Маршруты панели продавца. Подключается в src/urls.py под префиксом
/dashboard/ — БЕЗ i18n_patterns (панель делаем только на русском для
простоты — ты сам общаешься с продавцами на русском, добавить
переключатель языка сюда всегда можно позже так же, как на витрине).
"""

from django.urls import path
from django.contrib.auth import views as auth_views

from core.working_views import views_dashboard
from core.working_views.views_branches import seller_branches_view


urlpatterns = [

    path("branches/", seller_branches_view, name="seller-branches"),

    path("login/", views_dashboard.seller_login_view, name="seller-login"),
    path("login/telegram-init-auth/", views_dashboard.telegram_init_auth_api, name="api-telegram-init-auth"),
    path("login/verify-otp/", views_dashboard.seller_verify_otp_view, name="seller-verify-otp"),
    path("login/resend-otp/", views_dashboard.seller_resend_otp_view, name="seller-resend-otp"),
    path("login/check-tg-status/", views_dashboard.seller_check_tg_status_api, name="seller-check-tg-status"),
    path("verify-otp/", views_dashboard.seller_verify_otp_view),
    path("resend-otp/", views_dashboard.seller_resend_otp_view),
    path("forgot-password/", views_dashboard.seller_forgot_password_view, name="seller-forgot-password"),
    path("forgot-password/verify/", views_dashboard.seller_reset_password_verify_view, name="seller-reset-password-verify"),
    path("forgot-password/resend-otp/", views_dashboard.seller_reset_password_resend_otp_view, name="seller-reset-password-resend-otp"),
    path("logout/", views_dashboard.seller_logout_view, name="seller-logout"),
    path("register/", views_dashboard.seller_register_view, name="seller-register"),

    path("", views_dashboard.dashboard_home, name="seller-dashboard"),
    path("products/", views_dashboard.product_list_view, name="seller-products"),
    path("products/add/", views_dashboard.product_add_view, name="seller-product-add"),
    path(
        "products/<int:product_id>/toggle/",
        views_dashboard.product_toggle_active_view,
        name="seller-product-toggle",
    ),
    path("orders/", views_dashboard.order_list_view, name="seller-orders"),
    path(
        "orders/<int:order_id>/handed/",
        views_dashboard.order_mark_handed_view,
        name="seller-order-handed",
    ),
    path(
        "orders/<int:order_id>/delivered/",
        views_dashboard.order_mark_delivered_view,
        name="seller-order-delivered",
    ),
    path(
        "orders/<int:order_id>/confirm-payment/",
        views_dashboard.order_confirm_payment_view,
        name="seller-order-confirm-payment",
    ),
    path('finance/', views_dashboard.seller_finance_view, name='seller-finance'),
    path('products/<int:product_id>/delete/', views_dashboard.product_delete_view, name='seller-product-delete'),
    path('products/<int:product_id>/edit/', views_dashboard.product_edit_view, name='seller-product-edit'),
    path('products/<int:product_id>/inventory/', views_dashboard.product_inventory_edit_view, name='seller-product-inventory'),
    path('settings/', views_dashboard.store_settings_view, name='seller-settings'),
    path('reviews/', views_dashboard.seller_reviews_view, name='seller-reviews'),
    path('customers/', views_dashboard.customers_view, name='seller-customers'),
    path('waitlist/', views_dashboard.seller_waitlist_view, name='seller-waitlist'),
    path('broadcasts/', views_dashboard.seller_broadcasts_view, name='seller-broadcasts'),
    path('api/ai-generate-broadcast/', views_dashboard.ai_generate_broadcast_view, name='api-ai-generate-broadcast'),
    path('api/ai-generate-description/', views_dashboard.ai_generate_description_view, name='api-ai-generate-description'),
]
