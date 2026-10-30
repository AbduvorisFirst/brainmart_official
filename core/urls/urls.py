"""
core/urls.py — маршруты приложения core.
Подключается в src/urls.py как: path('api/', include('core.urls'))

Значит финальные адреса будут такими:
  /api/products/
  /api/products/<id>/
  /api/orders/
  /api/instagram/webhook/
"""

"""
core/urls.py — маршруты приложения core.
Подключается в src/urls.py как: path('api/', include('core.urls'))

Значит финальные адреса будут такими:
  /api/products/
  /api/products/<id>/
  /api/orders/
  /api/instagram/webhook/
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from core.working_views.views import (
    ProductViewSet, OrderViewSet, InstagramWebhookView, StoreCustomInstagramWebhookView
)
from core.working_views.telegram_bot import TelegramWebhookView, StoreCustomTelegramWebhookView

from core.working_views import views_web

router = DefaultRouter()
router.register("products", ProductViewSet, basename="product")
router.register("orders", OrderViewSet, basename="order")

urlpatterns = [
    path('ai/chat/', views_web.web_ai_chat_view, name='api-ai-chat'),
    path("", include(router.urls)),
    # Глобальный вебхук платформы
    path(
        "instagram/webhook/",
        InstagramWebhookView.as_view(),
        name="instagram-webhook",
    ),
    # Персональный вебхук верифицированного приложения Meta конкретного магазина
    path(
        "instagram/webhook/store/<slug:store_slug>/",
        StoreCustomInstagramWebhookView.as_view(),
        name="store-instagram-webhook-slug",
    ),
    path(
        "instagram/webhook/store/<int:store_slug>/",
        StoreCustomInstagramWebhookView.as_view(),
        name="store-instagram-webhook-id",
    ),
    # Главный системный бот платформы (@brainmart_bot)
    path(
        "telegram/webhook/",
        TelegramWebhookView.as_view(),
        name="telegram-webhook",
    ),
    # Персональный вебхук личного бота конкретного магазина (@sultan_wear_bot и т.д.)
    path(
        "telegram/webhook/store/<int:store_id>/",
        StoreCustomTelegramWebhookView.as_view(),
        name="store-telegram-webhook",
    ),
]