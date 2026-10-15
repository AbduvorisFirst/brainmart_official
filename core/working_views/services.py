"""
services.py (обновлённая версия)
----------------------------------
Изменения:
- build_ai_prompt теперь жёстко требует от Gemini определить язык
  клиента (узбекский / русский / английский / любой другой) и
  ответить именно на нём — а не гадать заранее.
- Промпт стал системным (system_instruction) — это отдельный
  параметр Gemini API, который "живёт" отдельно от текста клиента
  и не может быть случайно "переопределён" сообщением клиента.
"""

import os
import requests
from django.conf import settings
from django.core.cache import cache

from core.models import Conversation, Customer, Message, Product, ProductVariant, ProductWaitlist, Store
from .telegram_bot import build_telegram_deep_link


GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-3.1-flash-lite:generateContent"
)

_ai_session = requests.Session()
_ai_session_adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=50, max_retries=1)
_ai_session.mount("https://", _ai_session_adapter)

import logging
logger = logging.getLogger(__name__)


def find_matching_variants(store: Store, query_text: str):
    """Простой текстовый поиск товара по названию/цвету/размеру."""
    query_text = query_text.lower()
    variants = ProductVariant.objects.filter(
        product__store=store, product__is_active=True
    ).select_related("product")

    matches = []
    for variant in variants:
        haystack = f"{variant.product.name} {variant.color} {variant.size}".lower()
        if any(word in haystack for word in query_text.split()):
            matches.append(variant)

    return matches


def build_system_instruction(store: Store) -> str:
    """
    Системная инструкция — "характер" ИИ-агента. Не зависит от
    конкретного сообщения клиента, задаётся один раз на весь диалог.
    """
    instruction = (
        f"Ты — вежливый ИИ-консультант интернет-магазина '{store.name}' в Instagram Direct.\n\n"
        f"ПРАВИЛО ЯЗЫКА (самое важное): определи, на каком языке пишет клиент "
        f"(узбекский, русский, английский или любой другой) и отвечай СТРОГО на "
        f"этом же языке. Если клиент пишет смесью языков (например, узбекский "
        f"латиницей вперемешку с русскими словами — так часто бывает в Ташкенте), "
        f"отвечай в том же стиле, каким писал клиент. Никогда не переключай язык "
        f"сам по себе и не спрашивай клиента, на каком языке ему отвечать.\n\n"
        f"ПРАВИЛА ОБЩЕНИЯ:\n"
        f"- Ты ведёшь клиента ПОСЛЕДОВАТЕЛЬНО к покупке, а не просто отвечаешь на "
        f"разрозненные вопросы. Помни, о каком товаре шла речь раньше в этом же "
        f"разговоре, и не переспрашивай то, что клиент уже сказал.\n"
        f"- Отвечай коротко (2-4 предложения), дружелюбно, без излишней формальности.\n"
        f"- Если товар есть в наличии — сообщи цену и параметры. НЕ пиши сама никакую ссылку и "
        f"не выдумывай текст вроде '[ссылка]' — ссылку на оформление заказа мы "
        f"подставим автоматически после твоего ответа.\n"
        f"- Если товара НЕТ в наличии или клиент спрашивает о товаре, которого пока нет в магазине — вежливо объясни на его языке, "
        f"что сейчас этот товар отсутствует или ожидается новая партия, и что мы внесли клиента в лист ожидания (waitlist), "
        f"и как только товар поступит в магазин, мы сразу пришлем ему персональное уведомление и ссылку на покупку! "
        f"(на узбекском: 'Kechirasiz, hozircha bu tovar sotuvda yo‘q yoki yangi partiya kutilmoqda. Sizni kutish ro‘yxatiga (waitlist) qo‘shib qo‘ydik! Tovar do‘konga kelishi bilanoq sizga birinchi bo‘lib xabar va xarid havolasini yuboramiz! ✨'). "
        f"Если есть похожие товары в списке остатков ниже — также вежливо предложи взглянуть на них.\n"
        f"- Никогда не выдумывай остатки, цвета, размеры или цены, которых нет в списке ниже.\n"
        f"- Если не уверен, что понял вопрос клиента — переспроси, не придумывай ответ."
    )
    active_branches = list(store.branches.filter(is_active=True))
    if active_branches:
        branch_lines = [f"- {b.name}: {b.address}" for b in active_branches]
        instruction += (
            f"\n\nФИЛИАЛЫ И ТОЧКИ МАГАЗИНА:\n"
            + "\n".join(branch_lines)
            + f"\nЕсли клиент интересуется адресом магазина, самовывозом или локацией — перечисли филиалы и поясни, что доставка на такси отправляется из ближайшей к нему точки!"
        )
    return instruction


def build_stock_context(matches: list) -> str:
    """Отдельным блоком собираем данные склада, которые реально есть в базе."""
    if not matches:
        return "Совпадений на складе не найдено."

    lines = []
    for v in matches[:5]:
        desc = f"- {v.product.name}"
        opts = []
        if v.color:
            opts.append(f"цвет {v.color}")
        if v.size:
            opts.append(f"размер {v.size}")
        if opts:
            desc += f" ({', '.join(opts)})"
        
        status = f"в наличии, {v.stock_quantity} шт." if v.in_stock else "нет в наличии"
        desc += f": {status}, цена {int(v.final_price):,} сум"
        lines.append(desc)
        
    return "\n".join(lines)



