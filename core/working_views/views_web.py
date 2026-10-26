"""
views_web.py
-------------
Простые view-функции для клиентской части маркетплейса.
"""
import json
import logging
import requests
from django.http import JsonResponse
from django.core.cache import cache
from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.clickjacking import xframe_options_exempt

logger = logging.getLogger(__name__)


from django.contrib import messages
from django.utils.translation import gettext as _
from core.models import Product, ProductVariant, Customer, Order, OrderItem, Store, Category, ProductReview


def resolve_store_from_request(request):
    """
    Определяет магазин по поддомену хоста (например, sultan.brainmart.uz),
    по GET-параметрам (?store_id=... или ?store=...) либо возвращает None (глобальный маркетплейс).
    """
    # 1. Если передан параметр ?global=1 — это принудительный выход на глобальный рынок
    if request.GET.get("global") == "1":
        return None

    host = request.get_host().split(":")[0].lower()
    system_subdomains = {"www", "admin", "api", "platform", "bot", "static", "media", "app", "dashboard", "mail", "dev"}

    # 2. Проверяем поддомен: <subdomain>.brainmart.uz или <subdomain>.localhost
    subdomain = None
    base_domain = getattr(settings, "SITE_DOMAIN", "brainmart.uz").lower()
    if host.endswith(f".{base_domain}"):
        subdomain = host[:-len(f".{base_domain}")]
    elif host.endswith(".localhost"):
        subdomain = host[:-len(".localhost")]

    if subdomain and subdomain not in system_subdomains:
        # 1. Поиск по slug
        store = Store.objects.filter(slug__iexact=subdomain).first()
        if store:
            return store

        # 2. Поиск по instagram_username с вариациями дефиса и подчеркивания
        sub_variations = [
            subdomain,
            subdomain.replace("-", "_"),
            subdomain.replace("_", "-"),
            subdomain.replace("-", ""),
            subdomain.replace("_", ""),
        ]
        for var in sub_variations:
            store = Store.objects.filter(instagram_username__iexact=var).first()
            if store:
                if not store.slug:
                    store.slug = subdomain
                    store.save(update_fields=["slug"])
                return store

        # 3. Умный поиск по имени магазина
        for s in Store.objects.all():
            s_slug = s.clean_slug()
            s_name_slug = s.clean_slug(s.name) if s.name else ""
            s_insta_slug = s.clean_slug(s.instagram_username) if s.instagram_username else ""
            sub_compact = subdomain.replace("-", "").replace("_", "")
            name_compact = s.name.lower().replace(" ", "").replace("-", "").replace("_", "") if s.name else ""
            insta_compact = s.instagram_username.lower().replace("_", "").replace("-", "") if s.instagram_username else ""

            if subdomain in (s_slug, s_name_slug, s_insta_slug) or \
               (s.slug and s.slug.lower() == subdomain) or \
               (name_compact and name_compact == sub_compact) or \
               (insta_compact and insta_compact == sub_compact):
                if not s.slug:
                    s.slug = subdomain
                    s.save(update_fields=["slug"])
                return s

    # 3. Проверяем query-параметры ?store_id=... или ?store=...
    store_id = request.GET.get("store_id")
    if store_id:
        try:
            return Store.objects.filter(id=int(store_id)).first()
        except (ValueError, TypeError):
            pass

    store_slug = request.GET.get("store")
    if store_slug:
        store = Store.objects.filter(slug__iexact=store_slug).first()
        if not store:
            store = Store.objects.filter(instagram_username__iexact=store_slug).first()
        if store:
            return store

    return None


