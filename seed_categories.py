import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'src.settings')
django.setup()

from core.models import Category, Product

# Ровно 8 главных ниш маркетплейса BrainMart
eight_niches = [
    "🚗 Автотовары и шины",
    "👕 Одежда и обувь",
    "🍏 Продукты питания",
    "📱 Электроника и техника",
    "🛋️ Мебель и товары для дома",
    "💄 Красота и здоровье",
    "🧸 Детские товары",
    "⚽ Спорт и отдых"
]

# Создаём или получаем 8 основных категорий
created_objs = {}
for name in eight_niches:
    obj, _ = Category.objects.get_or_create(name=name)
    created_objs[name] = obj

# Если у существующих товаров была привязана старая категория - перепривяжем на подходящую
for prod in Product.objects.all():
    if prod.category and prod.category.name not in eight_niches:
        prod.category = created_objs["👕 Одежда и обувь"]
        prod.save(update_fields=['category'])

# Удаляем все остальные категории (очищаем базу от старых лишних категорий)
Category.objects.exclude(name__in=eight_niches).delete()

print(f"Готово! В базе успешно сохранено ровно {Category.objects.count()} категорий.")
