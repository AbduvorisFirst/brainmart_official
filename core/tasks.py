import logging
from celery import shared_task

logger = logging.getLogger(__name__)

@shared_task
def send_telegram_message_task(chat_id, text, web_app_url=None, reply_markup=None):
    """Фоновая задача для отправки простого текста в Telegram."""
    from core.working_views.telegram_bot import _send_telegram_message
    _send_telegram_message(chat_id, text, web_app_url=web_app_url, reply_markup=reply_markup)

@shared_task
def send_telegram_photo_task(chat_id, photo_url_or_path, caption, reply_markup=None):
    """Фоновая задача для отправки фото в Telegram."""
    from core.working_views.telegram_bot import _send_telegram_photo
    _send_telegram_photo(chat_id, photo_url_or_path, caption, reply_markup=reply_markup)

@shared_task
def notify_seller_about_order_task(order_id):
    """Фоновая задача: сборка фото-коллажа и уведомление продавца об оплаченном заказе."""
    from core.models import Order
    from core.working_views.telegram_bot import notify_seller_about_order
    try:
        order = Order.objects.get(id=order_id)
        notify_seller_about_order(order)
    except Order.DoesNotExist:
        logger.warning(f"Order {order_id} not found in notify_seller_about_order_task")
    except Exception as e:
        logger.exception("Ошибка при уведомлении продавца о заказе")

@shared_task
def notify_customer_about_payment_task(order_id):
    """Фоновая задача: уведомление клиента об оплате."""
    from core.models import Order
    from core.working_views.telegram_bot import notify_customer_about_payment
    try:
        order = Order.objects.get(id=order_id)
        notify_customer_about_payment(order)
    except Order.DoesNotExist:
        pass

@shared_task
def notify_customer_about_delivery_task(order_id):
    """Фоновая задача: уведомление клиента об отправке заказа курьером."""
    from core.models import Order
    from core.working_views.telegram_bot import notify_customer_about_delivery
    try:
        order = Order.objects.get(id=order_id)
        notify_customer_about_delivery(order)
    except Order.DoesNotExist:
        pass

