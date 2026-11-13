"""
subscription_service.py
-----------------------
Сервис управления ежемесячной SaaS & AI подпиской BrainMart:
- Тариф: 400 000 сум/мес (включает 1-ю точку) + 150 000 сум за каждый дополнительный филиал.
- 6-дневный льготный период (Grace Period).
- Ежедневные предупреждения в Telegram:
  * За 3 дня, 2 дня, 1 день и в день окончания срока.
  * 1-6 дни льготного периода (с обратным отсчетом дней до заморозки).
- Автоматическая заморозка на 7-й день просрочки (> 6 дней):
  * Витрина магазина скрывается из каталога.
  * ИИ-ассистент в Telegram/Instagram автоматически отключается.
  * ВСЕ ДАННЫЕ (товары, фото, остатки, филиалы, заказы) сохраняются в целости и сохранности.
- Продление:
  * Если оплачено во время 6 дней льготного периода — новый месяц отсчитывается
    от предыдущей даты окончания (срок не сдвигается вперед, просроченные дни засчитываются).
  * Если оплачено после заморозки — магазин размораживается, цикл начинается с даты оплаты.
"""

import logging
import threading
import time

from django.core.cache import cache
from django.db import close_old_connections
from django.db.models import Count

from core.models import Store, StoreManager
from core.working_views.telegram_bot import _send_telegram_message

logger = logging.getLogger(__name__)

_AUTO_CHECK_INTERVAL_SECONDS = 6 * 60 * 60  # Проверять каждые 6 часов (stage защищает от дублей)
_AUTO_CHECK_CACHE_KEY = "bm_subscription_auto_check_v1"
_IN_PROCESS_LOCK = threading.Lock()
_LAST_IN_PROCESS_RUN = 0.0


def _build_fee_breakdown_lines(store):
    """Формирует расчёт стоимости подписки магазина с учётом всех точек/филиалов."""
    bd = store.get_subscription_fee_breakdown()
    if bd["extra_branches_count"] > 0:
        calc_block = (
            f"💳 Расчёт оплаты по вашему магазину ({bd['effective_branches']} точек):\n"
            f"• Базовый тариф (1-я точка): {bd['fmt_base']} сум/мес\n"
            f"• Доп. точки ({bd['extra_branches_count']} × {bd['fmt_extra_unit']}): +{bd['fmt_extra_total']} сум/мес\n"
            f"💰 Итого к оплате: {bd['fmt_total']} сум/мес"
        )
    else:
        calc_block = (
            f"💳 Расчёт оплаты по вашему магазину ({bd['effective_branches']} точка):\n"
            f"• Базовый тариф (1-я точка): {bd['fmt_base']} сум/мес\n"
            f"• Доп. точки: 0 (+0 сум)\n"
            f"💰 Итого к оплате: {bd['fmt_total']} сум/мес"
        )
    return bd, calc_block


