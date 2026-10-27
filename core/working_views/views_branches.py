import math
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from core.models import StoreBranch


def _parse_coord(val):
    """Безопасный парсинг координат с поддержкой запятых и пробелов."""
    if val is None:
        return None
    val_str = str(val).replace(",", ".").replace(" ", "").strip()
    if not val_str:
        return None
    try:
        return Decimal(val_str)
    except Exception:
        return None


def calculate_distance_km(lat1, lon1, lat2, lon2):
    """
    Вычисляет расстояние между двумя координатами (в км)
    по формуле гаверсинусов (Haversine formula).
    """
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    try:
        lat1_f = float(lat1)
        lon1_f = float(lon1)
        lat2_f = float(lat2)
        lon2_f = float(lon2)
    except (ValueError, TypeError):
        return None

    R = 6371.0  # Радиус Земли в километрах
    phi1 = math.radians(lat1_f)
    phi2 = math.radians(lat2_f)
    delta_phi = math.radians(lat2_f - lat1_f)
    delta_lambda = math.radians(lon2_f - lon1_f)

    try:
        a = (math.sin(delta_phi / 2.0) ** 2 +
             math.cos(phi1) * math.cos(phi2) *
             math.sin(delta_lambda / 2.0) ** 2)
        a = max(0.0, min(1.0, a))
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return round(R * c, 1)
    except Exception:
        return None


def find_nearest_branch(store, delivery_latitude=None, delivery_longitude=None, delivery_address=""):
    """
    Находит наиболее близкий филиал (точку) магазина к покупателю:
    1. Если у покупателя есть GPS-координаты (delivery_latitude, delivery_longitude),
       вычисляет расстояние по формуле гаверсинусов ко всем филиалам магазина
       и выбирает филиал с минимальным расстоянием.
    2. Если GPS нет, ищет совпадение по названиям районов Ташкента в адресе клиента
       с адресом/названием филиалов магазина.
    3. Если совпадений нет, возвращает базовый/первый филиал магазина.
    Возвращает: (nearest_branch, distance_km)
    """
    if not store:
        return None, None

    # Получаем все активные филиалы магазина
    branches = list(store.branches.filter(is_active=True).order_by("id"))
    if not branches:
        # Автоматически создаем базовую точку, если у магазина ещё нет ни одной
        base_branch = StoreBranch.objects.create(
            store=store,
            name=f"Основная точка ({store.name})",
            address=store.address or "Ташкент (Главный филиал)",
            latitude=store.latitude,
            longitude=store.longitude,
            is_active=True,
        )
        branches = [base_branch]

    if len(branches) == 1:
        single_branch = branches[0]
        dist = None
        if delivery_latitude and delivery_longitude and single_branch.latitude and single_branch.longitude:
            dist = calculate_distance_km(delivery_latitude, delivery_longitude, single_branch.latitude, single_branch.longitude)
        return single_branch, dist

    # Сценарий 1: Расчёт по GPS координатам
    if delivery_latitude and delivery_longitude:
        best_branch = None
        min_dist = float("inf")

        for b in branches:
            if b.latitude and b.longitude:
                d = calculate_distance_km(delivery_latitude, delivery_longitude, b.latitude, b.longitude)
                if d is not None and d < min_dist:
                    min_dist = d
                    best_branch = b

        if best_branch is not None:
            return best_branch, min_dist

    # Сценарий 2: Поиск по текстовому адресу и ключевым районам Ташкента
    addr_lower = (delivery_address or "").lower()
    if addr_lower:
        districts = [
            "юнусабад", "yunusobod", "чиланзар", "chilonzor",
            "мирзо улугбек", "мирзо-улугбек", "mirzo", "ulug'bek",
            "яккасарай", "yakkasaroy", "мирабад", "mirobod",
            "шайхантахур", "shayxontohur", "алмазар", "olmazor",
            "учтепа", "uchtepa", "сергели", "sergeli",
            "янгихаёт", "yangihayot", "яшнабад", "yashnobod",
            "бектемир", "bektemir", "куйлюк", "qo'yliq"
        ]
        for dist_word in districts:
            if dist_word in addr_lower:
                for b in branches:
                    b_haystack = f"{b.name} {b.address}".lower()
                    if dist_word in b_haystack:
                        return b, None

    # Сценарий 3: Возвращаем первый/основной филиал
    return (branches[0], None) if branches else (None, None)


@login_required
def seller_branches_view(request):
    """
    Управление точками/филиалами магазина:
    - Базовая 1 точка входит в тариф (50 000 сум/мес).
    - Каждая дополнительная точка: +20 000 сум/мес.
    - Выбор точки на карте Google Maps, адрес, редактирование.
    """
    if not hasattr(request.user, "store") or not request.user.store:
        return redirect("seller-register")
    store = request.user.store

    # Если у магазина еще нет ни одной точки, создаем базовую точку
    if store.branches.count() == 0:
        StoreBranch.objects.create(
            store=store,
            name=f"Основная точка ({store.name})",
            address=store.address or "Ташкент (Главный магазин)",
            latitude=store.latitude,
            longitude=store.longitude,
            is_active=True
        )

    if request.method == "POST":
        action = request.POST.get("action", "save")

        if action in ("add", "edit", "save"):
            branch_id = request.POST.get("branch_id", "").strip()
            name = request.POST.get("name", "").strip()
            address = request.POST.get("address", "").strip()
            lat = request.POST.get("latitude", "").strip()
            lon = request.POST.get("longitude", "").strip()

            lat_d = _parse_coord(lat)
            lon_d = _parse_coord(lon)

            if not name:
                name = f"Точка «{store.name}»"
            if not address:
                address = "Ташкент"

            if branch_id and branch_id.isdigit():
                branch = get_object_or_404(StoreBranch, id=int(branch_id), store=store)
                branch.name = name
                branch.address = address
                branch.latitude = lat_d
                branch.longitude = lon_d
                branch.save()
                messages.success(request, f"✅ Локация точки «{name}» успешно сохранена!")
            else:
                branch = StoreBranch.objects.create(
                    store=store,
                    name=name,
                    address=address,
                    latitude=lat_d,
                    longitude=lon_d,
                    is_active=True
                )
                messages.success(request, f"✅ Новая точка «{name}» успешно добавлена на карту!")

            # Если это первая или основная точка, обновляем и в самом магазине
            if store.branches.count() == 1 or "основная" in branch.name.lower():
                store.latitude = lat_d
                store.longitude = lon_d
                store.address = address
                store.save(update_fields=["latitude", "longitude", "address"])

        elif action == "delete":
            branch_id = request.POST.get("branch_id")
            if branch_id and branch_id.isdigit():
                branch = get_object_or_404(StoreBranch, id=int(branch_id), store=store)
                if store.branches.count() <= 1:
                    messages.warning(request, "Нельзя удалить единственную точку магазина. Вы можете изменить её адрес и координаты.")
                else:
                    b_name = branch.name
                    branch.delete()
                    messages.success(request, f"Точка «{b_name}» удалена.")

        return redirect("seller-branches")

    branches = store.branches.all().order_by("id")
    branches_count = branches.count()
    base_fee = 50000
    extra_branch_price = 20000
    extra_count = max(0, branches_count - 1)
    extra_fee = extra_count * extra_branch_price
    total_monthly_fee = base_fee + extra_fee

    context = {
        "store": store,
        "branches": branches,
        "branches_count": branches_count,
        "base_fee": base_fee,
        "extra_count": extra_count,
        "extra_fee": extra_fee,
        "total_monthly_fee": total_monthly_fee,
    }
    return render(request, "dashboard/branches.html", context)
