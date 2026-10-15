"""
views.py
--------
Два типа views в этом файле:

1. DRF ViewSets — обычный REST API для фронтенда маркетплейса
   (каталог товаров, оформление заказа и т.д.)

2. InstagramWebhookView — специальный endpoint, на который Meta
   присылает новые сообщения из Instagram Direct. Именно отсюда
   мы вызываем services.handle_incoming_instagram_message()
"""

import logging

from django.conf import settings
from django.http import HttpResponse
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Product, Order, Store
from core.working_views.serializers import ProductListSerializer, ProductDetailSerializer, OrderSerializer
from core.working_views import services

logger = logging.getLogger(__name__)


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Публичный каталог товаров для сайта-маркетплейса.
    Только чтение — товары добавляются через CRM продавца, не через этот API.
    """
    queryset = Product.objects.filter(is_active=True, store__is_active=True)
    permission_classes = [permissions.AllowAny]

    def get_serializer_class(self):
        # Список товаров отдаём "облегчённым", страницу одного товара — полной
        if self.action == "retrieve":
            return ProductDetailSerializer
        return ProductListSerializer


class OrderViewSet(viewsets.ModelViewSet):
    """
    Оформление и просмотр заказов.
    Создание доступно всем (клиент маркетплейса ещё не залогинен как Store),
    а вот менять/удалять заказы могут только авторизованные продавцы —
    и только свои собственные заказы (см. get_queryset).
    """
    serializer_class = OrderSerializer

    def get_permissions(self):
        if self.action == "create":
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        # Продавец должен видеть только заказы своего магазина
        if self.request.user.is_authenticated and hasattr(self.request.user, "store"):
            return Order.objects.filter(store=self.request.user.store)
        return Order.objects.none()

    @action(detail=True, methods=["post"])
    def mark_handed_to_courier(self, request, pk=None):
        """
        Кастомный action: продавец нажимает кнопку в CRM
        "Передать курьеру" — статус заказа меняется.
        """
        order = self.get_object()
        order.status = Order.Status.HANDED_TO_COURIER
        order.save(update_fields=["status"])
        return Response(OrderSerializer(order).data)


class InstagramWebhookView(APIView):
    """
    Endpoint, который слушает Meta Graph API.

    GET  — используется Meta один раз при подключении вебхука для верификации.
    POST — сюда приходят реальные события:
           • messaging — новые Direct-сообщения от клиентов
           • changes / comments — новые комментарии к постам
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        # Meta присылает эти параметры при первичной настройке вебхука в консоли разработчика
        verify_token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        if verify_token == settings.META_WEBHOOK_VERIFY_TOKEN:
            return HttpResponse(challenge)
        return HttpResponse("Неверный verify_token", status=403)

    def post(self, request):
        # --- БЕЗОПАСНОСТЬ: Проверяем подпись Meta (X-Hub-Signature-256) ---
        # Meta подписывает каждый webhook HMAC-SHA256 с App Secret.
        # Если подпись не совпадает — запрос поддельный, отклоняем.
        app_secret = getattr(settings, "META_APP_SECRET", "")
        if app_secret:
            import hashlib, hmac
            signature_header = request.META.get("HTTP_X_HUB_SIGNATURE_256", "")
            if signature_header.startswith("sha256="):
                expected_sig = signature_header[7:]
                body = request.body
                computed_sig = hmac.new(
                    app_secret.encode("utf-8"),
                    body,
                    hashlib.sha256,
                ).hexdigest()
                if not hmac.compare_digest(computed_sig, expected_sig):
                    logger.warning("Instagram webhook: неверная подпись X-Hub-Signature-256 — запрос отклонён")
                    return Response(status=status.HTTP_403_FORBIDDEN)
            else:
                logger.warning("Instagram webhook: отсутствует X-Hub-Signature-256 — запрос отклонён")
                return Response(status=status.HTTP_403_FORBIDDEN)

        payload = request.data
        logger.info("Instagram webhook payload: %s", payload)

        for entry in payload.get("entry", []):
            instagram_business_id = entry.get("id")

            store = Store.objects.filter(
                instagram_business_id=instagram_business_id, is_active=True
            ).first()
            if not store:
                # Попробуем найти первый активный магазин и автоматически связать его с этим Instagram Business ID
                store = Store.objects.filter(is_active=True).first()
                if store and not store.instagram_business_id:
                    store.instagram_business_id = str(instagram_business_id)
                    store.save(update_fields=["instagram_business_id"])

            if not store:
                logger.warning(
                    "Получено сообщение для неизвестного instagram_business_id=%s. "
                    "В базе нет подходящего активного Store.",
                    instagram_business_id,
                )
                continue

            # ── 1. Direct-сообщения (Instagram Messaging / DM) ────────────────
            for messaging_event in entry.get("messaging", []):
                sender_id = messaging_event.get("sender", {}).get("id")
                message_text = messaging_event.get("message", {}).get("text")

                if not sender_id or not message_text:
                    continue

                try:
                    from core.tasks import handle_incoming_instagram_message_task
                    handle_incoming_instagram_message_task.delay(
                        store_id=store.id,
                        instagram_scoped_id=sender_id,
                        message_text=message_text,
                    )
                except Exception:
                    logger.info("Celery недоступен, обрабатываем Instagram DM синхронно")
                    from core.working_views.services import handle_incoming_instagram_message
                    handle_incoming_instagram_message(store, sender_id, message_text)

            # ── 2. Комментарии к постам (Instagram Comments webhook) ──────────
            for change in entry.get("changes", []):
                if change.get("field") != "comments":
                    continue

                value = change.get("value", {})
                comment_id = value.get("id")
                comment_text = value.get("text", "")
                sender_id = value.get("from", {}).get("id")

                # Пропускаем, если это наш собственный ответ-бот (избегаем петли)
                if str(sender_id) == str(instagram_business_id):
                    continue

                if not comment_id or not sender_id:
                    continue

                try:
                    from core.tasks import handle_incoming_instagram_comment_task
                    handle_incoming_instagram_comment_task.delay(
                        store_id=store.id,
                        comment_id=comment_id,
                        sender_id=sender_id,
                        comment_text=comment_text,
                    )
                except Exception:
                    logger.info("Celery недоступен, обрабатываем Instagram comment синхронно")
                    from core.working_views.services import handle_incoming_instagram_comment
                    handle_incoming_instagram_comment(store, comment_id, sender_id, comment_text)

        # Meta требует ответ 200 OK, иначе будет повторять попытки отправки
        return Response(status=status.HTTP_200_OK)