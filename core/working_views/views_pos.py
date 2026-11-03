import json
import logging
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from core.models import Store, CashierProfile, CashShift, ProductVariant, Order, OrderItem, StockMovement, Customer

logger = logging.getLogger(__name__)

def get_current_cashier(request):
    """Возвращает текущего кассира из сессии, если он авторизован в POS."""
    cashier_id = request.session.get('pos_cashier_id')
    if not cashier_id:
        return None
    try:
        return CashierProfile.objects.get(id=cashier_id, is_active=True)
    except CashierProfile.DoesNotExist:
        return None

def pos_home(request):
    """POS терминал временно в разработке."""
    return render(request, 'pos/under_development.html')

def pos_login(request):
    """Страница входа POS — временно в разработке."""
    return render(request, 'pos/under_development.html')

def pos_logout(request):
    if 'pos_cashier_id' in request.session:
        del request.session['pos_cashier_id']
    return redirect('pos:pos_login')

def pos_terminal(request):
    """Основной интерфейс терминала кассира — временно в разработке."""
    return render(request, 'pos/under_development.html')
        
    store = cashier.store
    # Проверяем, открыта ли смена
    current_shift = CashShift.objects.filter(store=store, cashier=cashier, status=CashShift.Status.OPEN).first()
    
    return render(request, 'pos/terminal.html', {
        'cashier': cashier,
        'store': store,
        'current_shift': current_shift,
    })

def api_search_products(request):
    """Поиск товаров по штрихкоду, SKU или названию (AJAX)."""
    cashier = get_current_cashier(request)
    if not cashier:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': []})
        
    # Ищем варианты товара для этого магазина
    variants = ProductVariant.objects.filter(product__store=cashier.store)
    
    # Сначала ищем точное совпадение по штрихкоду
    exact_barcode = variants.filter(barcode=query)
    if exact_barcode.exists():
        variants = exact_barcode
    else:
        # Иначе ищем по названию продукта или SKU
        variants = variants.filter(product__name__icontains=query) | variants.filter(sku__icontains=query)
        
    variants = variants.select_related('product')[:20]
    
    results = []
    for v in variants:
        results.append({
            'id': v.id,
            'name': v.product.name,
            'color': v.color,
            'size': v.size,
            'barcode': v.barcode,
            'sku': v.sku,
            'price': float(v.final_price),
            'stock': v.stock_quantity,
        })
        
    return JsonResponse({'results': results})

@require_POST
def api_checkout(request):
    """Оформление продажи (AJAX)."""
    cashier = get_current_cashier(request)
    if not cashier:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    current_shift = CashShift.objects.filter(store=cashier.store, cashier=cashier, status=CashShift.Status.OPEN).first()
    if not current_shift:
        return JsonResponse({'error': 'Смена не открыта'}, status=400)
        
    try:
        data = json.loads(request.body)
        items = data.get('items', [])
        payment_method = data.get('payment_method', 'cash') # cash, card
        
        if not items or not isinstance(items, list):
            return JsonResponse({'error': 'Корзина пуста'}, status=400)
            
        with transaction.atomic():
            # Создаем или получаем Dummy клиента для офлайн продаж
            offline_customer, _ = Customer.objects.get_or_create(
                instagram_scoped_id=f"pos_offline_{cashier.store.id}",
                defaults={'full_name': 'Покупатель (Офлайн)'}
            )
                
            # Создаем "офлайн" заказ для учета
            order = Order.objects.create(
                store=cashier.store,
                customer=offline_customer,
                status=Order.Status.DELIVERED, # Оффлайн покупка сразу доставлена
                payment_method=payment_method,
                total_price=0
            )
            
            total = 0
            for item in items:
                variant_id = item.get('id')
                try:
                    qty = int(item.get('qty', 1))
                except (ValueError, TypeError):
                    transaction.set_rollback(True)
                    return JsonResponse({'error': 'Некорректное количество товара'}, status=400)
                if qty <= 0:
                    transaction.set_rollback(True)
                    return JsonResponse({'error': 'Количество товара должно быть больше 0'}, status=400)
                
                try:
                    variant = ProductVariant.objects.select_for_update().get(id=variant_id, product__store=cashier.store)
                except (ProductVariant.DoesNotExist, ValueError, TypeError):
                    transaction.set_rollback(True)
                    return JsonResponse({'error': 'Товар не найден'}, status=404)

                if variant.stock_quantity < qty:
                    transaction.set_rollback(True)
                    return JsonResponse({
                        'error': f'Недостаточно товара «{variant.product.name}» на складе (доступно: {variant.stock_quantity} шт.)'
                    }, status=400)

                price = variant.final_price
                
                OrderItem.objects.create(
                    order=order,
                    variant=variant,
                    quantity=qty,
                    price_at_purchase=price
                )
                
                total += price * qty
                
                # Списание остатков
                variant.stock_quantity -= qty
                variant.save(update_fields=['stock_quantity'])
                
                # Запись движения
                StockMovement.objects.create(
                    variant=variant,
                    movement_type=StockMovement.MovementType.SALE,
                    quantity=-qty,
                    author=cashier,
                    reason=f"Продажа, чек #{order.id}"
                )
                
            order.recalculate_total()
        
        return JsonResponse({'success': True, 'order_id': order.id, 'total': float(order.total_price)})
        
    except Exception as e:
        logger.exception("POS checkout error: %s", e)
        return JsonResponse({'error': 'Внутренняя ошибка при оформлении продажи.'}, status=500)

@require_POST
def api_shift_open(request):
    """Открытие смены (AJAX)."""
    cashier = get_current_cashier(request)
    if not cashier:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    data = json.loads(request.body)
    start_cash = data.get('start_cash', 0)
    
    # Проверяем, нет ли уже открытой смены
    existing = CashShift.objects.filter(store=cashier.store, status=CashShift.Status.OPEN).first()
    if existing:
        return JsonResponse({'error': f'Смена уже открыта сотрудником {existing.cashier.name}'}, status=400)
        
    shift = CashShift.objects.create(
        store=cashier.store,
        cashier=cashier,
        start_cash=start_cash,
        status=CashShift.Status.OPEN
    )
    
    return JsonResponse({'success': True, 'shift_id': shift.id})

@require_POST
def api_shift_close(request):
    """Закрытие смены (AJAX)."""
    cashier = get_current_cashier(request)
    if not cashier:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    data = json.loads(request.body)
    closing_cash_actual = data.get('closing_cash_actual', 0)
    
    shift = CashShift.objects.filter(store=cashier.store, cashier=cashier, status=CashShift.Status.OPEN).first()
    if not shift:
        return JsonResponse({'error': 'У вас нет открытой смены'}, status=400)
        
    # Рассчет ожидаемой суммы (упрощенно)
    # Нужно бы посчитать сумму продаж за смену (где метод cash) + start_cash
    orders_cash = Order.objects.filter(
        store=cashier.store, 
        created_at__gte=shift.opened_at,
        payment_method='cash',
        status=Order.Status.DELIVERED
    )
    total_sales_cash = sum(o.total_price for o in orders_cash)
    expected_cash = float(shift.start_cash) + float(total_sales_cash)
    
    difference = float(closing_cash_actual) - expected_cash
    
    shift.closed_at = timezone.now()
    shift.closing_cash_actual = closing_cash_actual
    shift.difference = difference
    shift.status = CashShift.Status.CLOSED
    shift.save()
    
    return JsonResponse({'success': True, 'difference': difference, 'expected': expected_cash})