@shared_task
def handle_incoming_telegram_message_task(
    chat_id, message_text="", first_name="", username="",
    photo_file_id=None, voice_file_id=None, bot_token=None
):
    """Фоновая задача: обработка текста, фото и голосовых клиента ИИ-ботом в Telegram."""
    from core.working_views.services import handle_incoming_telegram_message
    from core.working_views.telegram_bot import _send_telegram_photo, _send_telegram_message, _send_telegram_chat_action
    from core.models import Customer
    from django.conf import settings
    
    try:
        # Мгновенно показываем клиенту статус 'печатает...', пока ИИ генерирует ответ
        _send_telegram_chat_action(chat_id, "typing", bot_token=bot_token)
        ai_reply, matched_products = handle_incoming_telegram_message(
            chat_id=chat_id,
            message_text=message_text,
            first_name=first_name,
            username=username,
            photo_file_id=photo_file_id,
            voice_file_id=voice_file_id,
            bot_token=bot_token,
        )
        if ai_reply:
            site_url = getattr(settings, 'SITE_URL', 'https://brainmart.uz')
            cust = Customer.objects.filter(telegram_chat_id=str(chat_id)).first()
            cust_lang = getattr(cust, "language", "uz") or "uz"

            global_kb = None
            if not bot_token or bot_token == getattr(settings, "TELEGRAM_BOT_TOKEN", None):
                global_label = {
                    "uz": "🌍 BrainMart Global Bozori",
                    "en": "🌍 BrainMart Global Market",
                    "ru": "🌍 Глобальный маркет BrainMart",
                }.get(cust_lang, "🌍 Глобальный маркет BrainMart")
                global_kb = {"inline_keyboard": [[{"text": global_label, "web_app": {"url": f"{site_url}/{cust_lang}/?global=1"}}]]}

            if matched_products:
                from core.working_views.services import get_best_variant_photo
                v = matched_products[0]
                photo = get_best_variant_photo(v, message_text)
                color_param = f"?color={v.color}" if v.color else ""
                product_url = f"{site_url}/{cust_lang}/product/{v.product.id}/{color_param}"
                if cust_lang == "uz":
                    card_info = (
                        f"✨ <b>{v.product.name}</b>\n"
                        f"💰 Narxi: <b>{int(v.final_price):,} so'm</b>\n"
                        f"🎨 Rangi: {v.color or '—'} | O'lchami: {v.size or 'Standart'} (Omborda: {v.stock_quantity} dona)"
                    )
                    btn_1click = "⚡ 1-klikda buyurtma berish"
                    btn_order_text = f"🛒 Vitrina orqali ({v.product.name[:22]})"
                elif cust_lang == "en":
                    card_info = (
                        f"✨ <b>{v.product.name}</b>\n"
                        f"💰 Price: <b>{int(v.final_price):,} UZS</b>\n"
                        f"🎨 Color: {v.color or '—'} | Size: {v.size or 'One Size'} (In stock: {v.stock_quantity})"
                    )
                    btn_1click = "⚡ 1-Click Order"
                    btn_order_text = f"🛒 Open Product ({v.product.name[:22]})"
                else:
                    card_info = (
                        f"✨ <b>{v.product.name}</b>\n"
                        f"💰 Цена: <b>{int(v.final_price):,} сум</b>\n"
                        f"🎨 Цвет: {v.color or '—'} | Размер: {v.size or 'One Size'} (В наличии: {v.stock_quantity} шт.)"
                    )
                    btn_1click = "⚡ Заказать в 1 клик"
                    btn_order_text = f"🛒 Открыть товар ({v.product.name[:22]})"

                rows = [
                    [{"text": btn_1click, "callback_data": f"quick_buy_{v.id}"}],
                    [{"text": btn_order_text, "web_app": {"url": product_url}}],
                ]
                if global_kb and global_kb.get("inline_keyboard"):
                    rows.extend(global_kb["inline_keyboard"])
                kb = {"inline_keyboard": rows}
                full_caption = f"{ai_reply}\n\n{card_info}"
                if photo and len(full_caption) <= 950:
                    _send_telegram_photo(chat_id, photo, caption=full_caption, reply_markup=kb, bot_token=bot_token)
                elif photo:
                    _send_telegram_photo(chat_id, photo, caption=card_info, reply_markup=kb, bot_token=bot_token)
                    _send_telegram_message(chat_id, ai_reply, reply_markup=global_kb, bot_token=bot_token)
                else:
                    _send_telegram_message(chat_id, full_caption, reply_markup=kb, bot_token=bot_token)
            else:
                _send_telegram_message(chat_id, ai_reply, reply_markup=global_kb, bot_token=bot_token)
    except Exception as e:
        logger.exception("Ошибка при обработке ИИ-сообщения в Telegram: %s", e)

@shared_task
def handle_incoming_instagram_message_task(store_id, instagram_scoped_id, message_text):
    """Фоновая задача: обработка сообщений клиента ИИ-ботом в Instagram."""
    from core.models import Store
    from core.working_views.services import handle_incoming_instagram_message
    try:
        store = Store.objects.get(id=store_id)
        handle_incoming_instagram_message(store, instagram_scoped_id, message_text)
    except Store.DoesNotExist:
        pass
    except Exception as e:
        logger.exception("Ошибка при обработке ИИ-сообщения в Instagram")

@shared_task
def handle_incoming_instagram_comment_task(store_id, comment_id, sender_id, comment_text):
    """Фоновая задача: обработка комментариев клиента ИИ-ботом в Instagram."""
    from core.models import Store
    from core.working_views.services import handle_incoming_instagram_comment
    try:
        store = Store.objects.get(id=store_id)
        handle_incoming_instagram_comment(store, comment_id, sender_id, comment_text)
    except Store.DoesNotExist:
        pass
    except Exception as e:
        logger.exception("Ошибка при обработке комментария в Instagram")