@xframe_options_exempt
def catalog_view(request):
    """Каталог с фильтрацией по конкретному магазину, категориям, текстовым поиском и ИИ-Стилистом."""
    from django.db.models import Q
    from django.utils import translation
    from core.working_views.services import semantic_ai_catalog_search

    base_domain = getattr(settings, "SITE_DOMAIN", "brainmart.uz").lower()
    host = request.get_host().split(":")[0].lower()
    is_subdomain = host.endswith(f".{base_domain}") and host != base_domain and host != f"www.{base_domain}"
    current_lang = translation.get_language() or "ru"

    # Если на поддомене нажат 🌍 "Глобальный рынок" (?global=1) — перенаправляем на brainmart.uz
    if is_subdomain and request.GET.get("global") == "1":
        return redirect(f"https://{base_domain}/{current_lang}/")

    current_store = resolve_store_from_request(request)

    # Если адрес с поддоменом, но магазин не найден — редиректим на общий brainmart.uz
    if is_subdomain and not current_store:
        return redirect(f"https://{base_domain}/{current_lang}/")

    category_id = request.GET.get("category")
    search_query = request.GET.get("q", "").strip()
    is_ai_search = request.GET.get("ai") == "1"
    ai_advice = None

    # Категории: в витрине магазина - только его категории, в глобальном маркете - все
    if current_store:
        store_cat_ids = Product.objects.filter(store=current_store, is_active=True).values_list("category_id", flat=True).distinct()
        categories = Category.objects.filter(id__in=store_cat_ids).order_by("id")
    else:
        categories = Category.objects.all().order_by("id")

    # Интеллектуальный поиск: многословный, по названию, цвету, описанию и ИИ
    if search_query:
        import re
        q_clean = search_query.lower()
        semantic_keywords = [
            "premium", "премиум", "качество", "качества", "люкс", "стиль", "образ", 
            "одежда", "одежды", "айтишник", "свидание", "подарок", "спорт", "лето", 
            "зима", "офис", "вечеринка", "it", "casual", "old money", "оверсайз", 
            "мода", "тренды", "вечерний", "хлопок", "зипуха", "куртка", "look"
        ]
        is_semantic = is_ai_search or any(word in q_clean for word in semantic_keywords) or len(q_clean.split()) >= 2

        if is_semantic:
            ai_res = semantic_ai_catalog_search(search_query, current_store)
            variants = ai_res.get("variants", [])
            ai_advice = ai_res.get("advice")
        else:
            variants = []

        # Если семантический поиск вернул мало или запрос обычный — выполняем точный поиск по базе
        if not variants:
            base_qs = ProductVariant.objects.filter(
                product__is_active=True,
                product__store__is_active=True,
                product__store__is_frozen_by_admin=False
            ).select_related("product", "product__store", "product__category").order_by("product_id", "id")

            if current_store:
                base_qs = base_qs.filter(product__store=current_store)
            if category_id:
                base_qs = base_qs.filter(product__category_id=category_id)

            # 1. Поиск по полной фразе во всех полях
            full_match = base_qs.filter(
                Q(product__name__icontains=search_query) |
                Q(product__description__icontains=search_query) |
                Q(color__icontains=search_query) |
                Q(product__category__name__icontains=search_query) |
                Q(product__store__name__icontains=search_query)
            )

            if full_match.exists():
                variants = list(full_match)
            else:
                # 2. Поиск по отдельным словам
                words = [w for w in re.findall(r'\w+', search_query) if len(w) >= 2]
                word_q = Q()
                for w in words:
                    word_q |= Q(product__name__icontains=w)
                    word_q |= Q(product__description__icontains=w)
                    word_q |= Q(color__icontains=w)
                    word_q |= Q(product__category__name__icontains=w)

                word_matches = base_qs.filter(word_q) if words else base_qs.none()
                if word_matches.exists():
                    variants = list(word_matches)
                    ai_advice = f"Найдено по ключевым словам '{search_query}':"
                else:
                    # 3. Умная рекомендация, чтобы не оставлять пустой экран
                    ai_res = semantic_ai_catalog_search(search_query, current_store)
                    variants = ai_res.get("variants", [])
                    ai_advice = ai_res.get("advice") or f"По запросу '{search_query}' подобраны похожие качественные товары:"
    else:
        variants = ProductVariant.objects.filter(
            product__is_active=True,
            product__store__is_active=True,
            product__store__is_frozen_by_admin=False
        ).select_related("product", "product__store", "product__category").order_by("product_id", "id")

        if current_store:
            variants = variants.filter(product__store=current_store)

        if category_id:
            variants = variants.filter(product__category_id=category_id)

    # Группируем по уникальной паре (product_id, color) в Python для SQLite
    seen = set()
    cards = []
    for v in variants:
        key = (v.product_id, v.color)
        if key not in seen:
            seen.add(key)
            final_p = int(v.final_price)
            old_p = int(final_p * 1.20)
            cards.append({
                "id": v.product.id,
                "color": v.color,
                "name": f"{v.product.name} ({v.color})" if v.color else v.product.name,
                "base_price": final_p,
                "formatted_price": f"{final_p:,}".replace(",", " "),
                "old_price": old_p,
                "formatted_old_price": f"{old_p:,}".replace(",", " "),
                "discount_percent": "17",
                "main_image": v.image if v.image else v.product.main_image,
                "store": v.product.store,
                "category": v.product.category,
                "average_rating": v.product.average_rating,
                "reviews_count": v.product.reviews_count,
            })

    effective_bot = current_store.telegram_bot_username if (current_store and current_store.telegram_bot_username and current_store.telegram_bot_active) else settings.TELEGRAM_BOT_USERNAME
    from core.models import PromoBanner
    promo_banners = PromoBanner.get_active_banners() if not current_store else []
    context = {
        "products": cards,
        "promo_banners": promo_banners,
        "categories": categories,
        "selected_category_id": int(category_id) if (category_id and category_id.isdigit()) else None,
        "search_query": search_query,
        "is_ai_search": is_ai_search,
        "ai_advice": ai_advice,
        "current_store_id": current_store.id if current_store else None,
        "current_store": current_store,
        "bot_username": effective_bot,
        "global_market_url": f"https://{base_domain}/{current_lang}/",
    }
    return render(request, "core/catalog.html", context)



