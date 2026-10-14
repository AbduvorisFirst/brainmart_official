from datetime import timedelta
from decimal import Decimal
import json

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import User
from django.db.models import Count, Sum, Q, F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import (
    Customer,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    Store,
    SubscriptionPayment,
)


def superuser_required(view_func):
    """Декоратор строго для владельца платформы / суперпользователя (superuser).
    Наемные администраторы (staff) перенаправляются в стандартный Django Admin (/admin/).
    Обычные пользователи перенаправляются на страницу авторизации.
    """
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("seller-login")
        if not request.user.is_superuser:
            if request.user.is_staff:
                messages.warning(request, "Финансовая аналитика платформы (GMV и чистая прибыль) доступна только владельцу (Superuser). Вы перенаправлены в панель управления.")
                return redirect("/brainmart-secure-admin-panel/")
            messages.error(request, "Доступ ограничен. Требуются права суперпользователя.")
            return redirect("catalog")
        return view_func(request, *args, **kwargs)
    return _wrapped


@superuser_required
def platform_admin_dashboard_view(request):
    """Главный дашборд платформы: общая аналитика, GMV, комиссии, заказы сегодня, топ товаров и список магазинов."""
    today = timezone.localdate()

    # Обработка действий модерации (Одобрение / Отклонение / Заморозка / Разморозка)
    if request.method == "POST":
        action = request.POST.get("action")
        store_id = request.POST.get("store_id")

        if action == "unlink_telegram":
            from core.models import PlatformSettings
            ps = PlatformSettings.get_settings()
            ps.superadmin_telegram_chat_id = None
            ps.superadmin_telegram_username = ""
            ps.save(update_fields=["superadmin_telegram_chat_id", "superadmin_telegram_username"])
            messages.info(request, "Telegram супер-админа успешно отвязан от платформы.")
            return redirect("platform-admin-dashboard")

        if store_id:
            store = get_object_or_404(Store, id=store_id)

            if action == "approve_store":
                store.verification_status = Store.VerificationStatus.APPROVED
                store.is_active = True
                store.rejection_reason = ""
                store.save(update_fields=["verification_status", "is_active", "rejection_reason"])
                messages.success(request, f"🎉 Магазин «{store.name}» успешно ОДОБРЕН и активирован! Витрина открыта для клиентов.")
                try:
                    from core.working_views.telegram_bot import notify_seller_about_approval
                    notify_seller_about_approval(store)
                except Exception:
                    pass

            elif action == "reject_store":
                reason = request.POST.get("reason", "").strip() or "Не соответствует требованиям платформы"
                store.verification_status = Store.VerificationStatus.REJECTED
                store.rejection_reason = reason
                store.is_active = False
                store.save(update_fields=["verification_status", "rejection_reason", "is_active"])
                messages.warning(request, f"Заявка магазина «{store.name}» отклонена. Причина: {reason}")

            elif action == "freeze_store":
                reason = request.POST.get("reason", "").strip() or "Подозрение на мошенничество / нарушение правил платформы"
                store.is_frozen_by_admin = True
                store.freeze_reason = reason
                store.save(update_fields=["is_frozen_by_admin", "freeze_reason"])
                messages.warning(request, f"Магазин '{store.name}' заблокирован администратором! Причина: {reason}")

            elif action == "unfreeze_store":
                store.is_frozen_by_admin = False
                store.freeze_reason = ""
                store.save(update_fields=["is_frozen_by_admin", "freeze_reason"])
                messages.success(request, f"Магазин '{store.name}' успешно разблокирован и доступен для покупок.")

            elif action == "toggle_active":
                store.is_active = not store.is_active
                store.save(update_fields=["is_active"])
                messages.info(request, f"Статус активации магазина '{store.name}' изменен на: {'Активен' if store.is_active else 'Выключен'}")

        return redirect("platform-admin-dashboard")

    # --- 1. АНАЛИТИЧЕСКИЕ МЕТРИКИ ПЛАТФОРМЫ ---
    total_stores_count = Store.objects.count()
    active_stores_count = Store.objects.filter(is_active=True, is_frozen_by_admin=False).count()
    frozen_stores_count = Store.objects.filter(is_frozen_by_admin=True).count()
    total_sellers_count = User.objects.filter(store__isnull=False).count()

    all_orders = Order.objects.exclude(status=Order.Status.CANCELLED)
    paid_or_delivered = all_orders.filter(
        status__in=[Order.Status.PAID, Order.Status.HANDED_TO_COURIER, Order.Status.DELIVERED]
    )

    # Заказы за сегодня
    orders_today_count = Order.objects.filter(created_at__date=today).count()
    orders_today_revenue = paid_or_delivered.filter(created_at__date=today).aggregate(
        total=Sum("total_price")
    )["total"] or Decimal("0")

    # Общий оборот платформы (Total GMV)
    total_gmv = paid_or_delivered.aggregate(total=Sum("total_price"))["total"] or Decimal("0")
    total_commission_revenue = Decimal("0")
    total_subscription_revenue = SubscriptionPayment.objects.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    total_platform_net_revenue = total_commission_revenue + total_subscription_revenue

    # Всего покупателей в системе
    total_buyers_count = Customer.objects.count()

    # --- 2. ТОП ПРОДАВАЕМЫХ ТОВАРОВ ПО ВСЕЙ ПЛАТФОРМЕ ---
    top_products_qs = OrderItem.objects.filter(
        order__status__in=[Order.Status.PAID, Order.Status.HANDED_TO_COURIER, Order.Status.DELIVERED]
    ).values(
        "variant__product__name",
        "variant__product__store__name",
        "variant__product__store__id"
    ).annotate(
        units_sold=Sum("quantity"),
        total_revenue=Sum("price_at_purchase")
    ).order_by("-units_sold")[:7]

    # --- 3. ГРАФИК ОБОРОТА И ЗАКАЗОВ ЗА 7 ДНЕЙ ---
    chart_labels = []
    chart_gmv = []
    
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        lbl = f"{d.strftime('%d.%m')} ({day_names[d.weekday()]})"
        daily_orders = paid_or_delivered.filter(created_at__date=d)
        daily_gmv = daily_orders.aggregate(total=Sum("total_price"))["total"] or 0

        chart_labels.append(lbl)
        chart_gmv.append(float(daily_gmv))

    # --- 4. СПИСОК МАГАЗИНОВ С ПОИСКОМ И ФИЛЬТРАЦИЕЙ ---
    search_q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all")

    stores_qs = Store.objects.select_related("owner").annotate(
        total_orders=Count("orders", distinct=True),
        store_revenue=Sum(
            "orders__total_price",
            filter=Q(orders__status__in=[Order.Status.PAID, Order.Status.HANDED_TO_COURIER, Order.Status.DELIVERED]),
            distinct=True
        )
    ).order_by("-created_at")

    if search_q:
        stores_qs = stores_qs.filter(
            Q(name__icontains=search_q) |
            Q(owner__username__icontains=search_q) |
            Q(phone_number__icontains=search_q) |
            Q(instagram_username__icontains=search_q)
        )

    pending_stores_count = Store.objects.filter(verification_status=Store.VerificationStatus.PENDING).count()
    pending_stores = list(
        Store.objects.filter(verification_status=Store.VerificationStatus.PENDING)
        .select_related("owner")
        .order_by("-created_at")
    )

    if status_filter == "pending":
        stores_qs = stores_qs.filter(verification_status=Store.VerificationStatus.PENDING)
    elif status_filter == "frozen":
        stores_qs = stores_qs.filter(is_frozen_by_admin=True)
    elif status_filter == "active":
        stores_qs = stores_qs.filter(is_active=True, is_frozen_by_admin=False, verification_status=Store.VerificationStatus.APPROVED)
    elif status_filter == "suspended":
        stores_qs = stores_qs.filter(subscription_status="suspended")

    stores = list(stores_qs[:100])

    from core.models import PlatformSettings
    from core.working_views.telegram_bot import build_superadmin_link_deep_link
    platform_settings = PlatformSettings.get_settings()
    superadmin_telegram_link = build_superadmin_link_deep_link()

    context = {
        "total_stores_count": total_stores_count,
        "active_stores_count": active_stores_count,
        "frozen_stores_count": frozen_stores_count,
        "pending_stores_count": pending_stores_count,
        "pending_stores": pending_stores,
        "platform_settings": platform_settings,
        "superadmin_telegram_link": superadmin_telegram_link,
        "total_sellers_count": total_sellers_count,
        "orders_today_count": orders_today_count,
        "orders_today_revenue": orders_today_revenue,
        "total_gmv": total_gmv,
        "total_commission_revenue": total_commission_revenue,
        "total_subscription_revenue": total_subscription_revenue,
        "total_platform_net_revenue": total_platform_net_revenue,
        "total_buyers_count": total_buyers_count,
        "top_products": top_products_qs,
        "chart_labels": json.dumps(chart_labels),
        "chart_gmv": json.dumps(chart_gmv),
        
        "stores": stores,
        "search_q": search_q,
        "status_filter": status_filter,
    }
    return render(request, "platform_admin/dashboard.html", context)