@shared_task
def notify_superadmin_about_new_store_task(store_id):
    """Фоновая задача: уведомление супер-админа о новой заявке на открытие магазина."""
    from core.models import Store
    from core.working_views.telegram_bot import notify_superadmin_about_new_store
    try:
        store = Store.objects.get(id=store_id)
        notify_superadmin_about_new_store(store)
    except Store.DoesNotExist:
        pass
    except Exception as e:
        logger.exception("Ошибка при уведомлении супер-админа о новом магазине")



@shared_task
def send_abandoned_order_reminder_task(order_id: int):
    """
    Авто-напоминание о незавершённом заказе в Telegram (через 20 минут)
    и авто-очистка старых неоплаченных заказов (> 2 часов без чека).
    """
    from datetime import timedelta
    from django.utils import timezone
    from django.core.cache import cache
    from django.conf import settings
    from core.models import Order
    from core.working_views.telegram_bot import _send_telegram_message

    try:
        # 1. Авто-отмена зависших заказов старше 2 часов без прикрепленного чека
        two_hours_ago = timezone.now() - timedelta(hours=2)
        Order.objects.filter(
            status=Order.Status.PENDING_PAYMENT,
            payment_confirmed_by_customer=False,
            created_at__lt=two_hours_ago,
        ).update(status=Order.Status.CANCELLED)

        # 2. Проверяем целевой заказ для вежливого напоминания клиенту
        order = Order.objects.select_related("store", "customer").filter(id=order_id).first()
        if not order or order.status != Order.Status.PENDING_PAYMENT or order.payment_confirmed_by_customer:
            return

        cache_key = f"abandoned_reminded_{order.id}"
        if cache.get(cache_key):
            return
        cache.set(cache_key, True, timeout=86400)

        chat_id = getattr(order.customer, "telegram_chat_id", None)
        if not chat_id:
            return

        store = order.store
        bot_token = store.telegram_bot_token if (store.telegram_bot_token and store.telegram_bot_active) else getattr(settings, "TELEGRAM_BOT_TOKEN", None)
        site_url = getattr(settings, "SITE_URL", "https://brainmart.uz")
        cust_lang = getattr(order.customer, "language", "ru") or "ru"
        order_url = f"{site_url}/{cust_lang}/order/{order.id}/success/"

        if cust_lang == "uz":
            msg = (
                f"👋 Assalomu alaykum! Siz <b>«{store.name}»</b> do'konida <b>#{order.id}</b>-sonli buyurtmani boshlagan edingiz.\n\n"
                f"🛍 Tanlagan mahsulotingiz hali ham siz uchun band qilib turilibdi (Summa: <b>{int(order.total_price):,} so'm</b>).\n"
                f"📸 Buyurtmani yakunlash uchun to'lov chekini yuboring yoki quyidagi tugmani bosing:"
            )
            btn_label = "💳 Buyurtmani yakunlash"
        elif cust_lang == "en":
            msg = (
                f"👋 Hello! You started order <b>#{order.id}</b> at <b>«{store.name}»</b>.\n\n"
                f"🛍 Your item is still reserved for you (Total: <b>{int(order.total_price):,} UZS</b>).\n"
                f"📸 Send your payment receipt screenshot or tap the button below to complete your order:"
            )
            btn_label = "💳 Complete Order"
        else:
            msg = (
                f"👋 Здравствуйте! Вы оформили заказ <b>#{order.id}</b> в магазине <b>«{store.name}»</b>, но ещё не завершили его.\n\n"
                f"🛍 Ваш товар всё ещё забронирован за вами (К оплате: <b>{int(order.total_price):,} сум</b>).\n"
                f"📸 Пришлите скриншот чека перевода прямо сюда в чат или нажмите кнопку ниже:"
            )
            btn_label = "💳 Завершить заказ"

        kb = {"inline_keyboard": [[{"text": btn_label, "web_app": {"url": order_url}}]]}
        _send_telegram_message(chat_id, msg, reply_markup=kb, bot_token=bot_token)
    except Exception as e:
        logger.warning("send_abandoned_order_reminder_task error: %s", e)