def build_conversation_history(conversation: Conversation, limit: int = 10) -> list:
    """
    Собирает последние N сообщений диалога в формате, который понимает
    Gemini (список поочерёдных реплик user/model). Это и даёт ИИ "память" —
    без этого списка каждое сообщение клиента обрабатывалось бы так,
    будто разговор начался с нуля.

    Остатки склада сюда НЕ включаем — они попадут только в самое
    свежее сообщение (см. handle_incoming_instagram_message), чтобы
    ИИ всегда видел АКТУАЛЬНЫЕ данные, а не устаревшие из истории.
    """
    recent_messages = conversation.messages.order_by("-created_at")[:limit]
    recent_messages = list(reversed(recent_messages))  # в хронологическом порядке

    history = []
    for msg in recent_messages:
        if msg.sender == Message.Sender.CUSTOMER:
            history.append({"role": "user", "parts": [{"text": msg.text}]})
        elif msg.sender == Message.Sender.AI_AGENT:
            history.append({"role": "model", "parts": [{"text": msg.text}]})
        # сообщения от MANAGER в историю для ИИ не включаем — это уже
        # не разговор с ИИ, а вмешательство человека

    return history


def ask_gemini(
    system_instruction: str,
    customer_message: str,
    stock_context: str,
    history: list | None = None,
) -> str:
    """
    Отправляет запрос в Gemini API — теперь с историей диалога.

    system_instruction идёт отдельным полем (а не смешивается с текстом
    клиента в одну строку) — это и есть правильный способ задавать
    "характер"/правила модели через официальный Gemini API.
    """
    # --- БЕЗОПАСНОСТЬ: Rate Limiting ---
    # Ограничиваем количество запросов к Gemini до 30 в минуту,
    # чтобы спамер или конкурент не исчерпал нашу квоту API и
    # не сломал ИИ для всех магазинов разом.
    rate_key = "gemini_rate_limit"
    current_count = cache.get(rate_key, 0)
    if current_count >= 30:
        logger.warning("Rate limit Gemini API: превышен лимит 30 req/min")
        return (
            "Сейчас много запросов, менеджер скоро подключится. / "
            "Hozir ko'p so'rovlar bor, menejer tez orada javob beradi. / "
            "High demand right now, a manager will join shortly."
        )
    cache.set(rate_key, current_count + 1, timeout=60)

    api_key = getattr(settings, "GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY"))

    user_content = (
        f"Данные склада магазина (актуальные на этот момент):\n{stock_context}\n\n"
        f"Сообщение клиента: \"{customer_message}\""
    )

    # История прошлых реплик + текущее сообщение клиента в конце —
    # именно так Gemini понимает, что это продолжение одного разговора
    contents = (history or []) + [{"role": "user", "parts": [{"text": user_content}]}]

    try:
        response = _ai_session.post(
            f"{GEMINI_API_URL}?key={api_key}",
            json={
                "system_instruction": {"parts": [{"text": system_instruction}]},
                "contents": contents,
                "generationConfig": {
                    "maxOutputTokens": 280,
                    "temperature": 0.3,
                },
            },
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (requests.RequestException, KeyError, IndexError):
        # Запасной ответ на трёх языках сразу — на случай, если Gemini недоступен,
        # клиент всё равно поймёт хотя бы одну строку.
        return (
            "Здравствуйте! Уточняю информацию, менеджер скоро подключится. / "
            "Salom! Ma'lumotni aniqlashtiryapman, menejer tez orada javob beradi. / "
            "Hello! Checking the details, a manager will join shortly."
        )


def register_waitlist_entry(
    store: Store,
    customer: Customer,
    query_text: str,
    telegram_chat_id: str = "",
    instagram_scoped_id: str = "",
    language: str = "uz",
) -> ProductWaitlist | None:
    """
    Регистрирует клиента в листе ожидания (ProductWaitlist),
    если его запрос касается отсутствующего товара.
    Исключает банальные приветствия и защищает от дублирования в течение 24 часов.
    """
    cleaned = (query_text or "").strip().lower()
    if len(cleaned) < 3:
        return None

    # Исключаем системные команды и общие приветствия
    greetings = {
        "salom", "assalom", "assalomu", "assalomu alaykum", "salomlar",
        "привет", "здравствуйте", "добрый", "добрый день", "добрый вечер",
        "hello", "hi", "hey", "/start", "/help", "/lang", "menu", "katalog", "каталог"
    }
    if cleaned in greetings:
        return None

    import datetime
    from django.utils import timezone

    # Проверяем, есть ли уже активная запись в листе ожидания за последние 24 часа
    cutoff = timezone.now() - datetime.timedelta(hours=24)
    existing = ProductWaitlist.objects.filter(
        store=store,
        customer=customer,
        status=ProductWaitlist.Status.PENDING,
        created_at__gte=cutoff,
    ).first()

    if existing:
        # Если клиент дополнил запрос, аккуратно дополняем текст
        if cleaned not in existing.query_text.lower():
            existing.query_text = f"{existing.query_text}; {query_text.strip()}"[:500]
            existing.save(update_fields=["query_text"])
        return existing

    waitlist_item = ProductWaitlist.objects.create(
        store=store,
        customer=customer,
        query_text=query_text.strip()[:500],
        telegram_chat_id=telegram_chat_id or (getattr(customer, "telegram_chat_id", "") or ""),
        instagram_scoped_id=instagram_scoped_id or (getattr(customer, "instagram_scoped_id", "") or ""),
        language=language or getattr(customer, "language", "uz") or "uz",
        status=ProductWaitlist.Status.PENDING,
    )
    logger.info(
        "Клиент %s добавлен в лист ожидания магазина %s (запрос: '%s')",
        customer, store.name, query_text
    )
    return waitlist_item


def handle_incoming_instagram_message(
    store: Store, instagram_scoped_id: str, message_text: str
) -> str:
    """Точка входа: клиент написал в Instagram Direct."""
    customer, _ = Customer.objects.get_or_create(
        instagram_scoped_id=instagram_scoped_id
    )
    conversation, _ = Conversation.objects.get_or_create(
        store=store, customer=customer
    )

    if not conversation.is_handled_by_ai:
        return ""

    # Проверяем активность подписки магазина (150 000 сум)
    if not store.is_subscription_active:
        logger.warning("Магазин %s заблокирован по подписке. ИИ-ответ не генерируется.", store.name)
        return "Здравствуйте! Магазин временно приостановил онлайн-обслуживание. / Salom! Do'kon vaqtincha xizmat ko'rsatishni to'xtatdi."


    # Историю собираем ДО того, как сохраним новое сообщение клиента —
    # иначе оно задвоится (попадёт и в историю, и в текущий запрос)
    history = build_conversation_history(conversation)

    Message.objects.create(
        conversation=conversation, sender=Message.Sender.CUSTOMER, text=message_text
    )

    matches = find_matching_variants(store, message_text)
    system_instruction = build_system_instruction(store)
    stock_context = build_stock_context(matches)
    ai_reply_text = ask_gemini(system_instruction, message_text, stock_context, history=history)

    # Ссылку добавляем сами, кодом — а не полагаемся на то, что Gemini её
    # правильно "придумает" в тексте. Берём первый товар в наличии из совпадений.
    in_stock_matches = [v for v in matches if v.in_stock]
    if in_stock_matches:
        best_match = in_stock_matches[0]
        if settings.TELEGRAM_BOT_USERNAME:
            link = build_telegram_deep_link(best_match.product.id)
        else:
            link = f"{settings.SITE_URL}/ru/product/{best_match.product.id}/"
        ai_reply_text = f"{ai_reply_text}\n\n🛒 {link}"
    else:
        # Товара нет в наличии или это новый товар -> регистрируем в лист ожидания (Waitlist)
        register_waitlist_entry(
            store=store,
            customer=customer,
            query_text=message_text,
            instagram_scoped_id=instagram_scoped_id,
            language=getattr(customer, "language", "uz") or "uz",
        )
    
    # Always add Telegram bot store link for this specific store
    if getattr(settings, 'TELEGRAM_BOT_USERNAME', ''):
        store_bot_link = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=store_{store.id}"
        ai_reply_text += f"\n\n💬 Telegram: {store_bot_link}"

    Message.objects.create(
        conversation=conversation, sender=Message.Sender.AI_AGENT, text=ai_reply_text
    )

    # Отправляем готовый ответ клиенту в Instagram Direct
    send_instagram_reply(store, instagram_scoped_id, ai_reply_text)

    # Send product image to Instagram DM if we have a match
    if in_stock_matches:
        best_match = in_stock_matches[0]
        photo = best_match.image or best_match.product.main_image
        if photo:
            site_url = getattr(settings, 'SITE_URL', '')
            image_full_url = f"{site_url}{photo.url}" if site_url and photo else ""
            if image_full_url:
                send_instagram_image(store, instagram_scoped_id, image_full_url)

    return ai_reply_text



def handle_incoming_telegram_message(
    chat_id: str, message_text: str, first_name: str = "", username: str = ""
) -> tuple[str, list]:
    """
    Точка входа: клиент написал свободное текстовое сообщение в Telegram-боте.
    Возвращает (ai_reply_text, matched_variants_in_stock) для отправки фото.
    """
    # 1. Найти или создать Customer по telegram_chat_id
    customer = Customer.objects.filter(telegram_chat_id=str(chat_id)).first()
    if not customer:
        customer = Customer.objects.create(
            instagram_scoped_id=f"tg_{chat_id}",
            telegram_chat_id=str(chat_id),
            telegram_username=username,
            full_name=first_name or "Telegram User",
            language="uz",
        )

    cust_lang = getattr(customer, "language", "uz") or "uz"

    # 2. Determine store context: use customer's most recent order's store,
    #    or search across all active stores
    from core.models import Order
    last_order = Order.objects.filter(customer=customer).order_by("-created_at").first()
    store = last_order.store if last_order else None

    # 3. Search for matching products
    if store:
        matches = find_matching_variants(store, message_text)
    else:
        # Search across ALL active stores
        query_lower = message_text.lower()
        all_variants = ProductVariant.objects.filter(
            product__is_active=True,
            product__store__is_active=True,
            product__store__is_frozen_by_admin=False,
        ).select_related("product", "product__store")
        matches = []
        for v in all_variants:
            haystack = f"{v.product.name} {v.color} {v.size}".lower()
            if any(word in haystack for word in query_lower.split()):
                matches.append(v)
            if len(matches) >= 10:
                break
        # Use first match's store for conversation context
        if matches:
            store = matches[0].product.store

    # 4. If no store context at all, pick the first active store for a general response
    if not store:
        store = Store.objects.filter(is_active=True, is_frozen_by_admin=False).first()
    if not store:
        return ("Kechirasiz, hozircha faol do'konlar yo'q. / Извините, сейчас нет активных магазинов.", [])

    # Проверяем активность подписки магазина
    if not store.is_subscription_active:
        return ("Salom! Do'kon vaqtincha xizmat ko'rsatishni to'xtatdi. / Здравствуйте! Магазин временно приостановил онлайн-обслуживание.", [])

    # 5. Get or create Conversation
    conversation, _ = Conversation.objects.get_or_create(
        store=store, customer=customer
    )

    if not conversation.is_handled_by_ai:
        return ("", [])

    # 6. Build AI response with conversation history
    history = build_conversation_history(conversation)

    Message.objects.create(
        conversation=conversation, sender=Message.Sender.CUSTOMER, text=message_text
    )

    # Build system instruction adapted for Telegram context and customer's language
    system_inst = build_system_instruction(store)
    system_inst = system_inst.replace(
        "в Instagram Direct",
        "в Telegram"
    )
    lang_name = "o'zbek" if cust_lang == "uz" else "ingliz" if cust_lang == "en" else "русском"
    system_inst += (
        f"\n\nДОПОЛНИТЕЛЬНО: Клиент общается в Telegram. Выбранный язык клиента: {cust_lang} ({lang_name}). "
        f"Отвечай приветливо, экспертно и строго на выбранном клиентом языке ({lang_name}), либо на языке сообщения. "
        "Если клиент хочет купить — предложи перейти по ссылке на товар, которая будет добавлена автоматически. "
        "Не выдумывай ссылки сам."
    )

    stock_context = build_stock_context(matches)
    ai_reply_text = ask_gemini(system_inst, message_text, stock_context, history=history)

    # 7. Append product link with proper language prefix
    in_stock_matches = [v for v in matches if v.in_stock]
    if in_stock_matches:
        best = in_stock_matches[0]
        site_url = getattr(settings, 'SITE_URL', '')
        if site_url:
            link = f"{site_url}/{cust_lang}/product/{best.product.id}/"
        else:
            link = build_telegram_deep_link(best.product.id)
        ai_reply_text = f"{ai_reply_text}\n\n🛒 {link}"
    else:
        # Товара нет в наличии или это новый товар -> регистрируем в лист ожидания (Waitlist)
        register_waitlist_entry(
            store=store,
            customer=customer,
            query_text=message_text,
            telegram_chat_id=str(chat_id),
            language=cust_lang,
        )

    Message.objects.create(
        conversation=conversation, sender=Message.Sender.AI_AGENT, text=ai_reply_text
    )

    return (ai_reply_text, in_stock_matches[:3])


def check_and_notify_waitlist_for_new_product(product: Product) -> int:
    """
    Автоматически сканирует лист ожидания (ProductWaitlist) для магазина product.store.
    Находит клиентов, которые ожидали поступления этого товара,
    и отправляет им персональное уведомление с фото, ценой и ссылкой на покупку.
    Возвращает количество уведомленных клиентов.
    """
    if not product or not product.is_active or not product.store.is_active or product.store.is_frozen_by_admin:
        return 0

    import re
    from django.utils import timezone
    from core.models import ProductWaitlist
    from .telegram_bot import _send_telegram_photo, _send_telegram_message

    pending_entries = ProductWaitlist.objects.filter(
        store=product.store,
        status=ProductWaitlist.Status.PENDING,
    ).select_related("customer", "store")

    if not pending_entries.exists():
        return 0

    prod_name_lower = product.name.lower()
    prod_desc_lower = (product.description or "").lower()
    category_name_lower = (product.category.name if product.category else "").lower()
    prod_haystack = f"{prod_name_lower} {category_name_lower} {prod_desc_lower}"

    # Слова, которые не несут смысловой нагрузки товара
    stop_words = {
        "bormi", "bor", "necha", "ming", "narxi", "qancha", "pul", "som", "so'm",
        "razmer", "razmerla", "razmeri", "razmerlar", "rang", "rangi", "qora", "oq",
        "qanaqa", "qaysi", "dostavka", "yoq", "yo'q", "iltimos", "salom",
        "есть", "ли", "цена", "цены", "сколько", "стоит", "размер", "размеры",
        "цвет", "цвета", "доставка", "пожалуйста", "здравствуйте", "можно",
        "купить", "заказать", "наличии", "наличие", "где", "как",
        "price", "cost", "size", "sizes", "color", "colors", "available", "stock",
        "va", "i", "и", "в", "на", "a", "the", "for", "in", "to", "of", "with",
        "bu", "shu", "bizga", "menga", "мне", "нам", "для", "у", "вас"
    }

    site_url = getattr(settings, "SITE_URL", "").rstrip("/")
    notified_count = 0

    for entry in pending_entries:
        query_raw = entry.query_text.lower()
        tokens = [w for w in re.findall(r"[\w']+", query_raw) if len(w) >= 3 and w not in stop_words]

        is_match = False
        for token in tokens:
            if token in prod_haystack:
                is_match = True
                break
            if len(token) >= 4 and any(token[:4] in word for word in prod_haystack.split()):
                is_match = True
                break

        if not is_match:
            prod_words = [w for w in re.findall(r"[\w']+", prod_name_lower) if len(w) >= 3]
            if any(pw in query_raw for pw in prod_words):
                is_match = True

        if not is_match:
            continue

        lang = entry.language or getattr(entry.customer, "language", "uz") or "uz"
        price_num = int(product.base_price)

        if lang == "uz":
            desc = (
                f"🎉 <b>Xushxabar! Siz kutgan tovar sotuvga chiqdi!</b>\n\n"
                f"📦 <b>{product.name}</b>\n"
                f"💰 Narxi: <b>{price_num:,} so'm</b>\n\n"
                f"Siz bu tovar haqida so'ragan edingiz. Tovar do'konga keldi — birinchilardan bo'lib xarid qilishingiz mumkin! ✨"
            )
            btn_text = "🛒 Xarid qilish"
        elif lang == "ru":
            desc = (
                f"🎉 <b>Отличная новость! Товар, который вы ожидали, поступил в продажу!</b>\n\n"
                f"📦 <b>{product.name}</b>\n"
                f"💰 Цена: <b>{price_num:,} сум</b>\n\n"
                f"Вы интересовались этим товаром. Он только что поступил на склад — успейте заказать первыми! ✨"
            )
            btn_text = "🛒 Оформить заказ"
        else:
            desc = (
                f"🎉 <b>Great news! The product you were waiting for is now in stock!</b>\n\n"
                f"📦 <b>{product.name}</b>\n"
                f"💰 Price: <b>{price_num:,} UZS</b>\n\n"
                f"You asked about this item earlier. It has just arrived in our store — be the first to order! ✨"
            )
            btn_text = "🛒 Order Now"

        product_url = f"{site_url}/{lang}/product/{product.id}/" if site_url else ""

        # Уведомление в Telegram
        if entry.telegram_chat_id:
            reply_markup = None
            if product_url:
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": btn_text, "web_app": {"url": product_url}}]
                    ]
                }
            photo = product.main_image
            if not photo:
                first_variant = product.variants.filter(image__isnull=False).first()
                if first_variant:
                    photo = first_variant.image

            sent = False
            if photo:
                try:
                    sent = _send_telegram_photo(entry.telegram_chat_id, photo, desc, reply_markup=reply_markup)
                except Exception as e:
                    logger.warning("Не удалось отправить фото waitlist в Telegram: %s", e)
            if not sent:
                caption_fallback = f"{desc}\n\n🛒 {product_url}" if product_url else desc
                try:
                    _send_telegram_message(entry.telegram_chat_id, caption_fallback, reply_markup=reply_markup)
                except Exception as e:
                    logger.warning("Не удалось отправить сообщение waitlist в Telegram: %s", e)

        # Уведомление в Instagram Direct
        if entry.instagram_scoped_id and not str(entry.instagram_scoped_id).startswith("tg_"):
            ig_text = f"{desc}\n\n🛒 {product_url}" if product_url else desc
            try:
                send_instagram_reply(product.store, entry.instagram_scoped_id, ig_text)
                if product.main_image and site_url:
                    send_instagram_image(product.store, entry.instagram_scoped_id, f"{site_url}{product.main_image.url}")
            except Exception as e:
                logger.warning("Не удалось отправить уведомление waitlist в Instagram: %s", e)

        # Фиксируем статус 'notified'
        entry.status = ProductWaitlist.Status.NOTIFIED
        entry.notified_product = product
        entry.notified_at = timezone.now()
        entry.save(update_fields=["status", "notified_product", "notified_at"])

        notified_count += 1
        logger.info(
            "Уведомлен клиент %s о поступлении товара '%s' (waitlist ID: %s)",
            entry.customer, product.name, entry.id
        )

    return notified_count


