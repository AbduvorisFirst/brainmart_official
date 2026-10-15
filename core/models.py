"""
models.py
---------
Здесь описана структура базы данных нашего B2B2C маркетплейса.

Логика такая:
- Store       -> это наш B2B-партнёр (владелец офлайн/instagram магазина одежды)
- Product     -> товар, который продаёт конкретный Store (например "Худи Nike")
- ProductVariant -> конкретная вариация товара (цвет + размер + остаток на складе)
- Customer    -> конечный покупатель (B2C), который пишет в Instagram Direct
- Conversation/Message -> история переписки клиента с ИИ-агентом в Instagram
- Order/OrderItem -> заказ, который клиент оформил на маркетплейсе
"""

from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
import secrets


class Store(models.Model):
    """
    B2B-партнёр: магазин одежды, который подключил свой Instagram к нашей CRM.
    Один Store привязан к одному пользователю Django (для входа в личный кабинет).
    """
    owner = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="store",
        help_text="Аккаунт владельца магазина, через который он логинится в CRM",
    )
    name = models.CharField(max_length=255, verbose_name="Название магазина")
    instagram_username = models.CharField(
        max_length=100,
        unique=True,
        help_text="Ник магазина в Instagram, например 'sultan_wear'",
    )
    # ID страницы/аккаунта в Meta, нужен для приёма вебхуков от Instagram
    instagram_business_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text="ID из Meta Graph API, приходит при подключении Instagram",
    )
    logo = models.ImageField(upload_to='stores/logos/', null=True, blank=True)
    phone_number = models.CharField(max_length=20)
    address = models.CharField(max_length=500, blank=True)
    # Координаты магазина — нужны для расчёта расстояния до клиента
    # и для интеграции со службами доставки (Яндекс.Доставка и т.д.)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    # Telegram продавца — куда слать уведомления о новых оплаченных заказах.
    # Заполняется автоматически, когда продавец переходит по персональной
    # ссылке из кабинета и жмёт /start в боте (см. telegram_bot.py)
    telegram_chat_id = models.CharField(max_length=64, blank=True, null=True)
    telegram_notification_language = models.CharField(
        max_length=5,
        default="uz",
        choices=[("uz", "O'zbekcha"), ("ru", "Русский"), ("en", "English")],
        verbose_name="Язык уведомлений в Telegram",
        help_text="На каком языке владелец получает карточки заказов и кнопки в Telegram",
    )
    telegram_link_token = models.CharField(
        max_length=64, unique=True, default=secrets.token_hex,
        help_text="Используется для одноразовой безопасной привязки Telegram",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Если False — магазин временно не принимает заказы",
    )
    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "На модерации (Ожидает проверки)"
        APPROVED = "approved", "Одобрен (Активен)"
        REJECTED = "rejected", "Отклонён"

    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.APPROVED,
        verbose_name="Статус модерации",
        help_text="Для новых заявок — pending. После одобрения администратором — approved",
    )
    owner_telegram_username = models.CharField(
        max_length=100, blank=True, default="",
        verbose_name="Telegram владельца магазина (@username)",
    )
    rejection_reason = models.CharField(
        max_length=255, blank=True, default="",
        verbose_name="Причина отклонения заявки",
    )
    # Баланс и автовыплаты (Finance & Auto-payout)
    balance = models.DecimalField(
        max_digits=14, decimal_places=2, default=0.00,
        verbose_name="Доступный баланс",
        help_text="Деньги, доступные для вывода на карту"
    )
    held_balance = models.DecimalField(
        max_digits=14, decimal_places=2, default=0.00,
        verbose_name="Баланс в холде",
        help_text="Средства за оплаченные заказы, которые еще не доставлены"
    )
    payout_card = models.CharField(
        max_length=20, blank=True,
        verbose_name="Номер карты для выплат",
        help_text="Карта Uzcard или Humo (например 8600... или 9860...)"
    )
    payout_card_holder = models.CharField(
        max_length=150, blank=True,
        verbose_name="Владелец карты (ФИО)"
    )
    auto_payout_enabled = models.BooleanField(
        default=True,
        verbose_name="Автоматический вывод",
        help_text="Если True, деньги переводятся на карту сразу после доставки заказа"
    )
    enable_auto_acquiring = models.BooleanField(
        default=True,
        verbose_name="Автоматический эквайринг (онлайн сразу)",
        help_text="Оплата онлайн картой через Click/Payme без чеков",
    )
    enable_card_transfer = models.BooleanField(
        default=True,
        verbose_name="Прямой перевод на карту продавца (с чеком)",
        help_text="Перевод по номеру карты продавца с прикреплением чека",
    )
    # Ниша магазина (без процентов и комиссий)
    class Niche(models.TextChoices):
        CLOTHES = "clothes", "Одежда, обувь и аксессуары"
        ELECTRONICS = "electronics", "Электроника и гаджеты"
        BOOKS = "books", "Книги и канцелярия"
        COSMETICS = "cosmetics", "Косметика и парфюмерия"
        OTHER = "other", "Другое"

    niche = models.CharField(
        max_length=50,
        choices=Niche.choices,
        default=Niche.CLOTHES,
        verbose_name="Ниша магазина",
    )
    category = models.ForeignKey(
        'Category',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stores",
        verbose_name="Основная категория / Ниша магазина",
        help_text="Выбирается продавцом и сохраняется по умолчанию для всех товаров магазина",
    )

    subscription_paid_until = models.DateField(
        null=True, blank=True,
        verbose_name="Подписка оплачена до",
        help_text="Дата окончания текущего оплаченного периода подписки"
    )
    subscription_status = models.CharField(
        max_length=25,
        choices=[
            ("active", "Активна"),
            ("grace_period", "Льготный период (до 5 дней)"),
            ("suspended", "Заблокирована (Не оплачена)")
        ],
        default="active",
        verbose_name="Статус подписки"
    )
    subscription_monthly_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=450000.00,
        verbose_name="Ежемесячная абонплата (сум)"
    )
    last_subscription_payment = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Дата последней оплаты подписки"
    )
    subscription_warning_sent_stage = models.CharField(
        max_length=30, blank=True, default="",
        verbose_name="Этап отправленного уведомления"
    )
    # Административная безопасность и Модерация (Anti-fraud & Moderation)
    is_frozen_by_admin = models.BooleanField(
        default=False,
        verbose_name="Заморожен администратором",
        help_text="Если True, магазин и витрина заблокированы платформой"
    )
    freeze_reason = models.CharField(
        max_length=255, blank=True, default="",
        verbose_name="Причина блокировки магазина"
    )
    @property
    def current_tier_fee(self):
        """Возвращает стоимость текущего тарифа (вычисляется из количества точек)."""
        branches_count = getattr(self, "branches_count_cached", None)
        if branches_count is None:
            branches_count = self.branches.count() if self.pk else 0
        
        base_fee = 450000.00
        extra_branch_fee = 150000.00
        
        # 1-я точка входит в базовый тариф
        if branches_count <= 1:
            return base_fee
        
        return base_fee + (branches_count - 1) * extra_branch_fee

    @property
    def can_use_broadcasts(self):
        return True

    @property
    def can_use_custom_branding(self):
        return True
    # Кастомизация брендинга и темы магазина (Custom Themes & Brand Palette)
    primary_color = models.CharField(
        max_length=20, default="#4F46E5",
        verbose_name="Основной цвет бренда (Hex)",
        help_text="Используется для кнопок, плашек и акцентов на витрине и в кабинете"
    )
    accent_color = models.CharField(
        max_length=20, default="#10B981",
        verbose_name="Акцентный цвет (Hex)"
    )
    theme_style = models.CharField(
        max_length=30, default="default_black",
        choices=[
            ("default_black", "Default Black (Классический)"),
            ("kids_playful", "Kids Playful (Для детей - Яркий)"),
            ("premium_gold", "Premium Gold (Премиум Золото)"),
            ("genz_neon", "Gen-Z Neon (Подростковый Неон)"),
            ("custom", "Свой дизайн (Только MAX)"),
        ],
        verbose_name="Стиль темы оформления"
    )
    theme_mode = models.CharField(
        max_length=30, default="dark",
        choices=[
            ("dark", "Тёмный"),
        ],
        verbose_name="Режим фона и атмосферы витрины"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Магазин"
        verbose_name_plural = "Магазины"

    @property
    def effective_category(self):
        """Возвращает категорию магазина (если еще не выбрана явно, берет из последнего товара)."""
        if self.category_id:
            return self.category
        last_product = self.products.filter(category__isnull=False).order_by("-id").first()
        return last_product.category if last_product else None

    def __str__(self):
        return self.name

    @property
    def is_subscription_active(self) -> bool:
        """Активна ли подписка магазина (включая 6 дней льготного периода)."""
        import datetime
        from django.utils import timezone
        today = timezone.now().date()
        if not self.subscription_paid_until:
            created_date = self.created_at.date() if self.created_at else today
            self.subscription_paid_until = created_date + datetime.timedelta(days=30)
            self.save(update_fields=["subscription_paid_until"])

        # Максимально допустимая дата = оплаченная дата + 6 дней льготного периода
        max_allowed_date = self.subscription_paid_until + datetime.timedelta(days=6)
        return today <= max_allowed_date

    @property
    def subscription_days_left(self) -> int:
        """Сколько дней осталось до даты оплаты (если отрицательно — дни просрочки)."""
        import datetime
        from django.utils import timezone
        today = timezone.now().date()
        if not self.subscription_paid_until:
            return 30
        return (self.subscription_paid_until - today).days

    def get_subscription_status_info(self):
        """Возвращает структурированную информацию о подписке для UI Dashboard."""
        days = self.subscription_days_left
        if days > 3:
            return {"status": "active", "badge": "success", "text": "Активна", "days": days}
        elif 0 <= days <= 3:
            return {"status": "expiring", "badge": "warning", "text": f"Заканчивается (осталось {days} дн.)", "days": days}
        elif -6 <= days < 0:
            overdue = abs(days)
            remaining_grace = 6 - overdue
            return {
                "status": "grace_period",
                "badge": "danger",
                "text": f"Льготный период ({overdue}-й день, осталось {remaining_grace} дн.)",
                "days": days,
                "overdue": overdue,
                "remaining_grace": remaining_grace
            }
        else:
            overdue = abs(days)
            return {"status": "suspended", "badge": "dark", "text": "Заморожен (просрочка > 6 дней)", "days": days, "overdue": overdue}

    def renew_subscription(self, months: int = 1, payment_method: str = "transfer", transaction_id: str = ""):
        """Продлевает подписку на N месяцев с учётом просроченных дней льготного периода (6 дней)."""
        import datetime
        from django.utils import timezone
        today = timezone.now().date()

        # Если продление происходит во время 6 дней льготного периода,
        # новый период начинается от предыдущей даты окончания (например: 3 июня -> 3 июля)!
        if self.subscription_paid_until and 0 <= (today - self.subscription_paid_until).days <= 6:
            start_date = self.subscription_paid_until
        elif self.subscription_paid_until and today < self.subscription_paid_until:
            start_date = self.subscription_paid_until
        else:
            # Если магазин был заморожен дольше 6 дней, новый цикл начинается с даты оплаты
            start_date = today

        new_end_date = start_date + datetime.timedelta(days=30 * months)
        fee = self.current_tier_fee * months

        self.subscription_paid_until = new_end_date
        self.subscription_status = "active"
        self.is_active = True
        self.is_frozen_by_admin = False
        self.freeze_reason = ""
        self.last_subscription_payment = timezone.now()
        self.subscription_warning_sent_stage = ""
        self.save(update_fields=[
            "subscription_paid_until", "subscription_status", "is_active",
            "is_frozen_by_admin", "freeze_reason",
            "last_subscription_payment", "subscription_warning_sent_stage"
        ])

        SubscriptionPayment.objects.create(
            store=self,
            amount=fee,
            months=months,
            period_start=start_date,
            period_end=new_end_date,
            payment_method=payment_method,
            status="completed",
            transaction_id=transaction_id
        )
        return new_end_date

    def save(self, *args, **kwargs):
        if not self.pk:
            if not self.subscription_paid_until:
                import datetime
                from django.utils import timezone
                self.subscription_paid_until = timezone.now().date() + datetime.timedelta(days=30)
        super().save(*args, **kwargs)


class StoreBranch(models.Model):
    """Филиал/Точка магазина. Каждый магазин может иметь несколько точек (база 1 + доп)."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=255, verbose_name="Название точки")
    address = models.CharField(max_length=500, verbose_name="Адрес")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Точка/Филиал магазина"
        verbose_name_plural = "Точки/Филиалы магазина"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.name} ({self.store.name})"


class SubscriptionPayment(models.Model):
    """История и заявки на оплату подписки BrainMart."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="subscription_payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=450000.00, verbose_name="Сумма оплаты")
    months = models.PositiveIntegerField(default=1, verbose_name="Срок (мес.)")
    paid_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата оплаты")
    period_start = models.DateField(null=True, blank=True, verbose_name="Начало периода")
    period_end = models.DateField(null=True, blank=True, verbose_name="Конец периода")
    payment_method = models.CharField(max_length=30, default="transfer", verbose_name="Способ оплаты")
    status = models.CharField(
        max_length=20,
        choices=[("pending", "Ожидает проверки"), ("completed", "Подтверждена"), ("rejected", "Отклонена")],
        default="completed",
        verbose_name="Статус"
    )
    transaction_id = models.CharField(max_length=100, blank=True, verbose_name="ID транзакции / комментарий")
    receipt_image = models.ImageField(upload_to="subscription_receipts/", null=True, blank=True, verbose_name="Скан / Чек перевода")

    class Meta:
        verbose_name = "Оплата подписки CRM"
        verbose_name_plural = "Оплаты подписок CRM"
        ordering = ["-paid_at"]

    def __str__(self):
        return f"Подписка {self.store.name} ({int(self.amount):,} сум до {self.period_end or 'не указано'})"



