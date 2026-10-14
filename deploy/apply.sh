#!/bin/bash
set -e

echo "=== 1. Копируем конфигурацию Gunicorn ==="
cp /var/www/brainmart/deploy/brainmart.service /etc/systemd/system/brainmart.service

echo "=== 2. Копируем конфигурацию Nginx ==="
cp /var/www/brainmart/deploy/nginx_brainmart.conf /etc/nginx/sites-available/brainmart
ln -sf /etc/nginx/sites-available/brainmart /etc/nginx/sites-enabled/brainmart
rm -f /etc/nginx/sites-enabled/default

echo "=== 3. Создаем папки логов и медиа ==="
mkdir -p /var/log/gunicorn /var/log/celery /var/www/brainmart/media /var/www/brainmart/staticfiles

echo "=== 4. Перезапускаем службы ==="
systemctl daemon-reload
systemctl restart brainmart nginx

echo "=== 5. Проверяем ответ сайта ==="
sleep 2
curl -I http://127.0.0.1/
echo ""
echo "=== УСПЕХ! САЙТ РАБОТАЕТ! ==="