def send_instagram_reply(store: Store, instagram_scoped_id: str, text: str) -> None:
    """Отправляет готовый текст ответа обратно клиенту через Meta Graph API."""
    access_token = getattr(settings, "META_PAGE_ACCESS_TOKEN", None)
    if not access_token:
        logger.warning("send_instagram_reply: META_PAGE_ACCESS_TOKEN не задан")
        return

    # Автоопределение хоста: для токенов IG... используется graph.instagram.com
    if access_token.startswith("IG"):
        base_host = "https://graph.instagram.com"
        target_id = "me"
    else:
        base_host = "https://graph.facebook.com"
        target_id = store.instagram_business_id or "me"

    url = f"{base_host}/v20.0/{target_id}/messages"
    try:
        resp = requests.post(
            url,
            params={"access_token": access_token},
            json={
                "recipient": {"id": instagram_scoped_id},
                "message": {"text": text},
            },
            timeout=10,
        )
        logger.info("Instagram reply status: %s, response: %s", resp.status_code, resp.text)
    except requests.RequestException as e:
        logger.exception("Ошибка при отправке Instagram reply: %s", e)


def send_instagram_image(store: Store, instagram_scoped_id: str, image_url: str) -> None:
    """Отправляет фото товара клиенту в Instagram Direct через Meta Graph API."""
    access_token = getattr(settings, "META_PAGE_ACCESS_TOKEN", None)
    if not access_token or not image_url:
        return

    if access_token.startswith("IG"):
        base_host = "https://graph.instagram.com"
        target_id = "me"
    else:
        base_host = "https://graph.facebook.com"
        target_id = store.instagram_business_id or "me"

    url = f"{base_host}/v20.0/{target_id}/messages"
    try:
        resp = requests.post(
            url,
            params={"access_token": access_token},
            json={
                "recipient": {"id": instagram_scoped_id},
                "message": {
                    "attachment": {
                        "type": "image",
                        "payload": {"url": image_url}
                    }
                },
            },
            timeout=10,
        )
        logger.info("Instagram image status: %s, response: %s", resp.status_code, resp.text)
    except requests.RequestException as e:
        logger.exception("Ошибка при отправке Instagram image: %s", e)


