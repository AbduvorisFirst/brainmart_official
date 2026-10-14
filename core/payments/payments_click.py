"""
payments_click.py
-------------------
Интеграция с Click.uz — самой популярной платёжной системой в Узбекистане.

Как это работает (коротко):
1. Клиент оформляет заказ на сайте -> мы генерируем ссылку на оплату
   через build_click_pay_url() -> редиректим клиента на сайт Click.
2. Клиент вводит карту и платит на стороне Click (мы этого не видим).
3. Click сам стучится к НАМ на два наших адреса (по очереди):
   - Prepare (action=0) — "клиент начал оплату, подтверди, что заказ существует"
   - Complete (action=1) — "оплата прошла успешно, подтверди получение"
   Оба запроса обрабатывает один и тот же ClickWebhookView.

ВАЖНО: у Click есть официальная документация с точным списком полей —
https://docs.click.uz/en/click-api/#merchant-api
Перед реальным запуском в проде сверь названия полей с актуальной
документацией в личном кабинете Click (иногда бывают мелкие отличия
между версиями API).

Все секреты (CLICK_SECRET_KEY и т.д.) берутся из .env через settings.py,
как и раньше с Gemini/Meta токенами.
"""

import hashlib
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Order
from core.working_views.telegram_bot import notify_seller_about_order


# --- Коды ошибок, которые понимает протокол Click ---
CLICK_ERROR_SUCCESS = 0
CLICK_ERROR_SIGN_CHECK_FAILED = -1
CLICK_ERROR_ORDER_NOT_FOUND = -5
CLICK_ERROR_ALREADY_PAID = -4
CLICK_ERROR_TRANSACTION_NOT_FOUND = -6
CLICK_ERROR_AMOUNT_MISMATCH = -2


def build_click_pay_url(order: Order) -> str:
    """
    Генерирует ссылку на оплату заказа через Click.
    Именно на эту ссылку нужно отправить клиента после оформления заказа —
    это и есть "второй клик" в сценарии "заказ за 2 клика".
    """
    return_url = f"{settings.SITE_URL}/ru/order/{order.id}/success/"

    return (
        "https://my.click.uz/services/pay"
        f"?service_id={settings.CLICK_SERVICE_ID}"
        f"&merchant_id={settings.CLICK_MERCHANT_ID}"
        f"&amount={order.total_price}"
        f"&transaction_param={order.id}"
        f"&return_url={return_url}"
    )


