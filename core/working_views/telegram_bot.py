"""
telegram_bot.py
-----------------
Telegram Mini App интеграция + уведомления продавцу.

Три сценария в одном webhook (различаются параметром после /start):
1. /start product_42        -> клиент, ему открываем конкретный товар
2. /start link_<токен>      -> продавец привязывает свой Telegram для
                                уведомлений о новых оплаченных заказах
3. /start store_5           -> открывает каталог конкретного магазина
4. /start (без параметра)   -> проверка: если продавец — даем админку, если клиент — общий каталог

Интерактивный заказ:
Ловит GPS (location) и телефон (contact) прямо в чате, используя кэш (state)
для запоминания шагов клиента, и выдает инвойс Click.

ВАЖНОЕ ПРАВИЛО: этот webhook НИКОГДА не должен возвращать Telegram
код 500. Если Telegram получает 500 (или таймаут), он считает доставку
неуспешной и повторяет отправку того же самого обновления снова и снова.
Поэтому вся обработка обёрнута в try/except.
"""

import logging
import re
import requests

from django.conf import settings
from django.core.cache import cache
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (
    Store, Order, StoreManager, Customer, StoreBranch, SubscriptionPayment, PlatformSettings
)

logger = logging.getLogger(__name__)


def build_telegram_deep_link(product_id: int) -> str:
    """Ссылка на конкретный товар — её отправляет ИИ-агент клиенту."""
    bot_username = settings.TELEGRAM_BOT_USERNAME
    return f"https://t.me/{bot_username}?start=product_{product_id}"


def build_seller_link_deep_link(store: Store) -> str:
    """Ссылка для привязки Telegram продавца к его магазину."""
    bot_username = settings.TELEGRAM_BOT_USERNAME
    return f"https://t.me/{bot_username}?start=link_{store.telegram_link_token}"


def build_manager_link_deep_link(manager) -> str:
    """Ссылка для привязки Telegram менеджера."""
    bot_username = settings.TELEGRAM_BOT_USERNAME
    return f"https://t.me/{bot_username}?start=mgr_{manager.telegram_link_token}"


def build_superadmin_link_deep_link() -> str:
    """Ссылка для привязки Telegram Главного Администратора платформы."""
    ps = PlatformSettings.get_settings()
    bot_username = settings.TELEGRAM_BOT_USERNAME
    return f"https://t.me/{bot_username}?start=adminlink_{ps.superadmin_telegram_link_token}"


def _clean_telegram_text(text: str) -> str:
    """Очищает текст от любых случайных HTML-тегов (<b>, <code>, <i> и т.д.), чтобы сообщение всегда отображалось красиво и чисто."""
    if not text:
        return ""
    import re
    return re.sub(r'</?(?:b|code|i|u|strong|em|a|pre|s|strike|tg-spoiler)[^>]*>', '', str(text))


_tg_session = requests.Session()
_tg_session_adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=50, max_retries=1)
_tg_session.mount("https://", _tg_session_adapter)


def _send_telegram_chat_action(chat_id, action: str = "typing", bot_token: str | None = None) -> None:
    """Мгновенно показывает статус 'печатает...' в чате Telegram."""
    token = bot_token or getattr(settings, "TELEGRAM_BOT_TOKEN", None)
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendChatAction"
    try:
        _tg_session.post(url, json={"chat_id": chat_id, "action": action}, timeout=3)
    except Exception:
        pass


def _send_telegram_message(chat_id, text: str, web_app_url: str | None = None, reply_markup: dict | None = None, bot_token: str | None = None) -> bool:
    """Низкоуровневая отправка сообщения через Telegram Bot API с пулом соединений (поддерживает личный токен магазина)."""
    token = bot_token or getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    text = _clean_telegram_text(text)
    payload = {"chat_id": chat_id, "text": text}

    if reply_markup:
        payload["reply_markup"] = reply_markup
    elif web_app_url:
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "🛍 Открыть магазин", "web_app": {"url": web_app_url}}
            ]]
        }

    try:
        response = _tg_session.post(url, json=payload, timeout=8)
        if response.status_code != 200:
            logger.warning("Telegram sendMessage вернул ошибку: %s", response.text)
            return False
        return True
    except requests.RequestException:
        logger.exception("Не удалось отправить сообщение в Telegram (chat_id=%s)", chat_id)
        return False


def _send_telegram_photo(chat_id, photo_url_or_file, caption: str, reply_markup: dict | None = None, bot_token: str | None = None) -> bool:
    """Отправка фото товара с подписью и кнопками в Telegram (поддерживает личный токен магазина)."""
    import os
    import json
    import io
    token = bot_token or getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendPhoto"

    caption = _clean_telegram_text(caption)
    # Telegram ограничивает подпись к фото в 1024 символа
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    # 1. Если это BytesIO / буфер в памяти
    if isinstance(photo_url_or_file, (io.BytesIO, io.BufferedReader)) or hasattr(photo_url_or_file, "read"):
        try:
            if hasattr(photo_url_or_file, "seek"):
                photo_url_or_file.seek(0)
            fname = getattr(photo_url_or_file, "name", "order.jpg")
            files = {"photo": (fname, photo_url_or_file, "image/jpeg")}
            data = {"chat_id": chat_id, "caption": caption}
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)
            res = _tg_session.post(url, data=data, files=files, timeout=20)
            return res.status_code == 200
        except Exception as e:
            logger.warning("Не удалось отправить фото из буфера: %s", e)

    # 2. Если это путь к файлу на диске или FieldFile
    file_path = getattr(photo_url_or_file, "path", str(photo_url_or_file))
    if isinstance(file_path, str) and os.path.exists(file_path):
        try:
            with open(file_path, "rb") as f:
                data = {"chat_id": chat_id, "caption": caption}
                if reply_markup:
                    data["reply_markup"] = json.dumps(reply_markup)
                files = {"photo": ("order.jpg", f, "image/jpeg")}
                res = _tg_session.post(url, data=data, files=files, timeout=20)
                return res.status_code == 200
        except Exception as e:
            logger.warning("Не удалось отправить локальное фото: %s", e)

    # 3. Если это URL или строка
    photo_str = str(photo_url_or_file) if photo_url_or_file else ""
    if photo_str.startswith("http"):
        payload = {"chat_id": chat_id, "photo": photo_str, "caption": caption}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            res = _tg_session.post(url, json=payload, timeout=15)
            return res.status_code == 200
        except Exception as e:
            logger.warning("Не удалось отправить фото по URL: %s", e)

    return False


def _prepare_order_photo(order: Order):
    """
    Возвращает объект фото для заказа:
    - Если 1 товар -> путь к файлу
    - Если 2-3 товара -> горизонтальный коллаж через PIL (BytesIO),
      чтобы клиент/продавец получил ровно 1 сообщение с фотографиями всех вещей
    """
    import io
    import os
    from PIL import Image

    image_paths = []
    image_urls = []
    for item in order.items.select_related("variant__product").all():
        img = item.variant.image or item.variant.product.main_image
        if not img:
            continue
        if hasattr(img, "path") and os.path.exists(img.path):
            if img.path not in image_paths:
                image_paths.append(img.path)
        elif hasattr(img, "url") and img.url:
            if img.url not in image_urls:
                image_urls.append(img.url)

    # Если 2 или более фото — склеиваем в 1 аккуратный горизонтальный коллаж
    if len(image_paths) >= 2:
        try:
            images = []
            target_height = 600
            for p in image_paths[:3]:  # максимум 3 товара в ряд
                im = Image.open(p).convert("RGB")
                aspect = im.width / im.height
                target_width = int(target_height * aspect)
                im_resized = im.resize((target_width, target_height), Image.Resampling.LANCZOS)
                images.append(im_resized)

            divider_width = 8
            total_width = sum(im.width for im in images) + divider_width * (len(images) - 1)
            collage = Image.new("RGB", (total_width, target_height), color=(15, 23, 42))

            curr_x = 0
            for im in images:
                collage.paste(im, (curr_x, 0))
                curr_x += im.width + divider_width

            buf = io.BytesIO()
            collage.save(buf, format="JPEG", quality=90)
            buf.seek(0)
            buf.name = f"order_{order.id}_collage.jpg"
            return buf
        except Exception as e:
            logger.warning("Не удалось склеить коллаж товаров: %s", e)
            if image_paths:
                return image_paths[0]

    if len(image_paths) == 1:
        return image_paths[0]

    if image_urls:
        return image_urls[0]

    return None


def ensure_store_telegram_chat_id(store: Store) -> str | None:
    """
    Возвращает telegram_chat_id владельца магазина.
    Если он не был сохранен ранее, но указан owner_telegram_username —
    автоматически находит chat_id среди пользователей бота и сохраняет в базу.
    """
    if store.telegram_chat_id:
        return str(store.telegram_chat_id)
    if store.owner_telegram_username:
        clean = store.owner_telegram_username.lstrip("@").strip().lower()
        customer = Customer.objects.filter(
            telegram_username__iexact=clean
        ).exclude(telegram_chat_id="").exclude(telegram_chat_id__isnull=True).first()
        if customer and customer.telegram_chat_id:
            store.telegram_chat_id = str(customer.telegram_chat_id)
            store.save(update_fields=["telegram_chat_id"])
            return str(store.telegram_chat_id)
    return None


def notify_order_created(order: Order) -> None:
    """
    Вызывается сразу после оформления заказа покупателем на сайте.
    1. Отправляет уведомление Владельцу магазина с реквизитами карты, составом и кнопками подтверждения.
    2. Отправляет карточку заказа ВСЕМ активным Менеджерам магазина, чтобы они начали собирать товар к отправке.
    """
    store = order.store
    customer = order.customer

    # Контактные данные покупателя
    client_display = customer.full_name or "Покупатель"
    if customer.telegram_username:
        client_display += f" (@{customer.telegram_username.lstrip('@')})"

    # Реквизиты карты продавца
    card_info = store.payout_card or "Реквизиты уточняются"
    if store.payout_card_holder:
        card_info += f" ({store.payout_card_holder})"

    # Локация и такси
    taxi_map_url = None
    if order.delivery_latitude and order.delivery_longitude:
        taxi_map_url = f"https://yandex.com/maps/?rtext=~{order.delivery_latitude},{order.delivery_longitude}&rtt=auto"

    # Ответственный менеджер
    active_managers = list(store.managers.filter(is_active=True))
    assigned_mgr = None
    for mgr in active_managers:
        if mgr.telegram_chat_id:
            assigned_mgr = mgr
            break
    if not assigned_mgr and active_managers:
        assigned_mgr = active_managers[0]
    if assigned_mgr and not order.assigned_manager:
        order.assigned_manager = assigned_mgr
        order.save(update_fields=["assigned_manager"])

    mgr_display = assigned_mgr.display_contact if assigned_mgr else None

    # Фото заказа
    order_photo = _prepare_order_photo(order)

    # 1. ОТПРАВЛЯЕМ ВЛАДЕЛЬЦУ МАГАЗИНА
    owner_chat_id = ensure_store_telegram_chat_id(store)
    if owner_chat_id:
        owner_lang = _get_store_lang(store)
        items_text = _build_items_text(order, owner_lang)
        delivery_text = _build_delivery_text(order, owner_lang)
        manager_line = _nt(owner_lang, 'mgr_assigned', mgr=mgr_display) if assigned_mgr else _nt(owner_lang, 'mgr_none')
        owner_text = _nt(
            owner_lang, 'notif_new_order',
            order_id=order.id, store=store.name,
            total=f"{int(order.total_price):,}",
            card_info=card_info, client=client_display,
            phone=customer.phone_number,
            delivery=delivery_text, items=items_text,
            manager_line=manager_line,
        )
        owner_reply_markup = {
            "inline_keyboard": [
                [
                    {"text": _nt(owner_lang, 'btn_confirm_pay'), "callback_data": f"confirm_pay_{order.id}"},
                    {"text": _nt(owner_lang, 'btn_reject_pay'), "callback_data": f"reject_pay_{order.id}"}
                ]
            ]
        }
        store_bot_token = store.telegram_bot_token if (store.telegram_bot_token and store.telegram_bot_active) else None
        sent = False
        if order_photo:
            sent = _send_telegram_photo(owner_chat_id, order_photo, owner_text, reply_markup=owner_reply_markup, bot_token=store_bot_token)
        if not sent:
            _send_telegram_message(owner_chat_id, owner_text, reply_markup=owner_reply_markup, bot_token=store_bot_token)

    # 2. ОТПРАВЛЯЕМ ВСЕМ АКТИВНЫМ МЕНЕДЖЕРАМ
    for mgr in active_managers:
        if mgr.telegram_chat_id and mgr.telegram_chat_id != owner_chat_id:
            mgr_lang = _get_mgr_lang(mgr)
            mgr_items = _build_items_text(order, mgr_lang)
            mgr_delivery = _build_delivery_text(order, mgr_lang)
            mgr_text = _nt(
                mgr_lang, 'notif_order_mgr_new',
                order_id=order.id, store=store.name,
                total=f"{int(order.total_price):,}",
                client=client_display, phone=customer.phone_number,
                delivery=mgr_delivery, items=mgr_items,
            )
            mgr_kb = []
            if taxi_map_url:
                mgr_kb.append([{"text": _nt(mgr_lang, 'btn_taxi_yandex'), "url": taxi_map_url}])
            reply_markup = {"inline_keyboard": mgr_kb} if mgr_kb else None

            sent = False
            if order_photo:
                sent = _send_telegram_photo(mgr.telegram_chat_id, order_photo, mgr_text, reply_markup=reply_markup, bot_token=store_bot_token)
            if not sent:
                _send_telegram_message(mgr.telegram_chat_id, mgr_text, reply_markup=reply_markup, bot_token=store_bot_token)


def notify_order_receipt_uploaded(order: Order) -> None:
    """
    Вызывается, когда покупатель прикрепил чек об оплате на сайте.
    Только в этот момент магазин узнает о заказе (не беспокоим продавцов пустыми заказами без оплаты).
    Отправляет полный состав заказа вместе с фото чека владельцу и менеджерам.
    """
    store = order.store
    customer = order.customer
    owner_chat_id = ensure_store_telegram_chat_id(store)

    client_display = customer.full_name or "Покупатель"
    if customer.telegram_username:
        client_display += f" (@{customer.telegram_username.lstrip('@')})"

    card_info = store.payout_card or "Ваша карта"
    if store.payout_card_holder:
        card_info += f" ({store.payout_card_holder})"

    # Доставка и координаты
    taxi_map_url = None
    if order.delivery_latitude and order.delivery_longitude:
        taxi_map_url = f"https://yandex.com/maps/?rtext=~{order.delivery_latitude},{order.delivery_longitude}&rtt=auto"

    # Ответственный менеджер
    active_managers = list(store.managers.filter(is_active=True))
    assigned_mgr = None
    for mgr in active_managers:
        if mgr.telegram_chat_id:
            assigned_mgr = mgr
            break
    if not assigned_mgr and active_managers:
        assigned_mgr = active_managers[0]
    if assigned_mgr and not order.assigned_manager:
        order.assigned_manager = assigned_mgr
        order.save(update_fields=["assigned_manager"])

    mgr_display = assigned_mgr.display_contact if assigned_mgr else None

    # 1. ТЕКСТ ДЛЯ ВЛАДЕЛЬЦА (с фото чека и кнопками подтверждения)
    if owner_chat_id:
        owner_lang = _get_store_lang(store)
        owner_items = _build_items_text(order, owner_lang)
        owner_delivery = _build_delivery_text(order, owner_lang)
        owner_text = _nt(
            owner_lang, 'notif_receipt_owner',
            order_id=order.id, store=store.name,
            total=f"{int(order.total_price):,}",
            card_info=card_info, client=client_display,
            phone=customer.phone_number,
            delivery=owner_delivery, items=owner_items,
        )
        owner_reply_markup = {
            "inline_keyboard": [
                [
                    {"text": _nt(owner_lang, 'btn_confirm_pay'), "callback_data": f"confirm_pay_{order.id}"},
                    {"text": _nt(owner_lang, 'btn_reject_pay'), "callback_data": f"reject_pay_{order.id}"}
                ]
            ]
        }
        store_bot_token = store.telegram_bot_token if (store.telegram_bot_token and store.telegram_bot_active) else None
        sent = False
        if order.payment_receipt:
            sent = _send_telegram_photo(owner_chat_id, order.payment_receipt, owner_text, reply_markup=owner_reply_markup, bot_token=store_bot_token)
        if not sent:
            _send_telegram_message(owner_chat_id, owner_text, reply_markup=owner_reply_markup, bot_token=store_bot_token)

    # 2. ТЕКСТ ДЛЯ ВСЕХ АКТИВНЫХ МЕНЕДЖЕРОВ (с фото чека и маршрутом такси)
    for mgr in active_managers:
        if mgr.telegram_chat_id and mgr.telegram_chat_id != owner_chat_id:
            mgr_lang = _get_mgr_lang(mgr)
            mgr_items = _build_items_text(order, mgr_lang)
            mgr_delivery = _build_delivery_text(order, mgr_lang)
            mgr_text = _nt(
                mgr_lang, 'notif_receipt_mgr',
                order_id=order.id, store=store.name,
                total=f"{int(order.total_price):,}",
                client=client_display, phone=customer.phone_number,
                delivery=mgr_delivery, items=mgr_items,
            )
            mgr_kb = [
                [
                    {"text": _nt(mgr_lang, 'btn_confirm_pay'), "callback_data": f"confirm_pay_{order.id}"},
                    {"text": _nt(mgr_lang, 'btn_reject_pay'), "callback_data": f"reject_pay_{order.id}"}
                ]
            ]
            if taxi_map_url:
                mgr_kb.append([{"text": _nt(mgr_lang, 'btn_taxi_yandex'), "url": taxi_map_url}])
            mgr_reply_markup = {"inline_keyboard": mgr_kb}

            sent = False
            if order.payment_receipt:
                sent = _send_telegram_photo(mgr.telegram_chat_id, order.payment_receipt, mgr_text, reply_markup=mgr_reply_markup, bot_token=store_bot_token)
            if not sent:
                _send_telegram_message(mgr.telegram_chat_id, mgr_text, reply_markup=mgr_reply_markup, bot_token=store_bot_token)