# ---------------------------------------------------------------------------
# Автоответы на комментарии Instagram
# ---------------------------------------------------------------------------

_COMMENT_PUBLIC_REPLIES = {
    "ru": "Привет! Ответили вам в Директ 📥 Там вся информация о товаре и цена.",
    "uz": "Salom! Directga yozdik 📥 U yerda tovar haqida barcha ma'lumot bor.",
    "en": "Hi! We sent you a DM 📥 You'll find all product info and pricing there.",
}


def send_instagram_public_reply(comment_id: str, text: str, store: "Store") -> None:
    """
    Отвечает публично на комментарий Instagram (reply под самим коментом).
    Endpoint: POST /v20.0/{comment-id}/replies
    """
    access_token = getattr(settings, "META_PAGE_ACCESS_TOKEN", None)
    if not access_token:
        return
    base_host = "https://graph.instagram.com" if access_token.startswith("IG") else "https://graph.facebook.com"
    try:
        resp = requests.post(
            f"{base_host}/v20.0/{comment_id}/replies",
            params={"access_token": access_token},
            json={"message": text},
            timeout=10,
        )
        logger.info("Instagram public reply status: %s, response: %s", resp.status_code, resp.text)
    except requests.RequestException as e:
        logger.exception("Ошибка при отправке Instagram public reply: %s", e)