@xframe_options_exempt
def product_detail_view(request, product_id):
    """Страница товара с вариантами и отзывами."""
    from django.utils import translation
    base_domain = getattr(settings, "SITE_DOMAIN", "brainmart.uz").lower()
    current_lang = translation.get_language() or "ru"

    product = get_object_or_404(
        Product,
        id=product_id,
        is_active=True,
        store__is_active=True,
        store__is_frozen_by_admin=False
    )
    reviews = product.reviews.filter(is_visible=True).order_by("-created_at")
    effective_bot = product.store.telegram_bot_username if (product.store.telegram_bot_username and product.store.telegram_bot_active) else settings.TELEGRAM_BOT_USERNAME

    # Собираем все фото товара и его вариантов (Uzum / Wildberries стиль)
    gallery_images = []
    seen_urls = set()

    # 1. Главное фото
    if product.main_image:
        try:
            m_url = product.main_image.url
            if m_url not in seen_urls:
                gallery_images.append({
                    "url": m_url,
                    "color": getattr(product, "color", "") or "",
                    "title": product.name,
                })
                seen_urls.add(m_url)
        except Exception:
            pass

    # 2. Дополнительные фото из ProductImage
    for p_img in product.images.all():
        try:
            if p_img.image and p_img.image.url not in seen_urls:
                gallery_images.append({
                    "url": p_img.image.url,
                    "color": p_img.color or "",
                    "title": f"{product.name} ({p_img.color})" if p_img.color else product.name,
                })
                seen_urls.add(p_img.image.url)
        except Exception:
            pass

    # 3. Фото вариантов по цветам (ProductVariant.image)
    for v in product.variants.all():
        try:
            if v.image and v.image.url not in seen_urls:
                gallery_images.append({
                    "url": v.image.url,
                    "color": v.color or "",
                    "title": f"{product.name} - {v.color}" if v.color else product.name,
                })
                seen_urls.add(v.image.url)
        except Exception:
            pass

    context = {
        "product": product,
        "current_store": product.store,
        "variants": product.variants.all(),
        "gallery_images": gallery_images,
        "reviews": reviews,
        "reviews_count": reviews.count(),
        "average_rating": product.average_rating,
        "bot_username": effective_bot,
        "global_market_url": f"https://{base_domain}/{current_lang}/",
    }
    return render(request, "core/product_detail.html", context)


