"""
set_telegram_webhook.py
-----------------------
Скрипт для безопасной установки Telegram-вебхука с секретным токеном.

Использование:
    python set_telegram_webhook.py

Он прочитает TELEGRAM_BOT_TOKEN, SITE_URL и TELEGRAM_WEBHOOK_SECRET из .env
и зарегистрирует вебхук с secret_token — чтобы хакеры не могли
подделывать запросы к /api/telegram/webhook/.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import requests

# Загружаем .env
load_dotenv(Path(__file__).resolve().parent / ".env")

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
site_url = os.environ.get("SITE_URL", "")
secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

if not token or not site_url:
    print("❌ Ошибка: TELEGRAM_BOT_TOKEN и SITE_URL должны быть указаны в .env")
    sys.exit(1)

webhook_url = f"{site_url}/api/telegram/webhook/"

payload = {"url": webhook_url}
if secret:
    payload["secret_token"] = secret
    print(f"🔒 Устанавливаем вебхук с секретным токеном...")
else:
    print(f"⚠️  TELEGRAM_WEBHOOK_SECRET не указан — вебхук будет без защиты!")

response = requests.post(
    f"https://api.telegram.org/bot{token}/setWebhook",
    json=payload,
)

result = response.json()
if result.get("ok"):
    print(f"✅ Вебхук успешно установлен: {webhook_url}")
    if secret:
        print(f"🔒 Secret token: активен")
else:
    print(f"❌ Ошибка: {result}")
    sys.exit(1)

# Проверяем
info = requests.get(f"https://api.telegram.org/bot{token}/getWebhookInfo").json()
print(f"\n📋 Текущий вебхук: {info.get('result', {}).get('url', 'НЕ УСТАНОВЛЕН')}")
print(f"   Has secret token: {info.get('result', {}).get('has_custom_certificate', 'N/A')}")