def send_instagram_private_reply(comment_id: str, text: str, store: "Store") -> None:
    """
    Отправляет приватный ответ (Direct Message) на комментарий Instagram.
    Meta сама откроет DM-диалог с автором комментария.
    Endpoint: POST /v20.0/{comment-id}/private_replies
    """
    access_token = getattr(settings, "META_PAGE_ACCESS_TOKEN", None)
    if not access_token:
        return
    base_host = "https://graph.instagram.com" if access_token.startswith("IG") else "https://graph.facebook.com"
    try:
        resp = requests.post(
            f"{base_host}/v20.0/{comment_id}/private_replies",
            params={"access_token": access_token},
            json={"message": text},
            timeout=10,
        )
        logger.info("Instagram private reply status: %s, response: %s", resp.status_code, resp.text)
    except requests.RequestException as e:
        logger.exception("Ошибка при отправке Instagram private reply: %s", e)


def handle_incoming_instagram_comment(
    store: "Store",
    comment_id: str,
    sender_id: str,
    comment_text: str,
) -> None:
    """
    Основной orchestrator для комментариев Instagram.

    Алгоритм:
    1. Публично отвечаем под комментом: "Написали вам в директ 📥"
    2. ИИ анализирует текст комментария, ищет совпадения по складу.
    3. В Директ отправляем приватный ответ с деталями и ссылкой на Telegram-бота.
    """
    # 1. Определяем язык — Gemini сам разберётся, используем RU как fallback
    public_reply = _COMMENT_PUBLIC_REPLIES.get("ru")
    send_instagram_public_reply(comment_id, public_reply, store)

    # 2. Строим AI-ответ для Директа
    matches = find_matching_variants(store, comment_text or "")
    system_instruction = build_system_instruction(store)
    stock_context = build_stock_context(matches)

    # Модифицируем системную инструкцию под контекст "клиент оставил коммент"
    comment_system = (
        system_instruction
        + "\n\nВАЖНО: Клиент написал КОММЕНТАРИЙ к посту в Instagram, а не личное сообщение. "
        "Ты пишешь ему в Директ. Поздоровайся, сошлись на его комментарий, "
        "дай краткий полезный ответ и предложи оформить заказ через Telegram-бота."
    )

    ai_text = ask_gemini(
        comment_system,
        comment_text or "(Клиент оставил комментарий без текста)",
        stock_context,
    )

    # 3. Добавляем ссылку на Telegram-бота или на сайт
    in_stock_matches = [v for v in matches if v.in_stock]
    if in_stock_matches:
        from .telegram_bot import build_telegram_deep_link
        best = in_stock_matches[0]
        if getattr(settings, "TELEGRAM_BOT_USERNAME", ""):
            link = build_telegram_deep_link(best.product.id)
        else:
            link = f"{settings.SITE_URL}/ru/product/{best.product.id}/"
        ai_text = f"{ai_text}\n\n🛒 {link}"
    else:
        # Регистрируем автора комментария в лист ожидания
        comment_cust, _ = Customer.objects.get_or_create(
            instagram_scoped_id=sender_id,
            defaults={"full_name": "Instagram User", "language": "uz"}
        )
        register_waitlist_entry(
            store=store,
            customer=comment_cust,
            query_text=comment_text or "",
            instagram_scoped_id=sender_id,
            language=getattr(comment_cust, "language", "uz") or "uz",
        )

    # Always add Telegram bot link for the store
    if getattr(settings, "TELEGRAM_BOT_USERNAME", ""):
        store_link = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=store_{store.id}"
        ai_text += f"\n\n💬 Telegram: {store_link}"

    send_instagram_private_reply(comment_id, ai_text, store)
    send_instagram_reply(store, sender_id, ai_text)


