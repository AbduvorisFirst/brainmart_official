"""
serializers.py
---------------
Сериализаторы превращают объекты Django (models) в JSON и обратно.
Именно их использует DRF (Django REST Framework), чтобы отдавать
данные на фронтенд маркетплейса.
"""

from rest_framework import serializers
from core.models import (
    Store,
    Category,
    Product,
    ProductVariant,
    Customer,
    Order,
    OrderItem,
)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name"]


class StoreSerializer(serializers.ModelSerializer):
    """Публичная информация о магазине (для карточки товара на сайте)."""

    class Meta:
        model = Store
        fields = ["id", "name", "instagram_username", "is_active"]


class ProductVariantSerializer(serializers.ModelSerializer):
    # final_price и in_stock — это @property в модели, а не поля БД,
    # поэтому явно указываем их как read_only через SerializerMethodField-like поля
    final_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    in_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "color",
            "size",
            "sku",
            "stock_quantity",
            "final_price",
            "in_stock",
        ]


class ProductListSerializer(serializers.ModelSerializer):
    """Короткая версия товара — используется в списках (каталог)."""
    store = StoreSerializer(read_only=True)

    class Meta:
        model = Product
        fields = ["id", "name", "base_price", "main_image", "store"]


class ProductDetailSerializer(serializers.ModelSerializer):
    """Полная версия товара с вариантами — для страницы товара."""
    store = StoreSerializer(read_only=True)
    category = CategorySerializer(read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "base_price",
            "main_image",
            "store",
            "category",
            "variants",
        ]


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "full_name", "phone_number", "delivery_address"]


class OrderItemSerializer(serializers.ModelSerializer):
    variant_label = serializers.CharField(source="variant.__str__", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "variant", "variant_label", "quantity", "price_at_purchase"]
        read_only_fields = ["price_at_purchase"]


class OrderSerializer(serializers.ModelSerializer):
    """
    Сериализатор для создания и просмотра заказа.
    items передаются вложенным списком при создании заказа с сайта.
    """
    items = OrderItemSerializer(many=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "store",
            "customer",
            "status",
            "delivery_address",
            "total_price",
            "payment_method",
            "items",
            "created_at",
        ]
        read_only_fields = ["status", "total_price", "created_at"]

    def create(self, validated_data):
        """
        Создаёт заказ вместе со всеми позициями заказа одним запросом.
        Цену берём из variant.final_price на текущий момент —
        так клиент не сможет подделать цену через запрос.
        """
        items_data = validated_data.pop("items")
        order = Order.objects.create(**validated_data)

        for item_data in items_data:
            variant = item_data["variant"]
            OrderItem.objects.create(
                order=order,
                variant=variant,
                quantity=item_data["quantity"],
                price_at_purchase=variant.final_price,
            )

        order.recalculate_total()
        return order