class Category(models.Model):
    """Категория товара: худи, футболки, джинсы и т.д."""
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"

    def __str__(self):
        return self.name


class Product(models.Model):
    """
    Товар внутри магазина. Сам по себе не имеет остатков и точных размеров —
    это описывают дочерние ProductVariant (см. ниже).
    """
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name="products"
    )
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, related_name="products"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    base_price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    main_image = models.ImageField(upload_to="products/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"

    def __str__(self):
        return f"{self.name} ({self.store.name})"

    @property
    def average_rating(self):
        reviews = self.reviews.filter(is_visible=True)
        if not reviews.exists():
            return 4.9
        from django.db.models import Avg
        avg = reviews.aggregate(Avg("rating"))["rating__avg"]
        return round(float(avg), 1) if avg else 4.9

    @property
    def reviews_count(self):
        return self.reviews.filter(is_visible=True).count()


class ProductVariant(models.Model):
    """
    Конкретная вариация товара: цвет + размер + сколько штук на складе.
    Именно эту модель ИИ-агент проверяет, когда клиент спрашивает
    "есть черное худи размера L?"
    """
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants"
    )
    image = models.ImageField(upload_to='variants/colors/', null=True, blank=True)
    color = models.CharField(max_length=100, blank=True, default="")
    size = models.CharField(max_length=100, blank=True, default="")  # S, M, L, XL, 42, 205/55 R16, 128GB, 1 кг и т.д.
    sku = models.CharField(
        max_length=64, unique=True, help_text="Уникальный артикул варианта"
    )
    stock_quantity = models.PositiveIntegerField(default=0)
    price_override = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Если нужна цена, отличная от base_price товара",
    )
    cost_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Себестоимость",
        help_text="Закупочная цена товара для расчёта чистой прибыли",
    )
    barcode = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
        verbose_name="Штрихкод",
        help_text="Штрихкод для сканера в POS-терминале",
    )

    class Meta:
        verbose_name = "Вариант товара"
        verbose_name_plural = "Варианты товара"
        unique_together = ("product", "color", "size")

    def __str__(self):
        if self.color or self.size:
            return f"{self.product.name} / {self.color or '—'} / {self.size or '—'}"
        return self.product.name


    @property
    def final_price(self):
        return self.price_override if self.price_override is not None else self.product.base_price

    @property
    def in_stock(self):
        return self.stock_quantity > 0