def notify_seller_about_order(order: Order) -> None:
    """
    Отправляет уведомление об оплаченном заказе владельцу и менеджеру.
    - В 1 сообщении: фото всех товаров (коллаж если 2-3 вещи), состав (размеры, цвета),
      псевдоним покупателя, координаты и точка такси, кнопки действий.
    - Владельцу отправляется сообщение о том, что заказ поступил и передан менеджеру (с указанием псевдонима менеджера).
    - Покупателю отправляется подтверждение оплаты с контактами менеджера.
    """
    if order.status != Order.Status.PAID:
        logger.info("Заказ #%s еще не оплачен (статус %s). Уведомление продавцу не отправляется.", order.id, order.status)
        return

    store = order.store
    customer = order.customer

    # Имя и псевдоним клиента
    client_display = customer.full_name or "Клиент"
    if customer.telegram_username:
        client_display += f" (@{customer.telegram_username.lstrip('@')})"

    # Находим ответственного менеджера магазина
    active_managers = list(store.managers.filter(is_active=True))
    assigned_mgr = None
    for mgr in active_managers:
        if mgr.telegram_chat_id:
            assigned_mgr = mgr
            break
    if not assigned_mgr and active_managers:
        assigned_mgr = active_managers[0]

    if assigned_mgr and not order.assigned_manager:
        order.assigned_manager = assigned_mgr
        order.save(update_fields=["assigned_manager"])

    mgr_display = assigned_mgr.display_contact if assigned_mgr else "Администратор"

    # Данные доставки и Яндекс Такси
    taxi_map_url = None
    if order.delivery_latitude and order.delivery_longitude:
        taxi_map_url = f"https://yandex.com/maps/?rtext=~{order.delivery_latitude},{order.delivery_longitude}&rtt=auto"

    pay_method_label = "⚡ Авто-эквайринг (Оплачено онлайн)" if order.payment_method in ["auto_acquiring", "click"] else "💳 Перевод на карту продавца (Чек проверен)"
    if order.payment_transaction_id:
        pay_method_label += f" [ID: {order.payment_transaction_id}]"

    # Готовим фото (одно или аккуратный коллаж из 2-3 фото)
    order_photo = _prepare_order_photo(order)

    def _build_card_reply_markup(lang: str):
        inline_keyboard = [
            [
                {"text": _nt(lang, 'btn_track_delivery'), "callback_data": f"track_{order.id}"},
                {"text": _nt(lang, 'btn_mark_delivered'), "callback_data": f"delivered_{order.id}"}
            ]
        ]
        if taxi_map_url:
            inline_keyboard.append([{"text": _nt(lang, 'btn_taxi_yandex'), "url": taxi_map_url}])
        elif order.delivery_map_url:
            inline_keyboard.append([{"text": _nt(lang, 'btn_open_map'), "url": order.delivery_map_url}])
        return {"inline_keyboard": inline_keyboard}

    store_bot_token = store.telegram_bot_token if (store.telegram_bot_token and store.telegram_bot_active) else None

    def _send_single_card(target_chat_id, lang: str):
        """Отправляет ровно 1 сообщение с фото, описанием и кнопками."""
        card_items = _build_items_text(order, lang)
        card_delivery = _build_delivery_text(order, lang)
        card_markup = _build_card_reply_markup(lang)
        method_label = _nt(lang, 'pay_method_auto') if order.payment_method in ["auto_acquiring", "click"] else _nt(lang, 'pay_method_card')
        if order.payment_transaction_id:
            method_label += f" [ID: {order.payment_transaction_id}]"
        order_card_text = _nt(
            lang, 'notif_card_paid_single',
            order_id=order.id,
            pay_method=method_label,
            items=card_items,
            total=f"{int(order.total_price):,}",
            client=client_display,
            phone=customer.phone_number,
            delivery=card_delivery,
        )
        sent = False
        if order_photo:
            sent = _send_telegram_photo(target_chat_id, order_photo, order_card_text, reply_markup=card_markup, bot_token=store_bot_token)
        if not sent:
            _send_telegram_message(target_chat_id, order_card_text, reply_markup=card_markup, bot_token=store_bot_token)

    # 1. Если у магазина есть менеджер:
    if assigned_mgr and assigned_mgr.telegram_chat_id:
        mgr_lang = _get_mgr_lang(assigned_mgr)
        # Менеджер получает единую интерактивную карточку заказа с кнопками и фото
        _send_single_card(assigned_mgr.telegram_chat_id, mgr_lang)

        # Владелец получает уведомление о том, что заказ оплачен и передан его менеджеру
        if store.telegram_chat_id and store.telegram_chat_id != assigned_mgr.telegram_chat_id:
            owner_lang = _get_store_lang(store)
            owner_items = _build_items_text(order, owner_lang)
            owner_method = _nt(owner_lang, 'pay_method_auto') if order.payment_method in ["auto_acquiring", "click"] else _nt(owner_lang, 'pay_method_card')
            if order.payment_transaction_id:
                owner_method += f" [ID: {order.payment_transaction_id}]"
            owner_text = _nt(
                owner_lang, 'notif_paid_owner_has_mgr',
                order_id=order.id,
                pay_method=owner_method,
                mgr_display=mgr_display,
                mgr_name=assigned_mgr.name,
                items=owner_items,
                total=f"{int(order.total_price):,}",
                client=client_display,
                phone=customer.phone_number,
                address=order.delivery_address or _nt(owner_lang, 'delivery_pending'),
            )
            _send_telegram_message(store.telegram_chat_id, owner_text, bot_token=store_bot_token)
    else:
        # Если менеджера нет — владелец получает единую карточку заказа сам
        if store.telegram_chat_id:
            owner_lang = _get_store_lang(store)
            _send_single_card(store.telegram_chat_id, owner_lang)

    # 2. Уведомляем покупателя об успешной оплате и передаче заказа менеджеру
    notify_customer_about_payment(order)


def notify_customer_about_payment(order: Order) -> None:
    """
    Отправляет покупателю подтверждение оплаты заказа в Telegram:
    - Сумма и статус оплаты
    - Название магазина и состав
    - Псевдоним (@username) ответственного менеджера для связи
    - Напоминание, что ссылка на отслеживание такси придёт сюда
    """
    customer = order.customer
    chat_id = customer.telegram_chat_id
    if not chat_id:
        return

    store = order.store
    assigned_mgr = order.assigned_manager or store.managers.filter(is_active=True).first()
    if assigned_mgr and assigned_mgr.telegram_username:
        seller_contact = f"@{assigned_mgr.telegram_username.lstrip('@')}"
    elif store.owner_telegram_username:
        seller_contact = f"@{store.owner_telegram_username.lstrip('@')}"
    elif store.owner and store.owner.username:
        seller_contact = f"@{store.owner.username}"
    else:
        seller_contact = f"@{store.telegram_bot_username or 'поддержка'}"

    cust_lang = getattr(order.customer, "language", None) or _get_customer_lang(chat_id)
    items_text = _build_items_text(order, cust_lang)

    text = _nt(
        cust_lang, 'notif_customer_paid',
        order_id=order.id,
        store=store.name,
        total=f"{int(order.total_price):,}",
        items=items_text,
        seller_contact=seller_contact,
    )

    if order.branch:
        dist_km = order.get_dispatch_distance_km()
        dist_s = f" ({dist_km} km)" if dist_km is not None else ""
        if cust_lang == "uz":
            text += f"\n\n📍 *Eng yaqin filial:* «{order.branch.name}»{dist_s}\nBuyurtma tezroq yetib borishi uchun aynan shu filialdan tayyorlanmoqda! 🚀"
        elif cust_lang == "en":
            text += f"\n\n📍 *Nearest dispatch branch:* «{order.branch.name}»{dist_s}\nBeing prepared at this branch for fastest delivery! 🚀"
        else:
            text += f"\n\n📍 *Ближайший филиал:* «{order.branch.name}»{dist_s}\nЗаказ собирается в этом филиале для максимально быстрой доставки! 🚀"

    store_bot_token = store.telegram_bot_token if (store.telegram_bot_token and store.telegram_bot_active) else None
    _send_telegram_message(chat_id, text, bot_token=store_bot_token)


def notify_customer_about_delivery(order: Order) -> None:
    """
    Отправляет клиенту уведомление что заказ передан курьеру Яндекс Такси.
    Включает состав заказа, ссылку на онлайн-отслеживание такси и контакт менеджера.
    """
    customer = order.customer
    chat_id = customer.telegram_chat_id
    if not chat_id:
        return

    store = order.store
    cust_lang = getattr(order.customer, "language", None) or _get_customer_lang(chat_id)

    reply_markup = None
    if order.tracking_url:
        text = _nt(
            cust_lang, 'notif_customer_delivery',
            order_id=order.id,
            store=store.name,
            tracking_url=order.tracking_url,
        )
        reply_markup = {
            "inline_keyboard": [
                [{"text": _nt(cust_lang, 'btn_taxi_yandex'), "url": order.tracking_url}]
            ]
        }
    else:
        text = _nt(
            cust_lang, 'notif_customer_delivery_no_link',
            order_id=order.id,
            store=store.name,
        )

    if order.branch:
        dist_km = order.get_dispatch_distance_km()
        dist_s = f" ({dist_km} km)" if dist_km is not None else ""
        if cust_lang == "uz":
            text += f"\n\n🏢 Jo'natilgan filial: «{order.branch.name}»{dist_s}"
        elif cust_lang == "en":
            text += f"\n\n🏢 Dispatched from branch: «{order.branch.name}»{dist_s}"
        else:
            text += f"\n\n🏢 Отправлено из филиала: «{order.branch.name}»{dist_s}"

    store_bot_token = store.telegram_bot_token if (store.telegram_bot_token and store.telegram_bot_active) else None
    _send_telegram_message(chat_id, text, reply_markup=reply_markup, bot_token=store_bot_token)


def notify_superadmin_about_new_store(store: Store) -> None:
    """Уведомляет основателя (@abdvoris07) в Telegram о новой заявке на открытие магазина."""
    ps = PlatformSettings.get_settings()
    chat_id = ps.superadmin_telegram_chat_id

    # Если chat_id еще не сохранен в настройках, используем подтвержденный chat_id основателя @abdvoris07
    if not chat_id:
        abdvoris_cust = Customer.objects.filter(telegram_username__iexact="abdvoris07").first()
        if abdvoris_cust and abdvoris_cust.telegram_chat_id:
            chat_id = abdvoris_cust.telegram_chat_id
            ps.superadmin_telegram_chat_id = chat_id
            ps.save(update_fields=["superadmin_telegram_chat_id"])
        else:
            chat_id = "1821219115"
            ps.superadmin_telegram_chat_id = chat_id
            ps.save(update_fields=["superadmin_telegram_chat_id"])

    if not chat_id:
        logger.warning("Не удалось определить chat_id основателя для отправки заявки на модерацию.")
        return

    ig_link = f"https://instagram.com/{store.instagram_username}"
    tg_user = f"@{store.owner_telegram_username.lstrip('@')}" if store.owner_telegram_username else "Не указан"

    text = (
        f"🔔 НОВАЯ ЗАЯВКА НА ОТКРЫТИЕ МАГАЗИНА!\n\n"
        f"🏬 Магазин: «{store.name}»\n"
        f"👤 Логин владельца: {store.owner.username}\n"
        f"📱 Телефон: {store.phone_number}\n"
        f"📸 Instagram: @{store.instagram_username}\n"
        f"💬 Telegram владельца: {tg_user}\n"
        f"📦 Ниша: {store.get_niche_display()}\n"
        f"💳 Модель: SaaS Подписка ({int(store.current_tier_fee):,} сум/мес, 0% комиссии)\n\n"
        f"👇 Изучите анкету продавца и подтвердите создание:"
    )

    inline_keyboard = [
        [
            {"text": "✅ Одобрить и создать", "callback_data": f"sa_approve_{store.id}"},
            {"text": "❌ Отклонить", "callback_data": f"sa_reject_{store.id}"}
        ],
        [
            {"text": "🔍 Панель администратора", "url": f"{settings.SITE_URL}/platform-admin/?q={store.name}"}
        ]
    ]
    reply_markup = {"inline_keyboard": inline_keyboard}
    _send_telegram_message(chat_id, text, reply_markup=reply_markup)


def notify_seller_about_approval(store: Store) -> None:
    """Уведомляет продавца в Telegram об одобрении его магазина администрацией."""
    chat_id = store.telegram_chat_id
    if not chat_id:
        return
    text = (
        f"🎉 ПОЗДРАВЛЯЕМ! Ваш магазин «{store.name}» успешно проверен и одобрен!\n\n"
        f"✅ Теперь ваш магазин активен и готов к приёму заказов.\n"
        f"🛍 Ваша витрина доступна покупателям.\n"
        f"📊 Перейдите в личный кабинет для добавления товаров и управления."
    )
    reply_markup = {
        "inline_keyboard": [
            [{"text": "📊 Панель управления", "web_app": {"url": f"{settings.SITE_URL}/dashboard/?tg_auth={store.telegram_link_token}" if store.telegram_link_token else f"{settings.SITE_URL}/dashboard/"}}],
            [{"text": "🛍 Моя витрина", "web_app": {"url": f"{settings.SITE_URL}/ru/?store_id={store.id}"}}]
        ]
    }
    _send_telegram_message(chat_id, text, reply_markup=reply_markup)


def _reverse_geocode(lat, lon) -> str:
    """
    Обратное геокодирование: координаты → текстовый адрес.
    Использует Яндекс Geocoder API если есть ключ, иначе пустую строку.
    """
    api_key = getattr(settings, 'YANDEX_MAPS_API_KEY', '')
    if not api_key:
        return ""

    try:
        resp = requests.get(
            "https://geocode-maps.yandex.ru/1.x/",
            params={
                "apikey": api_key,
                "geocode": f"{lon},{lat}",
                "format": "json",
                "results": 1,
                "lang": "ru_RU",
            },
            timeout=5,
        )
        data = resp.json()
        geo_obj = (
            data.get("response", {})
            .get("GeoObjectCollection", {})
            .get("featureMember", [{}])[0]
            .get("GeoObject", {})
            .get("metaDataProperty", {})
            .get("GeocoderMetaData", {})
            .get("text", "")
        )
        return geo_obj
    except Exception:
        logger.warning("Reverse geocoding failed for %s, %s", lat, lon)
        return ""



# ---------------------------------------------------------------------------
# Мультиязычные тексты для Telegram-бота
# ---------------------------------------------------------------------------

