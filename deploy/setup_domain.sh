#!/bin/bash
set -e

echo "=== 1. Обновляем конфигурацию Nginx ==="
cp /var/www/brainmart/deploy/nginx_brainmart.conf /etc/nginx/sites-available/brainmart
ln -sf /etc/nginx/sites-available/brainmart /etc/nginx/sites-enabled/brainmart
nginx -t
systemctl reload nginx

echo "=== 2. Выпускаем бесплатный SSL-сертификат (HTTPS) через Certbot ==="
certbot --nginx -d brainmart.uz -d www.brainmart.uz --non-interactive --agree-tos -m abdvoris2bbc@gmail.com --redirect

echo "=== 3. Переводим сайт в .env на https://brainmart.uz ==="
sed -i 's|SITE_URL=.*|SITE_URL=https://brainmart.uz|g' /var/www/brainmart/.env
sed -i 's|DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=brainmart.uz,www.brainmart.uz,169.58.175.117,localhost|g' /var/www/brainmart/.env
sed -i 's|DJANGO_CSRF_TRUSTED_ORIGINS=.*|DJANGO_CSRF_TRUSTED_ORIGINS=https://brainmart.uz,https://www.brainmart.uz,http://169.58.175.117|g' /var/www/brainmart/.env

echo "=== 4. Перезапускаем Gunicorn и Celery ==="
systemctl daemon-reload
systemctl restart brainmart brainmart_celery nginx

echo "=== 5. Регистрируем Telegram Webhook ==="
/var/www/brainmart/venv/bin/python /var/www/brainmart/set_telegram_webhook.py

echo ""
echo "🎉 ВСЁ ГОТОВО! Домен https://brainmart.uz активен, SSL замочек работает, Telegram-бот подключен!"