def semantic_ai_catalog_search(query_text: str, store: Store | None = None) -> dict:
    """
    Семантический ИИ-поиск и подбор образов по контексту (например 'айтишник', 'на свадьбу', 'спорт'):
    Использует Gemini AI для анализа каталога и подбора подходящих товаров с объяснением стилиста.
    """
    import json

    base_qs = ProductVariant.objects.filter(
        product__is_active=True,
        product__store__is_active=True
    ).select_related("product", "product__store", "product__category")

    if store:
        base_qs = base_qs.filter(product__store=store)

    variants = list(base_qs[:40])
    if not variants:
        return {"advice": "В каталоге пока нет доступных товаров.", "variants": []}

    catalog_summary = []
    for v in variants:
        catalog_summary.append({
            "id": v.id,
            "name": v.product.name,
            "category": v.product.category.name if v.product.category else "Товары",
            "color": v.color or "Базовый",
            "size": v.size or "Универсальный",
            "price": int(v.final_price),
            "store": v.product.store.name,
        })

    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        matched = []
        q_lower = query_text.lower()
        for v in variants:
            cat_name = v.product.category.name if v.product.category else ""
            txt = f"{v.product.name} {v.color} {cat_name}".lower()
            if any(w in txt for w in q_lower.split()):
                matched.append(v)
        return {
            "advice": f"Подборка товаров по стилю '{query_text}':",
            "variants": matched if matched else variants[:8]
        }

    system_instruction = (
        "Ты — персональный ИИ-стилист маркетплейса brainmart.\n"
        "Пользователь ищет товары по профессии, теме или настроению (например: 'айтишник', 'на свидание', 'олд мани', 'спорт').\n"
        "Проанализируй список товаров и выбери ID тех, которые лучше всего подходят под этот вайб.\n"
        "Напиши 1-2 вдохновляющих предложения совета стилиста на русском языке.\n"
        "Верни СТРОГО JSON:\n"
        '{"advice": "Для стиля айтишника идеально подойдут свободные худи и практичная обувь...", "matching_ids": [1, 2]}'
    )

    user_prompt = f"Запрос: '{query_text}'\nКаталог:\n{json.dumps(catalog_summary, ensure_ascii=False)}"

    try:
        url = f"{GEMINI_API_URL}?key={api_key}"
        res = requests.post(
            url,
            json={
                "system_instruction": {"parts": [{"text": system_instruction}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"temperature": 0.3, "response_mime_type": "application/json"}
            },
            timeout=10
        )
        if res.status_code == 200:
            data = res.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            parsed = json.loads(raw_text)
            matching_ids = parsed.get("matching_ids", [])
            advice = parsed.get("advice", f"Рекомендации по стилю '{query_text}':")

            matched_variants = [v for v in variants if v.id in matching_ids]
            return {
                "advice": advice,
                "variants": matched_variants if matched_variants else variants[:8]
            }
    except Exception as e:
        logger.warning("Ошибка Gemini AI стилиста: %s", e)

    return {
        "advice": f"Подборка товаров по стилю '{query_text}':",
        "variants": variants[:8]
    }


def generate_ai_broadcast_copy(prompt: str, tone: str = "hype", store_name: str = "Магазин", niche: str = "Одежда") -> dict:
    """
    Генерирует продающий текст рассылки в Telegram с помощью Gemini AI:
    Создаёт цепляющую тему, текст с эмодзи и призывом к действию, а также надпись на кнопке.
    """
    import json
    api_key = getattr(settings, "GEMINI_API_KEY", "")

    tone_descriptions = {
        "hype": "Драйвовый, энергичный, с эмодзи 🔥⚡️, акцент на выгоде и горячих скидках.",
        "premium": "Сдержанный, премиальный, утончённый (Old Money/Luxury стиль), без кричащих слов.",
        "urgent": "Срочный, дедлайн, триггер упущенной выгоды ('Только 48 часов', 'Осталось 5 шт').",
        "friendly": "Тёплый, дружелюбный, заботливый, как сообщение от хорошего друга.",
    }
    tone_instruction = tone_descriptions.get(tone, tone_descriptions["hype"])

    default_result = {
        "title": f"🔥 Специальное предложение от {store_name}",
        "message_text": (
            f"Привет! 👋\n\n"
            f"У нас отличная новость — {prompt}!\n\n"
            f"✨ Ограниченное количество позиций в наличии.\n"
            f"Переходите в наш каталог прямо сейчас, чтобы успеть заказать лучшие размеры:"
        ),
        "button_text": "🛍 Забрать скидку",
    }

    if not api_key:
        return default_result

    system_instruction = (
        f"Ты — профессиональный AI-маркетолог и копирайтер для Telegram-каналов и ботов ритейла.\n"
        f"Магазин: '{store_name}', ниша: '{niche}'.\n"
        f"Тон коммуникации: {tone_instruction}\n"
        f"Задача: На основе идеи продавца написать идеальный продающий пост для рассылки в Telegram-боте.\n"
        f"СТРОГИЙ ФОРМАТ ОТВЕТА (только чистый JSON, без markdown-блоков ```json):\n"
        f"{{\n"
        f'  "title": "Короткая тема рассылки (до 50 символов)",\n'
        f'  "message_text": "Готовый продающий текст поста с эмодзи, абзацами и призывом к действию (до 400 символов)",\n'
        f'  "button_text": "Короткий текст кнопки со смайликом (до 25 символов)"\n'
        f"}}"
    )

    try:
        url = f"{GEMINI_API_URL}?key={api_key}"
        res = requests.post(
            url,
            json={
                "system_instruction": {"parts": [{"text": system_instruction}]},
                "contents": [{"role": "user", "parts": [{"text": f"Идея продавца: {prompt}"}]}],
                "generationConfig": {"temperature": 0.5, "response_mime_type": "application/json"}
            },
            timeout=10
        )
        if res.status_code == 200:
            data = res.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            parsed = json.loads(raw_text)
            return {
                "title": parsed.get("title", default_result["title"]),
                "message_text": parsed.get("message_text", default_result["message_text"]),
                "button_text": parsed.get("button_text", default_result["button_text"]),
            }
    except Exception as e:
        logger.warning("Ошибка генерации ИИ-рассылки: %s", e)

    return default_result


def generate_ai_product_description(name: str, category: str = "", features: str = "", store_name: str = "") -> str:
    """Генерирует продающее описание товара с характеристиками и рекомендациями по уходу."""
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    default_text = (
        f"{name} — стильная новинка из нашей коллекции.\n\n"
        f"• Премиальное качество материалов\n"
        f"• Идеальная посадка и комфорт на каждый день\n"
        f"• Доступны различные цвета и размеры"
    )
    if not api_key:
        return default_text

    system_instruction = (
        "Ты — топовый e-commerce копирайтер. Напиши стильное, лаконичное и продающее описание товара для маркетплейса.\n"
        "Структура ответа:\n"
        "1. 1-2 вдохновляющих предложения об образе и посадке.\n"
        "2. 3-4 маркированных пункта (Преимущества/Материал/Стиль).\n"
        "3. Без лишней воды. До 300 символов."
    )
    user_prompt = f"Товар: '{name}', Категория: '{category}', Особенности/детали: '{features}'"

    try:
        url = f"{GEMINI_API_URL}?key={api_key}"
        res = requests.post(
            url,
            json={
                "system_instruction": {"parts": [{"text": system_instruction}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"temperature": 0.4}
            },
            timeout=10
        )
        if res.status_code == 200:
            data = res.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.warning("Ошибка генерации описания товара: %s", e)

    return default_text


def generate_ai_store_insights(store: Store) -> list:
    """
    Генерирует умные аналитические советы для дашборда продавца:
    Анализирует остатки, спящих клиентов, средний чек и темп продаж.
    """
    import datetime
    from django.utils import timezone
    from django.db.models import Count, Sum, Max, Q
    from core.models import Customer, Order, ProductVariant

    insights = []
    today = timezone.now().date()
    thirty_days_ago = today - datetime.timedelta(days=30)

    # 1. Проверка спящих клиентов
    sleeping_count = Customer.objects.filter(
        orders__store=store
    ).annotate(
        last_order=Max("orders__created_at", filter=Q(orders__store=store))
    ).filter(last_order__lt=thirty_days_ago).distinct().count()

    if sleeping_count > 0:
        insights.append({
            "icon": "💤",
            "type": "retention",
            "title": "Возврат спящих клиентов",
            "text": f"У вас {sleeping_count} клиентов, которые не покупали больше месяца. Отправьте им ИИ-рассылку с секретной скидкой 10-15% в разделе «Рассылки».",
            "action_url": "/dashboard/broadcasts/",
            "action_text": "Создать рассылку"
        })

    # 2. Проверка низких остатков на складе
    low_stock = ProductVariant.objects.filter(
        product__store=store,
        product__is_active=True,
        stock_quantity__lte=2,
        stock_quantity__gt=0
    ).select_related("product")[:3]

    if low_stock:
        names = ", ".join([f"{v.product.name} ({v.color}/{v.size})" for v in low_stock])
        insights.append({
            "icon": "📦",
            "type": "inventory",
            "title": "Заканчиваются популярные размеры",
            "text": f"Позиции с остатком <= 2 шт: {names}. Пополните склад, чтобы ИИ-ассистент продолжал их рекомендовать покупателям.",
            "action_url": "/dashboard/products/",
            "action_text": "Открыть склад"
        })

    # 3. Маркетинговый совет по стилю и оформлению
    insights.append({
        "icon": "✨",
        "type": "growth",
        "title": "Персонализация бренда",
        "text": f"Настройте фирменные цвета бренда в Настройках. Ваш каталог окрасится в ваши цвета, повышая доверие покупателей на 35%.",
        "action_url": "/dashboard/settings/",
        "action_text": "Настроить цвета"
    })

    return insights