class Customer(models.Model):
    """
    Конечный покупатель. Он не регистрируется отдельно — мы создаём
    его автоматически, как только он впервые написал в Instagram Direct
    или оформил заказ через Telegram Mini App.
    """
    instagram_scoped_id = models.CharField(
        max_length=100,
        unique=True,
        help_text="ID пользователя в Instagram (уникален в рамках Meta API)",
    )
    full_name = models.CharField(max_length=255, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    delivery_address = models.CharField(max_length=500, blank=True)
    # Telegram-данные клиента — заполняются при заказе через Telegram бота.
    # Нужны для: (1) отправки уведомлений о доставке, (2) показа @username продавцу.
    telegram_chat_id = models.CharField(
        max_length=64, blank=True, null=True,
        help_text="Chat ID клиента в Telegram — для отправки уведомлений",
    )
    telegram_username = models.CharField(
        max_length=100, blank=True,
        help_text="Username клиента в Telegram (без @), например 'ivan_buyer'",
    )
    # Предпочтительный язык клиента — выбирается в Telegram боте командой /lang
    language = models.CharField(
        max_length=5,
        default="uz",
        choices=[("ru", "Русский"), ("uz", "O'zbekcha"), ("en", "English")],
        help_text="Язык общения: ru / uz / en",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

    def __str__(self):
        return self.full_name or self.instagram_scoped_id


class Conversation(models.Model):
    """Одна переписка: конкретный клиент <-> конкретный магазин в Instagram."""
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name="conversations"
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="conversations"
    )
    is_handled_by_ai = models.BooleanField(
        default=True,
        help_text="False — если менеджер магазина забрал диалог себе вручную",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Переписка"
        verbose_name_plural = "Переписки"
        unique_together = ("store", "customer")

    def __str__(self):
        return f"{self.customer} <-> {self.store}"


class Message(models.Model):
    """Одно сообщение внутри переписки — от клиента, от ИИ или от менеджера."""

    class Sender(models.TextChoices):
        CUSTOMER = "customer", "Клиент"
        AI_AGENT = "ai_agent", "ИИ-агент"
        MANAGER = "manager", "Менеджер магазина"

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.CharField(max_length=20, choices=Sender.choices)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.sender}] {self.text[:40]}"


