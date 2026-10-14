from django.contrib import admin
from django.contrib import messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import (
    StoreBranch,
    Store,
    Category,
    Product,
    ProductVariant,
    Customer,
    Conversation,
    Message,
    Order,
    OrderItem,
    ProductImage,
    PayoutTransaction,
    SubscriptionPayment,
    ProductReview,
    BroadcastCampaign,
)


class ProductVariantInline(admin.TabularInline):
    """Позволяет добавлять варианты товара прямо на странице товара."""
    model = ProductVariant
    extra = 1


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 2


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "store", "base_price", "is_active", "created_at"]
    list_filter = ["is_active", "category", "store"]
    search_fields = ["name", "store__name"]
    inlines = [ProductVariantInline, ProductImageInline]



class StoreBranchInline(admin.TabularInline):
    model = StoreBranch
    extra = 0

@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "name",
        "niche",
        "get_owner_info",
        "phone_number",
        "instagram_username",
        "get_tier_badge",
        "verification_status",
        "subscription_status",
        "balance_display",
        "is_active",
        "is_frozen_by_admin",
        "created_at",
    ]
    list_filter = [
        "niche",
        "verification_status",
        "subscription_status",
        "is_frozen_by_admin",
        "is_active",
    ]
    search_fields = ["name", "owner__username", "phone_number", "instagram_username"]
    readonly_fields = ["balance", "held_balance", "created_at"]
    actions = ["approve_stores_action", "freeze_stores_action", "unfreeze_stores_action", "activate_stores_action"]
    inlines = [StoreBranchInline]

    @admin.display(description="Владелец (Логин)")
    def get_owner_info(self, obj):
        if obj.owner:
            return format_html(
                '<a href="/brainmart-secure-admin-panel/auth/user/{}/change/">👤 {}</a>',
                obj.owner.id,
                obj.owner.username
            )
        return "—"

    @admin.display(description="Подписка / Точки")
    def get_tier_badge(self, obj):
        fee = int(obj.current_tier_fee)
        branches = obj.branches.count()
        return mark_safe(f'<span style="background: rgba(99,102,241,0.15); color: #4F46E5; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 10px;">{fee:,} сум ({branches} точ.)</span>')

    @admin.display(description="Баланс")
    def balance_display(self, obj):
        return f"{int(obj.balance):,} сум"

    @admin.action(description="✅ Одобрить и активировать выбранные магазины")
    def approve_stores_action(self, request, queryset):
        for store in queryset:
            store.verification_status = Store.VerificationStatus.APPROVED
            store.is_active = True
            store.rejection_reason = ""
            store.save(update_fields=["verification_status", "is_active", "rejection_reason"])
            try:
                from core.working_views.telegram_bot import notify_seller_about_approval
                notify_seller_about_approval(store)
            except Exception:
                pass
        self.message_user(request, f"{queryset.count()} магазинов успешно одобрено и активировано.", messages.SUCCESS)

    @admin.action(description="🔒 Заморозить выбранные магазины (Антифрод)")
    def freeze_stores_action(self, request, queryset):
        count = queryset.update(
            is_frozen_by_admin=True,
            freeze_reason="Заморожен администратором через Django Admin"
        )
        self.message_user(request, f"{count} магазинов успешно заморожено. Витрины скрыты от покупателей.", messages.WARNING)

    @admin.action(description="🔓 Разблокировать выбранные магазины")
    def unfreeze_stores_action(self, request, queryset):
        count = queryset.update(is_frozen_by_admin=False, freeze_reason="")
        self.message_user(request, f"{count} магазинов успешно разблокировано.", messages.SUCCESS)

    @admin.action(description="⚡ Активировать выбранные магазины")
    def activate_stores_action(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} магазинов активировано.", messages.SUCCESS)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ["variant", "quantity", "price_at_purchase"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "store",
        "customer",
        "status",
        "payout_status",
        "total_price_display",
        "processing_amount_display",
        "seller_amount_display",
        "tracking_url",
        "created_at",
    ]
    list_filter = ["status", "payout_status", "store", "created_at"]
    search_fields = [
        "id",
        "customer__full_name",
        "customer__phone_number",
        "customer__telegram_username",
        "delivery_address",
        "tracking_url",
    ]
    inlines = [OrderItemInline]

    @admin.display(description="Сумма заказа")
    def total_price_display(self, obj):
        return f"{int(obj.total_price):,} сум"

    @admin.display(description="Эквайринг (3%)")
    def processing_amount_display(self, obj):
        return f"{int(obj.processing_amount):,} сум"

    @admin.display(description="Продавцу (97%)")
    def seller_amount_display(self, obj):
        return f"{int(obj.seller_amount):,} сум"


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["id", "full_name", "phone_number", "telegram_username", "telegram_chat_id", "created_at"]
    search_fields = ["full_name", "phone_number", "telegram_username", "telegram_chat_id", "instagram_scoped_id"]


@admin.register(PayoutTransaction)
class PayoutTransactionAdmin(admin.ModelAdmin):
    list_display = ["id", "store", "amount_display", "card_number", "status", "provider_tx_id", "created_at"]
    list_filter = ["status", "store"]
    search_fields = ["store__name", "card_number", "provider_tx_id"]

    @admin.display(description="Сумма")
    def amount_display(self, obj):
        return f"{int(obj.amount):,} сум"


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "store", "amount_display", "payment_method", "paid_at", "period_start", "period_end"]
    list_filter = ["payment_method", "store"]
    search_fields = ["store__name"]

    @admin.display(description="Сумма")
    def amount_display(self, obj):
        return f"{int(obj.amount):,} сум"


@admin.register(BroadcastCampaign)
class BroadcastCampaignAdmin(admin.ModelAdmin):
    list_display = ["id", "store", "title", "target_segment", "recipients_count", "delivered_count", "status", "created_at"]
    list_filter = ["status", "target_segment", "store"]
    search_fields = ["title", "store__name"]


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ["sender", "text", "created_at"]
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["store", "customer", "is_handled_by_ai", "created_at"]
    list_filter = ["is_handled_by_ai", "store"]
    inlines = [MessageInline]


admin.site.register(Category)
@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    list_display = ["product", "customer_name", "rating", "is_visible", "created_at"]
    list_filter = ["is_visible", "rating", "created_at"]
    search_fields = ["product__name", "customer_name", "text"]


from .models import PlatformSettings

@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = ["id", "superadmin_telegram_username", "superadmin_telegram_chat_id", "notify_on_new_store", "updated_at"]

