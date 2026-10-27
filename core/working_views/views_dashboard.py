"""
views_dashboard.py
--------------------
Личный кабинет продавца. Здесь продавец:
- видит выручку и количество заказов
- быстро добавляет товар с фото и описанием
- включает/выключает товар, редактирует и удаляет его
- загружает свой логотип
- смотрит список заказов и передвигает их по статусам

Все view здесь защищены @login_required — доступны только вошедшим
пользователям, и только к СВОЕМУ магазину.
"""

import logging
import json
import secrets
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum, Count, Q
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.template import engines
from django.core.cache import cache

logger = logging.getLogger(__name__)

from decimal import Decimal
from core.models import Category, Product, ProductVariant, Order, Store, StoreBranch, StoreManager, ProductImage, Customer, Message, Conversation, OrderItem, ProductReview
from .telegram_bot import build_seller_link_deep_link, build_manager_link_deep_link, notify_customer_about_delivery
from .services import generate_ai_store_insights, generate_ai_broadcast_copy, generate_ai_product_description





def _get_store_or_none(request):
    """
    Возвращает Store текущего пользователя.
    1. Если у пользователя есть свой Store (owner).
    2. Если пользователь — менеджер магазина (StoreManager).
    3. Если пользователь — суперпользователь (admin) или staff без личного магазина:
       возвращает выбранный магазин (?store_id=...) или первый активный магазин,
       чтобы администратор мог полноценно видеть и управлять кабинетом!
    """
    if not request.user.is_authenticated:
        return None

    store = getattr(request.user, "store", None)

    if not store and (request.user.is_superuser or request.user.is_staff):
        target_store_id = request.GET.get("store_id") or (request.session.get("admin_view_store_id") if hasattr(request, "session") else None)
        if target_store_id:
            store = Store.objects.filter(id=target_store_id).first()
        if not store:
            store = Store.objects.filter(is_active=True).first() or Store.objects.first()
        if store and hasattr(request, "session"):
            request.session["admin_view_store_id"] = store.id

    if store and hasattr(request, "session"):
        from django.utils import translation
        session_lang = request.session.get("django_language")
        if not session_lang and store.telegram_notification_language:
            translation.activate(store.telegram_notification_language)
            request.LANGUAGE_CODE = store.telegram_notification_language
    return store


@login_required
def dashboard_home(request):
    """Главная страница кабинета — сводка по магазину, реальный график, реальная AI-аналитика."""
    store = _get_store_or_none(request)
    if store is None:
        if request.method == "POST" and request.POST.get("action") == "create_initial_store":
            store_name = request.POST.get("store_name", "").strip()
            slug_raw = request.POST.get("slug", "").strip()
            phone = request.POST.get("phone_number", "").strip()
            if store_name:
                from django.utils.text import slugify
                trans_map = str.maketrans({
                    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh','з':'z','и':'i','й':'y',
                    'к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f',
                    'х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
                    'ў':'o','қ':'q','ғ':'g','ҳ':'h'
                })
                raw_to_slug = (slug_raw or store_name).lower().translate(trans_map)
                slug_val = slugify(raw_to_slug) or f"store-{request.user.id}"
                base_slug = slug_val
                counter = 1
                while Store.objects.filter(slug=slug_val).exists():
                    slug_val = f"{base_slug}-{counter}"
                    counter += 1
                new_store = Store.objects.create(
                    owner=request.user,
                    name=store_name,
                    slug=slug_val,
                    phone_number=phone or "998900000000",
                    is_active=True,
                )
                messages.success(request, f"Магазин «{new_store.name}» успешно создан! Добро пожаловать!")
                return redirect("seller-dashboard")
        return render(request, "dashboard/no_store.html")

    # --- БЫСТРОЕ СОЗДАНИЕ ТОВАРА С ГЛАВНОЙ ПАНЕЛИ ---
    if request.method == "POST" and request.POST.get("action") == "quick_add_product":
        from django.utils.translation import gettext as _
        name = request.POST.get("name", "").strip()
        base_price = request.POST.get("base_price", "").strip()
        category_id = request.POST.get("category_id", "").strip()
        description = request.POST.get("description", "").strip()
        color = request.POST.get("color", "").strip() or "Основной"
        size_raw = request.POST.get("size", "").strip()
        stock_quantity_raw = request.POST.get("stock_quantity", "10").strip()
        main_image = request.FILES.get("main_image")

        if not name or not base_price:
            messages.error(request, _("Ошибка: укажите название и базовую цену товара."))
            return redirect("seller-dashboard")

        try:
            price_clean = base_price.replace(" ", "").replace(",", ".")
            price_val = float(price_clean)
            if price_val < 0:
                price_val = 0.0
        except (ValueError, TypeError):
            messages.error(request, _("Ошибка: некорректная цена товара."))
            return redirect("seller-dashboard")

        try:
            stock_qty = int(stock_quantity_raw)
            if stock_qty < 0:
                stock_qty = 0
        except (ValueError, TypeError):
            stock_qty = 10

        category = None
        if category_id:
            category = Category.objects.filter(id=category_id).first()
            if category and store.category_id != category.id:
                store.category = category
                store.save(update_fields=["category"])

        product = Product.objects.create(
            store=store,
            category=category,
            name=name,
            description=description,
            base_price=price_val,
            main_image=main_image,
        )

        colors = [c.strip() for c in color.split(",") if c.strip()] if color else ["Основной"]
        sizes = [s.strip() for s in size_raw.split(",") if s.strip()] if size_raw else [""]
        for c in colors:
            for s in sizes:
                sku = f"{store.id}-{product.id}-{secrets.token_hex(3)}"
                ProductVariant.objects.create(
                    product=product,
                    color=c,
                    size=s,
                    sku=sku,
                    stock_quantity=stock_qty,
                    image=main_image,
                )

        if main_image:
            try:
                for c in colors:
                    ProductImage.objects.create(product=product, image=main_image, color=c)
            except Exception as img_err:
                logger.warning("Ошибка при сохранении ProductImage: %s", img_err)

        try:
            from core.working_views.services import check_and_notify_waitlist_for_new_product
            notified_count = check_and_notify_waitlist_for_new_product(product)
            if notified_count > 0:
                messages.info(
                    request,
                    _("✨ Автоматически отправлено %(count)d уведомлений покупателям из листа ожидания!") % {"count": notified_count}
                )
        except Exception as e:
            logger.exception("Ошибка при уведомлении листа ожидания: %s", e)

        messages.success(
            request,
            _("🎉 Товар «%(name)s» успешно создан и опубликован в Telegram-боте и на веб-витрине!") % {"name": product.name}
        )
        return redirect("seller-dashboard")
    # -----------------------------------------------

    # --- БЛОК ДЛЯ СОХРАНЕНИЯ ЛОГОТИПА ---
    if request.method == "POST" and request.FILES.get("logo"):
        store.logo = request.FILES.get("logo")
        store.save(update_fields=["logo"])
        messages.success(request, "Логотип магазина успешно обновлен!")
        return redirect("seller-dashboard")
    # ------------------------------------

    orders = Order.objects.filter(store=store)
    paid_orders = orders.filter(status=Order.Status.PAID)
    revenue_stats = paid_orders.aggregate(total=Sum("total_price"))

    low_stock_variants = ProductVariant.objects.filter(
        product__store=store, stock_quantity__lte=3, stock_quantity__gt=0
    ).select_related("product")

    # --- РЕАЛЬНЫЙ ГРАФИК ПРОДАЖ ЗА 7 ДНЕЙ ---
    today = timezone.localdate()
    chart_labels = []
    chart_data = []
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        label = day_names[day.weekday()]
        daily_revenue = paid_orders.filter(
            created_at__date=day
        ).aggregate(total=Sum("total_price"))["total"] or 0
        chart_labels.append(label)
        chart_data.append(float(daily_revenue))

    # --- РЕАЛЬНАЯ AI-АНАЛИТИКА ---
    # Все диалоги этого магазина
    ai_conversations_count = Conversation.objects.filter(store=store).count()
    # Сообщения от ИИ = количество ответов агента
    ai_messages_count = Message.objects.filter(
        conversation__store=store,
        sender=Message.Sender.AI_AGENT
    ).count()
    # Конверсия: заказы из Telegram / общее кол-во ИИ-диалогов
    tg_orders_count = orders.filter(customer__instagram_scoped_id__startswith="tg_").count()
    ai_conversion = 0
    if ai_conversations_count > 0:
        ai_conversion = round((tg_orders_count / ai_conversations_count) * 100, 1)

    context = {
        "store": store,
        "total_revenue": revenue_stats["total"] or 0,
        "paid_orders_count": paid_orders.count(),
        "pending_orders_count": orders.filter(status=Order.Status.PENDING_PAYMENT).count(),
        "products_count": Product.objects.filter(store=store, is_active=True).count(),
        "categories": Category.objects.all().order_by("name"),
        "recent_products": Product.objects.filter(store=store).prefetch_related("variants").order_by("-id")[:4],
        "low_stock_variants": low_stock_variants,
        "store_catalog_url": store.get_subdomain_url(request),
        "telegram_bot_url": store.get_bot_url(),
        "telegram_connected": bool(store.telegram_chat_id),
        "telegram_link": (
            None if store.telegram_chat_id
            else build_seller_link_deep_link(store) if settings.TELEGRAM_BOT_USERNAME
            else None
        ),
        # График
        "chart_labels": json.dumps(chart_labels),
        "chart_data": json.dumps(chart_data),
        # AI-аналитика
        "ai_conversations_count": ai_conversations_count,
        "ai_messages_count": ai_messages_count,
        "ai_conversion": ai_conversion,
        "ai_insights": generate_ai_store_insights(store),
    }
    return render(request, "dashboard/home.html", context)


@login_required
def product_list_view(request):
    """Список всех товаров магазина (и активных, и выключенных)."""
    store = _get_store_or_none(request)
    if store is None:
        return redirect("seller-dashboard")

    products = Product.objects.filter(store=store).prefetch_related("variants")
    return render(request, "dashboard/products.html", {"products": products})