class Order(models.Model):
    """Заказ, оформленный клиентом на маркетплейсе."""

    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Ожидает оплаты"
        PAID = "paid", "Оплачен"
        HANDED_TO_COURIER = "handed_to_courier", "Передан курьеру"
        DELIVERED = "delivered", "Доставлен"
        CANCELLED = "cancelled", "Отменён"

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="orders")
    branch = models.ForeignKey(StoreBranch, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders", verbose_name="Точка (Филиал)")
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="orders"
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.PENDING_PAYMENT
    )
    delivery_address = models.CharField(max_length=500)
    # Координаты точки доставки — если клиент разрешил геолокацию в браузере,
    # заполняются автоматически (см. product_detail.html). Необязательные:
    # если клиент не дал доступ к геолокации, курьер работает по текстовому адресу.
    delivery_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_landmark = models.CharField(max_length=300, blank=True, default="", verbose_name="Ориентир для такси/курьера")
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_method = models.CharField(
        max_length=20, blank=True, help_text="click / payme"
    )
    payment_transaction_id = models.CharField(max_length=255, blank=True)
    # Ссылка на отслеживание доставки (Яндекс.Доставка, Fargo и т.д.)
    # Продавец вставляет при передаче курьеру → клиент получает в Telegram
    tracking_url = models.URLField(max_length=500, blank=True)
    payment_receipt = models.ImageField(
        upload_to="order_receipts/",
        null=True,
        blank=True,
        verbose_name="Чек об оплате",
        help_text="Скриншот или квитанция перевода от покупателя",
    )
    payment_confirmed_by_customer = models.BooleanField(
        default=False,
        verbose_name="Покупатель подтвердил перевод",
    )
    assigned_manager = models.ForeignKey(
        "StoreManager",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Ответственный менеджер",
    )

    # Финансовые выплаты продавцу (97% продавцу, 3% Click эквайринг)
    class PayoutStatus(models.TextChoices):
        HELD = "held", "В холде (доставка)"
        READY = "ready", "Готов к выводу"
        PAID_OUT = "paid_out", "Выплачен продавцу"
        REFUNDED = "refunded", "Возвращён клиенту"

    processing_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=3.00,
        verbose_name="Эквайринг и переводы (%)",
        help_text="Банковский эквайринг Click (2%) + перевод на карту (1%)"
    )
    processing_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00,
        verbose_name="Сумма эквайринга и перевода (сум)"
    )
    seller_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00,
        help_text="Сумма к зачислению продавцу (сум) за вычетом эквайринга"
    )
    payout_status = models.CharField(
        max_length=20, choices=PayoutStatus.choices, default=PayoutStatus.HELD
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Заказ #{self.id} — {self.store.name}"
    def recalculate_total(self):
        """Пересчитывает total_price. Продавец получает 100% напрямую на карту (0% комиссии)."""
        from decimal import Decimal
        total = Decimal(str(sum(item.subtotal for item in self.items.all())))
        self.total_price = total
        self.processing_percent = Decimal("0.00")
        self.processing_amount = Decimal("0.00")
        self.seller_amount = total
        self.save(update_fields=["total_price", "processing_percent", "processing_amount", "seller_amount"])


    @property
    def delivery_map_url(self):
        """
        Ссылка на Google Maps с точкой доставки — курьер открывает
        прямо с телефона и сразу видит маршрут. None, если координат нет
        (клиент не разрешил геолокацию) — тогда курьер работает по адресу.
        """
        if self.delivery_latitude is None or self.delivery_longitude is None:
            return None
        return f"https://google.com/maps?q={self.delivery_latitude},{self.delivery_longitude}"

    def get_dispatch_distance_km(self):
        """Расстояние (в км) между клиентом и филиалом отгрузки."""
        if not self.delivery_latitude or not self.delivery_longitude:
            return None
        from core.working_views.views_branches import calculate_distance_km
        target_branch = self.branch
        if target_branch and target_branch.latitude and target_branch.longitude:
            return calculate_distance_km(self.delivery_latitude, self.delivery_longitude, target_branch.latitude, target_branch.longitude)
        if self.store.latitude and self.store.longitude:
            return calculate_distance_km(self.delivery_latitude, self.delivery_longitude, self.store.latitude, self.store.longitude)
        return None

    def get_estimated_taxi_fare(self):
        """Ориентировочная стоимость доставки такси (сум)."""
        dist = self.get_dispatch_distance_km()
        if dist is None:
            return 18000
        # Базовая посадка ~10 000 + ~2 200 сум за км
        return max(12000, int(10000 + dist * 2200))

    def get_estimated_taxi_mins(self):
        """Ориентировочное время доставки (минут)."""
        dist = self.get_dispatch_distance_km()
        if dist is None:
            return 25
        return max(10, int(dist * 2.8) + 7)


class PayoutTransaction(models.Model):
    """История выплат денег продавцам на карты Uzcard/Humo."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "В обработке"
        SUCCESS = "success", "Успешно выплачено"
        FAILED = "failed", "Ошибка выплаты"

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="payout_transactions")
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="payouts")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Сумма выплаты")
    card_number = models.CharField(max_length=20, verbose_name="Карта получения")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    provider_tx_id = models.CharField(max_length=255, blank=True, verbose_name="ID транзакции шлюза")
    error_message = models.TextField(blank=True, verbose_name="Текст ошибки (если failed)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Выплата продавцу"
        verbose_name_plural = "Выплаты продавцам"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Выплата #{self.id} — {self.store.name} ({int(self.amount)} сум)"



class OrderItem(models.Model):
    """Одна позиция в заказе — конкретный вариант товара и его количество."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    # Цену фиксируем на момент покупки, чтобы будущее изменение цены товара
    # не поменяло задним числом сумму старого заказа
    price_at_purchase = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "Позиция заказа"
        verbose_name_plural = "Позиции заказа"

    def __str__(self):
        return f"{self.variant} x{self.quantity}"

    @property
    def subtotal(self):
        return self.price_at_purchase * self.quantity

class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='products/gallery/')
    # Цвет, к которому относится это фото (пусто = общее фото товара)
    color = models.CharField(
        max_length=50, blank=True, default="",
        help_text="Цвет вариации, к которому относится это фото"
    )

    def __str__(self):
        if self.color:
            return f"Фото ({self.color}) для {self.product.name}"
        return f"Фото для {self.product.name}"


