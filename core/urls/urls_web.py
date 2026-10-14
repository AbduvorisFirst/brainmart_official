"""
core/urls_web.py
------------------
Маршруты страниц сайта для людей (в отличие от core/urls.py, где REST API).
Подключается в src/urls.py внутри i18n_patterns, поэтому у каждого адреса
автоматически появляется префикс языка: /ru/catalog/, /uz/catalog/, /en/catalog/
"""

from django.urls import path
from core.working_views import views_web

urlpatterns = [
    path("", views_web.catalog_view, name="catalog"),
    path("product/<int:product_id>/", views_web.product_detail_view, name="product-detail"),
    path("product/<int:product_id>/review/", views_web.submit_product_review, name="submit-product-review"),
    path("become-seller/", views_web.become_seller_view, name="become-seller"),
    # Корзина и оформление заказа:
    path('cart/checkout/', views_web.create_web_order, name='create-web-order'),
    path('cart/telegram-order/', views_web.create_telegram_order, name='create-telegram-order'),
    # Страница успешной оплаты и отправка чека перевода
    path('order/<int:order_id>/pay/', views_web.order_payment_gateway_view, name='order-pay'),
    path('order/<int:order_id>/success/', views_web.order_success_view, name='order-success'),
    path('order/<int:order_id>/upload-receipt/', views_web.order_upload_receipt_view, name='order-upload-receipt'),
]