@login_required
def product_add_view(request):
    """Добавление товара с выбором категории, мульти-загрузкой фото на каждый цвет и поддержкой размеров (для одежды) или без размеров (для электроники и др.)."""
    store = _get_store_or_none(request)
    if store is None:
        return redirect("seller-dashboard")

    if request.method == "POST":
        name        = request.POST.get("name", "").strip()
        base_price  = request.POST.get("base_price", "").strip()
        description = request.POST.get("description", "").strip()
        product_type = request.POST.get("product_type", "clothes").strip()  # 'clothes' | 'electronics'
        category_id = request.POST.get("category_id")

        if not name or not base_price:
            messages.error(request, "Ошибка: заполните название и цену товара.")
            return redirect("seller-product-add")

        color_indices = request.POST.getlist("color_indices[]")
        if not color_indices:
            messages.error(request, "Ошибка: добавьте хотя бы один цвет/вариант товара.")
            return redirect("seller-product-add")

        category = None
        if category_id:
            from core.models import Category
            category = Category.objects.filter(id=category_id).first()
            if category:
                # Выбранная продавцом категория сохраняется навечно как основная ниша магазина
                if store.category_id != category.id:
                    store.category = category
                    store.save(update_fields=["category"])

        # Создаём продукт
        product = Product.objects.create(
            store=store,
            category=category,
            name=name,
            description=description,
            base_price=float(base_price),
        )

        target_main_color = request.POST.get("main_color_idx", "1").strip()
        target_main_photo_raw = request.POST.get("main_photo_idx", "0").strip()
        try:
            target_main_photo_idx = int(target_main_photo_raw)
        except (ValueError, TypeError):
            target_main_photo_idx = 0

        # Сначала ищем явно выбранное пользователем главное фото
        chosen_main_image = None
        if target_main_color:
            chosen_color_images = request.FILES.getlist(f"color_images_{target_main_color}")
            if chosen_color_images:
                if 0 <= target_main_photo_idx < len(chosen_color_images):
                    chosen_main_image = chosen_color_images[target_main_photo_idx]
                else:
                    chosen_main_image = chosen_color_images[0]

        # Если не найдено, запасной вариант — первое фото первого цвета
        if not chosen_main_image:
            for idx in color_indices:
                imgs = request.FILES.getlist(f"color_images_{idx}")
                if imgs:
                    chosen_main_image = imgs[0]
                    break

        if chosen_main_image:
            try:
                product.main_image = chosen_main_image
                product.save(update_fields=["main_image"])
            except Exception as img_err:
                logger.warning("Не удалось сохранить главное фото товара: %s", img_err)

        for idx in color_indices:
            color_name   = request.POST.get(f"color_name_{idx}", "").strip()
            color_images = request.FILES.getlist(f"color_images_{idx}")  # список файлов (2-10 шт)

            first_image = color_images[0] if color_images else None
            variant_preview = chosen_main_image if (str(idx) == str(target_main_color) and chosen_main_image) else first_image

            # Сохраняем все фото этого цвета в ProductImage
            for img_file in color_images:
                try:
                    ProductImage.objects.create(product=product, image=img_file, color=color_name)
                except Exception as img_err:
                    logger.warning("Не удалось сохранить фото галереи товара: %s", img_err)

            has_variants_grid = product_type in (
                "clothes", "shoes", "tires", "electronics_variants", "food_groceries"
            )

            if has_variants_grid:
                # Для одежды, обуви, шин, гаджетов и продуктов — создаём вариант на каждый размер/параметр
                sizes  = request.POST.getlist(f"size_{idx}[]")
                qtys   = request.POST.getlist(f"qty_{idx}[]")
                prices = request.POST.getlist(f"price_{idx}[]")

                created_any = False
                for i, (size, qty) in enumerate(zip(sizes, qtys)):
                    size = size.strip()
                    if not size:
                        continue
                    sku = f"{store.id}-{product.id}-{secrets.token_hex(3)}"
                    price_val = None
                    if i < len(prices) and prices[i].strip():
                        try:
                            price_val = float(prices[i].strip())
                        except (ValueError, TypeError):
                            price_val = None

                    ProductVariant.objects.create(
                        product=product,
                        color=color_name,
                        size=size,
                        sku=sku,
                        stock_quantity=int(qty) if str(qty).isdigit() else 0,
                        price_override=price_val,
                        image=variant_preview,
                    )
                    created_any = True

                # Если продавец не ввёл строки размеров, но указал цвет/вариант и общее количество
                if not created_any:
                    qty_raw = request.POST.get(f"color_qty_{idx}", "0").strip()
                    sku = f"{store.id}-{product.id}-{secrets.token_hex(3)}"
                    ProductVariant.objects.create(
                        product=product,
                        color=color_name,
                        size="",
                        sku=sku,
                        stock_quantity=int(qty_raw) if qty_raw.isdigit() else 0,
                        image=variant_preview,
                    )
            else:
                # Для штучных товаров (бытовая техника, книги, парфюмерия, косметика) — один вариант без размера
                qty_raw = request.POST.get(f"color_qty_{idx}", "0").strip()
                sku = f"{store.id}-{product.id}-{secrets.token_hex(3)}"
                ProductVariant.objects.create(
                    product=product,
                    color=color_name,
                    size="",
                    sku=sku,
                    stock_quantity=int(qty_raw) if qty_raw.isdigit() else 0,
                    image=variant_preview,
                )

        # Сканируем лист ожидания: если клиенты ждали этот товар, оповещаем их автоматически!
        try:
            from core.working_views.services import check_and_notify_waitlist_for_new_product
            notified_count = check_and_notify_waitlist_for_new_product(product)
            if notified_count > 0:
                messages.info(
                    request,
                    f"🎉 Автоматически отправлено {notified_count} уведомлений клиентам из листа ожидания (Waitlist)!"
                )
        except Exception as e:
            logger.exception("Ошибка при уведомлении клиентов из листа ожидания: %s", e)

        messages.success(request, f'Товар "{name}" успешно опубликован!')
        return redirect("seller-products")

    from core.models import Category
    categories = Category.objects.all().order_by('id')
    default_category = store.effective_category
    default_category_id = default_category.id if default_category else None
    return render(request, "dashboard/product_add.html", {
        "store": store,
        "categories": categories,
        "default_category_id": default_category_id,
        "default_category": default_category,
    })




@login_required
def product_toggle_active_view(request, product_id):
    """Скрытие/отображение товара в каталоге."""
    store = _get_store_or_none(request)
    product = get_object_or_404(Product, id=product_id, store=store)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active"])
    return redirect("seller-products")


@login_required
def order_list_view(request):
    """Список заказов магазина."""
    store = _get_store_or_none(request)
    if store is None:
        return redirect("seller-dashboard")

    orders = Order.objects.filter(store=store).prefetch_related("items__variant__product")
    return render(request, "dashboard/orders.html", {"orders": orders})


@login_required
def order_mark_handed_view(request, order_id):
    """Смена статуса заказа на 'Передан курьеру' + отправка трекинг-ссылки клиенту."""
    store = _get_store_or_none(request)
    order = get_object_or_404(Order, id=order_id, store=store)

    if request.method == "POST":
        tracking_url = request.POST.get("tracking_url", "").strip()
        if tracking_url:
            order.tracking_url = tracking_url

        order.status = Order.Status.HANDED_TO_COURIER
        order.save(update_fields=["status", "tracking_url"] if tracking_url else ["status"])

        # Уведомляем клиента в Telegram с ссылкой на отслеживание доставки
        notify_customer_about_delivery(order)

        messages.success(request, f"Заказ #{order.id} передан курьеру! Клиент получил уведомление.")

    return redirect("seller-orders")


@login_required
def order_mark_delivered_view(request, order_id):
    """Смена статуса заказа на 'Доставлен' + разморозка средств и автовыплата продавцу."""
    store = _get_store_or_none(request)
    order = get_object_or_404(Order, id=order_id, store=store)

    if request.method == "POST":
        order.status = Order.Status.DELIVERED
        order.save(update_fields=["status"])

        from core.payments.payout_service import release_order_funds_and_auto_payout
        release_order_funds_and_auto_payout(order)

        messages.success(request, f"Заказ #{order.id} успешно доставлен!")

    return redirect("seller-orders")


@login_required
def order_confirm_payment_view(request, order_id):
    """Продавец подтверждает поступление перевода от покупателя на свою карту."""
    store = _get_store_or_none(request)
    order = get_object_or_404(Order, id=order_id, store=store)

    if request.method == "POST":
        if order.status != Order.Status.PAID:
            order.status = Order.Status.PAID
            order.save(update_fields=["status"])

            # Списываем остаток товара на складе в маркетплейсе
            for item in order.items.select_related("variant").all():
                if item.variant:
                    item.variant.stock_quantity = max(0, item.variant.stock_quantity - item.quantity)
                    item.variant.save(update_fields=["stock_quantity"])

        # Уведомляем менеджеров на сборку/такси и клиента об успешной оплате
        try:
            from core.working_views.telegram_bot import notify_seller_about_order
            notify_seller_about_order(order)
        except Exception as e:
            logger.warning("Не удалось уведомить о подтверждении оплаты заказа: %s", e)

        messages.success(request, f"✅ Оплата заказа #{order.id} подтверждена! Заказ передан менеджерам на сборку и отправку.")

    return redirect("seller-orders")




@login_required
def product_delete_view(request, product_id):
    """Полное удаление товара из базы данных."""
    store = _get_store_or_none(request)
    product = get_object_or_404(Product, id=product_id, store=store)

    if request.method == "POST":
        product.delete()
        messages.success(request, "Товар успешно удален!")

    return redirect("seller-products")


@login_required
def product_edit_view(request, product_id):
    """
    Редактирование информации товара и управление его фотографиями:
    - Изменение названия, цены, описания и категории
    - Выбор любого фото товара в качестве главного (main_image) в 1 клик
    - Загрузка новых фотографий в галерею или напрямую в главное фото
    - Удаление ненужных фотографий из галереи
    """
    store = _get_store_or_none(request)
    product = get_object_or_404(Product, id=product_id, store=store)

    if request.method == "POST":
        action = request.POST.get("action", "update_info")

        # 1. Сделать выбранное фото из галереи главным фото товара
        if action == "set_main_image":
            image_id = request.POST.get("image_id")
            if image_id:
                img_obj = ProductImage.objects.filter(id=image_id, product=product).first()
                if img_obj and img_obj.image:
                    product.main_image = img_obj.image
                    product.save(update_fields=["main_image"])
                    messages.success(request, "⭐ Главное фото товара успешно обновлено!")
                    return redirect("seller-product-edit", product_id=product.id)

        # 2. Сделать фото из варианта главным фото товара
        elif action == "set_main_variant":
            variant_id = request.POST.get("variant_id")
            if variant_id:
                var_obj = ProductVariant.objects.filter(id=variant_id, product=product).first()
                if var_obj and var_obj.image:
                    product.main_image = var_obj.image
                    product.save(update_fields=["main_image"])
                    messages.success(request, "⭐ Главное фото товара успешно обновлено!")
                    return redirect("seller-product-edit", product_id=product.id)

        # 3. Загрузить новое главное фото напрямую
        elif action == "upload_main_image":
            if "new_main_file" in request.FILES:
                new_file = request.FILES["new_main_file"]
                product.main_image = new_file
                product.save(update_fields=["main_image"])
                ProductImage.objects.create(product=product, image=new_file)
                messages.success(request, "⭐ Новое главное фото успешно загружено и установлено!")
                return redirect("seller-product-edit", product_id=product.id)

        # 4. Добавить новые фотографии в галерею товара
        elif action == "add_gallery_images":
            files = request.FILES.getlist("gallery_files")
            if files:
                for f in files:
                    ProductImage.objects.create(product=product, image=f)
                messages.success(request, f"✅ Добавлено {len(files)} фото в галерею товара.")
                return redirect("seller-product-edit", product_id=product.id)

        # 5. Удалить фото из галереи
        elif action == "delete_image":
            image_id = request.POST.get("image_id")
            if image_id:
                img_obj = ProductImage.objects.filter(id=image_id, product=product).first()
                if img_obj:
                    img_obj.delete()
                    messages.success(request, "🗑 Фото успешно удалено из галереи.")
                    return redirect("seller-product-edit", product_id=product.id)

        # 6. Основное обновление информации: Название, Цена, Описание, Категория
        elif action == "update_info":
            name = request.POST.get("name", "").strip()
            base_price = request.POST.get("base_price", "").strip()
            description = request.POST.get("description", "").strip()
            category_id = request.POST.get("category_id")

            if not name or not base_price:
                messages.error(request, "Ошибка: заполните название и цену товара.")
                return redirect("seller-product-edit", product_id=product.id)

            try:
                product.base_price = float(base_price)
            except (ValueError, TypeError):
                messages.error(request, "Некорректная цена товара.")
                return redirect("seller-product-edit", product_id=product.id)

            product.name = name
            product.description = description
            if category_id:
                from core.models import Category
                cat = Category.objects.filter(id=category_id).first()
                product.category = cat

            product.save()
            messages.success(request, "✅ Информация о товаре успешно обновлена!")
            return redirect("seller-product-edit", product_id=product.id)

        # 7. Обновление остатков всех вариантов сразу
        elif action == "update_stocks":
            variants = product.variants.all()
            updated_count = 0
            for var in variants:
                val = request.POST.get(f"stock_{var.id}")
                if val is not None:
                    try:
                        new_qty = max(0, int(val))
                        if var.stock_quantity != new_qty:
                            var.stock_quantity = new_qty
                            var.save(update_fields=["stock_quantity"])
                            updated_count += 1
                    except (ValueError, TypeError):
                        pass
            messages.success(request, f"📦 Остатки обновлены (изменено позиций: {updated_count}).")
            return redirect("seller-product-edit", product_id=product.id)

        # 8. Добавление нового варианта (цвет, размер, количество, фото)
        elif action == "add_variant":
            color = request.POST.get("variant_color", "").strip()
            size = request.POST.get("variant_size", "").strip()
            stock_qty = request.POST.get("variant_stock", "0").strip()
            var_image = request.FILES.get("variant_image")

            try:
                qty = max(0, int(stock_qty))
            except (ValueError, TypeError):
                qty = 0

            new_var = ProductVariant.objects.create(
                product=product,
                color=color,
                size=size,
                stock_quantity=qty,
                image=var_image if var_image else None
            )
            if var_image:
                ProductImage.objects.create(product=product, image=var_image)

            messages.success(request, f"✅ Новый вариант '{color} ({size})' успешно добавлен с остатком {qty} шт.")
            return redirect("seller-product-edit", product_id=product.id)

        # 9. Удаление варианта
        elif action == "delete_variant":
            variant_id = request.POST.get("variant_id")
            if variant_id:
                var_obj = ProductVariant.objects.filter(id=variant_id, product=product).first()
                if var_obj:
                    var_name = f"{var_obj.color} {var_obj.size}".strip() or "вариант"
                    var_obj.delete()
                    messages.success(request, f"🗑 Вариант '{var_name}' удалён.")
            return redirect("seller-product-edit", product_id=product.id)

    from core.models import Category
    categories = Category.objects.all().order_by("name")
    gallery_images = product.images.all().order_by("id")
    all_variants = product.variants.all().order_by("id")
    variants_with_images = product.variants.filter(image__isnull=False).exclude(image="").order_by("id")
    total_stock = sum(v.stock_quantity for v in all_variants)

    # Список URL изображений для удобной проверки главного фото
    main_image_url = product.main_image.url if product.main_image else ""

    return render(
        request,
        "dashboard/product_edit.html",
        {
            "product": product,
            "categories": categories,
            "gallery_images": gallery_images,
            "all_variants": all_variants,
            "variants_with_images": variants_with_images,
            "total_stock": total_stock,
            "main_image_url": main_image_url,
        },
    )