@csrf_exempt
def submit_product_review(request, product_id):
    """Прием отзыва покупателя на товар."""
    product = get_object_or_404(Product, id=product_id, is_active=True)
    if request.method == "POST":
        customer_name = request.POST.get("customer_name", "").strip() or "Покупатель"
        customer_phone = request.POST.get("customer_phone", "").strip()
        text = request.POST.get("text", "").strip()
        try:
            rating = int(request.POST.get("rating", 5))
            rating = max(1, min(5, rating))
        except (ValueError, TypeError):
            rating = 5

        if not text:
            messages.error(request, "Пожалуйста, напишите текст отзыва.")
            return redirect("product-detail", product_id=product.id)

        ProductReview.objects.create(
            product=product,
            customer_name=customer_name,
            customer_phone=customer_phone,
            rating=rating,
            text=text,
            is_visible=True
        )
        messages.success(request, "Спасибо! Ваш отзыв успешно опубликован.")
        return redirect("product-detail", product_id=product.id)

    return redirect("product-detail", product_id=product.id)


@csrf_exempt
def create_telegram_order(request):
    """Принимает корзину, создает заказ и запускает опрос геопозиции в боте."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only POST"})

    data = json.loads(request.body)
    cart_items = data.get("cart")
    chat_id = data.get("chat_id")

    if not cart_items or not chat_id:
        return JsonResponse({"success": False, "error": "Пустая корзина или нет chat_id"})

    site_lang = getattr(request, "LANGUAGE_CODE", "uz") or "uz"
    if site_lang not in ("ru", "uz", "en"):
        site_lang = "uz"

    with transaction.atomic():
        username = data.get("username", "")
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip() or "Пользователь Telegram"

        customer, created = Customer.objects.get_or_create(
            instagram_scoped_id=f"tg_{chat_id}",
            defaults={
                "full_name": full_name,
                "phone_number": f"tg_{chat_id}",
                "telegram_chat_id": str(chat_id),
                "telegram_username": username,
                "language": site_lang,
            }
        )
        if not created:
            customer.language = site_lang
            if not customer.telegram_chat_id:
                customer.telegram_chat_id = str(chat_id)
            if username and not customer.telegram_username:
                customer.telegram_username = username
            if full_name and customer.full_name == "Пользователь Telegram":
                customer.full_name = full_name
            customer.save()

        # Синхронизируем язык для всех записей этого chat_id
        Customer.objects.filter(telegram_chat_id=str(chat_id)).update(language=site_lang)

        try:
            first_variant = ProductVariant.objects.get(id=cart_items[0]["variant_id"])
        except (ProductVariant.DoesNotExist, ValueError, KeyError):
            return JsonResponse({"success": False, "error": "Один из товаров в корзине не найден на складе. Пожалуйста, очистите корзину и выберите заново."})

        from core.working_views.views_branches import find_nearest_branch
        target_store = first_variant.product.store
        initial_branch, _ = find_nearest_branch(target_store)

        order = Order.objects.create(
            store=target_store,
            branch=initial_branch,
            customer=customer,
            status=Order.Status.PENDING_PAYMENT,
            payment_method="click"
        )

        for item in cart_items:
            try:
                variant = ProductVariant.objects.select_for_update().get(id=item["variant_id"])
            except (ProductVariant.DoesNotExist, ValueError, KeyError):
                return JsonResponse({"success": False, "error": f"Товар не найден на складе."})

            qty = max(1, int(item.get("quantity", 1)))
            if qty > variant.stock_quantity:
                return JsonResponse({
                    "success": False,
                    "error": f"К сожалению, товара «{variant.product.name}» ({variant.color or '—'}/{variant.size or '—'}) осталось всего {variant.stock_quantity} шт. Пожалуйста, уменьшите количество."
                })

            OrderItem.objects.create(
                order=order,
                variant=variant,
                quantity=qty,
                price_at_purchase=variant.final_price
            )
        order.recalculate_total()


    # Запоминаем шаг и даем боту команду запросить локацию на выбранном клиентом языке
    cache.set(f"tg_state_{chat_id}", {"state": "WAITING_LOCATION", "order_id": order.id}, timeout=3600)

    from core.working_views.telegram_bot import _nt
    header_text = _nt(site_lang, "order_opened_header", order_id=order.id)
    taxi_prompt = _nt(site_lang, "taxi_warning_prompt")
    btn_text = _nt(site_lang, "btn_send_location")

    payload = {
        "chat_id": chat_id,
        "text": f"{header_text}{taxi_prompt}",
        "reply_markup": {
            "keyboard": [[{"text": btn_text, "request_location": True}]],
            "resize_keyboard": True,
            "one_time_keyboard": True
        }
    }
    try:
        requests.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10
        )
    except Exception as e:
        logger.error(f"Не удалось отправить стартовое сообщение в Telegram бот: {e}")
        logger.error(f"Не удалось отправить стартовое сообщение в Telegram бот: {e}")

    return JsonResponse({
        "success": True,
        "order_id": order.id,
        "telegram_url": f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=order_{order.id}" if settings.TELEGRAM_BOT_USERNAME else ""
    })



@csrf_exempt
def create_web_order(request):
    """
    Принимает оформленный заказ с пошагового чекаута (товары, контакты, геолокация, ориентир для такси, способ оплаты).
    Создает клиента, заказ, позиции, рассчитывает комиссию и возвращает ссылку на Click, Telegram или страницу успеха.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only POST allowed"})

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"success": False, "error": "Неверный формат данных JSON."})

    cart_items = data.get("cart", [])
    full_name = data.get("full_name", "").strip()
    phone_number = data.get("phone_number", "").strip()
    delivery_address = data.get("delivery_address", "").strip()
    delivery_landmark = data.get("delivery_landmark", "").strip() or data.get("landmark", "").strip()
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    payment_method = data.get("payment_method", "card_transfer")
    chat_id = data.get("chat_id")
    username = data.get("username", "")

    if not cart_items:
        return JsonResponse({"success": False, "error": _("Корзина пуста.")})

    if not phone_number:
        return JsonResponse({"success": False, "error": _("Пожалуйста, укажите номер телефона для связи.")})

    if not delivery_address and not (latitude and longitude):
        return JsonResponse({"success": False, "error": _("Укажите адрес доставки или отправьте локацию.")})

    with transaction.atomic():
        scoped_id = f"tg_{chat_id}" if chat_id else f"web_{phone_number.replace('+', '').replace(' ', '')}"

        # Находим или создаем клиента
        customer = None
        if chat_id:
            customer = Customer.objects.filter(telegram_chat_id=str(chat_id)).first()
        if not customer and phone_number:
            customer = Customer.objects.filter(phone_number=phone_number).first()

        if not customer:
            customer = Customer.objects.create(
                instagram_scoped_id=scoped_id,
                phone_number=phone_number,
                full_name=full_name or "Покупатель",
                telegram_chat_id=str(chat_id) if chat_id else "",
                telegram_username=username or "",
                language=getattr(request, "LANGUAGE_CODE", "ru")
            )
        else:
            if full_name and full_name != "Покупатель":
                customer.full_name = full_name
            if phone_number and not customer.phone_number:
                customer.phone_number = phone_number
            if chat_id and not customer.telegram_chat_id:
                customer.telegram_chat_id = str(chat_id)
            if username and not customer.telegram_username:
                customer.telegram_username = username
            customer.save()

            # Sync language from site URL to customer record
            site_lang = getattr(request, "LANGUAGE_CODE", "uz") or "uz"
            if site_lang in ("ru", "uz", "en"):
                customer.language = site_lang
                customer.save(update_fields=["language"])
                if chat_id:
                    Customer.objects.filter(telegram_chat_id=str(chat_id)).update(language=site_lang)

        try:
            first_variant = ProductVariant.objects.select_related("product__store").get(id=cart_items[0]["variant_id"])
        except (ProductVariant.DoesNotExist, ValueError, KeyError):
            return JsonResponse({"success": False, "error": _("Один из товаров в корзине больше не доступен на складе.")})

        target_store = first_variant.product.store
        from core.working_views.views_branches import find_nearest_branch
        nearest_branch, _ = find_nearest_branch(
            target_store,
            delivery_latitude=latitude,
            delivery_longitude=longitude,
            delivery_address=delivery_address
        )

        order = Order.objects.create(
            store=target_store,
            branch=nearest_branch,
            customer=customer,
            status=Order.Status.PENDING_PAYMENT,
            delivery_address=delivery_address or "Локация по GPS",
            delivery_latitude=latitude if latitude else None,
            delivery_longitude=longitude if longitude else None,
            delivery_landmark=delivery_landmark,
            payment_method="card_transfer",
        )

        for item in cart_items:
            try:
                variant = ProductVariant.objects.select_for_update().get(id=item["variant_id"])
            except (ProductVariant.DoesNotExist, ValueError, KeyError):
                return JsonResponse({"success": False, "error": _("Товар не найден на складе.")})

            qty = max(1, int(item.get("quantity", 1)))
            if qty > variant.stock_quantity:
                return JsonResponse({
                    "success": False,
                    "error": _("К сожалению, товара «%(name)s» осталось только %(stock)d шт. Пожалуйста, измените количество в корзине.") % {
                        "name": f"{variant.product.name} ({variant.color or '—'}/{variant.size or '—'})",
                        "stock": variant.stock_quantity
                    }
                })

            OrderItem.objects.create(
                order=order,
                variant=variant,
                quantity=qty,
                price_at_purchase=variant.final_price
            )

        order.recalculate_total()

    # Магазин не беспокоим, пока покупатель не перевел деньги и не прикрепил фото чека!
    if target_store.telegram_bot_username and target_store.telegram_bot_active:
        target_bot = target_store.telegram_bot_username
    else:
        target_bot = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
    telegram_url = f"https://t.me/{target_bot}?start=order_{order.id}" if target_bot else ""
    lang = getattr(request, "LANGUAGE_CODE", "uz") or "uz"

    # Авто-эквайринг заморожен на доработку: покупатель всегда перенаправляется на перевод с чеком
    redirect_url = f"/{lang}/order/{order.id}/success/"

    return JsonResponse({
        "success": True,
        "order_id": order.id,
        "payment_method": "card_transfer",
        "telegram_url": telegram_url,
        "redirect_url": redirect_url,
    })