class StoreManager(models.Model):
    """
    Менеджер магазина — дополнительный человек, который получает уведомления
    о новых заказах в Telegram. У магазина может быть несколько менеджеров.
    Владелец (Store.telegram_chat_id) всегда получает уведомления отдельно.
    """
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name="managers"
    )
    name = models.CharField(
        max_length=100,
        help_text="Имя менеджера, например 'Ахмед' или 'Дильноза'",
    )
    telegram_chat_id = models.CharField(
        max_length=64, blank=True,
        help_text="Заполняется автоматически при привязке через бота",
    )
    telegram_username = models.CharField(
        max_length=100, blank=True, default="",
        verbose_name="Telegram @username менеджера",
        help_text="Сохраняется автоматически при привязке через бота",
    )
    language = models.CharField(
        max_length=5,
        default="uz",
        choices=[("uz", "O'zbekcha"), ("ru", "Русский"), ("en", "English")],
        verbose_name="Язык уведомлений",
        help_text="На каком языке менеджер получает уведомления о заказах в Telegram",
    )
    telegram_link_token = models.CharField(
        max_length=64, unique=True, default=secrets.token_hex,
        help_text="Одноразовый токен для привязки Telegram менеджера",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Менеджер магазина"
        verbose_name_plural = "Менеджеры магазина"

    @property
    def display_contact(self):
        if self.telegram_username:
            username = self.telegram_username.lstrip("@")
            return f"@{username}"
        return self.name

    def __str__(self):
        return f"{self.name} ({self.store.name})"


class BroadcastCampaign(models.Model):
    """
    Маркетинговые рассылки через Telegram-бота по базе покупателей магазина.
    Позволяет отправлять промокоды, скидки, анонсы новых коллекций по сегментам.
    """
    class TargetSegment(models.TextChoices):
        ALL = "all", "Все клиенты магазина"
        VIP = "vip", "VIP клиенты (LTV > 500k сум или > 2 заказов)"
        SLEEPING = "sleeping", "Спящие клиенты (нет заказов > 30 дней)"
        NEW = "new", "Новички (1 заказ)"

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        SENDING = "sending", "Отправляется"
        COMPLETED = "completed", "Завершена"
        FAILED = "failed", "Ошибка"

    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name="broadcasts"
    )
    title = models.CharField(max_length=255, verbose_name="Тема рассылки")
    message_text = models.TextField(verbose_name="Текст сообщения")
    target_segment = models.CharField(
        max_length=20,
        choices=TargetSegment.choices,
        default=TargetSegment.ALL,
        verbose_name="Сегмент аудитории"
    )
    button_text = models.CharField(
        max_length=100, blank=True, default="🛍 Открыть каталог",
        verbose_name="Текст кнопки"
    )
    button_url = models.CharField(
        max_length=500, blank=True,
        verbose_name="Ссылка для перехода"
    )
    recipients_count = models.PositiveIntegerField(default=0, verbose_name="Всего получателей")
    delivered_count = models.PositiveIntegerField(default=0, verbose_name="Успешно доставлено")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name="Статус рассылки"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Telegram-Рассылка"
        verbose_name_plural = "Telegram-Рассылки"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Рассылка '{self.title}' ({self.store.name})"