def _make_sign_string(*parts) -> str:
    """Склеивает переданные значения и берёт от них MD5 — так требует протокол Click."""
    raw = "".join(str(p) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class ClickWebhookView(APIView):
    """
    Единая точка входа для запросов от Click: и Prepare, и Complete
    приходят на один и тот же адрес, различаются полем `action`.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        data = request.data
        action = str(data.get("action"))

        if action == "0":
            return self._handle_prepare(data)
        elif action == "1":
            return self._handle_complete(data)

        return Response(
            {"error": CLICK_ERROR_SIGN_CHECK_FAILED, "error_note": "Unknown action"},
            status=status.HTTP_200_OK,
        )

    def _handle_prepare(self, data):
        """
        Шаг 1: Click спрашивает "этот заказ вообще существует и его можно оплатить?"
        Здесь мы НЕ подтверждаем оплату, только проверяем корректность данных.
        """
        merchant_trans_id = data.get("merchant_trans_id")  # это наш order.id
        amount = data.get("amount")

        sign_check = _make_sign_string(
            data.get("click_trans_id"),
            data.get("service_id"),
            settings.CLICK_SECRET_KEY,
            merchant_trans_id,
            amount,
            data.get("action"),
            data.get("sign_time"),
        )
        if sign_check != data.get("sign_string"):
            return Response(
                {"error": CLICK_ERROR_SIGN_CHECK_FAILED, "error_note": "Sign check failed"}
            )

        try:
            order = Order.objects.get(id=merchant_trans_id)
        except (Order.DoesNotExist, ValueError, TypeError):
            return Response(
                {"error": CLICK_ERROR_ORDER_NOT_FOUND, "error_note": "Order not found"}
            )

        if order.status == Order.Status.PAID:
            return Response(
                {"error": CLICK_ERROR_ALREADY_PAID, "error_note": "Already paid"}
            )

        if Decimal(str(amount)) != order.total_price:
            return Response(
                {"error": CLICK_ERROR_AMOUNT_MISMATCH, "error_note": "Amount mismatch"}
            )

        return Response({
            "click_trans_id": data.get("click_trans_id"),
            "merchant_trans_id": merchant_trans_id,
            "merchant_prepare_id": order.id,
            "error": CLICK_ERROR_SUCCESS,
            "error_note": "Success",
        })

    def _handle_complete(self, data):
        """
        Шаг 2: Click подтверждает "деньги реально списаны и зачислены".
        ТОЛЬКО здесь мы помечаем заказ оплаченным и списываем остатки на складе —
        именно это правило (не раньше!) обсуждали с самого начала проекта.
        """
        merchant_trans_id = data.get("merchant_trans_id")
        amount = data.get("amount")

        sign_check = _make_sign_string(
            data.get("click_trans_id"),
            data.get("service_id"),
            settings.CLICK_SECRET_KEY,
            merchant_trans_id,
            data.get("merchant_prepare_id"),
            amount,
            data.get("action"),
            data.get("sign_time"),
        )
        if sign_check != data.get("sign_string"):
            return Response(
                {"error": CLICK_ERROR_SIGN_CHECK_FAILED, "error_note": "Sign check failed"}
            )

        try:
            order = Order.objects.select_related("store").get(id=merchant_trans_id)
        except (Order.DoesNotExist, ValueError, TypeError):
            return Response(
                {"error": CLICK_ERROR_ORDER_NOT_FOUND, "error_note": "Order not found"}
            )

        # Защита от повторной обработки: Click иногда шлёт Complete повторно.
        # Если заказ уже оплачен — просто отвечаем "успех", ничего не списываем повторно.
        if order.status == Order.Status.PAID:
            return Response({
                "click_trans_id": data.get("click_trans_id"),
                "merchant_trans_id": merchant_trans_id,
                "merchant_confirm_id": order.id,
                "error": CLICK_ERROR_SUCCESS,
                "error_note": "Already confirmed",
            })

        error_code = data.get("error")
        if str(error_code) != "0":
            # Click сообщает, что оплата не удалась на его стороне
            order.status = Order.Status.CANCELLED
            order.save(update_fields=["status"])
            return Response({
                "click_trans_id": data.get("click_trans_id"),
                "merchant_trans_id": merchant_trans_id,
                "merchant_confirm_id": order.id,
                "error": CLICK_ERROR_SUCCESS,
                "error_note": "Payment failure noted",
            })

        # Всё хорошо — помечаем заказ оплаченным и списываем остатки товара.
        # transaction.atomic гарантирует: либо оба действия (статус + склад)
        # выполнятся вместе, либо ни одно — на случай сбоя посреди операции.
        with transaction.atomic():
            order.status = Order.Status.PAID
            order.payment_method = "click"
            order.payment_transaction_id = str(data.get("click_trans_id"))
            order.save(update_fields=["status", "payment_method", "payment_transaction_id"])

            # Финансовый сплит: рассчитываем комиссию 10% и замораживаем 90% в холде магазина
            from .payout_service import hold_order_funds
            hold_order_funds(order)

            for item in order.items.select_related("variant").all():
                variant = item.variant
                variant.stock_quantity = max(0, variant.stock_quantity - item.quantity)
                variant.save(update_fields=["stock_quantity"])

        # Уведомляем продавца в Telegram — вне transaction.atomic(), потому что
        # это внешний сетевой запрос, а не изменение базы данных. Если Telegram
        # окажется недоступен, это не должно откатывать уже сохранённую оплату.
        from core.tasks import notify_seller_about_order_task
        notify_seller_about_order_task.delay(order.id)

        return Response({
            "click_trans_id": data.get("click_trans_id"),
            "merchant_trans_id": merchant_trans_id,
            "merchant_confirm_id": order.id,
            "error": CLICK_ERROR_SUCCESS,
            "error_note": "Success",
        })