@xframe_options_exempt
def order_payment_gateway_view(request, order_id):
    """
    Онлайн-шлюз оплаты (автоматический эквайринг):
    ВРЕМЕННО ЗАМОРОЖЕН НА ТЕХНИЧЕСКУЮ ДОРАБОТКУ.
    Прямой ввод тестовых данных карт отключен во избежание несанкционированных подтверждений.
    Перенаправляет на защищенный перевод с отправкой чека.
    """
    order = get_object_or_404(Order, id=order_id)
    lang = getattr(request, "LANGUAGE_CODE", "uz") or "uz"

    messages.info(
        request,
        "⚙️ Автоматический эквайринг временно находится на доработке. "
        "Пожалуйста, выполните перевод на карту продавца и прикрепите фото чека."
    )
    return redirect(f"/{lang}/order/{order.id}/success/")


@csrf_exempt
@xframe_options_exempt
def order_upload_receipt_view(request, order_id):
    """
    Принимает подтверждение оплаты от покупателя: ОБЯЗАТЕЛЬНО фото/скриншот чека.
    Без фото чека заказ не передается в магазин.
    Сохраняет чек и только теперь отправляет полное уведомление владельцу и менеджерам.
    """
    order = get_object_or_404(Order, id=order_id)
    lang = getattr(request, "LANGUAGE_CODE", "uz") or "uz"

    if request.method == "POST":
        receipt_file = request.FILES.get("receipt_image")
        if not receipt_file:
            messages.error(request, _("⚠️ Пожалуйста, обязательно прикрепите фото или скриншот чека об оплате!"))
            return redirect(f"/{lang}/order/{order.id}/success/")

        order.payment_receipt = receipt_file
        order.payment_confirmed_by_customer = True
        order.save(update_fields=["payment_receipt", "payment_confirmed_by_customer"])

        if order.customer:
            if lang in ("uz", "ru", "en") and order.customer.language != lang:
                order.customer.language = lang
                order.customer.save(update_fields=["language"])
            if order.customer.telegram_chat_id:
                Customer.objects.filter(telegram_chat_id=str(order.customer.telegram_chat_id)).update(language=lang)

        try:
            from core.working_views.telegram_bot import notify_order_receipt_uploaded, _send_telegram_message, _nt
            notify_order_receipt_uploaded(order)

            # Отправляем подтверждение покупателю в Telegram, если у него привязан чат (через личный бот магазина)
            if order.customer and order.customer.telegram_chat_id:
                cust_lang = order.customer.language or lang or "uz"
                cust_text = _nt(cust_lang, "receipt_received_customer", order_id=order.id, store=order.store.name)
                store_bot_token = order.store.telegram_bot_token if (order.store.telegram_bot_token and order.store.telegram_bot_active) else None
                _send_telegram_message(order.customer.telegram_chat_id, cust_text, bot_token=store_bot_token)
        except Exception as e:
            logger.warning("Не удалось отправить уведомление о чеке продавцу: %s", e)

        messages.success(request, _("✅ Чек успешно передан магазину! Скоро заказ будет доставлен."))
        if order.store.telegram_bot_username and order.store.telegram_bot_active:
            bot_username = order.store.telegram_bot_username
        else:
            bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "BrainMart_bot") or "BrainMart_bot"
        telegram_url = f"https://t.me/{bot_username}?start=order_{order.id}" if bot_username else f"/{lang}/order/{order.id}/success/?receipt_sent=1"
        return redirect(telegram_url)

    return redirect(f"/{lang}/order/{order.id}/success/")


