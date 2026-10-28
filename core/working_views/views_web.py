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
                    try:
                        store.slug = subdomain
                        store.save(update_fields=["slug"])
                    except Exception:
                        pass
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
                    try:
                        s.slug = subdomain
                        s.save(update_fields=["slug"])
                    except Exception:
                        pass
                return s

    # 3. Проверяем query-параметры ?store_id=... или ?store=...
    store_id = request.GET.get("store_id")
    if store_id and str(store_id).isdigit():
        try:
            val = int(store_id)
            if 0 < val <= 2147483647:
                return Store.objects.filter(id=val).first()
        except Exception:
            pass

    store_slug = request.GET.get("store")
    if store_slug:
        store = Store.objects.filter(slug__iexact=store_slug).first()
        if not store:
            store = Store.objects.filter(instagram_username__iexact=store_slug).first()
        if store:
            return store

    return None


def _parse_coord(val):
    """Безопасный парсинг координат."""
    if val is None:
        return None
    from decimal import Decimal
    val_str = str(val).replace(",", ".").replace(" ", "").strip()
    if not val_str:
        return None
    try:
        return Decimal(val_str)
    except Exception:
        return None


def _safe_img_url(img):
    if not img:
        return ""
    try:
        return img.url
    except Exception:
        return ""


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
    # 1. Быстрый и оптимизированный запрос по Product (1 карточка на 1 товар)
    products_qs = Product.objects.filter(
        is_active=True,
        store__is_active=True,
        store__is_frozen_by_admin=False
    ).select_related("store", "category").prefetch_related("variants", "images")

    if current_store:
        products_qs = products_qs.filter(store=current_store)

    if category_id and str(category_id).isdigit():
        products_qs = products_qs.filter(category_id=int(category_id))

    if search_query:
        import re
        q_clean = search_query.strip()
        words = [w for w in re.findall(r'\w+', q_clean) if len(w) >= 2]
        q_filter = (
            Q(name__icontains=q_clean) |
            Q(description__icontains=q_clean) |
            Q(category__name__icontains=q_clean) |
            Q(store__name__icontains=q_clean) |
            Q(variants__color__icontains=q_clean)
        )
        for w in words:
            q_filter |= Q(name__icontains=w) | Q(description__icontains=w) | Q(variants__color__icontains=w)
        products_qs = products_qs.filter(q_filter).distinct()
        ai_advice = f"Найдено по запросу '{q_clean}':"

    products_qs = products_qs.order_by("-id")[:80]

    cards = []
    for p in products_qs:
        # Собираем фото по цветам для разделения карточек по цветам (как в Uzum / Wildberries)
        color_images = {}
        no_color_images = []
        if p.main_image:
            try:
                no_color_images.append(p.main_image.url)
            except Exception:
                pass

        for pimg in p.images.all():
            try:
                if pimg.image:
                    u = pimg.image.url
                    c = (pimg.color or '').strip()
                    if c:
                        lst = color_images.setdefault(c.lower(), [])
                        if u not in lst:
                            lst.append(u)
                    elif u not in no_color_images:
                        no_color_images.append(u)
            except Exception:
                pass

        all_vars = list(p.variants.all())
        for v in all_vars:
            try:
                if v.image:
                    u = v.image.url
                    c = (v.color or '').strip()
                    if c:
                        lst = color_images.setdefault(c.lower(), [])
                        if u not in lst:
                            lst.append(u)
                    elif u not in no_color_images:
                        no_color_images.append(u)
            except Exception:
                pass

        # Список уникальных цветов товара
        seen_c_keys = set()
        unique_colors = []
        for v in all_vars:
            c = (v.color or '').strip()
            if c and c.lower() not in seen_c_keys:
                seen_c_keys.add(c.lower())
                unique_colors.append(c)
        for pimg in p.images.all():
            c = (pimg.color or '').strip()
            if c and c.lower() not in seen_c_keys:
                seen_c_keys.add(c.lower())
                unique_colors.append(c)

        try:
            final_p = int(float(p.base_price)) if p.base_price is not None else 0
        except (ValueError, TypeError):
            final_p = 0
        old_p = p.old_price
        inst_p = p.installment_price

        # Если у товара 2 или более цветов — показываем в витрине отдельную карточку на каждый цвет (Uzum / WB стиль)
        if len(unique_colors) >= 2:
            for col in unique_colors:
                col_key = col.lower()
                c_vars = [v for v in all_vars if (v.color or '').strip().lower() == col_key]
                first_c_var = c_vars[0] if c_vars else None
                c_price = first_c_var.final_price if (first_c_var and first_c_var.final_price) else final_p
                try:
                    c_price_int = int(float(c_price))
                except (ValueError, TypeError):
                    c_price_int = final_p

                c_imgs = list(color_images.get(col_key, []))
                for u in no_color_images:
                    if u not in c_imgs:
                        c_imgs.append(u)

                main_img_url = c_imgs[0] if c_imgs else _safe_img_url(p.main_image)

                cards.append({
                    "id": p.id,
                    "variant_id": first_c_var.id if first_c_var else p.id,
                    "name": p.name,
                    "color": col,
                    "color_label": col,
                    "base_price": c_price_int,
                    "formatted_price": f"{c_price_int:,}".replace(",", " "),
                    "old_price": old_p,
                    "formatted_old_price": p.formatted_old_price,
                    "discount_percent": p.discount_percent,
                    "installment_price": inst_p,
                    "formatted_installment": p.formatted_installment,
                    "main_image": p.main_image,
                    "main_image_url": main_img_url,
                    "images_list": c_imgs[:8],
                    "colors_count": len(unique_colors),
                    "colors": unique_colors[:5],
                    "store": p.store,
                    "category": p.category,
                    "average_rating": 4.9,
                    "reviews_count": 140,
                })
        else:
            first_variant = all_vars[0] if all_vars else None
            single_color = unique_colors[0] if unique_colors else ""
            all_imgs = []
            if single_color and single_color.lower() in color_images:
                all_imgs = list(color_images[single_color.lower()])
            for u in no_color_images:
                if u not in all_imgs:
                    all_imgs.append(u)
            main_img_url = all_imgs[0] if all_imgs else _safe_img_url(p.main_image)

            cards.append({
                "id": p.id,
                "variant_id": first_variant.id if first_variant else p.id,
                "name": p.name,
                "color": single_color,
                "color_label": single_color,
                "base_price": final_p,
                "formatted_price": p.formatted_base_price,
                "old_price": old_p,
                "formatted_old_price": p.formatted_old_price,
                "discount_percent": p.discount_percent,
                "installment_price": inst_p,
                "formatted_installment": p.formatted_installment,
                "main_image": p.main_image,
                "main_image_url": main_img_url,
                "images_list": all_imgs[:8],
                "colors_count": len(unique_colors),
                "colors": unique_colors[:5],
                "store": p.store,
                "category": p.category,
                "average_rating": 4.9,
                "reviews_count": 140,
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

    # Определяем контекст магазина (глобальный маркет или магазин продавца)
    active_store = resolve_store_from_request(request)

    # Собираем все уникальные фото товара и его вариантов, сгруппированные по цветам (Uzum/WB)
    raw_images = []
    seen_urls = set()

    # 1. Главное фото товара
    if product.main_image:
        try:
            m_url = product.main_image.url
            if m_url not in seen_urls:
                raw_images.append({
                    "url": m_url,
                    "color": (getattr(product, "color", "") or "").strip(),
                    "title": product.name,
                })
                seen_urls.add(m_url)
        except Exception:
            pass

    # 2. Дополнительные фото из ProductImage
    for p_img in product.images.all().order_by("id"):
        try:
            if p_img.image and p_img.image.url not in seen_urls:
                p_col = (p_img.color or "").strip()
                raw_images.append({
                    "url": p_img.image.url,
                    "color": p_col,
                    "title": f"{product.name} ({p_col})" if p_col else product.name,
                })
                seen_urls.add(p_img.image.url)
        except Exception:
            pass

    # 3. Фото вариантов по цветам (ProductVariant.image)
    for v in product.variants.all().order_by("id"):
        try:
            if v.image and v.image.url not in seen_urls:
                v_col = (v.color or "").strip()
                raw_images.append({
                    "url": v.image.url,
                    "color": v_col,
                    "title": f"{product.name} - {v_col}" if v_col else product.name,
                })
                seen_urls.add(v.image.url)
        except Exception:
            pass

    # Группируем фото по цветам: все ракурсы одного цвета идут подряд
    color_groups = {}
    no_color_images = []
    for item in raw_images:
        c = item["color"]
        if c:
            color_groups.setdefault(c.lower(), []).append(item)
        else:
            no_color_images.append(item)

    requested_color = (request.GET.get("color") or "").strip().lower()

    gallery_images = []
    # Если из каталога передан конкретный цвет (?color=черный), ставим его фото ПЕРВЫМИ!
    if requested_color and requested_color in color_groups:
        gallery_images.extend(color_groups[requested_color])
        for c_key, items in color_groups.items():
            if c_key != requested_color:
                gallery_images.extend(items)
        gallery_images.extend(no_color_images)
    else:
        for c_key, items in color_groups.items():
            gallery_images.extend(items)
        gallery_images.extend(no_color_images)

    if not gallery_images and product.main_image:
        try:
            gallery_images.append({
                "url": product.main_image.url,
                "color": "",
                "title": product.name,
            })
        except Exception:
            pass

    context = {
        "product": product,
        "active_store": active_store,
        "current_store": active_store,
        "product_store": product.store,
        "variants": product.variants.all(),
        "gallery_images": gallery_images,
        "requested_color": request.GET.get("color", "").strip(),
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


def _resolve_cart_variant(item):
    """Надежно находит ProductVariant по variant_id или id (product_id)."""
    if not isinstance(item, dict):
        return None
    v_id = item.get("variant_id") or item.get("id")
    if not v_id:
        return None
    try:
        v_id_int = int(v_id)
    except (ValueError, TypeError):
        return None
    v = ProductVariant.objects.select_related("product__store").filter(id=v_id_int).first()
    if not v:
        v = ProductVariant.objects.select_related("product__store").filter(product_id=v_id_int).first()
    return v


@csrf_exempt
def create_telegram_order(request):
    """Принимает корзину, создает заказ и запускает опрос геопозиции в боте."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only POST"})

    try:
        data = json.loads(request.body) if request.body else {}
    except Exception:
        return JsonResponse({"success": False, "error": "Неверный формат данных JSON."})

    if not isinstance(data, dict):
        return JsonResponse({"success": False, "error": "Ожидается объект JSON."})

    cart_items = data.get("cart")
    chat_id = data.get("chat_id")

    if not cart_items or not isinstance(cart_items, list) or not chat_id:
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

        first_variant = _resolve_cart_variant(cart_items[0])
        if not first_variant:
            return JsonResponse({"success": False, "error": "Один из товаров в корзине не найден на складе. Пожалуйста, очистите корзину и выберите заново."})

        from core.working_views.views_branches import find_nearest_branch, _parse_coord
        target_store = first_variant.product.store
        initial_branch, _ = find_nearest_branch(target_store)

        order = Order.objects.create(
            store=target_store,
            branch=initial_branch,
            customer=customer,
            status=Order.Status.PENDING_PAYMENT,
            payment_method="click",
            delivery_address="Уточняется в Telegram боте"
        )

        for item in cart_items:
            variant = _resolve_cart_variant(item)
            if not variant:
                return JsonResponse({"success": False, "error": "Один из товаров не найден на складе."})

            try:
                raw_q = item.get("quantity") if isinstance(item, dict) else 1
                qty = max(1, min(999, int(raw_q or 1)))
            except (ValueError, TypeError):
                qty = 1
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
    payment_method = "card_transfer"
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
            customer = Customer.objects.filter(instagram_scoped_id=scoped_id).first()

        if not customer:
            customer, _ = Customer.objects.get_or_create(
                instagram_scoped_id=scoped_id,
                defaults={
                    "phone_number": phone_number[:20],
                    "full_name": (full_name or "Покупатель")[:255],
                    "telegram_chat_id": str(chat_id) if chat_id else "",
                    "telegram_username": (username or "")[:100],
                    "language": getattr(request, "LANGUAGE_CODE", "ru")
                }
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

        first_variant = _resolve_cart_variant(cart_items[0])
        if not first_variant:
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
            delivery_latitude=_parse_coord(latitude),
            delivery_longitude=_parse_coord(longitude),
            delivery_landmark=delivery_landmark,
            payment_method=payment_method,
        )

        for item in cart_items:
            variant = _resolve_cart_variant(item)
            if not variant:
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
        "payment_method": payment_method,
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
        if order.status == Order.Status.PAID:
            messages.info(request, _("Этот заказ уже оплачен и подтвержден."))
            return redirect(f"/{lang}/order/{order.id}/success/")

        receipt_file = request.FILES.get("receipt_image")
        if not receipt_file or getattr(receipt_file, "size", 0) == 0:
            messages.error(request, _("⚠️ Пожалуйста, обязательно прикрепите фото или скриншот чека об оплате!"))
            return redirect(f"/{lang}/order/{order.id}/success/")

        if getattr(receipt_file, "size", 0) > 15 * 1024 * 1024:
            messages.error(request, _("⚠️ Размер файла чека не должен превышать 15 МБ."))
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
        if order.store and order.store.telegram_bot_username and order.store.telegram_bot_active:
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


