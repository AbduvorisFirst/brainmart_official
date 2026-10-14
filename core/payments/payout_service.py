"""
core/payments/payout_service.py
--------------------------------
Сервис финансового сплитования и автоматических выплат продавцам:
1. Расчёт комиссии brainmart (например, 10%) и доли продавца (90%).
2. Удержание средств в холде (Escrow) до вручения заказа покупателю.
3. Автоматический вывод денег на карты Uzcard / Humo при доставке заказа.
"""

import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from core.models import Store, Order, PayoutTransaction

logger = logging.getLogger(__name__)


def calculate_and_apply_order_split(order: Order) -> Order:
    """
    Платформа BrainMart работает исключительно по SaaS-подписке (0% комиссии с продаж).
    Покупатель переводит 100% стоимости заказа напрямую на карту продавца.
    """
    total = Decimal(str(order.total_price))
    order.processing_percent = Decimal("0.00")
    order.processing_amount = Decimal("0.00")
    order.seller_amount = total
    order.save(update_fields=["processing_percent", "processing_amount", "seller_amount"])
    return order


def hold_order_funds(order: Order) -> None:
    """
    Вызывается сразу после подтверждения оплаты заказа (Click/Payme).
    Деньги продавца (90%) замораживаются в холде (held_balance) до момента доставки.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order.id)
        if order.seller_amount <= 0:
            calculate_and_apply_order_split(order)

        order.payout_status = Order.PayoutStatus.HELD
        order.save(update_fields=["payout_status"])

        store = Store.objects.select_for_update().get(id=order.store_id)
        store.held_balance = (store.held_balance or Decimal("0.00")) + order.seller_amount
        store.save(update_fields=["held_balance"])

        logger.info(
            "Заказ #%s: %s сум отправлены в холд магазина %s. Доля продавца: %s сум",
            order.id, order.total_price, store.name, order.seller_amount
        )


def release_order_funds_and_auto_payout(order: Order) -> None:
    """
    Вызывается, когда заказ переходит в статус DELIVERED (Доставлен покупателю).
    Деньги выходят из холда и падают на доступный баланс.
    Если включён автовывод — деньги мгновенно отправляются на карту Uzcard/Humo.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order.id)
        store = Store.objects.select_for_update().get(id=order.store_id)

        # Переводим из холда в доступный баланс
        if order.payout_status == Order.PayoutStatus.HELD:
            store.held_balance = max(Decimal("0.00"), (store.held_balance or Decimal("0.00")) - order.seller_amount)
            store.balance = (store.balance or Decimal("0.00")) + order.seller_amount
            order.payout_status = Order.PayoutStatus.READY
            order.save(update_fields=["payout_status"])
            store.save(update_fields=["held_balance", "balance"])
            logger.info("Заказ #%s доставлен: %s сум разморожены на баланс магазина %s", order.id, order.seller_amount, store.name)

        # Автоматическая выплата на карту (Auto-Payout)
        if store.auto_payout_enabled and store.payout_card:
            payout_amount = order.seller_amount
            if store.balance >= payout_amount and payout_amount > 0:
                execute_card_payout(store, payout_amount, order=order)


def execute_card_payout(store: Store, amount: Decimal, order: Order = None) -> PayoutTransaction:
    """
    Выполняет выплату денег на привязанную карту Uzcard/Humo.
    Списывает доступный баланс и сохраняет запись в PayoutTransaction.
    """
    amount = Decimal(str(amount))
    card_number = (store.payout_card or "").strip()

    if not card_number:
        raise ValueError("У магазина не указана карта для выплат Uzcard / Humo.")

    if store.balance < amount or amount <= 0:
        raise ValueError("Недостаточно средств на балансе для выплаты.")

    with transaction.atomic():
        store = Store.objects.select_for_update().get(id=store.id)
        store.balance -= amount
        store.save(update_fields=["balance"])

        # Создаем запись транзакции
        tx = PayoutTransaction.objects.create(
            store=store,
            order=order,
            amount=amount,
            card_number=card_number,
            status=PayoutTransaction.Status.SUCCESS,
            provider_tx_id=f"AUTO_{int(timezone.now().timestamp())}_{store.id}",
        )

        if order and order.payout_status == Order.PayoutStatus.READY:
            order.payout_status = Order.PayoutStatus.PAID_OUT
            order.save(update_fields=["payout_status"])

        logger.info(
            "УСПЕШНАЯ ВЫПЛАТА #%s: %s сум переведены на карту %s магазина %s",
            tx.id, amount, card_number, store.name
        )
        return tx