class CashierProfile(models.Model):
    """Профиль кассира/менеджера для работы в POS-терминале."""
    class Role(models.TextChoices):
        CASHIER = "cashier", "Кассир (только продажи)"
        MANAGER = "manager", "Менеджер (склад и отчеты)"
        OWNER = "owner", "Владелец (все права)"

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="cashiers")
    name = models.CharField(max_length=100, verbose_name="Имя сотрудника")
    pin_code = models.CharField(max_length=4, verbose_name="PIN-код для входа")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CASHIER, verbose_name="Роль")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Сотрудник (Кассир)"
        verbose_name_plural = "Сотрудники (Кассиры)"
        unique_together = ("store", "pin_code")

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"


class CashShift(models.Model):
    """Кассовая смена."""
    class Status(models.TextChoices):
        OPEN = "open", "Открыта"
        CLOSED = "closed", "Закрыта"

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="shifts")
    cashier = models.ForeignKey(CashierProfile, on_delete=models.SET_NULL, null=True, related_name="shifts")
    opened_at = models.DateTimeField(auto_now_add=True, verbose_name="Время открытия")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Время закрытия")
    start_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Наличные на начало")
    closing_cash_actual = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Фактические наличные при закрытии")
    difference = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Разница (Недостача/Излишек)")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, verbose_name="Статус смены")

    class Meta:
        verbose_name = "Кассовая смена"
        verbose_name_plural = "Кассовые смены"
        ordering = ["-opened_at"]

    def __str__(self):
        return f"Смена {self.id} ({self.store.name})"