def check_and_notify_all_subscriptions():
    """
    Проверяет статус подписки всех магазинов, считает тариф с учётом всех точек
    (400 000 база + 150 000 за каждую доп. точку) и рассылает уведомления / замораживает магазины.
    Вызывается ежедневно по расписанию (Celery Beat / cron) или автоматически в фоне.
    """
    stores = Store.objects.annotate(branches_count_cached=Count("branches")).all()
    results = {"checked": 0, "notified": 0, "suspended": 0}

    for store in stores:
        results["checked"] += 1
        days_left = store.subscription_days_left
        date_str = store.subscription_paid_until.strftime("%d.%m.%Y") if store.subscription_paid_until else "—"
        bd, calc_block = _build_fee_breakdown_lines(store)
        fee_str = f"{bd['fmt_total']} сум"

        stage = None
        message = None
        should_suspend = False

        # 1. За 3 дня до окончания
        if days_left == 3:
            stage = "reminder_3d"
            message = (
                f"📅 Напоминание BrainMart:\n\n"
                f"До окончания подписки магазина «{store.name}» осталось 3 дня.\n"
                f"🗓 Оплачено до: {date_str}\n\n"
                f"{calc_block}\n\n"
                f"Пожалуйста, продлите подписку в разделе «Финансы», чтобы витрина и ИИ продолжали принимать заказы без перебоев."
            )

        # 2. За 2 дня до окончания
        elif days_left == 2:
            stage = "reminder_2d"
            message = (
                f"📅 Напоминание BrainMart:\n\n"
                f"До окончания подписки магазина «{store.name}» осталось 2 дня (до {date_str}).\n\n"
                f"{calc_block}\n\n"
                f"Продлите подписку в личном кабинете («Финансы»), переведя {fee_str} на карту и прикрепив чек."
            )

        # 3. За 1 день до окончания
        elif days_left == 1:
            stage = "reminder_1d"
            message = (
                f"⚠️ Внимание! Завтра заканчивается подписка BrainMart!\n\n"
                f"Завтра ({date_str}) истекает оплаченный период магазина «{store.name}».\n\n"
                f"{calc_block}\n\n"
                f"Оплатите {fee_str} в разделе «Финансы», чтобы ИИ-продавец и приём заказов не прерывались."
            )

        # 4. Сегодня последний оплаченный день
        elif days_left == 0:
            stage = "reminder_0d"
            message = (
                f"⚠️ ВНИМАНИЕ! Сегодня последний оплаченный день подписки ({date_str})!\n\n"
                f"Магазин: «{store.name}».\n"
                f"С завтрашнего дня начнется 6-дневный льготный период.\n\n"
                f"{calc_block}\n\n"
                f"Рекомендуем оплатить {fee_str} сегодня, чтобы избежать предупреждений."
            )

        # 5. Дни 1-5 льготного периода (просрочка 1-5 дней)
        elif -5 <= days_left <= -1:
            overdue = abs(days_left)
            remaining_grace = 6 - overdue
            stage = f"grace_{overdue}d"
            message = (
                f"🚨 ВНИМАНИЕ! Срок подписки магазина «{store.name}» истёк {overdue} дн. назад ({date_str})!\n\n"
                f"Включён 6-дневный льготный период (осталось {remaining_grace} дн. до автоматической заморозки).\n"
                f"Витрина и ИИ пока работают, но через {remaining_grace} дн. будут отключены.\n\n"
                f"{calc_block}\n\n"
                f"Перейдите в раздел «Финансы», переведите {fee_str} и прикрепите чек."
            )

        # 6. День 6 льготного периода (ПОСЛЕДНИЙ ДЕНЬ)
        elif days_left == -6:
            stage = "grace_last_day"
            message = (
                f"🔥 КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ!\n\n"
                f"Сегодня ПОСЛЕДНИЙ (6-й) день льготного периода для магазина «{store.name}»!\n"
                f"Завтра в полночь ваш магазин, витрина товаров и ИИ-ассистент будут ПОЛНОСТЬЮ ЗАМОРОЖЕНЫ!\n\n"
                f"{calc_block}\n\n"
                f"Срочно оплатите {fee_str} в разделе «Финансы» и загрузите чек перевода, чтобы не потерять клиентов!"
            )

        # 7. День 7 и более просрочки — АВТОМАТИЧЕСКАЯ ЗАМОРОЗКА
        elif days_left <= -7:
            stage = "suspended"
            should_suspend = True
            message = (
                f"🛑 ВАШ МАГАЗИН ЗАМОРОЖЕН!\n\n"
                f"Истёк 6-дневный льготный период без оплаты подписки магазина «{store.name}».\n"
                f"• Витрина и товары магазина скрыты из каталога.\n"
                f"• ИИ-ассистент в Telegram/Instagram автоматически отключен.\n"
                f"• Все ваши товары, фото, филиалы и история СОХРАНЕНЫ в безопасности!\n\n"
                f"{calc_block}\n\n"
                f"Для немедленной разморозки перейдите в раздел «Финансы», переведите {fee_str} и отправьте чек."
            )

        if should_suspend:
            if store.is_active or not store.is_frozen_by_admin or store.subscription_status != "suspended":
                store.is_active = False
                store.is_frozen_by_admin = True
                store.freeze_reason = "Подписка не оплачена (просрочка более 6 дней)"
                store.subscription_status = "suspended"
                store.save(update_fields=["is_active", "is_frozen_by_admin", "freeze_reason", "subscription_status"])
                results["suspended"] += 1

        # Отправляем сообщение только при смене этапа, чтобы не спамить продавцу каждый час
        if stage and stage != store.subscription_warning_sent_stage and message:
            store.subscription_warning_sent_stage = stage
            store.save(update_fields=["subscription_warning_sent_stage"])

            if store.telegram_chat_id:
                _send_telegram_message(store.telegram_chat_id, message)
                results["notified"] += 1

            for manager in StoreManager.objects.filter(store=store, is_active=True):
                if manager.telegram_chat_id:
                    _send_telegram_message(manager.telegram_chat_id, message)

    return results


def _run_subscription_check_background():
    """Выполняет проверку подписок в фоновом потоке с закрытием соединений БД."""
    try:
        close_old_connections()
        check_and_notify_all_subscriptions()
    except Exception:
        logger.exception("Ошибка фоновой проверки подписок")
    finally:
        close_old_connections()


def trigger_subscription_check_if_due():
    """
    Неблокирующий автозапуск проверки подписок (не чаще 1 раза в 6 часов).
    Гарантирует отправку напоминаний даже если отдельный процесс Celery Beat не запущен.
    """
    global _LAST_IN_PROCESS_RUN
    now = time.monotonic()

    with _IN_PROCESS_LOCK:
        if now - _LAST_IN_PROCESS_RUN < _AUTO_CHECK_INTERVAL_SECONDS:
            return False
        _LAST_IN_PROCESS_RUN = now

    try:
        if not cache.add(_AUTO_CHECK_CACHE_KEY, "1", timeout=_AUTO_CHECK_INTERVAL_SECONDS):
            return False
    except Exception:
        # Если Redis/кэш временно недоступен, опираемся на in-process throttle выше
        pass

    thread = threading.Thread(
        target=_run_subscription_check_background,
        name="subscription-auto-check",
        daemon=True,
    )
    thread.start()
    return True
