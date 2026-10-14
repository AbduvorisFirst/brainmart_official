# Template writer script
import os

order_success = '''{% extends "core/base.html" %}
{% block content %}
<div style="text-align:center;padding:60px 24px;">
    <div style="font-size:80px;margin-bottom:24px;">🎉</div>
    <h1 style="font-size:26px;font-weight:800;color:white;margin:0 0 12px;">Оплата прошла!</h1>
    <p style="font-size:15px;color:#9CA3AF;margin:0 0 32px;">Заказ #{{ order.id }} оплачен.</p>
    <a href="/" style="display:inline-block;background:#4F46E5;color:white;padding:16px 32px;border-radius:14px;text-decoration:none;font-weight:700;">← В каталог</a>
</div>
{% endblock %}
'''

with open(r'c:\Users\Hp\OneDrive\Desktop\jm_ecommerce\templates\core\order_success.html', 'w', encoding='utf-8') as f:
    f.write(order_success)
print('Done!')