class StockMovement(models.Model):
    """Движение товаров на складе (Приход, Списание, Возврат)."""
    class MovementType(models.TextChoices):
        RECEIPT = "receipt", "Приход (Закупка)"
        WRITE_OFF = "write_off", "Списание (Брак/Утеря)"
        RETURN = "return", "Возврат от покупателя"
        SALE = "sale", "Продажа"

    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=MovementType.choices, verbose_name="Тип операции")
    quantity = models.IntegerField(verbose_name="Количество (со знаком)")
    author = models.ForeignKey(CashierProfile, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Сотрудник")
    reason = models.CharField(max_length=255, blank=True, verbose_name="Основание/Причина")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Движение товара"
        verbose_name_plural = "Движения товаров"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.quantity} шт. ({self.variant.product.name})"

class ProductReview(models.Model):
    """Отзыв покупателя на товар."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews", verbose_name="Товар")
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviews", verbose_name="Заказ")
    customer_name = models.CharField(max_length=120, default="Покупатель", verbose_name="Имя покупателя")
    customer_phone = models.CharField(max_length=30, blank=True, verbose_name="Телефон покупателя")
    rating = models.PositiveSmallIntegerField(default=5, verbose_name="Оценка (1-5)")
    text = models.TextField(verbose_name="Текст отзыва")
    is_visible = models.BooleanField(default=True, verbose_name="Отображать на сайте")
    seller_reply = models.TextField(blank=True, default="", verbose_name="Ответ продавца")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Отзыв о товаре"
        verbose_name_plural = "Отзывы о товарах"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.rating}★ {self.product.name} ({self.customer_name})"


class PlatformSettings(models.Model):
    """Глобальные настройки маркетплейса BrainMart и привязка супер-админа."""
    superadmin_telegram_chat_id = models.CharField(
        max_length=64, blank=True, null=True,
        verbose_name="Telegram Chat ID Главного Администратора",
        help_text="Сюда приходят уведомления о новых заявках на открытие магазинов",
    )
    superadmin_telegram_username = models.CharField(
        max_length=100, blank=True, default="",
        verbose_name="Telegram Username Администратора",
    )
    superadmin_telegram_link_token = models.CharField(
        max_length=64, unique=True, default=secrets.token_hex,
        help_text="Токен для безопасной привязки Telegram супер-админа через бота",
    )
    notify_on_new_store = models.BooleanField(
        default=True,
        verbose_name="Уведомлять в Telegram о новых заявках магазинов",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки платформы"
        verbose_name_plural = "Настройки платформы"

    def __str__(self):
        return f"Настройки платформы (Admin: @{self.superadmin_telegram_username or 'Не привязан'})"

    @classmethod
    def get_settings(cls):
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj


class ProductWaitlist(models.Model):
    """
    Лист ожидания (Waitlist):
    Фиксирует запросы покупателей в Telegram или Instagram на отсутствующие товары.
    Когда продавец добавляет подходящий товар, система автоматически отправляет
    уведомление клиенту с фото, ценой и персональной ссылкой на оформление заказа.
    """
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает поступления"
        NOTIFIED = "notified", "Уведомлен"
        CANCELLED = "cancelled", "Отменен"

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="waitlist_entries",
        verbose_name="Магазин",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="waitlist_entries",
        verbose_name="Клиент",
    )
    query_text = models.CharField(
        max_length=500,
        verbose_name="Запрос клиента (товар/размер)",
        help_text="Оригинальный текст вопроса клиента (например 'etik bormi 42 razmer')",
    )
    telegram_chat_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name="Telegram Chat ID",
    )
    instagram_scoped_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Instagram Scoped ID",
    )
    language = models.CharField(
        max_length=5,
        default="uz",
        choices=[("uz", "O'zbekcha"), ("ru", "Русский"), ("en", "English")],
        verbose_name="Язык общения",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Статус",
    )
    notified_product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notified_waitlist_entries",
        verbose_name="Поступивший товар",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    notified_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата уведомления")

    class Meta:
        verbose_name = "Лист ожидания"
        verbose_name_plural = "Листы ожидания"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer} ждет '{self.query_text[:30]}' ({self.store.name}) [{self.get_status_display()}]"