@login_required
def product_inventory_edit_view(request, product_id):
    """Управление остатками и себестоимостью товара (Приход)."""
    store = getattr(request.user, "store", None)
    if not store:
        return redirect("seller-dashboard")
        
    product = get_object_or_404(Product, id=product_id, store=store)
    
    if request.method == "POST":
        for variant in product.variants.all():
            cost_price_str = request.POST.get(f"cost_price_{variant.id}")
            add_qty_str = request.POST.get(f"add_qty_{variant.id}")
            
            if cost_price_str:
                variant.cost_price = float(cost_price_str)
                
            if add_qty_str and int(add_qty_str) != 0:
                qty = int(add_qty_str)
                variant.stock_quantity += qty
                from core.models import StockMovement, CashierProfile
                # Для прихода из дашборда используем владельца
                cashier, _ = CashierProfile.objects.get_or_create(
                    store=store, 
                    role=CashierProfile.Role.OWNER,
                    defaults={'name': 'Владелец (Dashboard)', 'pin_code': '0000'}
                )
                StockMovement.objects.create(
                    variant=variant,
                    movement_type=StockMovement.MovementType.RECEIPT if qty > 0 else StockMovement.MovementType.WRITE_OFF,
                    quantity=qty,
                    author=cashier,
                    reason="Приход через дашборд"
                )
            
            variant.save(update_fields=["cost_price", "stock_quantity"])

        # Если товар пополнился — оповещаем покупателей из листа ожидания
        try:
            from core.working_views.services import check_and_notify_waitlist_for_new_product
            notified_count = check_and_notify_waitlist_for_new_product(product)
            if notified_count > 0:
                messages.info(
                    request,
                    f"🎉 Автоматически отправлено {notified_count} уведомлений клиентам из листа ожидания (Waitlist)!"
                )
        except Exception as e:
            logger.exception("Ошибка при уведомлении клиентов из листа ожидания: %s", e)
            
        from django.contrib import messages
        messages.success(request, "Остатки и себестоимость успешно обновлены!")
        return redirect("seller-products")
        
    html_form = f"""
    {{% extends "dashboard/base.html" %}}
    {{% block content %}}
    <h2 style="margin-bottom: 24px;">📦 Склад: {{product.name}}</h2>
    <div class="card" style="max-width: 600px;">
        <form method="post">
            {{% csrf_token %}}
            <table style="width: 100%; text-align: left; border-collapse: collapse; margin-bottom: 20px;">
                <thead>
                    <tr style="border-bottom: 1px solid var(--border);">
                        <th style="padding: 10px 0;">Вариант</th>
                        <th style="padding: 10px 0;">Остаток</th>
                        <th style="padding: 10px 0;">Себестоимость</th>
                        <th style="padding: 10px 0;">Приход (шт)</th>
                    </tr>
                </thead>
                <tbody>
                    {{% for v in product.variants.all %}}
                    <tr style="border-bottom: 1px solid var(--border);">
                        <td style="padding: 12px 0;">{{% if v.color or v.size %}}{{{{ v.color }}}} ({{{{ v.size }}}}){{% else %}}Стандарт{{% endif %}}</td>
                        <td style="padding: 12px 0; font-weight: bold;">{{{{ v.stock_quantity }}}}</td>
                        <td style="padding: 12px 0; width: 120px;">
                            <input type="number" name="cost_price_{{{{ v.id }}}}" value="{{{{ v.cost_price|default_if_none:'' }}}}" style="margin:0; padding: 8px;" placeholder="0">
                        </td>
                        <td style="padding: 12px 0; width: 100px;">
                            <input type="number" name="add_qty_{{{{ v.id }}}}" value="0" style="margin:0; padding: 8px;">
                        </td>
                    </tr>
                    {{% endfor %}}
                </tbody>
            </table>

            <button type="submit" class="btn" style="width: 100%; margin-top: 10px;">💾 Сохранить изменения</button>
            <a href="{{% url 'seller-products' %}}" class="btn btn-secondary" style="width: 100%; margin-top: 10px; text-align: center;">Отмена</a>
        </form>
    </div>
    {{% endblock %}}
    """
    
    from django.template import engines
    template = engines['django'].from_string(html_form)
    return HttpResponse(template.render({"product": product, "request": request}, request))


