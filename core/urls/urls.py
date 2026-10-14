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

from core.working_views.views import ProductViewSet, OrderViewSet, InstagramWebhookView
from core.working_views.telegram_bot import TelegramWebhookView

router = DefaultRouter()
router.register("products", ProductViewSet, basename="product")
router.register("orders", OrderViewSet, basename="order")

urlpatterns = [
    path("", include(router.urls)),
    # Этот полный адрес (https://твой-домен/api/instagram/webhook/)
    # нужно указать в Meta Developer Console как "Callback URL"
    path(
        "instagram/webhook/",
        InstagramWebhookView.as_view(),
        name="instagram-webhook",
    ),
    # Этот адрес прописывается один раз через Telegram Bot API (setWebhook)
    path(
        "telegram/webhook/",
        TelegramWebhookView.as_view(),
        name="telegram-webhook",
    ),
]