BOT_TEXTS = {
    "ru": {
        "welcome_buyer": "Добро пожаловать! Здесь можно посмотреть каталог и оформить заказ:",
        "welcome_seller": "🏪 С возвращением, магазин {name}!\n\nКакую панель открыть?",
        "btn_dashboard": "📊 Панель управления",
        "btn_storefront": "🛍 Моя витрина",
        "taxi_warning_prompt": (
            "⚠️ ВАЖНО: Продавец в этой локации закажет такси, поэтому вы должны отправить её максимально точно! 📍\n\n"
            "Пожалуйста, нажмите кнопку ниже, чтобы отправить текущую геопозицию:"
        ),
        "location_received": (
            "Локация получена ✅\n\n"
            "🏢 Напишите, пожалуйста, точный ориентир буквами для водителя такси:\n"
            "(номер подъезда, этаж, домофон или заметная вывеска/магазин рядом)"
        ),
        "landmark_received": "Ориентир сохранён! ✅\n\nТеперь отправьте ваш номер телефона, чтобы курьер мог позвонить при доставке 📱",
        "btn_send_phone": "📱 Отправить мой номер",
        "order_ready": "🎉 Заказ #{order_id} успешно оформлен!\n\nИтого к оплате: {total} сум.\n\nНажмите кнопку ниже, чтобы перевести деньги с карты:",
        "btn_pay": "💳 Оплатить",
        "product_intro": "Отлично! Вот этот товар — можно сразу выбрать размер и оплатить:",
        "store_intro": "Добро пожаловать в каталог магазина! 🛍 Выбирайте ниже:",
        "seller_linked": "✅ Уведомления подключены! Магазин: {name}\n\nСюда будут приходить уведомления о каждом оплаченном заказе.",
        "link_invalid": "Ссылка недействительна. Возьмите новую ссылку в своём кабинете продавца.",
        "manager_linked": "✅ Вы успешно подключены как менеджер магазина '{store}' (Имя: {name})!\n\nСюда будут приходить все новые оплаченные заказы с подробностями доставки.",
        "manager_link_invalid": "Ссылка для подключения менеджера недействительна или устарела.",
        # --- Уведомления о заказах ---
        "notif_new_order": "🔔 НОВЫЙ ЗАКАЗ #{order_id} (Ожидает перевода)!\n\n🏪 Магазин: «{store}»\n💰 Сумма к получению: {total} сум (100% на карту)\n💳 Ваша карта: {card_info}\n👤 Покупатель: {client}\n📱 Телефон: {phone}\n{delivery}\n\n📦 Состав заказа:\n{items}\n\n⏳ Статус: Клиент переводит сумму на вашу карту. Ожидается поступление.\n👥 Сборка: {manager_line}\n\nПроверьте поступление перевода в Payme/Click. Как только деньги поступят, нажмите:",
        "notif_receipt_owner": "🔔 НОВЫЙ ЗАКАЗ #{order_id}! (ЧЕК ПРИКРЕПЛЁН)\n\n🏪 Магазин: «{store}»\n💰 Сумма: {total} сум (100% на карту)\n💳 Ваша карта: {card_info}\n👤 Покупатель: {client}\n📱 Телефон: {phone}\n{delivery}\n\n📦 Состав заказа:\n{items}\n\n🧾 Клиент перевёл оплату и прикрепил чек (см. фото). Проверьте поступление и нажмите:",
        "notif_receipt_mgr": "📦 НОВЫЙ ЗАКАЗ #{order_id} НА СБОРКУ! (ЧЕК ПРИКРЕПЛЁН)\n\n🏪 Магазин: «{store}»\n🛍 Состав товаров:\n{items}\n\n💵 Сумма: {total} сум\n👤 Покупатель: {client}\n📱 Телефон: {phone}\n{delivery}\n\n🧾 Клиент перевёл оплату и прикрепил чек (см. фото). Проверьте чек и подтвердите оплату, либо дождитесь подтверждения от владельца.\nКак только оплата подтверждена, отправляйте такси 🚕",
        "notif_paid_owner_has_mgr": "💰 ОПЛАЧЕН новый заказ #{order_id}!\n\n💳 Оплата: {pay_method}\n👨‍💼 Заказ передан в обработку вашему менеджеру:\n👉 {mgr_display} ({mgr_name})\n\n📦 Состав заказа:\n{items}\n\n💵 Сумма: {total} сум (Оплачено ✅)\n👤 Покупатель: {client}\n📱 Телефон: {phone}\n📍 Доставка: {address}\n\nМенеджер уже получил заказ с фото и локацией и занимается отправкой через Яндекс Такси 🚕",
        "notif_customer_paid": "✅ Перевод успешно подтверждён продавцом! Оплата принята.\n\nЗаказ #{order_id} оформлен.\n\n🏬 Магазин: «{store}»\n💵 Сумма оплаты: {total} сум (Оплачено ✅)\n\n📦 Ваш заказ:\n{items}\n\n👨‍💼 Ваш менеджер / продавец: {seller_contact}\n\n🚕 Заказ передан на сборку и доставку. Как только курьер/такси выедет — ссылка на отслеживание или фото доставки придёт сюда!",
        "notif_customer_delivery": "🚕 Ваш заказ #{order_id} из «{store}» передан курьеру и уже в пути!\n\n🔗 Ссылка на отслеживание такси:\n{tracking_url}\n\nВодитель везёт ваш заказ. Ожидайте — скоро будет у вас! 📦",
        "notif_customer_delivery_no_link": "🚕 Ваш заказ #{order_id} из «{store}» передан курьеру!\n\nОжидайте доставку. Продавец отправил ваш заказ! 📦",
        "btn_confirm_pay": "✅ Подтвердить оплату",
        "btn_reject_pay": "❌ Отклонить",
        "btn_taxi_yandex": "🚕 Открыть точку такси (Яндекс)",
        "btn_track_delivery": "🚚 Отправить ссылку на доставку",
        "btn_mark_delivered": "✅ Заказ доставлен",
        "item_color": "Цвет",
        "item_size": "Размер",
        "item_pcs": "шт.",
        "mgr_assigned": "Передано менеджеру {mgr}",
        "mgr_none": "Менеджеров нет, заказ для вас",
        "delivery_address": "📍 Адрес доставки: {addr}",
        "delivery_gps": "🗺 GPS: {lat}, {lon}",
        "delivery_landmark": "🏢 Ориентир для такси: {lm}",
        "delivery_pending": "📍 Адрес уточняется",
        "notif_order_mgr_new": "📦 НОВЫЙ ЗАКАЗ #{order_id} НА СБОРКУ!\n\n🏪 Магазин: «{store}»\n🛍 Состав товаров:\n{items}\n\n💵 Сумма: {total} сум\n👤 Покупатель: {client}\n📱 Телефон: {phone}\n{delivery}\n\n⏳ Внимание: Клиент переводит оплату на карту владельца. Пожалуйста, соберите и упакуйте товары! Как только владелец подтвердит получение денег, вам придёт команда на отправку курьера/такси 🚕",
        "track_prompt": "🚚 Отправьте ссылку на Яндекс.Доставку или фото/скриншот отправки (такси, водитель, товар) для заказа #{order_id} в ответном сообщении 🔗📸",
        "track_wait": "Ожидаем ссылку",
        "choose_lang": "🌐 Выберите язык / Tilni tanlang / Choose language:",
        "lang_set": "✅ Язык изменён на Русский",
        "checkout_card_info": "🎉 *Заказ #{order_id} успешно сформирован!*\n\n🏪 Магазин: *{store}*\n💰 Сумма к оплате: *{total} сум*\n\n💳 *РЕКВИЗИТЫ КАРТЫ ПРОДАВЦА ДЛЯ ОПЛАТЫ:*\n👉 `{card_number}`{card_holder_line}\n\n⚠️ Пожалуйста, переведите точную сумму *{total} сум* на указанную карту через любое приложение (Payme, Click, банк).\n\n📸 *После перевода отправьте сюда фото или скриншот чека* — продавец подтвердит получение и отправит заказ на такси! ✨",
        "card_holder_prefix": "\n👤 Владелец: *{holder}*",
        "card_number_pending": "Уточняется у продавца",
        "receipt_photo_required": "⚠️ Пожалуйста, прикрепите и отправьте именно фото или скриншот чека об оплате! 📸\n\nБез подтверждающего скриншота магазин не сможет проверить платёж по заказу #{order_id} и начать сборку.",
        "receipt_received_customer": "🧾 Чек по заказу #{order_id} принят и передан магазину «{store}»!\nВладелец и менеджеры проверяют поступление средств и готовят заказ к отправке на такси. Спасибо! ✨",
        "pay_confirmed_toast": "✅ Оплата заказа #{order_id} подтверждена!",
        "pay_confirmed_seller_msg": "✅ Оплата заказа #{order_id} успешно подтверждена!\nСтатус: 'Оплачен (В сборке)'. Товар готов к отправке на такси 🚕",
        "pay_rejected_toast": "❌ Оплата заказа #{order_id} отклонена.",
        "pay_rejected_seller_msg": "❌ Оплата заказа #{order_id} отклонена.",
        "pay_rejected_customer_msg": "⚠️ Продавец магазина «{store}» не обнаружил поступление средств по заказу #{order_id}.\nПожалуйста, свяжитесь с магазином по телефону: {phone}.",
        "order_opened_header": "🛒 Заказ #{order_id} открыт!\n\n",
        "btn_send_location": "📍 Отправить локацию для такси",
        "notif_card_paid_single": "💰 ОПЛАЧЕН новый заказ #{order_id}!\n\n💳 Оплата: {pay_method}\n📦 Состав:\n{items}\n\n💵 Сумма: {total} сум (Оплачено ✅)\n👤 Клиент: {client}\n📱 Телефон: {phone}\n\n{delivery}\n\n👇 Подготовьте товар и отправьте такси кнопками ниже:",
        "pay_method_auto": "⚡ Авто-эквайринг (Оплачено онлайн)",
        "pay_method_card": "💳 Перевод на карту продавца (Чек проверен)",
        "btn_open_map": "📍 Открыть на карте",
        "btn_global_market": "🌍 Глобальный маркет BrainMart",
        "btn_catalog": "🛍 Каталог товаров",
    },
    "uz": {
        "welcome_buyer": "Xush kelibsiz! Bu yerda katalogni ko'rib, buyurtma berish mumkin:",
        "welcome_seller": "🏪 Xush kelibsiz, {name} do'koni!\n\nQaysi panelni ochmoqchisiz?",
        "btn_dashboard": "📊 Boshqaruv paneli",
        "btn_storefront": "🛍 Mening vitrinam",
        "taxi_warning_prompt": (
            "⚠️ MUHIM: Sotuvchi ushbu manzilga taksi buyurtma qiladi, shuning uchun joylashuvingizni aniq yuborishingiz kerak! 📍\n\n"
            "Joylashuvni yuborish uchun quyidagi tugmani bosing:"
        ),
        "location_received": (
            "Joylashuv qabul qilindi ✅\n\n"
            "🏢 Taksi haydovchisi uchun aniq mo'ljalni matn bilan yozing:\n"
            "(podyezd raqami, qavat, domofon yoki yaqin atrofidagi mo'ljal)"
        ),
        "landmark_received": "Mo'ljal saqlandi! ✅\n\nEndi haydovchi yetkazib berishda qo'ng'iroq qilishi uchun telefon raqamingizni yuboring 📱",
        "btn_send_phone": "📱 Raqamimni yuborish",
        "order_ready": "🎉 #{order_id}-buyurtma muvaffaqiyatli rasmiylashtirildi!\n\nTo'lov summasi: {total} so'm.\n\nTo'lash uchun quyidagi tugmani bosing:",
        "btn_pay": "💳 To'lash",
        "product_intro": "Ajoyib! Mana bu mahsulot — o'lchamni tanlang va to'lang:",
        "store_intro": "Do'kon katalogiga xush kelibsiz! 🛍 Quyida tanlang:",
        "seller_linked": "✅ Bildirishnomalar ulandi! Do'kon: {name}\n\nHar bir to'langan buyurtma haqida xabar keladi.",
        "link_invalid": "Havola yaroqsiz. Do'konchi kabinetidan yangi havolani oling.",
        "manager_linked": "✅ Siz '{store}' do'konining menejeri sifatida ulandi (Ismi: {name})!\n\nBarcha yangi to'langan buyurtmalar bu yerga keladi.",
        "manager_link_invalid": "Menejer ulanish havolasi yaroqsiz yoki eskirgan.",
        # --- Buyurtma bildirishnomalari ---
        "notif_new_order": "🔔 YANGI BUYURTMA #{order_id} (To'lov kutilmoqda)!\n\n🏪 Do'kon: «{store}»\n💰 Qabul qilinadigan summa: {total} so'm (100% sizning kartangizga)\n💳 Sizning kartangiz: {card_info}\n👤 Xaridor: {client}\n📱 Telefon: {phone}\n{delivery}\n\n📦 Buyurtma tarkibi:\n{items}\n\n⏳ Holat: Mijoz summani kartangizga o'tkazmoqda. Pul tushumini kuting.\n👥 Yig'ish: {manager_line}\n\nPayme/Click orqali pul tushumini tekshiring. Pul tushgach tugmani bosing:",
        "notif_receipt_owner": "🔔 YANGI BUYURTMA #{order_id}! (CHEK BIRIKTIRILGAN)\n\n🏪 Do'kon: «{store}»\n💰 Summa: {total} so'm (100% sizning kartangizga)\n💳 Sizning kartangiz: {card_info}\n👤 Xaridor: {client}\n📱 Telefon: {phone}\n{delivery}\n\n📦 Buyurtma tarkibi:\n{items}\n\n🧾 Mijoz to'lov qildi va chekni biriktirdi (rasmga qarang). Pul tushumini tekshiring va tugmani bosing:",
        "notif_receipt_mgr": "📦 YIG'ISH UCHUN YANGI BUYURTMA #{order_id}! (CHEK BIRIKTIRILGAN)\n\n🏪 Do'kon: «{store}»\n🛍 Mahsulotlar:\n{items}\n\n💵 Summa: {total} so'm\n👤 Xaridor: {client}\n📱 Telefon: {phone}\n{delivery}\n\n🧾 Mijoz to'lov qildi va chekni biriktirdi (rasmga qarang). Chekni tekshiring va to'lovni tasdiqlang yoki egasining tasdiqini kuting.\nTo'lov tasdiqlangach, taksi yuboring 🚕",
        "notif_paid_owner_has_mgr": "💰 YANGI BUYURTMA #{order_id} TO'LANDI!\n\n💳 To'lov: {pay_method}\n👨‍💼 Buyurtma menejeringizga topshirildi:\n👉 {mgr_display} ({mgr_name})\n\n📦 Buyurtma tarkibi:\n{items}\n\n💵 Summa: {total} so'm (To'landi ✅)\n👤 Xaridor: {client}\n📱 Telefon: {phone}\n📍 Yetkazish: {address}\n\nMenejer buyurtmani rasm va joylashuv bilan oldi va Yandex Taksi orqali jo'natmoqda 🚕",
        "notif_customer_paid": "✅ Pul o'tkazmasi sotuvchi tomonidan muvaffaqiyatli tasdiqlandi! To'lov qabul qilindi.\n\nBuyurtma #{order_id} rasmiylashtirildi.\n\n🏬 Do'kon: «{store}»\n💵 To'lov summasi: {total} so'm (To'landi ✅)\n\n📦 Sizning buyurtmangiz:\n{items}\n\n👨‍💼 Sizning menejeringiz / sotuvchingiz: {seller_contact}\n\n🚕 Buyurtma yig'ilmoqda. Kuryer/taksi yo'lga chiqqach — kuzatish havolasi yoki yetkazish fotosi shu yerga keladi!",
        "notif_customer_delivery": "🚕 Sizning buyurtmangiz #{order_id} «{store}» do'konidan kuryerga topshirildi va yo'lda!\n\n🔗 Taksini kuzatish havolasi:\n{tracking_url}\n\nHaydovchi buyurtmangizni olib kelmoqda. Kuting — tez orada yetib boradi! 📦",
        "notif_customer_delivery_no_link": "🚕 Sizning buyurtmangiz #{order_id} «{store}» do'konidan kuryerga topshirildi!\n\nYetkazib berishni kuting. Sotuvchi buyurtmangizni jo'natdi! 📦",
        "btn_confirm_pay": "✅ To'lovni tasdiqlash",
        "btn_reject_pay": "❌ Bekor qilish",
        "btn_taxi_yandex": "🚕 Taksi nuqtasini ochish (Yandex)",
        "btn_track_delivery": "🚚 Yetkazish havolasini yuborish",
        "btn_mark_delivered": "✅ Buyurtma yetkazildi",
        "item_color": "Rangi",
        "item_size": "O'lchami",
        "item_pcs": "dona",
        "mgr_assigned": "Menejerga topshirildi: {mgr}",
        "mgr_none": "Menejerlar yo'q, buyurtma siz uchun",
        "delivery_address": "📍 Yetkazish manzili: {addr}",
        "delivery_gps": "🗺 GPS: {lat}, {lon}",
        "delivery_landmark": "🏢 Taksi uchun mo'ljal: {lm}",
        "delivery_pending": "📍 Manzil aniqlanmoqda",
        "notif_order_mgr_new": "📦 YIG'ISH UCHUN YANGI BUYURTMA #{order_id}!\n\n🏪 Do'kon: «{store}»\n🛍 Mahsulotlar:\n{items}\n\n💵 Summa: {total} so'm\n👤 Xaridor: {client}\n📱 Telefon: {phone}\n{delivery}\n\n⏳ Diqqat: Mijoz to'lovni eganing kartasiga o'tkazmoqda. Mahsulotlarni yig'ing va tayyorlang! Egasi pulni tasdiqlagach, kuryer/taksi yuborish buyrug'i keladi 🚕",
        "track_prompt": "🚚 #{order_id}-buyurtma uchun Yandex.Yetkazib berish havolasini yoki jo'natish fotosuratini (taksi, kuryer, posilka) javob xabarida yuboring 🔗📸",
        "track_wait": "Havola kutilmoqda",
        "choose_lang": "🌐 Выберите язык / Tilni tanlang / Choose language:",
        "lang_set": "✅ Til o'zbekchaga o'zgartirildi",
        "checkout_card_info": "🎉 *#{order_id}-buyurtma muvaffaqiyatli shakllantirildi!*\n\n🏪 Do'kon: *{store}*\n💰 To'lov summasi: *{total} so'm*\n\n💳 *TO'LOV UCHUN SOTUVCHINING KARTA REKVIZITLARI:*\n👉 `{card_number}`{card_holder_line}\n\n⚠️ Iltimos, istalgan ilova (Payme, Click, bank) orqali ko'rsatilgan kartaga aniq *{total} so'm* o'tkazing.\n\n📸 *O'tkazgach, to'lov cheki yoki skrinshotini shu yerga yuboring* — sotuvchi tushumni tasdiqlaydi va buyurtmani taksiga beradi! ✨",
        "card_holder_prefix": "\n👤 Karta egasi: *{holder}*",
        "card_number_pending": "Sotuvchidan aniqlanmoqda",
        "receipt_photo_required": "⚠️ Iltimos, to'lov chekining fotosurati yoki skrinshotini yuboring! 📸\n\nSkrinshotsiz do'kon #{order_id}-buyurtma bo'yicha to'lovni tekshira olmaydi va mahsulotni yig'ishni boshlay olmaydi.",
        "receipt_received_customer": "🧾 #{order_id}-buyurtma bo'yicha chek qabul qilindi va «{store}» do'koniga yuborildi!\nDo'kon egasi va menejerlar pul tushumini tekshirib, buyurtmani taksiga tayyorlamoqda. Rahmat! ✨",
        "pay_confirmed_toast": "✅ #{order_id}-buyurtma to'lovi tasdiqlandi!",
        "pay_confirmed_seller_msg": "✅ #{order_id}-buyurtma to'lovi muvaffaqiyatli tasdiqlandi!\nHolati: 'To'landi (Yig'ilmoqda)'. Mahsulot taksiga tayyor 🚕",
        "pay_rejected_toast": "❌ #{order_id}-buyurtma to'lovi bekor qilindi.",
        "pay_rejected_seller_msg": "❌ #{order_id}-buyurtma to'lovi bekor qilindi.",
        "pay_rejected_customer_msg": "⚠️ «{store}» do'koni #{order_id}-buyurtma bo'yicha pul tushumini aniqlay olmadi.\nIltimos, do'kon bilan bog'laning: {phone}.",
        "order_opened_header": "🛒 #{order_id}-buyurtma ochildi!\n\n",
        "btn_send_location": "📍 Taksi uchun lokatsiyani yuborish",
        "notif_card_paid_single": "💰 YANGI BUYURTMA #{order_id} TO'LANDI!\n\n💳 To'lov: {pay_method}\n📦 Mahsulotlar:\n{items}\n\n💵 Summa: {total} so'm (To'landi ✅)\n👤 Xaridor: {client}\n📱 Telefon: {phone}\n\n{delivery}\n\n👇 Mahsulotni tayyorlang va quyidagi tugmalar orqali taksi yuboring:",
        "pay_method_auto": "⚡ Avto-ekvayring (Onlayn to'langan)",
        "pay_method_card": "💳 Sotuvchi kartasiga o'tkazma (Chek tekshirildi)",
        "btn_open_map": "📍 Xaritada ochish",
        "btn_global_market": "🌍 BrainMart Global Bozori",
        "btn_catalog": "🛍 Tovarlar katalogi",
    },
    "en": {
        "welcome_buyer": "Welcome! Browse the catalog and place your order here:",
        "welcome_seller": "🏪 Welcome back, {name} store!\n\nWhich panel do you want to open?",
        "btn_dashboard": "📊 Dashboard",
        "btn_storefront": "🛍 My Storefront",
        "taxi_warning_prompt": (
            "⚠️ IMPORTANT: The seller will order a taxi to this location, so please send it accurately! 📍\n\n"
            "Press the button below to send your location:"
        ),
        "location_received": (
            "Location received ✅\n\n"
            "🏢 Please write a text landmark for the taxi driver:\n"
            "(entrance number, floor, intercom or nearby landmark)"
        ),
        "landmark_received": "Landmark saved! ✅\n\nNow please send your phone number so the courier can call upon delivery 📱",
        "btn_send_phone": "📱 Send my number",
        "order_ready": "🎉 Order #{order_id} has been placed!\n\nTotal: {total} UZS.\n\nPress the button below to pay with your card:",
        "btn_pay": "💳 Pay Now",
        "product_intro": "Great! Here is the product — select a size and pay:",
        "store_intro": "Welcome to the store catalog! 🛍 Browse below:",
        "seller_linked": "✅ Notifications connected! Store: {name}\n\nYou'll receive a notification for every paid order.",
        "link_invalid": "Invalid link. Get a new link from your seller dashboard.",
        "manager_linked": "✅ You are now connected as a manager of '{store}' store (Name: {name})!\n\nAll new paid orders with delivery details will come here.",
        "manager_link_invalid": "Manager connection link is invalid or expired.",
        # --- Order notifications ---
        "notif_new_order": "🔔 NEW ORDER #{order_id} (Awaiting transfer)!\n\n🏪 Store: «{store}»\n💰 Amount to receive: {total} UZS (100% to your card)\n💳 Your card: {card_info}\n👤 Customer: {client}\n📱 Phone: {phone}\n{delivery}\n\n📦 Order contents:\n{items}\n\n⏳ Status: Customer is transferring funds to your card. Awaiting receipt.\n👥 Fulfillment: {manager_line}\n\nCheck your Payme/Click for incoming transfer. Once received, press:",
        "notif_receipt_owner": "🔔 NEW ORDER #{order_id}! (RECEIPT ATTACHED)\n\n🏪 Store: «{store}»\n💰 Amount: {total} UZS (100% to your card)\n💳 Your card: {card_info}\n👤 Customer: {client}\n📱 Phone: {phone}\n{delivery}\n\n📦 Order contents:\n{items}\n\n🧾 Customer has paid and attached a receipt (see photo). Please verify and confirm:",
        "notif_receipt_mgr": "📦 NEW ORDER #{order_id} FOR FULFILLMENT! (RECEIPT ATTACHED)\n\n🏪 Store: «{store}»\n🛍 Products:\n{items}\n\n💵 Amount: {total} UZS\n👤 Customer: {client}\n📱 Phone: {phone}\n{delivery}\n\n🧾 Customer has paid and attached a receipt (see photo). Verify the receipt and confirm payment, or wait for owner confirmation.\nOnce payment is confirmed, dispatch taxi 🚕",
        "notif_paid_owner_has_mgr": "💰 ORDER #{order_id} PAID!\n\n💳 Payment: {pay_method}\n👨‍💼 Order assigned to your manager:\n👉 {mgr_display} ({mgr_name})\n\n📦 Order contents:\n{items}\n\n💵 Amount: {total} UZS (Paid ✅)\n👤 Customer: {client}\n📱 Phone: {phone}\n📍 Delivery: {address}\n\nManager has received the order with photos and location and is dispatching via Yandex Taxi 🚕",
        "notif_customer_paid": "✅ Payment successfully confirmed by seller! Transfer accepted.\n\nOrder #{order_id} confirmed.\n\n🏬 Store: «{store}»\n💵 Amount paid: {total} UZS (Paid ✅)\n\n📦 Your order:\n{items}\n\n👨‍💼 Your manager / seller: {seller_contact}\n\n🚕 Your order is being prepared. Once the courier/taxi departs — tracking link or dispatch photo will appear here!",
        "notif_customer_delivery": "🚕 Your order #{order_id} from «{store}» has been dispatched!\n\n🔗 Tracking link:\n{tracking_url}\n\nThe driver is on the way with your order. Expect delivery soon! 📦",
        "notif_customer_delivery_no_link": "🚕 Your order #{order_id} from «{store}» has been dispatched!\n\nPlease wait for delivery. Your order is on the way! 📦",
        "btn_confirm_pay": "✅ Confirm Payment",
        "btn_reject_pay": "❌ Reject",
        "btn_taxi_yandex": "🚕 Open taxi point (Yandex)",
        "btn_track_delivery": "🚚 Send delivery link",
        "btn_mark_delivered": "✅ Order delivered",
        "item_color": "Color",
        "item_size": "Size",
        "item_pcs": "pcs",
        "mgr_assigned": "Assigned to manager {mgr}",
        "mgr_none": "No managers, order is for you",
        "delivery_address": "📍 Delivery address: {addr}",
        "delivery_gps": "🗺 GPS: {lat}, {lon}",
        "delivery_landmark": "🏢 Taxi landmark: {lm}",
        "delivery_pending": "📍 Address pending",
        "notif_order_mgr_new": "📦 NEW ORDER #{order_id} FOR FULFILLMENT!\n\n🏪 Store: «{store}»\n🛍 Products:\n{items}\n\n💵 Amount: {total} UZS\n👤 Customer: {client}\n📱 Phone: {phone}\n{delivery}\n\n⏳ Note: Customer is transferring payment to the owner's card. Please pack the items! Once the owner confirms receipt, you'll get the dispatch command 🚕",
        "track_prompt": "🚚 Send the Yandex Delivery link for order #{order_id} in your reply message 🔗",
        "track_wait": "Awaiting link",
        "choose_lang": "🌐 Выберите язык / Tilni tanlang / Choose language:",
        "lang_set": "✅ Language changed to English",
        "checkout_card_info": "🎉 *Order #{order_id} has been created!*\n\n🏪 Store: *{store}*\n💰 Amount to pay: *{total} UZS*\n\n💳 *SELLER'S CARD DETAILS FOR PAYMENT:*\n👉 `{card_number}`{card_holder_line}\n\n⚠️ Please transfer the exact amount *{total} UZS* to this card via any banking app (Payme, Click, etc.).\n\n📸 *After transfer, send a photo or screenshot of the receipt here* — the seller will verify and dispatch your order via taxi! ✨",
        "card_holder_prefix": "\n👤 Cardholder: *{holder}*",
        "card_number_pending": "Pending seller confirmation",
        "receipt_photo_required": "⚠️ Please attach and send a photo or screenshot of your payment receipt! 📸\n\nWithout a receipt screenshot, the store cannot verify payment for order #{order_id} and start fulfillment.",
        "receipt_received_customer": "🧾 Receipt for order #{order_id} received and sent to «{store}» store!\nThe seller is verifying funds and preparing the order for taxi dispatch. Thank you! ✨",
        "pay_confirmed_toast": "✅ Payment for order #{order_id} confirmed!",
        "pay_confirmed_seller_msg": "✅ Payment for order #{order_id} confirmed!\nStatus: 'Paid (Fulfilling)'. Ready for taxi dispatch 🚕",
        "pay_rejected_toast": "❌ Payment for order #{order_id} rejected.",
        "pay_rejected_seller_msg": "❌ Payment for order #{order_id} rejected.",
        "pay_rejected_customer_msg": "⚠️ The seller of «{store}» store could not verify payment for order #{order_id}.\nPlease contact the store: {phone}.",
        "order_opened_header": "🛒 Order #{order_id} opened!\n\n",
        "btn_send_location": "📍 Send location for taxi",
        "notif_card_paid_single": "💰 NEW ORDER #{order_id} PAID!\n\n💳 Payment: {pay_method}\n📦 Items:\n{items}\n\n💵 Amount: {total} UZS (Paid ✅)\n👤 Customer: {client}\n📱 Phone: {phone}\n\n{delivery}\n\n👇 Prepare the items and dispatch taxi using buttons below:",
        "pay_method_auto": "⚡ Auto-acquiring (Paid online)",
        "pay_method_card": "💳 Card transfer (Receipt verified)",
        "btn_open_map": "📍 Open on map",
        "btn_global_market": "🌍 BrainMart Global Market",
        "btn_catalog": "🛍 Product catalog",
    },
}