@xframe_options_exempt
def order_success_view(request, order_id):
    """Страница подтверждения заказа после оформления или отправки чека."""
    order = get_object_or_404(Order, id=order_id)
    receipt_sent = request.GET.get("receipt_sent") == "1" or order.payment_confirmed_by_customer

    store = order.store

    # Находим ответственного менеджера магазина (или владельца, если менеджера нет)
    assigned_mgr = order.assigned_manager
    if not assigned_mgr:
        assigned_mgr = store.managers.filter(is_active=True).exclude(telegram_username="").first()
        if not assigned_mgr:
            assigned_mgr = store.managers.filter(is_active=True).first()
        if assigned_mgr and not order.assigned_manager:
            order.assigned_manager = assigned_mgr
            order.save(update_fields=["assigned_manager"])

    contact_type = "manager" if assigned_mgr else "owner"
    contact_name = assigned_mgr.name if assigned_mgr else store.name
    contact_username = ""

    if assigned_mgr and assigned_mgr.telegram_username:
        contact_username = assigned_mgr.telegram_username.lstrip("@").strip()
    elif store.owner_telegram_username:
        contact_username = store.owner_telegram_username.lstrip("@").strip()

    if store.telegram_bot_username and store.telegram_bot_active:
        bot_username = store.telegram_bot_username
    else:
        bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "BrainMart_bot") or "BrainMart_bot"
    telegram_bot_url = f"https://t.me/{bot_username}?start=order_{order.id}" if bot_username else ""

    context = {
        "order": order,
        "receipt_sent": receipt_sent,
        "contact_type": contact_type,
        "contact_name": contact_name,
        "contact_username": contact_username,
        "store_phone": store.phone_number,
        "bot_username": bot_username,
        "telegram_bot_url": telegram_bot_url,
    }
    return render(request, "core/order_success.html", context)


def become_seller_view(request):
    """Информационная страница для новых продавцов: условия, тарифы, преимущества и регистрация."""
    context = {
        "bot_username": settings.TELEGRAM_BOT_USERNAME,
    }
    return render(request, "core/become_seller.html", context)