@csrf_exempt
def telegram_init_auth_api(request):
    """
    Автоматический 1-клик вход для пользователей Telegram Mini App через initData.
    Получает init_data из JavaScript window.Telegram.WebApp.initData,
    проверяет криптографическую подпись HMAC-SHA256 и авторизует владельца магазина.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "method_not_allowed"}, status=405)

    try:
        data = json.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception:
        data = {}

    init_data_raw = data.get("init_data") or request.POST.get("init_data", "")
    next_url = data.get("next") or request.POST.get("next") or "/dashboard/"
    if not next_url or not next_url.startswith("/") or "/login" in next_url or "/logout" in next_url:
        next_url = "/dashboard/"

    if not init_data_raw:
        return JsonResponse({"success": False, "error": "empty_init_data"}, status=400)

    import urllib.parse
    import hmac
    import hashlib

    parsed_data = dict(urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True))
    received_hash = parsed_data.pop("hash", "")
    bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""

    # Валидация подписи Telegram, если бот-токен настроен
    is_valid = True
    if bot_token and received_hash:
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        is_valid = hmac.compare_digest(calc_hash, received_hash)

    if not is_valid:
        logger.warning("Недействительная HMAC-подпись initData от Telegram")
        return JsonResponse({"success": False, "error": "invalid_signature"}, status=403)

    user_raw = parsed_data.get("user")
    if not user_raw:
        return JsonResponse({"success": False, "error": "no_user_in_init_data"}, status=400)

    try:
        user_info = json.loads(user_raw)
    except Exception:
        return JsonResponse({"success": False, "error": "invalid_user_json"}, status=400)

    tg_id = user_info.get("id")
    tg_uname = (user_info.get("username") or "").strip().lstrip("@")

    target_user = None
    store = None

    # 1. Поиск Store по chat_id
    if tg_id:
        store = Store.objects.filter(telegram_chat_id=str(tg_id)).select_related("owner").first()
        if store and store.owner:
            target_user = store.owner

    # 2. Поиск Store по owner_telegram_username
    if not target_user and tg_uname:
        store = Store.objects.filter(owner_telegram_username__iexact=tg_uname).select_related("owner").first()
        if store and store.owner:
            target_user = store.owner

    # 3. Поиск User по username
    if not target_user and tg_uname:
        target_user = User.objects.filter(username__iexact=tg_uname).first()
        if target_user:
            store = getattr(target_user, "store", None)

    # 4. Поиск по Customer telegram_chat_id
    if not target_user and tg_id:
        customer = Customer.objects.filter(telegram_chat_id=str(tg_id)).first()
        if customer and customer.telegram_username:
            store = Store.objects.filter(owner_telegram_username__iexact=customer.telegram_username.lstrip("@")).select_related("owner").first()
            if store and store.owner:
                target_user = store.owner

    # 5. Поиск суперпользователя платформы
    if not target_user and tg_uname:
        from core.models import PlatformSettings
        ps = PlatformSettings.objects.first()
        if ps and ps.superadmin_telegram_username and ps.superadmin_telegram_username.lower().lstrip("@") == tg_uname.lower():
            target_user = User.objects.filter(is_superuser=True).first()

    if not target_user:
        return JsonResponse({
            "success": False,
            "error": "user_not_found",
            "message": "Пользователь Telegram еще не привязан к магазину",
            "telegram_id": tg_id,
            "telegram_username": tg_uname
        }, status=200)

    # Успешная авторизация!
    login(request, target_user)
    request.session.save()
    messages.success(request, f"Вход выполнен через Telegram! Добро пожаловать, {store.name if store else target_user.username}!")
    logger.info("Успешный 1-клик вход через Telegram WebApp initData: user=%s (tg_id=%s)", target_user.username, tg_id)

    return JsonResponse({
        "success": True,
        "redirect_url": next_url,
        "username": target_user.username,
        "store": store.name if store else None
    })


def seller_login_view(request):
    """
    Авторизация продавца:
    Поддерживает:
    1. Моментальный вход по логину / телефону / поддомену + паролю.
    2. Вход по 6-значному коду из Telegram (/code в боте) без знания пароля.
    3. Запрос кода в Telegram по нажатию кнопки «Получить код в Telegram».
    4. Автоматический вход по ?tg_auth=<token>.
    5. Полноценная поддержка мобильных телефонов (нечувствительность к регистру, пробелам в телефонах).
    """
    import re

    # Проверяем параметр tg_auth для 1-клик входа из Telegram
    tg_auth = request.GET.get("tg_auth")
    if tg_auth:
        store = Store.objects.filter(telegram_link_token=tg_auth).select_related("owner").first()
        if store and store.owner:
            login(request, store.owner)
            request.session.save()
            messages.success(request, f"Вход выполнен через Telegram! Добро пожаловать, {store.name}!")
            return redirect("seller-dashboard")

    if request.user.is_authenticated:
        return redirect("seller-dashboard")

    if request.method == "POST":
        action = request.POST.get("action", "login").strip()
        username_raw = request.POST.get("username", "").strip()
        password_raw = request.POST.get("password", "").strip()

        # Получаем параметр next для безопасного редиректа
        next_url = request.POST.get("next") or request.GET.get("next")
        if not next_url or not next_url.startswith("/") or "/login" in next_url or "/logout" in next_url:
            next_url = None

        # Проверка OTP-кандидата (6 цифр)
        cleaned_code = password_raw.replace(" ", "").strip()
        is_otp_candidate = bool(cleaned_code and len(cleaned_code) == 6 and cleaned_code.isdigit())

        # Поиск целевого пользователя (User) и магазина (Store)
        target_user = None
        store = None

        if username_raw:
            clean_login = username_raw.strip()
            clean_tg = clean_login.lstrip("@").lower()
            clean_digits = re.sub(r"\D", "", clean_login)

            # 1. Поиск по username или email
            target_user = (
                User.objects.filter(username__iexact=clean_login).first()
                or User.objects.filter(email__iexact=clean_login).first()
            )

            # 2. Поиск по номеру телефона магазина
            if not target_user and len(clean_digits) >= 7:
                store_by_phone = Store.objects.filter(phone_number__icontains=clean_digits[-9:]).first()
                if store_by_phone:
                    store = store_by_phone
                    target_user = store.owner

            # 3. Поиск по Store (slug, имя, instagram, telegram)
            if not target_user:
                store = (
                    Store.objects.filter(slug__iexact=clean_login).first()
                    or Store.objects.filter(name__iexact=clean_login).first()
                    or Store.objects.filter(owner_telegram_username__iexact=clean_tg).first()
                    or Store.objects.filter(instagram_username__iexact=clean_tg).first()
                )
                if store:
                    target_user = store.owner
            else:
                if not store:
                    store = getattr(target_user, "store", None)

        # Если target_user не найден по логину, но введен 6-значный OTP код:
        if not target_user and is_otp_candidate:
            user_id_from_otp = cache.get(f"otp_to_user_{cleaned_code}")
            if user_id_from_otp:
                target_user = User.objects.filter(id=user_id_from_otp).first()
                if target_user:
                    store = getattr(target_user, "store", None)

        # Действие: пользователь нажал кнопку «Получить код в Telegram»
        if action == "request_otp":
            if not target_user:
                messages.error(request, f"Пользователь или магазин '{username_raw}' не найден. Проверьте правильность логина или номера.")
                return render(request, "dashboard/login.html", {"username": username_raw, "next": next_url})

            # Генерируем 6-значный OTP код (сохраняем в БД магазина для надёжности между воркерами)
            if store:
                otp_code = store.generate_login_otp()
            else:
                otp_code = f"{secrets.randbelow(900000) + 100000}"
            cache.set(f"store_pending_otp_{store.id if store else target_user.id}", otp_code, timeout=600)
            cache.set(f"user_login_otp_{target_user.id}", otp_code, timeout=600)
            cache.set(f"username_pending_otp_{target_user.username.lower()}", otp_code, timeout=600)
            cache.set(f"otp_to_user_{otp_code}", target_user.id, timeout=600)
            if store and store.slug:
                cache.set(f"username_pending_otp_{store.slug.lower()}", otp_code, timeout=600)

            # Сохраняем во временную сессию
            request.session["pre_2fa_user_id"] = target_user.id
            request.session["otp_code"] = otp_code
            request.session["otp_expires_at"] = (timezone.now() + timedelta(minutes=10)).timestamp()
            request.session.save()

            # Определяем Telegram chat_id
            chat_id = store.telegram_chat_id if store else None
            if not chat_id and store and store.owner_telegram_username:
                cust = Customer.objects.filter(telegram_username__iexact=store.owner_telegram_username.lstrip("@")).first()
                if cust and cust.telegram_chat_id:
                    chat_id = cust.telegram_chat_id
                    store.telegram_chat_id = str(chat_id)
                    store.save(update_fields=["telegram_chat_id"])

            if chat_id:
                try:
                    from core.working_views.telegram_bot import _send_telegram_message
                    time_str = timezone.localtime().strftime("%H:%M:%S")
                    fast_link = f"{settings.SITE_URL}/dashboard/?tg_auth={store.telegram_link_token}" if (store and store.telegram_link_token) else None
                    reply_markup = None
                    if fast_link:
                        reply_markup = {"inline_keyboard": [[{"text": "🚀 Войти в 1 клик", "web_app": {"url": fast_link}}]]}
                    _send_telegram_message(
                        chat_id,
                        f"🔐 ВАШ КОД ВХОДА В BRAINMART:\n\n"
                        f"👉 {otp_code} 👈\n\n"
                        f"👤 Логин: {target_user.username}\n"
                        f"🏪 Магазин: «{store.name if store else 'Кабинет'}»\n"
                        f"🕒 Время: {time_str}\n"
                        f"⏱ Действует 10 минут.\n\n"
                        f"💡 Введите этот 6-значный код на сайте или нажмите кнопку ниже для входа в 1 клик!",
                        reply_markup=reply_markup
                    )
                    messages.success(request, "Код подтверждения отправлен в ваш Telegram! Введите его в поле ниже.")
                except Exception as e:
                    logger.warning("Не удалось отправить OTP в Telegram: %s", e)
                    messages.warning(request, "Не удалось отправить сообщение в Telegram. Проверьте подключение бота.")
            else:
                messages.warning(request, "Telegram-бот еще не привязан к магазину. Вы можете войти по паролю.")

            return render(request, "dashboard/login.html", {
                "username": target_user.username,
                "code_sent": True,
                "next": next_url,
            })

        # --- СЦЕНАРИЙ А: Проверка входа по 6-значному коду из Telegram ---
        if target_user and is_otp_candidate:
            store_otp = str(cache.get(f"store_pending_otp_{store.id}") or "") if store else ""
            user_otp = str(cache.get(f"user_login_otp_{target_user.id}") or "")
            uname_otp = str(cache.get(f"username_pending_otp_{target_user.username.lower()}") or "")
            slug_otp = str(cache.get(f"username_pending_otp_{store.slug.lower()}") or "") if (store and store.slug) else ""
            session_otp = str(request.session.get("otp_code") or "")

            valid_otps = {c for c in [store_otp, user_otp, uname_otp, slug_otp, session_otp] if c}
            is_db_valid = store.verify_login_otp(cleaned_code) if store else False
            is_global_otp_valid = (cache.get(f"otp_to_user_{cleaned_code}") == target_user.id)

            if (cleaned_code in valid_otps) or is_db_valid or is_global_otp_valid:
                login(request, target_user)
                request.session.save()

                if store:
                    cache.delete(f"store_pending_otp_{store.id}")
                    store.clear_login_otp()
                cache.delete(f"user_login_otp_{target_user.id}")
                cache.delete(f"username_pending_otp_{target_user.username.lower()}")
                cache.delete(f"otp_to_user_{cleaned_code}")

                messages.success(request, f"Вход выполнен по коду подтверждения! Добро пожаловать, {store.name if store else target_user.username}!")
                dest = next_url if (next_url and "/login" not in next_url and "/logout" not in next_url) else "seller-dashboard"
                return redirect(dest)

        # --- СЦЕНАРИЙ Б: Авторизация по логину и паролю (МОМЕНТАЛЬНЫЙ ВХОД!) ---
        auth_username = target_user.username if target_user else username_raw
        user = authenticate(request, username=auth_username, password=password_raw)

        if user is None:
            if is_otp_candidate and target_user:
                messages.error(request, "Неверный код подтверждения из Telegram или срок его действия истёк. Нажмите «Получить код в Telegram» или отправьте /code боту.")
            else:
                messages.error(request, "Неверный логин или пароль. Вы также можете нажать «Получить код в Telegram» для входа без пароля.")
            return render(request, "dashboard/login.html", {"username": username_raw, "next": next_url})

        # Пользователь успешно прошёл аутентификацию по паролю! ВХОДИМ СРАЗУ!
        login(request, user)
        request.session.save()

        store = _get_store_or_none(request)
        messages.success(request, f"Вход выполнен! Добро пожаловать в панель управления, {store.name if store else user.username}!")
        dest = next_url if (next_url and "/login" not in next_url and "/logout" not in next_url) else "seller-dashboard"
        return redirect(dest)

    return render(request, "dashboard/login.html")


def seller_verify_otp_view(request):
    """
    Проверка одноразового кода OTP для завершения входа.
    """
    user_id = request.session.get("pre_2fa_user_id")
    username_param = request.GET.get("u", "").strip() or request.POST.get("username", "").strip()

    user = None
    if user_id:
        user = User.objects.filter(id=user_id).first()
    if not user and username_param:
        user = User.objects.filter(username__iexact=username_param).first()
        if not user:
            s = Store.objects.filter(slug__iexact=username_param).first() or Store.objects.filter(name__iexact=username_param).first()
            if s:
                user = s.owner

    if not user:
        messages.error(request, "Сессия входа истекла. Пожалуйста, введите ваш логин.")
        return redirect("seller-login")

    store = getattr(user, "store", None)
    now_ts = timezone.now().timestamp()
    expires_ts = request.session.get("otp_expires_at", 0)
    if not expires_ts:
        expires_ts = now_ts + 600

    chat_id = store.telegram_chat_id if store else None

    if request.method == "POST":
        entered_code = request.POST.get("otp_code", "").strip().replace(" ", "")

        # Собираем все валидные коды (сессия + кэш)
        expected_session_code = str(request.session.get("otp_code", ""))
        cached_store_otp = str(cache.get(f"store_pending_otp_{store.id}") or "") if store else ""
        cached_user_otp = str(cache.get(f"user_login_otp_{user.id}") or "")
        cached_uname_otp = str(cache.get(f"username_pending_otp_{user.username.lower()}") or "")
        cached_slug_otp = str(cache.get(f"username_pending_otp_{store.slug.lower()}") or "") if (store and store.slug) else ""

        valid_codes = {c for c in [expected_session_code, cached_store_otp, cached_user_otp, cached_uname_otp, cached_slug_otp] if c}
        is_db_valid = store.verify_login_otp(entered_code) if store else False

        if entered_code and ((entered_code in valid_codes) or is_db_valid):
            # Успешный вход!
            if store:
                cache.delete(f"store_pending_otp_{store.id}")
                store.clear_login_otp()
            cache.delete(f"user_login_otp_{user.id}")
            cache.delete(f"username_pending_otp_{user.username.lower()}")

            request.session.pop("pre_2fa_user_id", None)
            request.session.pop("pre_2fa_store_id", None)
            request.session.pop("otp_code", None)
            request.session.pop("otp_expires_at", None)
            request.session.pop("otp_attempts", None)
            request.session.pop("otp_last_sent_at", None)

            login(request, user)
            request.session.save()

            if chat_id:
                try:
                    from core.working_views.telegram_bot import _send_telegram_message
                    _send_telegram_message(
                        chat_id,
                        f"✅ Успешный вход в панель BrainMart выполнен ({user.username})."
                    )
                except Exception:
                    pass

            next_url = request.POST.get("next") or request.GET.get("next")
            if not next_url or not next_url.startswith("/"):
                next_url = None
            messages.success(request, f"Вход выполнен! Добро пожаловать, {store.name if store else user.username}!")
            return redirect(next_url or "seller-dashboard")
        else:
            attempts = request.session.get("otp_attempts", 0) + 1
            request.session["otp_attempts"] = attempts
            request.session.save()
            left_attempts = max(0, 5 - attempts)
            messages.error(request, f"Неверный код подтверждения! Проверьте код в Telegram или запросите новый. Осталось попыток: {left_attempts}")

    seconds_left = max(0, int(expires_ts - now_ts))
    bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
    link_url = f"https://t.me/{bot_username}?start=link_{store.telegram_link_token}" if (store and bot_username and store.telegram_link_token) else ""

    return render(request, "dashboard/verify_otp.html", {
        "store": store,
        "user_obj": user,
        "seconds_left": seconds_left,
        "bot_username": bot_username,
        "link_url": link_url,
        "has_telegram": bool(chat_id),
    })


def seller_resend_otp_view(request):
    """
    Повторная отправка OTP-кода в Telegram с ограничением по времени (кулдаун 15 сек).
    """
    user_id = request.session.get("pre_2fa_user_id")
    username_param = request.GET.get("u", "").strip() or request.POST.get("username", "").strip()

    user = None
    if user_id:
        user = User.objects.filter(id=user_id).first()
    if not user and username_param:
        user = User.objects.filter(username__iexact=username_param).first()
        if not user:
            s = Store.objects.filter(slug__iexact=username_param).first()
            if s:
                user = s.owner

    if not user:
        return redirect("seller-login")

    store = getattr(user, "store", None)
    if store:
        new_code = store.generate_login_otp()
    else:
        new_code = f"{secrets.randbelow(900000) + 100000}"
    expires_ts = (timezone.now() + timedelta(minutes=10)).timestamp()

    request.session["pre_2fa_user_id"] = user.id
    request.session["otp_code"] = new_code
    request.session["otp_expires_at"] = expires_ts
    request.session["otp_attempts"] = 0
    request.session.save()

    if store:
        cache.set(f"store_pending_otp_{store.id}", new_code, timeout=600)
    cache.set(f"user_login_otp_{user.id}", new_code, timeout=600)
    cache.set(f"username_pending_otp_{user.username.lower()}", new_code, timeout=600)
    if store and store.slug:
        cache.set(f"username_pending_otp_{store.slug.lower()}", new_code, timeout=600)

    chat_id = store.telegram_chat_id if store else None
    if chat_id:
        try:
            from core.working_views.telegram_bot import _send_telegram_message
            time_str = timezone.localtime().strftime("%H:%M:%S")
            msg = (
                f"🔄 НОВЫЙ КОД ВХОДА BRAINMART\n\n"
                f"👉 {new_code} 👈\n\n"
                f"👤 Логин: {user.username}\n"
                f"🕒 Время: {time_str}\n"
                f"⏱ Действует 10 минут."
            )
            _send_telegram_message(chat_id, msg)
            messages.success(request, "Новый код успешно отправлен в ваш Telegram!")
        except Exception:
            messages.error(request, "Ошибка при отправке в Telegram.")
    else:
        messages.warning(request, "Telegram еще не привязан к магазину.")

    return redirect(f"/dashboard/login/verify-otp/?u={user.username}")


def seller_check_tg_status_api(request):
    """
    AJAX проверка: привязал ли продавец Telegram-бота.
    """
    user_id = request.session.get("pre_2fa_user_id")
    if not user_id:
        return JsonResponse({"linked": False})
    user = User.objects.filter(id=user_id).first()
    store = getattr(user, "store", None) if user else None
    linked = bool(store and store.telegram_chat_id)
    return JsonResponse({"linked": linked})


def seller_forgot_password_view(request):
    """
    Запрос сброса пароля: продавец указывает логин, telegram-юзернейм или телефон.
    Код подтверждения (OTP) отправляется в Telegram владельца магазина.
    """
    if request.user.is_authenticated:
        return redirect("seller-dashboard")

    if request.method == "POST":
        identifier = request.POST.get("identifier", "").strip()
        if not identifier:
            messages.error(request, "Пожалуйста, введите ваш логин или Telegram-аккаунт.")
            return render(request, "dashboard/forgot_password.html")

        # Ищем пользователя или магазин по логину, @username или номеру телефона
        user = User.objects.filter(username__iexact=identifier).first()
        store = None

        clean_tg = identifier.lstrip("@").strip()
        if not user:
            store = Store.objects.filter(owner_telegram_username__iexact=clean_tg).first()
            if store:
                user = store.owner

        if not user and clean_tg:
            # Если в Store username не был прописан, ищем среди Customer
            customer_match = Customer.objects.filter(
                telegram_username__iexact=clean_tg
            ).exclude(telegram_chat_id__isnull=True).exclude(telegram_chat_id="").first()
            if customer_match:
                store = Store.objects.filter(telegram_chat_id=str(customer_match.telegram_chat_id)).first()
                if store:
                    user = store.owner

        if not user:
            clean_phone = identifier.replace("+", "").replace(" ", "").replace("-", "")
            if clean_phone:
                store = Store.objects.filter(phone_number__icontains=clean_phone).first()
                if store:
                    user = store.owner

        if not user:
            messages.error(request, "Аккаунт с таким логином или Telegram не найден. Проверьте правильность ввода.")
            return render(request, "dashboard/forgot_password.html", {"identifier": identifier})

        if not store:
            store = getattr(user, "store", None)

        if store and clean_tg and not store.owner_telegram_username:
            store.owner_telegram_username = clean_tg
            store.save(update_fields=["owner_telegram_username"])

        # Генерируем одноразовый 6-значный код
        otp_code = f"{secrets.randbelow(900000) + 100000}"
        now_ts = timezone.now().timestamp()
        expires_ts = (timezone.now() + timedelta(minutes=5)).timestamp()

        # Сохраняем во временную сессию сброса пароля
        request.session["reset_pwd_user_id"] = user.id
        request.session["reset_pwd_store_id"] = store.id if store else None
        request.session["reset_pwd_otp"] = otp_code
        request.session["reset_pwd_expires_at"] = expires_ts
        request.session["reset_pwd_attempts"] = 0
        request.session["reset_pwd_last_sent"] = now_ts

        if store:
            cache.set(f"store_pending_otp_{store.id}", otp_code, timeout=300)

        # Определяем Telegram chat_id
        chat_id = store.telegram_chat_id if store else None
        if not chat_id and store and store.owner_telegram_username:
            clean_username = store.owner_telegram_username.lstrip("@").strip().lower()
            customer_match = Customer.objects.filter(
                telegram_username__iexact=clean_username
            ).exclude(telegram_chat_id__isnull=True).exclude(telegram_chat_id="").first()
            if customer_match:
                chat_id = customer_match.telegram_chat_id
                store.telegram_chat_id = str(chat_id)
                store.save(update_fields=["telegram_chat_id"])

        if chat_id:
            try:
                from core.working_views.telegram_bot import _send_telegram_message
                time_str = timezone.localtime().strftime("%H:%M:%S")
                msg = (
                    f"🔐 СБРОС ПАРОЛЯ В BRAINMART\n\n"
                    f"Запрошено восстановление пароля для аккаунта: {user.username}\n"
                    f"🏪 Магазин: «{store.name if store else 'BrainMart'}»\n"
                    f"🕒 Время: {time_str}\n\n"
                    f"Ваш код для создания нового пароля:\n"
                    f"👉 {otp_code} 👈\n\n"
                    f"⏱ Код действует 5 минут.\n"
                    f"⚠️ Введите этот код на странице восстановления и задайте новый пароль."
                )
                _send_telegram_message(chat_id, msg)
                messages.info(request, "Код подтверждения отправлен в Telegram владельца магазина!")
            except Exception as e:
                logger.warning("Не удалось отправить код сброса пароля в Telegram: %s", e)
                messages.warning(request, "Не удалось отправить сообщение в Telegram. Проверьте бота @BrainMart_bot.")
        else:
            messages.warning(request, "Telegram-аккаунт еще не привязан к боту. Подключите бота по кнопке ниже, чтобы получить код.")

        return redirect("seller-reset-password-verify")

    return render(request, "dashboard/forgot_password.html")


def seller_reset_password_verify_view(request):
    """
    Проверка OTP и установка нового пароля.
    """
    if request.user.is_authenticated:
        return redirect("seller-dashboard")

    user_id = request.session.get("reset_pwd_user_id")
    if not user_id:
        messages.error(request, "Сессия восстановления устарела. Пожалуйста, начните заново.")
        return redirect("seller-forgot-password")

    try:
        user = User.objects.get(id=user_id)
        store = getattr(user, "store", None)
    except User.DoesNotExist:
        request.session.flush()
        return redirect("seller-forgot-password")

    now_ts = timezone.now().timestamp()
    expires_ts = request.session.get("reset_pwd_expires_at", 0)
    expected_otp = str(request.session.get("reset_pwd_otp", ""))
    attempts = request.session.get("reset_pwd_attempts", 0)
    chat_id = store.telegram_chat_id if store else None

    if request.method == "POST":
        entered_otp = request.POST.get("otp_code", "").strip().replace(" ", "")
        new_password = request.POST.get("new_password", "").strip()
        confirm_password = request.POST.get("confirm_password", "").strip()

        if now_ts > expires_ts:
            messages.error(request, "Срок действия кода истёк (5 минут). Запросите код повторно.")
            return render(request, "dashboard/reset_password_verify.html", {
                "user_obj": user,
                "store": store,
                "is_expired": True,
                "has_telegram": bool(chat_id),
                "bot_username": getattr(settings, "TELEGRAM_BOT_USERNAME", ""),
            })

        if attempts >= 5:
            request.session.flush()
            messages.error(request, "Превышено максимальное число попыток ввода (5). Пожалуйста, начните заново.")
            return redirect("seller-forgot-password")

        if entered_otp != expected_otp:
            request.session["reset_pwd_attempts"] = attempts + 1
            left_attempts = 5 - (attempts + 1)
            messages.error(request, f"Неверный код подтверждения! Осталось попыток: {left_attempts}")
            return render(request, "dashboard/reset_password_verify.html", {
                "user_obj": user,
                "store": store,
                "seconds_left": max(0, int(expires_ts - now_ts)),
                "has_telegram": bool(chat_id),
                "bot_username": getattr(settings, "TELEGRAM_BOT_USERNAME", ""),
            })

        if not new_password or len(new_password) < 6:
            messages.error(request, "Новый пароль должен содержать минимум 6 символов.")
            return render(request, "dashboard/reset_password_verify.html", {
                "user_obj": user,
                "store": store,
                "seconds_left": max(0, int(expires_ts - now_ts)),
                "has_telegram": bool(chat_id),
                "bot_username": getattr(settings, "TELEGRAM_BOT_USERNAME", ""),
            })

        if new_password != confirm_password:
            messages.error(request, "Введенные пароли не совпадают. Повторите ввод.")
            return render(request, "dashboard/reset_password_verify.html", {
                "user_obj": user,
                "store": store,
                "seconds_left": max(0, int(expires_ts - now_ts)),
                "has_telegram": bool(chat_id),
                "bot_username": getattr(settings, "TELEGRAM_BOT_USERNAME", ""),
            })

        # Обновляем пароль пользователя!
        user.set_password(new_password)
        user.save()

        if store:
            cache.delete(f"store_pending_otp_{store.id}")

        # Уведомляем в Telegram
        if chat_id:
            try:
                from core.working_views.telegram_bot import _send_telegram_message
                time_str = timezone.localtime().strftime("%H:%M:%S")
                _send_telegram_message(
                    chat_id,
                    f"🎉 ПАРОЛЬ УСПЕШНО ИЗМЕНЕН!\n\n"
                    f"Пароль от аккаунта {user.username} был успешно обновлен.\n"
                    f"🕒 Время: {time_str}\n\n"
                    f"Если это были не вы — немедленно свяжитесь с поддержкой платформы."
                )
            except Exception:
                pass

        # Очищаем сессионные данные сброса
        request.session.pop("reset_pwd_user_id", None)
        request.session.pop("reset_pwd_store_id", None)
        request.session.pop("reset_pwd_otp", None)
        request.session.pop("reset_pwd_expires_at", None)
        request.session.pop("reset_pwd_attempts", None)
        request.session.pop("reset_pwd_last_sent", None)

        # Автоматически авторизуем продавца!
        login(request, user)

        messages.success(request, f"🎉 Пароль успешно изменён! Добро пожаловать в панель управления, {store.name if store else user.username}!")
        return redirect("seller-dashboard")

    seconds_left = max(0, int(expires_ts - now_ts))
    bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "")
    link_url = f"https://t.me/{bot_username}?start=link_{store.telegram_link_token}" if (store and bot_username) else ""

    return render(request, "dashboard/reset_password_verify.html", {
        "user_obj": user,
        "store": store,
        "seconds_left": seconds_left,
        "bot_username": bot_username,
        "link_url": link_url,
        "has_telegram": bool(chat_id),
    })


def seller_reset_password_resend_otp_view(request):
    """
    Повторная отправка OTP для сброса пароля с кулдауном 45 секунд.
    """
    user_id = request.session.get("reset_pwd_user_id")
    if not user_id:
        return redirect("seller-forgot-password")

    try:
        user = User.objects.get(id=user_id)
        store = getattr(user, "store", None)
    except User.DoesNotExist:
        request.session.flush()
        return redirect("seller-forgot-password")

    now_ts = timezone.now().timestamp()
    last_sent = request.session.get("reset_pwd_last_sent", 0)

    if now_ts - last_sent < 45:
        remaining = int(45 - (now_ts - last_sent))
        messages.warning(request, f"Пожалуйста, подождите {remaining} сек. перед повторной отправкой.")
        return redirect("seller-reset-password-verify")

    new_code = f"{secrets.randbelow(900000) + 100000}"
    expires_ts = (timezone.now() + timedelta(minutes=5)).timestamp()

    request.session["reset_pwd_otp"] = new_code
    request.session["reset_pwd_expires_at"] = expires_ts
    request.session["reset_pwd_attempts"] = 0
    request.session["reset_pwd_last_sent"] = now_ts

    if store:
        cache.set(f"store_pending_otp_{store.id}", new_code, timeout=300)

    chat_id = store.telegram_chat_id if store else None
    if chat_id:
        try:
            from core.working_views.telegram_bot import _send_telegram_message
            time_str = timezone.localtime().strftime("%H:%M:%S")
            msg = (
                f"🔄 НОВЫЙ КОД СБРОСА ПАРОЛЯ\n\n"
                f"Запрошен новый проверочный код для аккаунта: {user.username}\n"
                f"🕒 Время: {time_str}\n\n"
                f"Ваш код:\n"
                f"👉 {new_code} 👈\n\n"
                f"⏱ Код действует 5 минут."
            )
            _send_telegram_message(chat_id, msg)
            messages.success(request, "Новый код успешно отправлен в ваш Telegram!")
        except Exception:
            messages.error(request, "Ошибка при отправке в Telegram.")
    else:
        messages.warning(request, "Telegram-бот еще не привязан.")

    return redirect("seller-reset-password-verify")


def seller_logout_view(request):
    """
    Безопасный выход из панели продавца.
    """
    request.session.flush()
    logout(request)
    messages.info(request, "Вы успешно вышли из панели управления.")
    return redirect("seller-login")


def seller_register_view(request):
    """Самостоятельная регистрация продавца с выбором тарифного плана."""
    # Если продавец уже авторизован и имеет магазин — сразу в панель управления
    if request.user.is_authenticated and hasattr(request.user, "store") and request.user.store:
        return redirect("seller-dashboard")

    form_data = {}
    

    if request.method == "POST":
        form_data = request.POST
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        store_name = request.POST.get("store_name", "").strip()
        slug_raw = request.POST.get("slug", "").strip()
        instagram_raw = request.POST.get("instagram_username", "").strip().lstrip("@")
        telegram_bot_token = request.POST.get("telegram_bot_token", "").strip()
        phone_number = request.POST.get("phone_number", "").strip()
        owner_telegram_username = request.POST.get("owner_telegram_username", "").strip().lstrip("@")
        niche = request.POST.get("niche", "clothes").strip()
        address = request.POST.get("address", "").strip()
        latitude = request.POST.get("latitude", "").strip()
        longitude = request.POST.get("longitude", "").strip()
        payout_card = request.POST.get("payout_card", "").strip().replace(" ", "")
        payout_card_holder = request.POST.get("payout_card_holder", "").strip()

        category_id = request.POST.get("category_id")
        initial_category = None
        if category_id:
            from core.models import Category
            initial_category = Category.objects.filter(id=category_id).first()

        # 1. Валидация только основных полей (Instagram теперь опционален)
        if not all([username, password, store_name, phone_number]):
            messages.error(request, "Пожалуйста, заполните логин, пароль, название магазина и номер телефона.")
            from core.models import Category
            return render(request, "dashboard/register.html", {
                "form_data": form_data,
                "categories": Category.objects.all().order_by("id"),
            })

        # 2. Валидация минимальной длины пароля
        if len(password) < 8:
            messages.error(request, "Пароль должен быть не короче 8 символов.")
            from core.models import Category
            return render(request, "dashboard/register.html", {
                "form_data": form_data,
                "categories": Category.objects.all().order_by("id"),
            })

        # 3. Предварительная проверка уникальности логина
        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, f"Логин «{username}» уже занят. Пожалуйста, придумайте другой логин.")
            from core.models import Category
            return render(request, "dashboard/register.html", {
                "form_data": form_data,
                "categories": Category.objects.all().order_by("id"),
            })

        # 4. Формирование уникального slug (поддомена) и псевдонима магазина
        dummy_store = Store()
        candidate_slug = dummy_store.clean_slug(slug_raw or instagram_raw or store_name)
        final_slug = candidate_slug
        counter = 1
        while Store.objects.filter(slug__iexact=final_slug).exists():
            final_slug = f"{candidate_slug}-{counter}"
            counter += 1

        # Псевдоним Instagram: если не указан, берем сгенерированный slug
        if instagram_raw:
            final_instagram = instagram_raw
            if Store.objects.filter(instagram_username__iexact=final_instagram).exists():
                final_instagram = f"{final_instagram}_{secrets.token_hex(2)}"
        else:
            final_instagram = final_slug.replace("-", "_")

        try:
            with transaction.atomic():
                user = User.objects.create_user(username=username, password=password)
                try:
                    lat_clean = str(latitude).replace(",", ".").strip() if latitude else ""
                    lon_clean = str(longitude).replace(",", ".").strip() if longitude else ""
                    lat_d = Decimal(lat_clean) if lat_clean else None
                    lon_d = Decimal(lon_clean) if lon_clean else None
                except Exception:
                    lat_d, lon_d = None, None

                store = Store.objects.create(
                    owner=user,
                    name=store_name,
                    slug=final_slug,
                    instagram_username=final_instagram,
                    owner_telegram_username=owner_telegram_username,
                    niche=niche,
                    category=initial_category,
                    phone_number=phone_number,
                    address=address,
                    latitude=lat_d,
                    longitude=lon_d,
                    payout_card=payout_card,
                    payout_card_holder=payout_card_holder,
                    telegram_link_token=secrets.token_hex(16),
                    is_active=False,
                    verification_status=Store.VerificationStatus.PENDING,
                )

                # Автоматически создаем 1-ю базовую точку на карте
                StoreBranch.objects.create(
                    store=store,
                    name=f"Основная точка ({store_name})",
                    address=address or "Ташкент (Главный магазин)",
                    latitude=lat_d,
                    longitude=lon_d,
                    is_active=True,
                )

                # Автоматическая настройка персонального Telegram-бота, если токен передан
                if telegram_bot_token:
                    from core.working_views.telegram_bot import setup_store_telegram_bot
                    bot_ok, bot_res = setup_store_telegram_bot(store, telegram_bot_token)
                    if bot_ok:
                        messages.info(request, f"🤖 Персональный Telegram-бот {bot_res} успешно привязан к вашей витрине!")
                    else:
                        messages.warning(request, f"⚠️ Токен бота сохранен, но не удалось подключить ({bot_res}). Вы сможете настроить бота в Настройках.")
        except Exception as e:
            messages.error(request, f"Не удалось завершить регистрацию: {e}")
            from core.models import Category
            return render(request, "dashboard/register.html", {
                "form_data": form_data,
                "categories": Category.objects.all().order_by("id"),
            })

        # Уведомляем основателя платформы в Telegram о новой заявке
        try:
            from core.tasks import notify_superadmin_about_new_store_task
            notify_superadmin_about_new_store_task.delay(store.id)
        except Exception:
            try:
                from core.working_views.telegram_bot import notify_superadmin_about_new_store
                notify_superadmin_about_new_store(store)
            except Exception:
                pass

        # Автоматическая авторизация нового продавца
        login(request, user)

        store_subdomain_url = store.get_subdomain_url(request)
        messages.success(
            request,
            f"🎉 Магазин «{store_name}» успешно зарегистрирован! "
            f"Адрес вашей персональной витрины: {store_subdomain_url} "
            "Заявка отправлена на проверку. Вы уже можете добавлять товары и настраивать магазин ✨"
        )
        return redirect("seller-dashboard")

    from core.models import Category
    return render(request, "dashboard/register.html", {
        "form_data": form_data,
        "categories": Category.objects.all().order_by("id"),
    })


@login_required
def store_settings_view(request):
    """Настройки магазина: данные магазина, Telegram-привязка и управление менеджерами."""
    store = _get_store_or_none(request)
    if store is None:
        return redirect("seller-dashboard")

    if request.method == "POST":
        action = request.POST.get("action", "update_store")

        if action == "add_manager":
            manager_name = request.POST.get("manager_name", "").strip()
            manager_lang = request.POST.get("manager_language", "").strip()
            if manager_lang not in ("uz", "ru", "en"):
                manager_lang = getattr(store, "telegram_notification_language", "uz") or "uz"
            if manager_name:
                StoreManager.objects.create(store=store, name=manager_name, language=manager_lang)
                messages.success(request, f"Менеджер '{manager_name}' добавлен! Отправьте ему ссылку для привязки Telegram.")
            else:
                messages.error(request, "Введите имя менеджера.")
            return redirect("seller-settings")

        elif action == "delete_manager":
            manager_id = request.POST.get("manager_id")
            StoreManager.objects.filter(id=manager_id, store=store).delete()
            messages.success(request, "Менеджер удален.")
            return redirect("seller-settings")

        elif action == "connect_custom_bot":
            bot_token = request.POST.get("telegram_bot_token", "").strip()
            if not bot_token:
                messages.error(request, "Пожалуйста, введите токен бота от @BotFather.")
                return redirect("seller-settings")
            from core.working_views.telegram_bot import setup_store_telegram_bot
            success, msg = setup_store_telegram_bot(store, bot_token)
            if success:
                messages.success(request, f"🎉 Персональный Telegram-бот {msg} успешно подключен и настроен!")
            else:
                messages.error(request, f"Не удалось подключить бота: {msg}")
            return redirect("seller-settings")

        elif action == "disconnect_custom_bot":
            from core.working_views.telegram_bot import setup_store_telegram_bot
            setup_store_telegram_bot(store, "")
            messages.info(request, "Персональный бот отключен. Витрина и уведомления переключены на общий бот платформы.")
            return redirect("seller-settings")

        elif action == "clear_store_history":
            from core.models import CashShift, StockMovement
            orders_count = Order.objects.filter(store=store).count()
            Order.objects.filter(store=store).delete()

            shifts_count = CashShift.objects.filter(store=store).count()
            CashShift.objects.filter(store=store).delete()

            movements_count = StockMovement.objects.filter(variant__product__store=store).count()
            StockMovement.objects.filter(variant__product__store=store).delete()

            # Обнуляем балансы от тестовых заказов
            store.balance = 0.00
            store.held_balance = 0.00
            store.save(update_fields=["balance", "held_balance"])

            messages.success(
                request,
                f"🧹 История успешно очищена! Удалено заказов: {orders_count}, кассовых смен: {shifts_count}, движений склада: {movements_count}. Баланс обнулен."
            )
            return redirect("seller-settings")

        else:
            store_name = request.POST.get("store_name", "").strip()
            phone_number = request.POST.get("phone_number", "").strip()
            address = request.POST.get("address", "").strip()
            slug_raw = request.POST.get("slug", "").strip()
            if slug_raw:
                new_slug = store.clean_slug(slug_raw)
                conflict = Store.objects.filter(slug=new_slug).exclude(id=store.id).exists()
                if conflict:
                    messages.error(request, f"Адрес (поддомен) '{new_slug}' уже занят другим магазином. Пожалуйста, выберите другой.")
                    return redirect("seller-settings")
                store.slug = new_slug
            elif not store.slug:
                store.slug = store.clean_slug(store_name or store.name or "store")

            instagram_username = request.POST.get("instagram_username", "").strip().lstrip("@")
            payout_card = request.POST.get("payout_card", "").strip().replace(" ", "")
            payout_card_holder = request.POST.get("payout_card_holder", "").strip()
            primary_color = request.POST.get("primary_color", "").strip() or "#4F46E5"
            accent_color = request.POST.get("accent_color", "").strip() or "#10B981"
            bg_color_raw = request.POST.get("bg_color", "").strip()
            bg_color_clear = request.POST.get("bg_color_clear", "").strip()
            # If "reset to default" was clicked, clear the custom bg
            if bg_color_clear == "1":
                bg_color = ""
            else:
                bg_color = bg_color_raw if bg_color_raw.startswith("#") else ""
            theme_style = request.POST.get("theme_style", "default_black")
            theme_mode = request.POST.get("theme_mode", "dark")
            telegram_notification_language = request.POST.get("telegram_notification_language", "uz")
            if telegram_notification_language not in ("ru", "uz", "en"):
                telegram_notification_language = "uz"

            category_id = request.POST.get("category_id")
            if category_id:
                try:
                    store.category_id = int(category_id)
                except (ValueError, TypeError):
                    pass
            elif "category_id" in request.POST:
                store.category = None

            if store_name:
                store.name = store_name
            if phone_number:
                store.phone_number = phone_number
            store.address = address
            if instagram_username:
                existing_store = Store.objects.filter(instagram_username__iexact=instagram_username).exclude(id=store.id).first()
                if existing_store:
                    # Если старый магазин неактивен или не имеет товаров (тестовый/заглушка)
                    if not existing_store.is_active or existing_store.products.count() == 0:
                        existing_store.instagram_username = f"{instagram_username}_old_{existing_store.id}"
                        existing_store.save(update_fields=["instagram_username"])
                        store.instagram_username = instagram_username
                        store.instagram_business_id = None
                    else:
                        messages.error(request, f"Ник Instagram @{instagram_username} уже занят другим магазином ('{existing_store.name}'). Пожалуйста, укажите ваш уникальный ник.")
                        return redirect("seller-settings")
                else:
                    if store.instagram_username != instagram_username:
                        store.instagram_username = instagram_username
                        store.instagram_business_id = None
            if payout_card:
                store.payout_card = payout_card
            if payout_card_holder:
                store.payout_card_holder = payout_card_holder
            store.primary_color = primary_color
            niche = request.POST.get("niche", "clothes").strip()
            if niche in dict(Store.Niche.choices):
                store.niche = niche

            store.accent_color = accent_color
            store.bg_color = bg_color
            store.theme_style = theme_style
            store.theme_mode = theme_mode
            store.telegram_notification_language = telegram_notification_language

            brand_signature_text = request.POST.get("brand_signature_text", "").strip()
            brand_signature_icon = request.POST.get("brand_signature_icon", "").strip()
            store.brand_signature_text = brand_signature_text
            store.brand_signature_icon = brand_signature_icon

            # Авто-эквайринг временно заморожен на техническую доработку
            store.enable_auto_acquiring = False
            store.enable_card_transfer = True
            store.show_global_market_button = (request.POST.get("show_global_market_button") == "on")

            store.save(update_fields=[
                "name", "slug", "phone_number", "address", "instagram_username",
                "payout_card", "payout_card_holder",
                "category", "niche",
                "primary_color", "accent_color", "bg_color", "theme_style", "theme_mode",
                "brand_signature_text", "brand_signature_icon",
                "telegram_notification_language",
                "enable_auto_acquiring", "enable_card_transfer",
                "show_global_market_button"
            ])
            # Синхронизируем язык текущей сессии
            if hasattr(request, "session") and telegram_notification_language in ("ru", "uz", "en"):
                request.session["django_language"] = telegram_notification_language
                from django.utils import translation
                translation.activate(telegram_notification_language)
                request.LANGUAGE_CODE = telegram_notification_language

            messages.success(request, "Настройки магазина, адрес витрины, ниша, способы оплаты и оформление сохранены!")
            return redirect("seller-settings")

    # Формируем список менеджеров с готовыми ссылками на привязку
    managers = []
    for mgr in StoreManager.objects.filter(store=store).order_by("-created_at"):
        managers.append({
            "id": mgr.id,
            "name": mgr.name,
            "language": mgr.language,
            "telegram_username": mgr.telegram_username,
            "is_connected": bool(mgr.telegram_chat_id),
            "link": build_manager_link_deep_link(mgr) if settings.TELEGRAM_BOT_USERNAME else None,
        })

    from core.models import Category
    categories = Category.objects.all().order_by("id")
    site_url = getattr(settings, "SITE_URL", "") or "https://brainmart.uz"
    store_catalog_url = store.get_subdomain_url(request)
    global_market_url = f"{site_url.rstrip('/')}/ru/?global=1"
    bot_showcase_url = store.get_bot_url()

    context = {
        "store": store,
        "categories": categories,
        "niche_choices": Store.Niche.choices,
        "managers": managers,
        "telegram_connected": bool(store.telegram_chat_id),
        "telegram_link": (
            None if store.telegram_chat_id
            else build_seller_link_deep_link(store) if settings.TELEGRAM_BOT_USERNAME
            else None
        ),
        "store_catalog_url": store_catalog_url,
        "global_market_url": global_market_url,
        "telegram_bot_url": bot_showcase_url,
        "custom_bot_active": bool(store.telegram_bot_active and store.telegram_bot_username),
        "custom_bot_username": store.telegram_bot_username or "",
    }
    return render(request, "dashboard/settings.html", context)


@login_required
def ai_generate_broadcast_view(request):
    """AJAX API: Генерация продающего текста Telegram-рассылки через Gemini AI."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    store = _get_store_or_none(request)
    if not store:
        return JsonResponse({"error": "Store not found"}, status=403)

    import json
    try:
        body_data = json.loads(request.body) if request.body else request.POST
    except Exception:
        body_data = request.POST

    prompt = body_data.get("prompt", "").strip()
    tone = body_data.get("tone", "hype")

    if not prompt:
        return JsonResponse({"error": "Prompt is required"}, status=400)

    from core.working_views.services import generate_ai_broadcast_copy
    result = generate_ai_broadcast_copy(
        prompt=prompt,
        tone=tone,
        store_name=store.name,
        niche=store.get_niche_display()
    )
    return JsonResponse({"status": "ok", "data": result})