def _get_customer_lang(chat_id: str) -> str:
    """Возвращает предпочтительный язык для chat_id (продавец, менеджер или покупатель). По умолчанию 'uz'."""
    store = Store.objects.filter(telegram_chat_id=str(chat_id)).first()
    if store and getattr(store, "telegram_notification_language", None):
        return store.telegram_notification_language
    mgr = StoreManager.objects.filter(telegram_chat_id=str(chat_id)).first()
    if mgr and getattr(mgr, "language", None):
        return mgr.language
    c = Customer.objects.filter(telegram_chat_id=str(chat_id)).order_by("-id").first()
    if c and getattr(c, "language", None):
        return c.language
    return "uz"


def _t(chat_id, key: str, **kwargs) -> str:
    """Возвращает переведённый текст для указанного chat_id и ключа."""
    lang = _get_customer_lang(chat_id)
    texts = BOT_TEXTS.get(lang, BOT_TEXTS["uz"])
    template = texts.get(key, BOT_TEXTS["uz"].get(key, key))
    return template.format(**kwargs) if kwargs else template


def _get_store_lang(store) -> str:
    """Возвращает язык уведомлений владельца магазина. По умолчанию 'uz'."""
    if not store:
        return 'uz'
    lang = getattr(store, 'telegram_notification_language', None)
    if lang in ('uz', 'ru', 'en'):
        return lang
    return 'uz'


def _get_mgr_lang(manager) -> str:
    """Возвращает язык уведомлений менеджера. По умолчанию 'uz'."""
    if not manager:
        return 'uz'
    lang = getattr(manager, 'language', None)
    if lang in ('uz', 'ru', 'en'):
        return lang
    return 'uz'


def _nt(lang: str, key: str, **kwargs) -> str:
    """Возвращает переведённый текст уведомления по коду языка и ключу."""
    if lang not in BOT_TEXTS:
        lang = "uz"
    texts = BOT_TEXTS.get(lang, BOT_TEXTS["uz"])
    template = texts.get(key, BOT_TEXTS["uz"].get(key, key))
    return template.format(**kwargs) if kwargs else template


def _build_items_text(order, lang: str) -> str:
    """Формирует строку состава заказа на нужном языке."""
    lines = []
    for item in order.items.select_related("variant__product").all():
        variant = item.variant
        color_str = f"{_nt(lang, 'item_color')}: {variant.color}" if variant.color else ""
        size_str = f"{_nt(lang, 'item_size')}: {variant.size}" if variant.size else ""
        details = " / ".join(filter(None, [color_str, size_str]))
        if details:
            details = f" ({details})"
        lines.append(f"• {variant.product.name}{details} — {item.quantity} {_nt(lang, 'item_pcs')}")
    return "\n".join(lines) or "—"


def _build_delivery_text(order, lang: str) -> str:
    """Формирует блок адреса доставки и филиала на нужном языке."""
    loc_lines = []

    # 1. Информация о филиале отгрузки и расстоянии
    branch = order.branch
    dist_km = order.get_dispatch_distance_km()
    if branch:
        dist_str = f" ({dist_km} км)" if dist_km is not None else ""
        if lang == "uz":
            dist_str_uz = f" ({dist_km} km)" if dist_km is not None else ""
            loc_lines.append(f"🏢 Jo'natuvchi filial: *«{branch.name}»*{dist_str_uz}")
            if branch.address:
                loc_lines.append(f"   📍 Filial manzili: {branch.address}")
        elif lang == "en":
            dist_str_en = f" ({dist_km} km)" if dist_km is not None else ""
            loc_lines.append(f"🏢 Dispatch branch: *«{branch.name}»*{dist_str_en}")
            if branch.address:
                loc_lines.append(f"   📍 Branch address: {branch.address}")
        else:
            loc_lines.append(f"🏢 Филиал отгрузки: *«{branch.name}»*{dist_str}")
            if branch.address:
                loc_lines.append(f"   📍 Адрес филиала: {branch.address}")

        if dist_km is not None:
            mins = order.get_estimated_taxi_mins()
            fare = order.get_estimated_taxi_fare()
            fare_str = f"{fare:,}".replace(",", " ")
            if lang == "uz":
                loc_lines.append(f"🚕 Taksi hisobi: ~{mins} daqiqa (taxminan {fare_str} so'm)")
            elif lang == "en":
                loc_lines.append(f"🚕 Taxi estimate: ~{mins} min (~{fare_str} UZS)")
            else:
                loc_lines.append(f"🚕 Оценка такси: ~{mins} мин (~{fare_str} сум)")

    if order.delivery_address:
        loc_lines.append(_nt(lang, 'delivery_address', addr=order.delivery_address))
    if order.delivery_latitude and order.delivery_longitude:
        loc_lines.append(_nt(lang, 'delivery_gps', lat=f"{order.delivery_latitude:.5f}", lon=f"{order.delivery_longitude:.5f}"))
    if order.delivery_landmark:
        loc_lines.append(_nt(lang, 'delivery_landmark', lm=order.delivery_landmark))

    # Анти-хитрость: умное напоминание продавцу, если клиент далеко (> 7 км) и у магазина всего 1 точка в системе
    if dist_km is not None and dist_km > 7.0 and order.store.branches.filter(is_active=True).count() <= 1:
        fare = order.get_estimated_taxi_fare()
        fare_str = f"{fare:,}".replace(",", " ")
        if lang == "uz":
            loc_lines.append(
                f"\n💡 *SOTUVCHI DIQQATIGA:* Mijoz yagona filialingizdan ancha uzoqda ({dist_km} km, taksi ~{fare_str} so'm). "
                f"Agar xaridorga yaqinroq do'koningiz bo'lsa — uni Boshqaruv panelida (Filiallar) qo'shing! "
                f"Ko'p filialli do'konlar 3 barobar ko'p sotadi, chunki taksi 12 000 so'mga 15 daqiqada boradi."
            )
        elif lang == "en":
            loc_lines.append(
                f"\n💡 *SELLER TIP:* Customer is far ({dist_km} km, taxi ~{fare_str} UZS). "
                f"If you have a physical shop near them, add it in your Dashboard (Branches) to cut delivery cost and time!"
            )
        else:
            loc_lines.append(
                f"\n💡 *СОВЕТ ПРОДАВЦУ:* Клиент находится далеко от вашего филиала ({dist_km} км, такси ~{fare_str} сум). "
                f"Если у вас есть магазин ближе к этому клиенту — добавьте его в панель (Филиалы)! "
                f"Магазины с несколькими точками продают в 3 раза чаще благодаря быстрой и дешевой доставке за 12 000 сум."
            )

    return "\n".join(loc_lines) if loc_lines else _nt(lang, 'delivery_pending')


