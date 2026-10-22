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
                _send_telegram_message(chat_id, ai_reply, reply_markup=global_kb, bot_token=bot_token)
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