@login_required
def ai_generate_description_view(request):
    """AJAX API: Генерация описания и характеристик товара через Gemini AI."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    store = _get_store_or_none(request)
    if not store:
        return JsonResponse({"error": "Store not found"}, status=403)

    import json
    try:
        body_data = json.loads(request.body) if request.body else request.POST
    except Exception:
        body_data = request.POST

    name = body_data.get("name", "").strip()
    category = body_data.get("category", "").strip()
    features = body_data.get("features", "").strip()

    if not name:
        return JsonResponse({"error": "Product name is required"}, status=400)

    from core.working_views.services import generate_ai_product_description
    desc = generate_ai_product_description(
        name=name,
        category=category,
        features=features,
        store_name=store.name
    )
    return JsonResponse({"status": "ok", "description": desc})




@login_required
def customers_view(request):
    """CRM — расширенная база покупателей магазина с RFM сегментацией, аналитикой LTV и фильтрацией."""
    store = _get_store_or_none(request)
    if store is None:
        return redirect("seller-dashboard")

    from django.db.models import Max, Avg
    import datetime
    from django.utils import timezone

    today = timezone.now().date()
    thirty_days_ago = today - datetime.timedelta(days=30)

    # Клиенты, у которых есть заказы в этом магазине
    qs = Customer.objects.filter(
        orders__store=store
    ).annotate(
        orders_count=Count("orders", filter=Q(orders__store=store)),
        total_spent=Sum("orders__total_price", filter=Q(orders__store=store, orders__status=Order.Status.PAID)),
        last_order_date=Max("orders__created_at", filter=Q(orders__store=store)),
    ).distinct()

    total_customers_count = qs.count()
    total_revenue = qs.aggregate(sum=Sum("total_spent"))["sum"] or 0
    avg_order_value = qs.aggregate(avg=Avg("total_spent"))["avg"] or 0

    annotated_customers = []
    vip_count = 0
    sleeping_count = 0
    new_count = 0
    repeat_count = 0

    segment_filter = request.GET.get("segment", "all")

    for c in qs.order_by("-total_spent"):
        c_spent = c.total_spent or 0
        c_orders = c.orders_count or 0
        last_date = c.last_order_date.date() if c.last_order_date else None

        if c_orders >= 2 or c_spent >= 500000:
            segment = "vip"
            segment_badge = "🌟 VIP"
            vip_count += 1
        elif last_date and last_date < thirty_days_ago:
            segment = "sleeping"
            segment_badge = "💤 Спящий"
            sleeping_count += 1
        elif c_orders == 1:
            segment = "new"
            segment_badge = "🆕 Новый"
            new_count += 1
        else:
            segment = "active"
            segment_badge = "🔥 Активный"

        if c_orders > 1:
            repeat_count += 1

        c.segment = segment
        c.segment_badge = segment_badge

        if segment_filter == "all" or segment_filter == segment:
            annotated_customers.append(c)

    repeat_rate = round((repeat_count / total_customers_count * 100), 1) if total_customers_count > 0 else 0

    context = {
        "customers": annotated_customers,
        "total_customers": total_customers_count,
        "vip_count": vip_count,
        "sleeping_count": sleeping_count,
        "new_count": new_count,
        "repeat_rate": repeat_rate,
        "total_revenue": total_revenue,
        "avg_order_value": avg_order_value,
        "active_segment": segment_filter,
    }
    return render(request, "dashboard/customers.html", context)


@login_required
def seller_broadcasts_view(request):
    """CRM — Маркетинговые рассылки через Telegram-бота по базе клиентов магазина."""
    store = _get_store_or_none(request)
    if store is None:
        return redirect("seller-dashboard")

    from core.models import BroadcastCampaign
    from core.working_views.telegram_bot import _send_telegram_message

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        message_text = request.POST.get("message_text", "").strip()
        target_segment = request.POST.get("target_segment", "all")
        button_text = request.POST.get("button_text", "").strip() or "🛍 Открыть каталог"
        button_url = request.POST.get("button_url", "").strip() or f"{settings.SITE_URL}/ru/?store_id={store.id}"

        if not title or not message_text:
            messages.error(request, "Пожалуйста, заполните тему и текст рассылки.")
            return redirect("seller-broadcasts")

        import datetime
        from django.utils import timezone
        today = timezone.now().date()
        thirty_days_ago = today - datetime.timedelta(days=30)

        base_customers = Customer.objects.filter(
            orders__store=store,
            telegram_chat_id__isnull=False
        ).exclude(telegram_chat_id="").distinct()

        if target_segment == "vip":
            base_customers = base_customers.annotate(
                orders_count=Count("orders", filter=Q(orders__store=store)),
                total_spent=Sum("orders__total_price", filter=Q(orders__store=store, orders__status=Order.Status.PAID))
            ).filter(Q(orders_count__gte=2) | Q(total_spent__gte=500000))

        elif target_segment == "sleeping":
            from django.db.models import Max
            base_customers = base_customers.annotate(
                last_order_date=Max("orders__created_at", filter=Q(orders__store=store))
            ).filter(last_order_date__lt=thirty_days_ago)

        elif target_segment == "new":
            base_customers = base_customers.annotate(
                orders_count=Count("orders", filter=Q(orders__store=store))
            ).filter(orders_count=1)

        target_list = list(base_customers)
        recipients_count = len(target_list)

        campaign = BroadcastCampaign.objects.create(
            store=store,
            title=title,
            message_text=message_text,
            target_segment=target_segment,
            button_text=button_text,
            button_url=button_url,
            recipients_count=recipients_count,
            status=BroadcastCampaign.Status.SENDING,
        )

        delivered = 0
        reply_markup = None
        if button_url:
            reply_markup = {
                "inline_keyboard": [[
                    {"text": button_text, "url": button_url}
                ]]
            }

        full_text = f"📢 {store.name}:\n\n{message_text}"

        for cust in target_list:
            if cust.telegram_chat_id:
                try:
                    success = _send_telegram_message(cust.telegram_chat_id, full_text, reply_markup=reply_markup)
                    if success:
                        delivered += 1
                except Exception as e:
                    logger.warning("Ошибка отправки рассылки клиенту %s: %s", cust.telegram_chat_id, e)

        campaign.delivered_count = delivered
        campaign.status = BroadcastCampaign.Status.COMPLETED
        campaign.save(update_fields=["delivered_count", "status"])

        messages.success(request, f"Рассылка '{title}' успешно отправлена {delivered} клиентам в Telegram!")
        return redirect("seller-broadcasts")

    campaigns = BroadcastCampaign.objects.filter(store=store).order_by("-created_at")[:30]
    total_subscribers = Customer.objects.filter(orders__store=store, telegram_chat_id__isnull=False).exclude(telegram_chat_id="").distinct().count()

    context = {
        "campaigns": campaigns,
        "total_subscribers": total_subscribers,
    }
    return render(request, "dashboard/broadcasts.html", context)



@login_required
def seller_finance_view(request):
    """Раздел 'Финансы и Выплаты' — баланс, карта Uzcard/Humo, автовывод и история выплат."""
    store = _get_store_or_none(request)
    if store is None:
        return redirect("seller-dashboard")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save_card":
            payout_card = request.POST.get("payout_card", "").strip().replace(" ", "")
            payout_card_holder = request.POST.get("payout_card_holder", "").strip()

            store.payout_card = payout_card
            store.payout_card_holder = payout_card_holder
            store.save(update_fields=["payout_card", "payout_card_holder"])
            messages.success(request, "Реквизиты вашей карты для приёма оплат успешно сохранены!")
            return redirect("seller-finance")

        elif action == "pay_subscription_transfer":
            from core.models import SubscriptionPayment
            transaction_id = request.POST.get("transaction_id", "").strip()
            try:
                months = int(request.POST.get("months", 1))
            except (ValueError, TypeError):
                months = 1
            if months < 1:
                months = 1
            receipt_image = request.FILES.get("receipt_image")

            fee = store.current_tier_fee * months

            # Создаём заявку на подтверждение оплаты (статус: pending)
            payment = SubscriptionPayment.objects.create(
                store=store,
                amount=fee,
                months=months,
                payment_method="transfer",
                status="pending",
                transaction_id=transaction_id or "Прямой перевод на карту BrainMart",
                receipt_image=receipt_image,
            )

            try:
                from core.working_views.telegram_bot import _send_telegram_photo, _send_telegram_message
                from core.models import PlatformSettings
                ps = PlatformSettings.get_settings()
                admin_chat = ps.superadmin_telegram_chat_id or "1821219115"

                current_until = store.subscription_paid_until.strftime("%d.%m.%Y") if store.subscription_paid_until else "—"

                admin_text = (
                    f"💳 ЗАЯВКА НА ПОДПИСКУ BRAINMART!\n\n"
                    f"🏪 Магазин: «{store.name}» (ID: {store.id})\n"
                    f"👤 Владелец: @{store.owner.username} ({store.phone_number})\n"
                    f"🏢 Филиалов: {store.branches.count()} точек\n"
                    f"👑 Тариф: SaaS ({int(store.current_tier_fee):,} сум/мес)\n"
                    f"💰 Сумма перевода: {int(fee):,} сум ({months} мес.)\n"
                    f"🧾 Чек/Реквизиты: {transaction_id or 'Без комментария'}\n"
                    f"📅 Оплачено до: {current_until}\n\n"
                    f"Проверьте поступление средств на карту и выберите действие:"
                )

                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": f"✅ Подтвердить ({months} мес.)", "callback_data": f"sub_approve_{payment.id}"},
                            {"text": "❌ Отклонить", "callback_data": f"sub_reject_{payment.id}"}
                        ]
                    ]
                }

                if receipt_image:
                    _send_telegram_photo(admin_chat, receipt_image, caption=admin_text, reply_markup=reply_markup)
                else:
                    _send_telegram_message(admin_chat, admin_text, reply_markup=reply_markup)
            except Exception as e:
                logger.warning("Не удалось отправить уведомление о переводе администратору: %s", e)

            messages.success(
                request,
                f"🎉 Чек и заявка на продление подписки ({months} мес.) отправлены администратору! После подтверждения подписка обновится автоматически."
            )
            return redirect("seller-finance")

    # Сводка по заказам
    from core.models import PayoutTransaction
    orders = Order.objects.filter(store=store).exclude(status=Order.Status.CANCELLED)
    paid_or_delivered = orders.filter(status__in=[Order.Status.PAID, Order.Status.HANDED_TO_COURIER, Order.Status.DELIVERED])

    total_gross = paid_or_delivered.aggregate(total=Sum("total_price"))["total"] or 0
    total_commission = 0
    total_processing = paid_or_delivered.aggregate(total=Sum("processing_amount"))["total"] or 0
    total_seller = paid_or_delivered.aggregate(total=Sum("seller_amount"))["total"] or 0

    payout_transactions = PayoutTransaction.objects.filter(store=store).order_by("-created_at")[:50]
    subscription_payments = store.subscription_payments.all()[:20]
    subscription_info = store.get_subscription_status_info()

    billing_card = getattr(settings, "BRAINMART_BILLING_CARD", "6262 4700 9041 1583")
    billing_card_clean = "".join(filter(str.isdigit, billing_card))
    billing_holder = getattr(settings, "BRAINMART_BILLING_HOLDER", "Munisa Yakubova")

    context = {
        "store": store,
        "balance": store.balance or 0,
        "held_balance": store.held_balance or 0,
        "total_gross": total_gross,
        "total_commission": total_commission,
        "total_processing": total_processing,
        "total_seller": total_seller,
        "orders": paid_or_delivered.order_by("-created_at")[:20],
        "payout_transactions": payout_transactions,
        "subscription_payments": subscription_payments,
        "subscription_info": subscription_info,
        "billing_card": billing_card,
        "billing_card_clean": billing_card_clean,
        "billing_holder": billing_holder,
    }
    return render(request, "dashboard/finance.html", context)


@login_required
def seller_reviews_view(request):
    """Управление отзывами покупателей: модерация, маскирование/скрытие, удаление и ответы."""
    store = _get_store_or_none(request)
    if store is None:
        return redirect("seller-dashboard")

    if request.method == "POST":
        action = request.POST.get("action")
        review_id = request.POST.get("review_id")
        review = get_object_or_404(ProductReview, id=review_id, product__store=store)

        if action == "toggle_visibility":
            review.is_visible = not review.is_visible
            review.save(update_fields=["is_visible"])
            status_label = "отображается на витрине" if review.is_visible else "скрыт (замаскирован)"
            messages.success(request, f"Отзыв #{review.id} теперь {status_label}.")

        elif action == "delete":
            review.delete()
            messages.success(request, "Отзыв успешно удалён из базы.")

        elif action == "reply":
            reply_text = request.POST.get("seller_reply", "").strip()
            review.seller_reply = reply_text
            review.save(update_fields=["seller_reply"])
            messages.success(request, f"Ответ на отзыв #{review.id} сохранён и виден покупателям.")

        return redirect("seller-reviews")

    reviews = ProductReview.objects.filter(product__store=store).select_related("product").order_by("-created_at")
    total_reviews = reviews.count()
    visible_reviews = reviews.filter(is_visible=True).count()
    hidden_reviews = reviews.filter(is_visible=False).count()

    from django.db.models import Avg
    avg_rating = reviews.aggregate(Avg("rating"))["rating__avg"]
    avg_rating_display = round(float(avg_rating), 1) if avg_rating else 5.0

    context = {
        "store": store,
        "reviews": reviews,
        "total_reviews": total_reviews,
        "visible_reviews": visible_reviews,
        "hidden_reviews": hidden_reviews,
        "avg_rating": avg_rating_display,
    }
    return render(request, "dashboard/reviews.html", context)


@login_required
def seller_waitlist_view(request):
    """
    Лист ожидания (Waitlist) магазина:
    Показывает запросы клиентов из Telegram и Instagram на отсутствующие товары.
    Продавец видит реальный спрос (что ищут клиенты) и статус авто-уведомления.
    """
    store = _get_store_or_none(request)
    if store is None:
        return redirect("seller-dashboard")

    from core.models import ProductWaitlist

    if request.method == "POST":
        action = request.POST.get("action")
        entry_id = request.POST.get("entry_id")
        entry = get_object_or_404(ProductWaitlist, id=entry_id, store=store)

        if action == "cancel":
            entry.status = ProductWaitlist.Status.CANCELLED
            entry.save(update_fields=["status"])
            messages.success(request, f"Запрос #{entry.id} отменен.")
        elif action == "restore":
            entry.status = ProductWaitlist.Status.PENDING
            entry.save(update_fields=["status"])
            messages.success(request, f"Запрос #{entry.id} возвращен в ожидание.")
        return redirect("seller-waitlist")

    filter_status = request.GET.get("status", "all")
    qs = ProductWaitlist.objects.filter(store=store).select_related("customer", "notified_product")

    if filter_status == "pending":
        qs = qs.filter(status=ProductWaitlist.Status.PENDING)
    elif filter_status == "notified":
        qs = qs.filter(status=ProductWaitlist.Status.NOTIFIED)

    waitlist_items = qs.order_by("-created_at")

    total_count = ProductWaitlist.objects.filter(store=store).count()
    pending_count = ProductWaitlist.objects.filter(store=store, status=ProductWaitlist.Status.PENDING).count()
    notified_count = ProductWaitlist.objects.filter(store=store, status=ProductWaitlist.Status.NOTIFIED).count()

    context = {
        "store": store,
        "waitlist_items": waitlist_items,
        "total_count": total_count,
        "pending_count": pending_count,
        "notified_count": notified_count,
        "filter_status": filter_status,
    }
    return render(request, "dashboard/waitlist.html", context)