class TelegramWebhookView(APIView):
    """Endpoint для Telegram Bot API."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # --- БЕЗОПАСНОСТЬ: Проверяем секретный токен от Telegram ---
        # При установке вебхука мы передаём secret_token, и Telegram
        # присылает его в каждом запросе в заголовке X-Telegram-Bot-Api-Secret-Token.
        # Если токен не совпадает — запрос от хакера, отклоняем.
        webhook_secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        if webhook_secret:
            incoming_secret = request.META.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
            if incoming_secret != webhook_secret:
                logger.warning("Telegram webhook: неверный secret_token — запрос отклонён")
                return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            self._handle(request)
        except Exception:
            logger.exception("Ошибка при обработке Telegram webhook")

        return Response(status=status.HTTP_200_OK)

    def _handle(self, request, bot_token=None):
        data = request.data

        # Обработка callback_query (нажатие инлайн-кнопок)
        callback = data.get("callback_query")
        if callback:
            self._handle_callback(callback, bot_token=bot_token)
            return

        message = data.get("message", {}) or {}
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text") or ""
        location = message.get("location")
        contact = message.get("contact")

        if not chat_id:
            return

        # 1. ПРОВЕРЯЕМ, ЖДЕМ ЛИ МЫ ОТ КЛИЕНТА ЛОКАЦИЮ ИЛИ НОМЕР
        state_data = cache.get(f"tg_state_{chat_id}")

        if state_data:
            state = state_data.get("state")
            order_id = state_data.get("order_id")

            order_obj = None
            if order_id:
                order_obj = Order.objects.select_related("store").filter(id=order_id).first()
            curr_token = bot_token
            if not curr_token and order_obj and order_obj.store.telegram_bot_token and order_obj.store.telegram_bot_active:
                curr_token = order_obj.store.telegram_bot_token
            if not curr_token:
                curr_token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)

            # Шаг 1: Клиент прислал локацию
            if state == "WAITING_LOCATION" and location:
                try:
                    order = order_obj or Order.objects.get(id=order_id)
                except Order.DoesNotExist:
                    return

                lat = location.get("latitude")
                lon = location.get("longitude")
                order.delivery_latitude = lat
                order.delivery_longitude = lon

                # Reverse geocoding через Яндекс Карты — получаем текстовый адрес
                address_text = _reverse_geocode(lat, lon)
                if address_text:
                    order.delivery_address = address_text

                # Автоматически находим ближайший филиал магазина по полученным координатам
                from core.working_views.views_branches import find_nearest_branch
                nearest_branch, _ = find_nearest_branch(
                    order.store,
                    delivery_latitude=lat,
                    delivery_longitude=lon,
                    delivery_address=order.delivery_address
                )
                if nearest_branch:
                    order.branch = nearest_branch

                order.save(update_fields=["delivery_latitude", "delivery_longitude", "delivery_address", "branch"])

                # Сохраняем telegram_username и chat_id в Customer
                tg_from = message.get("from", {})
                customer = order.customer
                tg_username = tg_from.get("username", "")
                if tg_username and not customer.telegram_username:
                    customer.telegram_username = tg_username
                if not customer.telegram_chat_id:
                    customer.telegram_chat_id = str(chat_id)
                customer.save(update_fields=["telegram_username", "telegram_chat_id"])

                # Переводим бота в режим ожидания текстового ориентира для такси
                cache.set(f"tg_state_{chat_id}", {"state": "WAITING_LANDMARK", "order_id": order.id}, timeout=3600)

                payload = {
                    "chat_id": chat_id,
                    "text": _t(chat_id, "location_received"),
                    "reply_markup": {
                        "remove_keyboard": True
                    }
                }
                if curr_token:
                    requests.post(f"https://api.telegram.org/bot{curr_token}/sendMessage", json=payload)
                return

            # Шаг 1.5: Клиент прислал текстовый ориентир для такси (подъезд, этаж, ориентир)
            elif state == "WAITING_LANDMARK" and text:
                try:
                    order = order_obj or Order.objects.get(id=order_id)
                    order.delivery_landmark = text.strip()
                    order.save(update_fields=["delivery_landmark"])
                except Order.DoesNotExist:
                    return

                # Переводим бота в режим ожидания номера телефона
                cache.set(f"tg_state_{chat_id}", {"state": "WAITING_CONTACT", "order_id": order.id}, timeout=3600)

                payload = {
                    "chat_id": chat_id,
                    "text": _t(chat_id, "landmark_received"),
                    "reply_markup": {
                        "keyboard": [[{"text": _t(chat_id, "btn_send_phone"), "request_contact": True}]],
                        "resize_keyboard": True,
                        "one_time_keyboard": True
                    }
                }
                if curr_token:
                    requests.post(f"https://api.telegram.org/bot{curr_token}/sendMessage", json=payload)
                return

            # Шаг 2: Клиент прислал контакт
            elif state == "WAITING_CONTACT" and contact:
                try:
                    order = order_obj or Order.objects.get(id=order_id)
                except Order.DoesNotExist:
                    cache.delete(f"tg_state_{chat_id}")
                    return

                # Обновляем данные клиента
                customer = order.customer
                customer.phone_number = contact.get("phone_number")
                customer.full_name = contact.get("first_name", "Клиент Telegram")
                customer.save(update_fields=["phone_number", "full_name"])

                store = order.store
                order.payment_method = "card_transfer"
                order.save(update_fields=["payment_method"])

                # Запоминаем, что ждем чек / подтверждение
                cache.set(f"tg_state_{chat_id}", {"state": "WAITING_RECEIPT", "order_id": order.id}, timeout=3600 * 48)

                card_number = store.payout_card or _t(chat_id, "card_number_pending")
                card_holder_line = _t(chat_id, "card_holder_prefix", holder=store.payout_card_holder) if store.payout_card_holder else ""

                ready_text = _t(
                    chat_id,
                    "checkout_card_info",
                    order_id=order.id,
                    store=store.name,
                    total=f"{int(order.total_price):,}",
                    card_number=card_number,
                    card_holder_line=card_holder_line,
                )

                if order.branch:
                    dist_km = order.get_dispatch_distance_km()
                    mins = order.get_estimated_taxi_mins()
                    fare = order.get_estimated_taxi_fare()
                    dist_line = f" ({dist_km} km)" if dist_km is not None else ""
                    fare_str = f"{fare:,}".replace(",", " ")
                    cust_l = _get_customer_lang(chat_id)
                    if cust_l == "uz":
                        branch_card_text = (
                            f"\n\n📍 *Eng yaqin filial:* «{order.branch.name}»{dist_line}\n"
                            f"🚕 *Yetkazish hisobi:* ~{mins} daqiqa (taxminan {fare_str} so'm taksi)\n"
                            f"🚀 Buyurtma tez va arzon yetkazilishi uchun aynan shu filialdan yuboriladi!"
                        )
                    elif cust_l == "en":
                        branch_card_text = (
                            f"\n\n📍 *Nearest dispatch branch:* «{order.branch.name}»{dist_line}\n"
                            f"🚕 *Taxi estimate:* ~{mins} mins (~{fare_str} UZS taxi fare)\n"
                            f"🚀 Dispatched from this branch for fastest and most affordable delivery!"
                        )
                    else:
                        branch_card_text = (
                            f"\n\n📍 *Ближайший филиал:* «{order.branch.name}»{dist_line}\n"
                            f"🚕 *Расчёт доставки:* ~{mins} мин (такси ~{fare_str} сум)\n"
                            f"🚀 Заказ будет отправлен именно из этой точки для быстрой и выгодной доставки!"
                        )
                    ready_text += branch_card_text

                payload = {
                    "chat_id": chat_id,
                    "text": ready_text,
                    "parse_mode": "Markdown",
                    "reply_markup": {
                        "remove_keyboard": True
                    }
                }
                if curr_token:
                    requests.post(f"https://api.telegram.org/bot{curr_token}/sendMessage", json=payload)

                # Магазин и менеджеров НЕ беспокоим, пока клиент не переведет оплату и не прикрепит фото чека!
                return

            # Шаг 2.5: Клиент присылает чек / фото подтверждения перевода
            elif state == "WAITING_RECEIPT":
                has_photo = bool(message.get("photo") or message.get("document"))
                if not has_photo:
                    # Если клиент просто отправил текст вместо фото чека
                    _send_telegram_message(
                        chat_id,
                        _t(chat_id, "receipt_photo_required", order_id=order_id),
                        bot_token=curr_token
                    )
                    return

                try:
                    order = order_obj or Order.objects.get(id=order_id)
                    store = order.store
                    cache.delete(f"tg_state_{chat_id}")

                    # Скачиваем фото чека из Telegram и сохраняем в заказ
                    photo_id = None
                    if message.get("photo"):
                        photo_id = message["photo"][-1]["file_id"]
                    elif message.get("document"):
                        photo_id = message["document"]["file_id"]

                    if photo_id and curr_token:
                        try:
                            file_info_resp = requests.get(
                                f"https://api.telegram.org/bot{curr_token}/getFile?file_id={photo_id}",
                                timeout=10
                            )
                            if file_info_resp.status_code == 200:
                                file_path = file_info_resp.json().get("result", {}).get("file_path")
                                if file_path:
                                    dl_resp = requests.get(
                                        f"https://api.telegram.org/file/bot{curr_token}/{file_path}",
                                        timeout=25
                                    )
                                    if dl_resp.status_code == 200:
                                        from django.core.files.base import ContentFile
                                        order.payment_receipt.save(
                                            f"receipt_order_{order.id}.jpg",
                                            ContentFile(dl_resp.content),
                                            save=False
                                        )
                        except Exception as e:
                            logger.warning("Не удалось скачать чек из Telegram: %s", e)

                    order.payment_confirmed_by_customer = True
                    order.save(update_fields=["payment_receipt", "payment_confirmed_by_customer"])

                    _send_telegram_message(
                        chat_id,
                        _t(chat_id, "receipt_received_customer", order_id=order.id, store=store.name),
                        bot_token=curr_token
                    )

                    # Только СЕЙЧАС отправляем полное уведомление с фото чека и кнопками владельцу и менеджерам!
                    notify_order_receipt_uploaded(order)
                    return
                except Order.DoesNotExist:
                    cache.delete(f"tg_state_{chat_id}")

            # Шаг 3 (Продавец): Прислал ссылку на Яндекс.Доставку или фото/скриншот отправки прямо в чат
            elif state == "SELLER_WAITING_TRACKING":
                photo_list = message.get("photo") or []
                photo_file_id = photo_list[-1]["file_id"] if photo_list else None
                if not photo_file_id and message.get("document"):
                    photo_file_id = message["document"]["file_id"]

                if text or photo_file_id:
                    try:
                        order = order_obj or Order.objects.get(id=order_id)
                        if text:
                            order.tracking_url = text.strip()
                        order.status = Order.Status.HANDED_TO_COURIER
                        order.save(update_fields=["tracking_url", "status"])
                        cache.delete(f"tg_state_{chat_id}")

                        cust_chat_id = order.customer.telegram_chat_id
                        cust_lang = getattr(order.customer, "language", "ru") or "ru"

                        if photo_file_id and cust_chat_id:
                            if cust_lang == "uz":
                                cap = (
                                    f"🚕 Sizning #{order.id}-buyurtmangiz «{order.store.name}» do'konidan kuryerga topshirildi va yo'lda! 📦\n\n"
                                    f"📸 Sotuvchidan yetkazish / kuryer fotosurati"
                                )
                            elif cust_lang == "en":
                                cap = (
                                    f"🚕 Your order #{order.id} from «{order.store.name}» has been handed to courier and is on the way! 📦\n\n"
                                    f"📸 Dispatch / courier photo from seller"
                                )
                            else:
                                cap = (
                                    f"🚕 Ваш заказ #{order.id} из «{order.store.name}» передан курьеру и уже в пути! 📦\n\n"
                                    f"📸 Фото отправки / курьера от продавца"
                                )
                            if order.tracking_url:
                                cap += f"\n\n🔗 {order.tracking_url}"

                            if curr_token:
                                requests.post(
                                    f"https://api.telegram.org/bot{curr_token}/sendPhoto",
                                    json={"chat_id": cust_chat_id, "photo": photo_file_id, "caption": cap},
                                    timeout=10
                                )
                        else:
                            from core.tasks import notify_customer_about_delivery_task
                            notify_customer_about_delivery_task.delay(order.id)

                        _send_telegram_message(
                            chat_id,
                            f"✅ Информация о доставке для заказа #{order.id} успешно отправлена клиенту в Telegram!\nСтатус заказа: 'У курьера (В пути)' 🚀",
                            bot_token=curr_token
                        )
                        return
                    except Order.DoesNotExist:
                        cache.delete(f"tg_state_{chat_id}")

        # Команда /lang или текстовый запрос на смену языка
        clean_text_lower = text.strip().lower()
        requested_lang = None
        if clean_text_lower in ["/lang", "/language", "til", "язык", "language"]:
            requested_lang = "CHOOSE"
        elif any(phrase in clean_text_lower for phrase in ["o'zbek", "ozbek", "uzbek", "ўзбек", "узбек", "o'zbekcha", "ozbekcha", "o'zbek tili", "uzbek tili"]):
            requested_lang = "uz"
        elif any(phrase in clean_text_lower for phrase in ["русский", "русском", "по-русски", "russian", "ruscha", "русский язык"]):
            requested_lang = "ru"
        elif any(phrase in clean_text_lower for phrase in ["english", "ingliz", "inglizcha", "in english", "to english", "english language"]):
            requested_lang = "en"

        if requested_lang == "CHOOSE":
            payload = {
                "chat_id": chat_id,
                "text": BOT_TEXTS["uz"]["choose_lang"],
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "🇺🇿 O'zbekcha", "callback_data": "lang_uz"},
                            {"text": "🇷🇺 Русский", "callback_data": "lang_ru"},
                            {"text": "🇬🇧 English", "callback_data": "lang_en"},
                        ]
                    ]
                }
            }
            lang_token = bot_token or getattr(settings, "TELEGRAM_BOT_TOKEN", None)
            if lang_token:
                requests.post(f"https://api.telegram.org/bot{lang_token}/sendMessage", json=payload)
            return

        elif requested_lang in ("uz", "ru", "en"):
            Customer.objects.filter(telegram_chat_id=str(chat_id)).update(language=requested_lang)
            Store.objects.filter(telegram_chat_id=str(chat_id)).update(telegram_notification_language=requested_lang)
            StoreManager.objects.filter(telegram_chat_id=str(chat_id)).update(language=requested_lang)
            lang_token = bot_token or getattr(settings, "TELEGRAM_BOT_TOKEN", None)
            lang_text = BOT_TEXTS[requested_lang]["lang_set"]
            _send_telegram_message(chat_id, lang_text, bot_token=lang_token)

            store_check = Store.objects.filter(telegram_chat_id=str(chat_id)).first()
            if store_check:
                inline_kb = [
                    [{"text": _nt(requested_lang, "btn_dashboard"), "web_app": {"url": f"{settings.SITE_URL}/dashboard/?tg_auth={store_check.telegram_link_token}" if store_check.telegram_link_token else f"{settings.SITE_URL}/dashboard/"}}],
                    [{"text": _nt(requested_lang, "btn_storefront"), "web_app": {"url": f"{settings.SITE_URL}/{requested_lang}/?store_id={store_check.id}"}}],
                ]
                if not lang_token or lang_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                    inline_kb.append([
                        {"text": _nt(requested_lang, "btn_global_market"), "web_app": {"url": f"{settings.SITE_URL}/{requested_lang}/?global=1"}}
                    ])
                _send_telegram_message(
                    chat_id,
                    _t(chat_id, "welcome_seller", name=store_check.name),
                    reply_markup={"inline_keyboard": inline_kb},
                    bot_token=lang_token
                )
            else:
                web_app_url = f"{settings.SITE_URL}/{requested_lang}/"
                inline_kb = [
                    [{"text": _nt(requested_lang, "btn_catalog"), "web_app": {"url": web_app_url}}],
                ]
                if not lang_token or lang_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                    inline_kb.append([
                        {"text": _nt(requested_lang, "btn_global_market"), "web_app": {"url": f"{settings.SITE_URL}/{requested_lang}/?global=1"}}
                    ])
                _send_telegram_message(
                    chat_id,
                    _t(chat_id, "welcome_buyer"),
                    reply_markup={"inline_keyboard": inline_kb},
                    bot_token=lang_token
                )
            return

        # Команда /otp, /code, /login — получение или генерация текущего одноразового кода для входа
        if text.strip().lower() in ["/otp", "/code", "/login", "код", "отп", "пароль", "логин"]:
            tg_username = (message.get("from", {}) or {}).get("username", "")
            store = Store.objects.filter(telegram_chat_id=str(chat_id)).first()
            if not store and tg_username:
                store = Store.objects.filter(owner_telegram_username__iexact=tg_username.lstrip("@")).first()
                if store and not store.telegram_chat_id:
                    store.telegram_chat_id = str(chat_id)
                    store.save(update_fields=["telegram_chat_id"])

            if store:
                pending_otp = cache.get(f"store_pending_otp_{store.id}")
                if not pending_otp:
                    pending_otp = f"{secrets.randbelow(900000) + 100000}"
                    cache.set(f"store_pending_otp_{store.id}", pending_otp, timeout=600)
                    cache.set(f"user_login_otp_{store.owner.id}", pending_otp, timeout=600)
                    cache.set(f"username_pending_otp_{store.owner.username.lower()}", pending_otp, timeout=600)
                    if store.slug:
                        cache.set(f"username_pending_otp_{store.slug.lower()}", pending_otp, timeout=600)

                fast_auth_url = f"{settings.SITE_URL}/dashboard/?tg_auth={store.telegram_link_token}" if store.telegram_link_token else None
                kb_code = None
                if fast_auth_url:
                    kb_code = {"inline_keyboard": [[{"text": "🚀 Войти в 1 клик", "web_app": {"url": fast_auth_url}}]]}
                _send_telegram_message(
                    chat_id,
                    f"🔐 ВАШ КОД ВХОДА В ПАНЕЛЬ BRAINMART:\n\n"
                    f"👉 {pending_otp} 👈\n\n"
                    f"👤 Ваш логин: {store.owner.username}\n"
                    f"🏪 Магазин: «{store.name}»\n"
                    f"⏱ Код действует 10 минут.\n\n"
                    f"💡 Введите этот 6-значный код и ваш логин на сайте или нажмите кнопку ниже для входа в 1 клик!",
                    reply_markup=kb_code
                )
            else:
                _send_telegram_message(
                    chat_id,
                    "⚠️ Магазин не привязан к данному аккаунту Telegram.\n"
                    "Для привязки перейдите по персональной ссылке из настроек магазина или зарегистрируйте магазин на сайте."
                )
            return

        order_match = re.match(r"^/start\s+order_(\d+)", text)
        product_match = re.match(r"^/start\s+product_(\d+)", text)
        link_match = re.match(r"^/start\s+link_([a-f0-9]+)", text)
        manager_match = re.match(r"^/start\s+mgr_([a-f0-9]+)", text)
        adminlink_match = re.match(r"^/start\s+adminlink_([a-f0-9]+)", text)
        store_match = re.match(r"^/start\s+store_(\d+)", text)

        if order_match:
            order_id = int(order_match.group(1))
            try:
                order = Order.objects.select_related("store", "customer", "branch", "assigned_manager").get(id=order_id)
                order_token = bot_token or (order.store.telegram_bot_token if (order.store.telegram_bot_token and order.store.telegram_bot_active) else getattr(settings, "TELEGRAM_BOT_TOKEN", None))
                tg_from = message.get("from", {}) or {}
                customer = order.customer
                tg_username = tg_from.get("username", "") or ""
                first_name = tg_from.get("first_name", "") or ""
                last_name = tg_from.get("last_name", "") or ""
                full_name = f"{first_name} {last_name}".strip()

                cust_update_fields = []
                if str(customer.telegram_chat_id) != str(chat_id):
                    customer.telegram_chat_id = str(chat_id)
                    cust_update_fields.append("telegram_chat_id")
                if tg_username and customer.telegram_username != tg_username:
                    customer.telegram_username = tg_username
                    cust_update_fields.append("telegram_username")
                if full_name and (not customer.full_name or customer.full_name in ("Покупатель", "Пользователь Telegram", "Клиент Telegram")):
                    customer.full_name = full_name
                    cust_update_fields.append("full_name")

                if cust_update_fields:
                    customer.save(update_fields=cust_update_fields)

                # Синхронизируем язык клиента из заказа с чатом
                cust_lang = getattr(customer, "language", None) or _get_customer_lang(chat_id) or "uz"
                if cust_lang not in ("ru", "uz", "en"):
                    cust_lang = "uz"
                Customer.objects.filter(telegram_chat_id=str(chat_id)).update(language=cust_lang)

                # Проверяем, нужна ли локация по GPS (только если заказ открыт в Telegram без адреса и без чека)
                is_location_needed = (
                    not order.delivery_address
                    or (order.delivery_address in ["Локация по GPS", "Адрес через Telegram"] and not order.delivery_latitude and not order.payment_confirmed_by_customer)
                )

                if is_location_needed:
                    cache.set(f"tg_state_{chat_id}", {"state": "WAITING_LOCATION", "order_id": order.id}, timeout=3600)
                    payload = {
                        "chat_id": chat_id,
                        "text": (
                            _nt(cust_lang, "order_opened_header", order_id=order.id) +
                            _nt(cust_lang, "taxi_warning_prompt")
                        ),
                        "reply_markup": {
                            "keyboard": [[{"text": _nt(cust_lang, "btn_send_location"), "request_location": True}]],
                            "resize_keyboard": True,
                            "one_time_keyboard": True
                        }
                    }
                    if order_token:
                        requests.post(f"https://api.telegram.org/bot{order_token}/sendMessage", json=payload)
                    return

                # --- КАРТОЧКА ОТСЛЕЖИВАНИЯ ЗАКАЗА ДЛЯ ПОКУПАТЕЛЯ ИЗ БРАУЗЕРА ---
                store = order.store

                # Находим контакт менеджера или владельца магазина
                assigned_mgr = order.assigned_manager
                if not assigned_mgr:
                    assigned_mgr = store.managers.filter(is_active=True).exclude(telegram_username="").first()
                    if not assigned_mgr:
                        assigned_mgr = store.managers.filter(is_active=True).first()
                    if assigned_mgr and not order.assigned_manager:
                        order.assigned_manager = assigned_mgr
                        order.save(update_fields=["assigned_manager"])

                seller_username = ""
                seller_name = ""
                if assigned_mgr and assigned_mgr.telegram_username:
                    seller_username = assigned_mgr.telegram_username.lstrip("@").strip()
                    seller_name = assigned_mgr.name or ("Менеджер" if cust_lang == "ru" else "Menejer")
                elif store.owner_telegram_username:
                    seller_username = store.owner_telegram_username.lstrip("@").strip()
                    seller_name = store.name
                elif store.owner and store.owner.username:
                    seller_username = store.owner.username.lstrip("@").strip()
                    seller_name = store.name
                else:
                    seller_name = store.name

                if seller_username:
                    seller_display = f"@{seller_username} ({seller_name})"
                else:
                    phone_part = f" (тел: {store.phone_number})" if store.phone_number else ""
                    seller_display = f"«{store.name}»{phone_part}"

                # Расчёт времени готовности и доставки
                branch = order.branch
                dist_km = order.get_dispatch_distance_km()
                mins = order.get_estimated_taxi_mins()
                if branch and dist_km is not None:
                    if cust_lang == "uz":
                        delivery_time_text = (
                            f"• Jo'natuvchi eng yaqin filial: «{branch.name}»\n"
                            f"• Taksi yetkazishi: ~{mins} daqiqa (masofa {dist_km} km)\n"
                            f"• Buyurtmani qabul qilish vaqti: ~{mins + 15}–{mins + 25} daqiqa ichida"
                        )
                    elif cust_lang == "en":
                        delivery_time_text = (
                            f"• Nearest dispatch branch: «{branch.name}»\n"
                            f"• Taxi delivery: ~{mins} mins (distance {dist_km} km)\n"
                            f"• Estimated arrival: within ~{mins + 15}–{mins + 25} minutes"
                        )
                    else:
                        delivery_time_text = (
                            f"• Ближайший филиал отгрузки: «{branch.name}»\n"
                            f"• Поездка такси: ~{mins} мин (расстояние {dist_km} км)\n"
                            f"• Примерное время получения: в течение ~{mins + 15}–{mins + 25} минут"
                        )
                else:
                    if cust_lang == "uz":
                        delivery_time_text = (
                            f"• Yig'ish va qadoqlash: ~10–20 daqiqa\n"
                            f"• Yetkazish: ~30–60 daqiqa (Toshkent bo'yicha) / 1–2 kun (viloyatlarga)"
                        )
                    elif cust_lang == "en":
                        delivery_time_text = (
                            f"• Assembly & packing: ~10–20 mins\n"
                            f"• Delivery: ~30–60 mins (Tashkent) / 1–2 days (regions)"
                        )
                    else:
                        delivery_time_text = (
                            f"• Сборка и упаковка: ~10–20 минут\n"
                            f"• Доставка курьером (такси): ~30–60 минут (по Ташкенту) / 1–2 дня (по регионам)"
                        )

                # Состав заказа и адрес
                items_text = _build_items_text(order, cust_lang)
                addr_text = order.delivery_address or ("Manzil aniqlanmoqda" if cust_lang == "uz" else "Адрес уточняется")
                if order.delivery_landmark:
                    addr_text += f" (Ориентир: {order.delivery_landmark})"
                total_formatted = f"{int(order.total_price):,}".replace(",", " ")

                is_paid = (order.status == Order.Status.PAID)
                is_receipt_sent = bool(order.payment_confirmed_by_customer or order.payment_receipt)

                if is_paid:
                    if cust_lang == "uz":
                        status_badge = "✅ To'landi (Sotuvchi jo'natishga tayyorlamoqda)"
                        footer_msg = "🚕 Buyurtma to'landi! Kuryer/taksi yo'lga chiqqach — taksini onlayn kuzatish havolasi to'g'ridan-to'g'ri shu chatga keladi!"
                    elif cust_lang == "en":
                        status_badge = "✅ Paid (Seller is preparing dispatch)"
                        footer_msg = "🚕 Order paid! As soon as courier/taxi departs — live tracking link will arrive directly in this chat!"
                    else:
                        status_badge = "✅ Оплачен (Продавец готовит отправку)"
                        footer_msg = "🚕 Заказ оплачен! Как только курьер/такси выедет — ссылка на онлайн-отслеживание поездки придёт прямо в этот чат!"

                elif is_receipt_sent:
                    if cust_lang == "uz":
                        status_badge = "⏳ Chek sotuvchida tekshiruvda"
                        footer_msg = "ℹ️ Biz chekingizni sotuvchiga topshirdik! Sotuvchi pul tushumini tekshirmoqda. To'lov tasdiqlanib taksi chaqirilgach — xabar va kuzatuv havolasi yoki yetkazish fotosi shu yerga keladi ✨"
                    elif cust_lang == "en":
                        status_badge = "⏳ Receipt is being verified by seller"
                        footer_msg = "ℹ️ We handed your receipt to the seller! The seller is verifying payment. Once confirmed and taxi dispatched — live tracking link or delivery photo will appear here ✨"
                    else:
                        status_badge = "⏳ Чек на проверке у продавца"
                        footer_msg = "ℹ️ Мы передали ваш чек продавцу! Продавец сверяет поступление оплаты по вашему чеку. Как только платёж подтвердится и такси выедет — ссылка на онлайн-отслеживание или фото доставки придёт сюда ✨"

                else:
                    # Ожидает перевод и чек
                    cache.set(f"tg_state_{chat_id}", {"state": "WAITING_RECEIPT", "order_id": order.id}, timeout=3600 * 48)
                    card_num = store.payout_card or "8600 0000 0000 0000"
                    holder_suffix = f" ({store.payout_card_holder})" if store.payout_card_holder else ""
                    if cust_lang == "uz":
                        status_badge = "⏳ To'lov va chek kutilmoqda"
                        footer_msg = (
                            f"💳 TO'LOV UCHUN KARTA:\n👉 {card_num}{holder_suffix}\n\n"
                            f"📸 To'lov chekining fotosuratini to'g'ridan-to'g'ri shu chatga yuboring! "
                            f"Sotuvchi darhol tekshirib, buyurtmangizni taksiga topshiradi ✨"
                        )
                    elif cust_lang == "en":
                        status_badge = "⏳ Awaiting card transfer & receipt"
                        footer_msg = (
                            f"💳 PAYMENT CARD:\n👉 {card_num}{holder_suffix}\n\n"
                            f"📸 Send the receipt screenshot directly in this chat! "
                            f"The seller will verify payment and dispatch your order ✨"
                        )
                    else:
                        status_badge = "⏳ Ожидает перевода на карту и отправки чека"
                        footer_msg = (
                            f"💳 КАРТА ДЛЯ ОПЛАТЫ:\n👉 {card_num}{holder_suffix}\n\n"
                            f"📸 Отправьте фото или скриншот чека прямо сюда в чат! "
                            f"Продавец сразу проверит платёж и отправит заказ на такси ✨"
                        )

                # Итоговый текст сообщения клиенту
                if cust_lang == "uz":
                    header_line = f"🧾 Biz chekingiz va buyurtmangizni sotuvchiga topshirdik!\n\n📦 #{order.id}-buyurtma" if is_receipt_sent else f"📦 #{order.id}-buyurtma sotuvchiga topshirildi!"
                    msg_text = (
                        f"{header_line}\n\n"
                        f"🏪 Do'kon: «{store.name}»\n"
                        f"📊 Holati: {status_badge}\n"
                        f"👨‍💼 Sotuvchi / Menejer: {seller_display}\n\n"
                        f"⏱ Tayyorlanish va yetkazish vaqti:\n"
                        f"{delivery_time_text}\n\n"
                        f"🛍 Buyurtma tarkibi:\n"
                        f"{items_text}\n\n"
                        f"💰 To'lov summasi: {total_formatted} so'm\n"
                        f"📍 Yetkazish manzili: {addr_text}\n\n"
                        f"{footer_msg}"
                    )
                elif cust_lang == "en":
                    header_line = f"🧾 We handed your receipt and order to the seller!\n\n📦 Order #{order.id}" if is_receipt_sent else f"📦 Order #{order.id} handed to seller!"
                    msg_text = (
                        f"{header_line}\n\n"
                        f"🏪 Store: «{store.name}»\n"
                        f"📊 Status: {status_badge}\n"
                        f"👨‍💼 Seller / Manager: {seller_display}\n\n"
                        f"⏱ Estimated readiness & delivery time:\n"
                        f"{delivery_time_text}\n\n"
                        f"🛍 Order items:\n"
                        f"{items_text}\n\n"
                        f"💰 Total price: {total_formatted} UZS\n"
                        f"📍 Delivery address: {addr_text}\n\n"
                        f"{footer_msg}"
                    )
                else:
                    header_line = f"🧾 Мы передали ваш чек и заказ продавцу!\n\n📦 Заказ #{order.id}" if is_receipt_sent else f"📦 Заказ #{order.id} передан продавцу!"
                    msg_text = (
                        f"{header_line}\n\n"
                        f"🏪 Магазин: «{store.name}»\n"
                        f"📊 Статус: {status_badge}\n"
                        f"👨‍💼 Продавец / Менеджер: {seller_display}\n\n"
                        f"⏱ Примерное время готовности и доставки:\n"
                        f"{delivery_time_text}\n\n"
                        f"🛍 Состав заказа:\n"
                        f"{items_text}\n\n"
                        f"💰 Сумма заказа: {total_formatted} сум\n"
                        f"📍 Адрес доставки: {addr_text}\n\n"
                        f"{footer_msg}"
                    )

                # Инлайн-кнопки
                inline_keyboard = []
                if seller_username:
                    contact_btn_label = {
                        "uz": f"💬 Sotuvchiga yozish (@{seller_username})",
                        "en": f"💬 Contact seller (@{seller_username})",
                        "ru": f"💬 Написать продавцу (@{seller_username})",
                    }.get(cust_lang, f"💬 Написать продавцу (@{seller_username})")
                    inline_keyboard.append([{"text": contact_btn_label, "url": f"https://t.me/{seller_username}"}])

                site_url = getattr(settings, "SITE_URL", "")
                if site_url:
                    store_url = f"{site_url}/{cust_lang}/?store_id={store.id}"
                    store_btn_label = {
                        "uz": "🛍 Do'konga o'tish",
                        "en": "🛍 Open store",
                        "ru": "🛍 В магазин",
                    }.get(cust_lang, "🛍 В магазин")
                    inline_keyboard.append([{"text": store_btn_label, "web_app": {"url": store_url}}])
                    if not order_token or order_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                        inline_keyboard.append([
                            {"text": _nt(cust_lang, "btn_global_market"), "web_app": {"url": f"{site_url}/{cust_lang}/?global=1"}}
                        ])

                reply_markup = {"inline_keyboard": inline_keyboard} if inline_keyboard else None
                _send_telegram_message(chat_id, msg_text, reply_markup=reply_markup, bot_token=order_token)
                return

            except Order.DoesNotExist:
                _send_telegram_message(chat_id, "Заказ не найден или уже был обработан.")
                return

        elif product_match:
            product_id = product_match.group(1)
            cust_lang = _get_customer_lang(chat_id) or "ru"
            site_url = getattr(settings, "SITE_URL", "")
            web_app_url = f"{site_url}/{cust_lang}/product/{product_id}/"
            inline_keyboard = [
                [{"text": "🛒 Оформить товар", "web_app": {"url": web_app_url}}]
            ]
            if not bot_token or bot_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                inline_keyboard.append([
                    {"text": _nt(cust_lang, "btn_global_market"), "web_app": {"url": f"{site_url}/{cust_lang}/?global=1"}}
                ])
            _send_telegram_message(
                chat_id,
                _t(chat_id, "product_intro"),
                reply_markup={"inline_keyboard": inline_keyboard},
                bot_token=bot_token,
            )

        elif store_match:
            store_id = store_match.group(1)
            target_store = Store.objects.filter(id=store_id).first()
            cust_lang = _get_customer_lang(chat_id) or "uz"
            site_url = getattr(settings, "SITE_URL", "")
            web_app_url = f"{site_url}/{cust_lang}/?store_id={store_id}"
            store_name = target_store.name if target_store else "Магазин"
            store_btn_label = {
                "uz": f"🛍 «{store_name}» do'koniga kirish",
                "en": f"🛍 Open «{store_name}»",
                "ru": f"🛍 В магазин «{store_name}»",
            }.get(cust_lang, f"🛍 В магазин «{store_name}»")
            
            inline_keyboard = [
                [{"text": store_btn_label, "web_app": {"url": web_app_url}}]
            ]
            if not bot_token or bot_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                inline_keyboard.append([
                    {"text": _nt(cust_lang, "btn_global_market"), "web_app": {"url": f"{site_url}/{cust_lang}/?global=1"}}
                ])

            _send_telegram_message(
                chat_id,
                _t(chat_id, "store_intro"),
                reply_markup={"inline_keyboard": inline_keyboard},
                bot_token=bot_token,
            )

        elif link_match:
            token = link_match.group(1)
            try:
                store = Store.objects.get(telegram_link_token=token)
                store.telegram_chat_id = str(chat_id)
                tg_from = message.get("from", {})
                tg_username = tg_from.get("username", "")
                if tg_username and not store.owner_telegram_username:
                    store.owner_telegram_username = tg_username
                    store.save(update_fields=["telegram_chat_id", "owner_telegram_username"])
                else:
                    store.save(update_fields=["telegram_chat_id"])
                _send_telegram_message(
                    chat_id,
                    _t(chat_id, "seller_linked", name=store.name),
                )
                pending_otp = cache.get(f"store_pending_otp_{store.id}")
                if pending_otp:
                    _send_telegram_message(
                        chat_id,
                        f"🔐 ВАШ ОДНОРАЗОВЫЙ КОД ВХОДА (2FA):\n\n"
                        f"👉 {pending_otp} 👈\n\n"
                        f"⏱ Код действует 5 минут. Введите его на странице входа в панель BrainMart."
                    )
            except Store.DoesNotExist:
                _send_telegram_message(chat_id, _t(chat_id, "link_invalid"))

        elif manager_match:
            token = manager_match.group(1)
            try:
                manager = StoreManager.objects.get(telegram_link_token=token)
                manager.telegram_chat_id = str(chat_id)
                manager.is_active = True
                tg_from = message.get("from", {})
                tg_username = tg_from.get("username", "")
                if tg_username:
                    manager.telegram_username = tg_username
                manager.save(update_fields=["telegram_chat_id", "is_active", "telegram_username"])
                _send_telegram_message(
                    chat_id,
                    _t(chat_id, "manager_linked", store=manager.store.name, name=manager.name),
                )
            except StoreManager.DoesNotExist:
                _send_telegram_message(chat_id, _t(chat_id, "manager_link_invalid"))

        elif adminlink_match:
            token = adminlink_match.group(1)
            ps = PlatformSettings.objects.filter(superadmin_telegram_link_token=token).first()
            if ps:
                tg_from = message.get("from", {})
                ps.superadmin_telegram_chat_id = str(chat_id)
                ps.superadmin_telegram_username = tg_from.get("username", "") or tg_from.get("first_name", "")
                ps.save(update_fields=["superadmin_telegram_chat_id", "superadmin_telegram_username"])
                _send_telegram_message(
                    chat_id,
                    f"👑 Поздравляем! Вы успешно подключены как Главный Администратор платформы BrainMart!\n\n"
                    f"📱 Chat ID: {chat_id}\n"
                    f"👤 Username: @{ps.superadmin_telegram_username}\n\n"
                    f"Сюда будут мгновенно приходить уведомления о всех новых заявках на открытие магазинов с кнопками для одобрения в один клик! 🚀"
                )
            else:
                _send_telegram_message(chat_id, "Ссылка для подключения администратора недействительна или устарела.")

        else:
            try:
                store = Store.objects.filter(telegram_chat_id=str(chat_id)).first()
                if not store:
                    raise Store.DoesNotExist
                store_lang = getattr(store, "telegram_notification_language", "uz") or "uz"
                inline_kb = [
                    [{"text": _t(chat_id, "btn_dashboard"), "web_app": {"url": f"{settings.SITE_URL}/dashboard/?tg_auth={store.telegram_link_token}" if store.telegram_link_token else f"{settings.SITE_URL}/dashboard/"}}],
                    [{"text": _t(chat_id, "btn_storefront"), "web_app": {"url": f"{settings.SITE_URL}/{store_lang}/?store_id={store.id}"}}],
                ]
                if not bot_token or bot_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                    inline_kb.append([
                        {"text": _nt(store_lang, "btn_global_market"), "web_app": {"url": f"{settings.SITE_URL}/{store_lang}/?global=1"}}
                    ])
                payload = {
                    "chat_id": chat_id,
                    "text": _t(chat_id, "welcome_seller", name=store.name),
                    "reply_markup": {
                        "inline_keyboard": inline_kb
                    }
                }
                _tg_session.post(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage", json=payload, timeout=8)

            except Store.DoesNotExist:
                # Обычный клиент: текст, фото товара или голосовое сообщение
                voice_obj = message.get("voice") or message.get("audio") or message.get("video_note")
                voice_file_id = voice_obj.get("file_id") if voice_obj else None

                photo_list = message.get("photo") or []
                photo_file_id = photo_list[-1]["file_id"] if photo_list else None
                if not photo_file_id and message.get("document"):
                    doc = message.get("document", {})
                    if (doc.get("mime_type") or "").startswith("image/"):
                        photo_file_id = doc.get("file_id")

                user_caption = (message.get("caption") or "").strip()
                query_text = (text or user_caption).strip()

                if (query_text and not query_text.startswith("/")) or photo_file_id or voice_file_id:
                    _send_telegram_chat_action(chat_id, "typing", bot_token=bot_token)
                    tg_from = message.get("from", {}) or {}
                    try:
                        from core.tasks import handle_incoming_telegram_message_task
                        handle_incoming_telegram_message_task.delay(
                            chat_id=str(chat_id),
                            message_text=query_text,
                            first_name=tg_from.get("first_name", ""),
                            username=tg_from.get("username", ""),
                            photo_file_id=photo_file_id,
                            voice_file_id=voice_file_id,
                            bot_token=bot_token,
                        )
                    except Exception:
                        from core.working_views.services import handle_incoming_telegram_message
                        ai_reply, matched_products = handle_incoming_telegram_message(
                            chat_id=str(chat_id),
                            message_text=query_text,
                            first_name=tg_from.get("first_name", ""),
                            username=tg_from.get("username", ""),
                            photo_file_id=photo_file_id,
                            voice_file_id=voice_file_id,
                            bot_token=bot_token,
                        )
                        if ai_reply:
                            site_url = getattr(settings, "SITE_URL", "https://brainmart.uz")
                            cust = Customer.objects.filter(telegram_chat_id=str(chat_id)).first()
                            cust_lang = getattr(cust, "language", "uz") or "uz"
                            global_kb = None
                            if not bot_token or bot_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                                global_kb = {"inline_keyboard": [[{"text": _nt(cust_lang, "btn_global_market"), "web_app": {"url": f"{site_url}/{cust_lang}/?global=1"}}]]}

                            if matched_products:
                                for v in matched_products[:3]:
                                    photo = v.image or v.product.main_image
                                    product_url = f"{site_url}/{cust_lang}/product/{v.product.id}/"
                                    if cust_lang == "uz":
                                        caption = (
                                            f"✨ {v.product.name}\n"
                                            f"💰 Narxi: {int(v.final_price):,} so'm\n"
                                            f"🎨 Rangi: {v.color or '—'} | O'lchami: {v.size or 'Standart'}"
                                        )
                                        btn_order_text = f"🛒 Buyurtma berish ({v.product.name[:25]})"
                                    elif cust_lang == "en":
                                        caption = (
                                            f"✨ {v.product.name}\n"
                                            f"💰 Price: {int(v.final_price):,} UZS\n"
                                            f"🎨 Color: {v.color or '—'} | Size: {v.size or 'One Size'}"
                                        )
                                        btn_order_text = f"🛒 Order Now ({v.product.name[:25]})"
                                    else:
                                        caption = (
                                            f"✨ {v.product.name}\n"
                                            f"💰 Цена: {int(v.final_price):,} сум\n"
                                            f"🎨 Цвет: {v.color or '—'} | Размер: {v.size or 'One Size'}"
                                        )
                                        btn_order_text = f"🛒 Оформить заказ ({v.product.name[:25]})"

                                    kb = {"inline_keyboard": [[{"text": btn_order_text, "web_app": {"url": product_url}}]]}
                                    if photo:
                                        _send_telegram_photo(chat_id, photo, caption=caption, reply_markup=kb, bot_token=bot_token)
                                    else:
                                        _send_telegram_message(chat_id, caption, reply_markup=kb, bot_token=bot_token)
                                _send_telegram_message(chat_id, ai_reply, reply_markup=global_kb, bot_token=bot_token)
                            else:
                                _send_telegram_message(chat_id, ai_reply, reply_markup=global_kb, bot_token=bot_token)
                else:
                    # /start без параметра — проверяем, выбирал ли клиент язык
                    existing = Customer.objects.filter(telegram_chat_id=str(chat_id)).order_by("-id").first()
                    if not existing or not getattr(existing, 'language', None):
                        # Новый пользователь — показываем выбор языка
                        _send_telegram_message(
                            chat_id,
                            BOT_TEXTS["uz"]["choose_lang"],
                            reply_markup={
                                "inline_keyboard": [
                                    [
                                        {"text": "🇺🇿 O'zbekcha", "callback_data": "lang_uz"},
                                        {"text": "🇷🇺 Русский", "callback_data": "lang_ru"},
                                        {"text": "🇬🇧 English", "callback_data": "lang_en"},
                                    ]
                                ]
                            },
                        )
                    else:
                        # Существующий клиент — показываем каталог и кнопку глобального маркета
                        client_lang = getattr(existing, 'language', 'uz') or 'uz'
                        web_app_url = f"{settings.SITE_URL}/{client_lang}/"
                        inline_kb = [
                            [{"text": _nt(client_lang, "btn_catalog"), "web_app": {"url": web_app_url}}],
                        ]
                        if not bot_token or bot_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                            inline_kb.append([
                                {"text": _nt(client_lang, "btn_global_market"), "web_app": {"url": f"{settings.SITE_URL}/{client_lang}/?global=1"}}
                            ])
                        _send_telegram_message(
                            chat_id,
                            _t(chat_id, "welcome_buyer"),
                            reply_markup={"inline_keyboard": inline_kb},
                            bot_token=bot_token,
                        )
    def _handle_callback(self, callback, bot_token=None):
        """Обрабатывает нажатие инлайн-кнопок (выбор языка)."""
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        callback_data = callback.get("data", "")
        callback_query_id = callback.get("id")

        if not chat_id:
            return

        if callback_data.startswith("lang_"):
            lang = callback_data.replace("lang_", "")
            if lang in ("ru", "uz", "en"):
                customer = Customer.objects.filter(telegram_chat_id=str(chat_id)).order_by("-id").first()
                if not customer:
                    customer = Customer.objects.create(
                        telegram_chat_id=str(chat_id),
                        instagram_scoped_id=f"tg_{chat_id}",
                        language=lang,
                    )
                Customer.objects.filter(telegram_chat_id=str(chat_id)).update(language=lang)

                # Если этот chat_id — владелец магазина, обновляем язык магазина
                Store.objects.filter(telegram_chat_id=str(chat_id)).update(telegram_notification_language=lang)

                # Если этот chat_id — менеджер, обновляем язык менеджера
                StoreManager.objects.filter(telegram_chat_id=str(chat_id)).update(language=lang)

                # Подтверждаем выбор языка
                lang_text = BOT_TEXTS[lang]["lang_set"]
                ans_token = bot_token or getattr(settings, "TELEGRAM_BOT_TOKEN", None)
                if ans_token:
                    requests.post(
                        f"https://api.telegram.org/bot{ans_token}/answerCallbackQuery",
                        json={"callback_query_id": callback_query_id, "text": lang_text},
                        timeout=5,
                    )
                _send_telegram_message(chat_id, lang_text, bot_token=ans_token)

                # Показываем приветствие на новом языке
                store_check = Store.objects.filter(telegram_chat_id=str(chat_id)).first()
                if store_check:
                    inline_kb = [
                        [{"text": _nt(lang, "btn_dashboard"), "web_app": {"url": f"{settings.SITE_URL}/dashboard/?tg_auth={store_check.telegram_link_token}" if store_check.telegram_link_token else f"{settings.SITE_URL}/dashboard/"}}],
                        [{"text": _nt(lang, "btn_storefront"), "web_app": {"url": f"{settings.SITE_URL}/{lang}/?store_id={store_check.id}"}}],
                    ]
                    if not ans_token or ans_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                        inline_kb.append([
                            {"text": _nt(lang, "btn_global_market"), "web_app": {"url": f"{settings.SITE_URL}/{lang}/?global=1"}}
                        ])
                    _send_telegram_message(
                        chat_id,
                        _t(chat_id, "welcome_seller", name=store_check.name),
                        reply_markup={"inline_keyboard": inline_kb},
                        bot_token=ans_token
                    )
                else:
                    web_app_url = f"{settings.SITE_URL}/{lang}/"
                    inline_kb = [
                        [{"text": _nt(lang, "btn_catalog"), "web_app": {"url": web_app_url}}],
                    ]
                    if not ans_token or ans_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                        inline_kb.append([
                            {"text": _nt(lang, "btn_global_market"), "web_app": {"url": f"{settings.SITE_URL}/{lang}/?global=1"}}
                        ])
                    _send_telegram_message(
                        chat_id,
                        _t(chat_id, "welcome_buyer"),
                        reply_markup={"inline_keyboard": inline_kb},
                        bot_token=ans_token
                    )

        elif callback_data.startswith("track_"):
            order_id = callback_data.replace("track_", "")
            cache.set(f"tg_state_{chat_id}", {"state": "SELLER_WAITING_TRACKING", "order_id": order_id}, timeout=3600)
            caller_lang = "ru"
            store_obj = Store.objects.filter(telegram_chat_id=str(chat_id)).first()
            if not store_obj:
                order_check = Order.objects.filter(id=order_id).select_related("store").first()
                if order_check:
                    store_obj = order_check.store
            if store_obj:
                caller_lang = _get_store_lang(store_obj)
            else:
                mgr_obj = StoreManager.objects.filter(telegram_chat_id=str(chat_id)).first()
                if mgr_obj:
                    caller_lang = _get_mgr_lang(mgr_obj)
                else:
                    caller_lang = _get_customer_lang(chat_id)

            curr_token = bot_token or (store_obj.telegram_bot_token if (store_obj and store_obj.telegram_bot_token and store_obj.telegram_bot_active) else getattr(settings, "TELEGRAM_BOT_TOKEN", None))
            if curr_token:
                requests.post(
                    f"https://api.telegram.org/bot{curr_token}/answerCallbackQuery",
                    json={"callback_query_id": callback_query_id, "text": _nt(caller_lang, 'track_wait')},
                    timeout=5,
                )
            _send_telegram_message(
                chat_id,
                _nt(caller_lang, 'track_prompt', order_id=order_id),
                bot_token=curr_token
            )

        elif callback_data.startswith("delivered_"):
            order_id = callback_data.replace("delivered_", "")
            try:
                order = Order.objects.select_related("store", "customer").get(id=order_id)
                order.status = Order.Status.DELIVERED
                order.save(update_fields=["status"])
                from core.payments.payout_service import release_order_funds_and_auto_payout
                release_order_funds_and_auto_payout(order)

                store_obj = order.store
                caller_lang = _get_store_lang(store_obj) if store_obj else "uz"

                curr_token = bot_token or (store_obj.telegram_bot_token if (store_obj and store_obj.telegram_bot_token and store_obj.telegram_bot_active) else getattr(settings, "TELEGRAM_BOT_TOKEN", None))
                if curr_token:
                    requests.post(
                        f"https://api.telegram.org/bot{curr_token}/answerCallbackQuery",
                        json={"callback_query_id": callback_query_id, "text": _nt(caller_lang, 'btn_mark_delivered')},
                        timeout=5,
                    )
                _send_telegram_message(
                    chat_id,
                    f"{_nt(caller_lang, 'btn_mark_delivered')} (#{order_id})!",
                    bot_token=curr_token
                )

                # Уведомляем клиента о доставке на выбранном им языке
                if order.customer and order.customer.telegram_chat_id:
                    cust_l = getattr(order.customer, "language", None) or _get_customer_lang(order.customer.telegram_chat_id) or "uz"
                    if cust_l == "uz":
                        deliv_msg = (
                            f"🎉 #{order.id}-buyurtmangiz yetkazildi! ✅\n\n"
                            f"🏪 «{store_obj.name}» do'konidan xarid qilganingiz uchun tashakkur! "
                            f"Kiyimlaringiz sizga quvonch keltirsin deb qolamiz. Yana kutib qolamiz! ✨"
                        )
                    elif cust_l == "en":
                        deliv_msg = (
                            f"🎉 Order #{order.id} has been delivered! ✅\n\n"
                            f"🏪 Thank you for shopping with «{store_obj.name}»! "
                            f"We hope you enjoy your purchase! ✨"
                        )
                    else:
                        deliv_msg = (
                            f"🎉 Ваш заказ #{order.id} успешно доставлен! ✅\n\n"
                            f"🏪 Спасибо за покупку в магазине «{store_obj.name}»! "
                            f"Носите с удовольствием и ждём вас снова! ✨"
                        )
                    _send_telegram_message(order.customer.telegram_chat_id, deliv_msg, bot_token=curr_token)
            except Order.DoesNotExist:
                pass

        elif callback_data.startswith("sa_approve_"):
            store_id = callback_data.replace("sa_approve_", "")
            try:
                store = Store.objects.get(id=store_id)
                store.verification_status = Store.VerificationStatus.APPROVED
                store.is_active = True
                store.save(update_fields=["verification_status", "is_active"])
                requests.post(
                    f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": callback_query_id, "text": f"✅ Магазин '{store.name}' одобрен!"},
                    timeout=5,
                )
                _send_telegram_message(
                    chat_id,
                    f"✅ Магазин «{store.name}» (Владелец: {store.owner.username}, Тел: {store.phone_number}) успешно ОДОБРЕН и активирован! 🎉\nВитрина открыта для покупателей."
                )
                notify_seller_about_approval(store)
            except Store.DoesNotExist:
                pass

        elif callback_data.startswith("sa_reject_"):
            store_id = callback_data.replace("sa_reject_", "")
            try:
                store = Store.objects.get(id=store_id)
                store.verification_status = Store.VerificationStatus.REJECTED
                store.is_active = False
                store.save(update_fields=["verification_status", "is_active"])
                requests.post(
                    f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": callback_query_id, "text": f"❌ Заявка магазина '{store.name}' отклонена."},
                    timeout=5,
                )
                _send_telegram_message(
                    chat_id,
                    f"❌ Заявка магазина «{store.name}» отклонена."
                )
            except Store.DoesNotExist:
                pass

        elif callback_data.startswith("sub_approve_"):
            payment_id = callback_data.replace("sub_approve_", "")
            try:
                payment = SubscriptionPayment.objects.select_related("store", "store__owner").get(id=payment_id)
                if payment.status == "completed":
                    requests.post(
                        f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                        json={"callback_query_id": callback_query_id, "text": "Платёж уже был подтверждён ранее."},
                        timeout=5,
                    )
                else:
                    store = payment.store
                    new_date = store.renew_subscription(
                        months=payment.months,
                        payment_method="transfer",
                        transaction_id=payment.transaction_id
                    )
                    payment.status = "completed"
                    payment.period_end = new_date
                    payment.save(update_fields=["status", "period_end"])

                    requests.post(
                        f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                        json={"callback_query_id": callback_query_id, "text": f"✅ Подписка '{store.name}' продлена до {new_date.strftime('%d.%m.%Y')}!"},
                        timeout=5,
                    )
                    _send_telegram_message(
                        chat_id,
                        f"✅ Оплата подписки магазина «{store.name}» ПОДТВЕРЖДЕНА!\n"
                        f"Сумма: {int(payment.amount):,} сум ({payment.months} мес.)\n"
                        f"Новая дата окончания: {new_date.strftime('%d.%m.%Y')} г. 🎉"
                    )

                    # Уведомляем продавца в Telegram
                    if store.telegram_chat_id:
                        _send_telegram_message(
                            store.telegram_chat_id,
                            f"🎉 Отличная новость! Ваш перевод за подписку BrainMart ({int(payment.amount):,} сум) успешно подтверждён администратором!\n\n"
                            f"🗓 Подписка активна до: {new_date.strftime('%d.%m.%Y')} г.\n"
                            f"✅ Магазин, витрина товаров и ИИ-ассистент работают в штатном режиме 24/7."
                        )
            except Exception as e:
                logger.error("Ошибка при подтверждении подписки: %s", e)

        elif callback_data.startswith("sub_reject_"):
            payment_id = callback_data.replace("sub_reject_", "")
            try:
                payment = SubscriptionPayment.objects.select_related("store").get(id=payment_id)
                payment.status = "rejected"
                payment.save(update_fields=["status"])

                requests.post(
                    f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": callback_query_id, "text": f"❌ Чек магазина '{payment.store.name}' отклонён."},
                    timeout=5,
                )
                _send_telegram_message(
                    chat_id,
                    f"❌ Чек магазина «{payment.store.name}» на сумму {int(payment.amount):,} сум отклонён."
                )
                if payment.store.telegram_chat_id:
                    _send_telegram_message(
                        payment.store.telegram_chat_id,
                        f"⚠️ Ваш чек на оплату подписки ({int(payment.amount):,} сум) не был подтверждён администратором.\n"
                        f"Пожалуйста, свяжитесь с поддержкой @abdvoris07 или загрузите корректный чек в разделе «Финансы»."
                    )
            except Exception as e:
                logger.error("Ошибка при отклонении чека подписки: %s", e)

        elif callback_data.startswith("confirm_pay_"):
            order_id = callback_data.replace("confirm_pay_", "")
            try:
                order = Order.objects.select_related("store", "customer").get(id=order_id)
                if order.status != Order.Status.PAID:
                    order.status = Order.Status.PAID
                    order.save(update_fields=["status"])

                    # СПИСЫВАЕМ ТОВАР В МАРКЕТПЛЕЙСЕ ТОЛЬКО ПОСЛЕ ПОДТВЕРЖДЕНИЯ ПРОДАВЦОМ В БОТЕ
                    for item in order.items.select_related("variant").all():
                        if item.variant:
                            item.variant.stock_quantity = max(0, item.variant.stock_quantity - item.quantity)
                            item.variant.save(update_fields=["stock_quantity"])

                store_obj = order.store
                caller_lang = _get_store_lang(store_obj) if store_obj else "ru"
                curr_token = bot_token or (store_obj.telegram_bot_token if (store_obj and store_obj.telegram_bot_token and store_obj.telegram_bot_active) else getattr(settings, "TELEGRAM_BOT_TOKEN", None))

                if curr_token:
                    requests.post(
                        f"https://api.telegram.org/bot{curr_token}/answerCallbackQuery",
                        json={"callback_query_id": callback_query_id, "text": _nt(caller_lang, 'pay_confirmed_toast', order_id=order_id)},
                        timeout=5,
                    )
                _send_telegram_message(
                    chat_id,
                    _nt(caller_lang, 'pay_confirmed_seller_msg', order_id=order_id),
                    bot_token=curr_token
                )

                # Уведомляем менеджеров на сборку/такси и отправляем подтверждение клиенту
                notify_seller_about_order(order)
            except Order.DoesNotExist:
                pass

        elif callback_data.startswith("reject_pay_"):
            order_id = callback_data.replace("reject_pay_", "")
            try:
                order = Order.objects.select_related("store", "customer").get(id=order_id)
                was_paid = (order.status == Order.Status.PAID)
                if order.status != Order.Status.CANCELLED:
                    order.status = Order.Status.CANCELLED
                    order.save(update_fields=["status"])
                    # Если ранее заказ уже был подтверждён и списан со склада — возвращаем остатки
                    if was_paid:
                        for item in order.items.select_related("variant").all():
                            if item.variant:
                                item.variant.stock_quantity += item.quantity
                                item.variant.save(update_fields=["stock_quantity"])

                store_obj = order.store
                caller_lang = _get_store_lang(store_obj) if store_obj else "ru"
                curr_token = bot_token or (store_obj.telegram_bot_token if (store_obj and store_obj.telegram_bot_token and store_obj.telegram_bot_active) else getattr(settings, "TELEGRAM_BOT_TOKEN", None))

                if curr_token:
                    requests.post(
                        f"https://api.telegram.org/bot{curr_token}/answerCallbackQuery",
                        json={"callback_query_id": callback_query_id, "text": _nt(caller_lang, 'pay_rejected_toast', order_id=order_id)},
                        timeout=5,
                    )
                _send_telegram_message(
                    chat_id,
                    _nt(caller_lang, 'pay_rejected_seller_msg', order_id=order_id),
                    bot_token=curr_token
                )
                if order.customer.telegram_chat_id:
                    cust_lang = getattr(order.customer, "language", "ru") or "ru"
                    _send_telegram_message(
                        order.customer.telegram_chat_id,
                        _nt(cust_lang, 'pay_rejected_customer_msg', store=order.store.name, order_id=order_id, phone=order.store.phone_number or ""),
                        bot_token=curr_token
                    )
            except Order.DoesNotExist:
                pass


def setup_store_telegram_bot(store: Store, bot_token: str) -> tuple[bool, str]:
    """
    Проверяет токен бота из @BotFather, настраивает webhook и WebApp меню.
    Возвращает (success: bool, message: str).
    """
    bot_token = bot_token.strip()
    if not bot_token:
        # Сброс персонального бота и удаление вебхука из Telegram
        if store.telegram_bot_token:
            try:
                requests.post(f"https://api.telegram.org/bot{store.telegram_bot_token}/deleteWebhook", timeout=5)
            except Exception:
                pass
        store.telegram_bot_token = None
        store.telegram_bot_username = None
        store.telegram_bot_active = False
        store.save(update_fields=["telegram_bot_token", "telegram_bot_username", "telegram_bot_active"])
        return (True, "Персональный бот отключен. Магазин переключен на системный бот платформы.")

    # 1. Проверяем токен через getMe
    try:
        me_resp = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
        me_data = me_resp.json()
        if not me_data.get("ok"):
            return (False, f"Неверный токен бота: {me_data.get('description', 'Ошибка Telegram')}")
        bot_user = me_data.get("result", {})
        bot_username = bot_user.get("username", "").lstrip("@")
    except Exception as e:
        return (False, f"Ошибка соединения с Telegram API: {e}")

    # 2. Настраиваем Webhook
    site_url = getattr(settings, "SITE_URL", "https://brainmart.uz").rstrip("/")
    webhook_url = f"{site_url}/api/telegram/webhook/store/{store.id}/"
    secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")

    set_webhook_payload = {
        "url": webhook_url,
        "drop_pending_updates": False,
        "allowed_updates": ["message", "callback_query"],
    }
    if secret:
        set_webhook_payload["secret_token"] = secret

    try:
        wh_resp = requests.post(f"https://api.telegram.org/bot{bot_token}/setWebhook", json=set_webhook_payload, timeout=10)
        wh_data = wh_resp.json()
        if not wh_data.get("ok"):
            logger.warning("Не удалось установить вебхук для бота магазина %s: %s", store.name, wh_data)
    except Exception as e:
        logger.warning("Исключение при setWebhook для магазина %s: %s", store.name, e)

    # 3. Настраиваем кнопку меню (Menu Button WebApp) на витрину магазина
    try:
        store_url = store.get_subdomain_url()
        menu_payload = {
            "menu_button": {
                "type": "web_app",
                "text": "🛍 Магазин",
                "web_app": {
                    "url": store_url
                }
            }
        }
        requests.post(f"https://api.telegram.org/bot{bot_token}/setChatMenuButton", json=menu_payload, timeout=8)
    except Exception as e:
        logger.warning("Исключение при setChatMenuButton для магазина %s: %s", store.name, e)

    # 4. Сохраняем в модели Store
    store.telegram_bot_token = bot_token
    store.telegram_bot_username = bot_username
    store.telegram_bot_active = True
    store.save(update_fields=["telegram_bot_token", "telegram_bot_username", "telegram_bot_active"])

    return (True, f"@{bot_username}")


class StoreCustomTelegramWebhookView(APIView):
    """
    Персональный Webhook для личного Telegram-бота продавца.
    URL: /api/telegram/webhook/store/<int:store_id>/
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request, store_id):
        webhook_secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        if webhook_secret:
            incoming_secret = request.META.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
            if incoming_secret and incoming_secret != webhook_secret:
                return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            store = Store.objects.filter(id=store_id).first()
            if not store:
                return Response(status=status.HTTP_200_OK)
            self._handle_store_bot(request.data, store)
        except Exception:
            logger.exception("Ошибка при обработке персонального Telegram webhook магазина %s", store_id)

        return Response(status=status.HTTP_200_OK)

    def _handle_store_bot(self, data, store: Store):
        bot_token = store.telegram_bot_token
        if not bot_token:
            return

        callback = data.get("callback_query")
        if callback:
            cb_id = callback.get("id")
            v = TelegramWebhookView()
            v._handle_callback(callback, bot_token=bot_token)
            try:
                requests.post(f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery", json={"callback_query_id": cb_id}, timeout=3)
            except Exception:
                pass
            return

        message = data.get("message", {}) or {}
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()

        if not chat_id:
            return

        # Проверяем, ждем ли мы от клиента геопозицию / контакт / чек
        state_data = cache.get(f"tg_state_{chat_id}")
        if state_data:
            v = TelegramWebhookView()
            v._handle(type("Req", (), {"data": data})(), bot_token=bot_token)
            return

        store_url = store.get_subdomain_url()
        owner_chat_id = store.telegram_chat_id

        # 1. Если пишет ВЛАДЕЛЕЦ магазина
        if owner_chat_id and str(chat_id) == str(owner_chat_id):
            if text.startswith("/start"):
                site_url = getattr(settings, "SITE_URL", "https://brainmart.uz")
                kb = {
                    "inline_keyboard": [
                        [{"text": "📊 Панель управления (Dashboard)", "web_app": {"url": f"{site_url}/dashboard/?tg_auth={store.telegram_link_token}" if store.telegram_link_token else f"{site_url}/dashboard/"}}],
                        [{"text": f"🛍 Моя витрина ({store.name})", "web_app": {"url": store_url}}],
                        [{"text": "➕ Добавить новый товар", "web_app": {"url": f"{site_url}/dashboard/products/add/?tg_auth={store.telegram_link_token}" if store.telegram_link_token else f"{site_url}/dashboard/products/add/"}}],
                    ]
                }
                _send_telegram_message(
                    chat_id,
                    f"👋 Здравствуйте, владелец магазина «{store.name}»!\n\n"
                    f"Это ваш персональный бот. Сюда будут мгновенно приходить заказы от покупателей с чеками об оплате, а также консультации от вашего ИИ.\n\n"
                    f"Выберите действие ниже:",
                    reply_markup=kb,
                    bot_token=bot_token
                )
                return

        # 2. Проверка: deep links (/start mgr_... или /start link_...)
        manager_match = re.match(r"^/start\s+mgr_([a-f0-9]+)", text)
        if manager_match:
            v = TelegramWebhookView()
            v._handle(type("Req", (), {"data": data})(), bot_token=bot_token)
            return

        link_match = re.match(r"^/start\s+link_([a-f0-9]+)", text)
        if link_match:
            v = TelegramWebhookView()
            v._handle(type("Req", (), {"data": data})(), bot_token=bot_token)
            return

        # 3. Если ПОКУПАТЕЛЬ написал /start
        if text.startswith("/start"):
            order_match = re.match(r"^/start\s+order_(\d+)", text)
            if order_match:
                v = TelegramWebhookView()
                v._handle(type("Req", (), {"data": data})(), bot_token=bot_token)
                return

            cust = Customer.objects.filter(telegram_chat_id=str(chat_id)).first()
            cust_lang = getattr(cust, "language", "uz") or "uz"

            welcome_msg = {
                "uz": (
                    f"Assalomu alaykum! «{store.name}» rasmiy Telegram-botiga xush kelibsiz! ✨\n\n"
                    f"Katalogimizdagi barcha tovarlarni ko'rish va buyurtma berish uchun quyidagi tugmani bosing.\n"
                    f"Shuningdek, menga xohlagan savolingizni yozishingiz yoki tovar rasmini yuborishingiz mumkin — sun'iy intellekt darhol yordam beradi!"
                ),
                "ru": (
                    f"Здравствуйте! Добро пожаловать в официальный Telegram-бот магазина «{store.name}»! ✨\n\n"
                    f"Нажмите кнопку ниже, чтобы открыть наш каталог и оформить заказ онлайн.\n"
                    f"Вы также можете написать мне любой вопрос или отправить фото нужного товара — наш ИИ-консультант поможет подобрать размер и оформить покупку!"
                ),
                "en": (
                    f"Hello! Welcome to «{store.name}» official Telegram bot! ✨\n\n"
                    f"Tap the button below to browse our online catalog and place an order.\n"
                    f"Feel free to ask any questions or send a photo of what you're looking for — our AI assistant will help you 24/7!"
                ),
            }.get(cust_lang, f"Добро пожаловать в «{store.name}»!")

            btn_label = {
                "uz": f"🛍 {store.name} katalogini ochish",
                "en": f"🛍 Open {store.name} store",
                "ru": f"🛍 Открыть витрину «{store.name}»",
            }.get(cust_lang, f"🛍 Открыть витрину «{store.name}»")

            kb = {
                "inline_keyboard": [
                    [{"text": btn_label, "web_app": {"url": store_url}}]
                ]
            }
            _send_telegram_message(chat_id, welcome_msg, reply_markup=kb, bot_token=bot_token)
            return

        # 4. ПОКУПАТЕЛЬ написал текст, отправил голосовое или фото — отвечает Gemini AI по каталогу этого магазина
        voice_obj = message.get("voice") or message.get("audio") or message.get("video_note")
        voice_file_id = voice_obj.get("file_id") if voice_obj else None

        photo_list = message.get("photo") or []
        photo_file_id = photo_list[-1]["file_id"] if photo_list else None
        if not photo_file_id and message.get("document"):
            doc = message.get("document", {})
            if (doc.get("mime_type") or "").startswith("image/"):
                photo_file_id = doc.get("file_id")

        user_caption = (message.get("caption") or "").strip()
        query_text = (text or user_caption).strip()

        if query_text or photo_file_id or voice_file_id:
            _send_telegram_chat_action(chat_id, "typing", bot_token=bot_token)
            tg_from = message.get("from", {}) or {}
            from core.working_views.services import handle_incoming_telegram_message
            ai_reply, matched_products = handle_incoming_telegram_message(
                chat_id=str(chat_id),
                message_text=query_text,
                first_name=tg_from.get("first_name", ""),
                username=tg_from.get("username", ""),
                photo_file_id=photo_file_id,
                voice_file_id=voice_file_id,
                store=store,
                bot_token=bot_token
            )
            if ai_reply:
                site_url = getattr(settings, "SITE_URL", "https://brainmart.uz")
                cust = Customer.objects.filter(telegram_chat_id=str(chat_id)).first()
                cust_lang = getattr(cust, "language", "uz") or "uz"

                # Если найдены подходящие товары — отправляем реальное фото каждого товара с кнопкой оформления
                if matched_products:
                    for v in matched_products[:3]:
                        photo = v.image or v.product.main_image
                        product_url = f"{site_url}/{cust_lang}/product/{v.product.id}/"
                        caption = (
                            f"✨ {v.product.name}\n"
                            f"💰 Цена: {int(v.final_price):,} сум\n"
                            f"🎨 Цвет: {v.color or '—'} | Размер: {v.size or 'One Size'}"
                        )
                        kb = {
                            "inline_keyboard": [
                                [{"text": f"🛒 Оформить заказ ({v.product.name[:25]})", "web_app": {"url": product_url}}],
                            ]
                        }
                        if photo:
                            _send_telegram_photo(chat_id, photo, caption=caption, reply_markup=kb, bot_token=bot_token)
                        else:
                            _send_telegram_message(chat_id, caption, reply_markup=kb, bot_token=bot_token)

                    # Отправляем сообщение-консультацию ИИ с кнопкой всей витрины
                    store_kb = {
                        "inline_keyboard": [
                            [{"text": f"🛍 Вся витрина «{store.name}»", "web_app": {"url": store_url}}]
                        ]
                    }
                    _send_telegram_message(chat_id, ai_reply, reply_markup=store_kb, bot_token=bot_token)
                else:
                    store_kb = {
                        "inline_keyboard": [
                            [{"text": f"🛍 Открыть витрину «{store.name}»", "web_app": {"url": store_url}}]
                        ]
                    }
                    _send_telegram_message(chat_id, ai_reply, reply_markup=store_kb, bot_token=bot_token)

