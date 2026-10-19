"""
Django settings for src project.

Изменения по сравнению с исходным файлом:
1. Добавлена загрузка .env через python-dotenv — все секреты (ключи, токены)
   теперь читаются из переменных окружения, а не хранятся в коде.
2. В INSTALLED_APPS добавлено приложение 'core' с полным путём.
3. Добавлены GEMINI_API_KEY, META_PAGE_ACCESS_TOKEN, META_WEBHOOK_VERIFY_TOKEN —
   их читает services.py через getattr(settings, ...).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Загружаем переменные из файла .env, который лежит рядом с manage.py
load_dotenv(BASE_DIR / ".env")


# --- Секреты и debug теперь читаются из .env, а не хранятся в коде ---
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "django-insecure-only-for-local-dev"
)
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"

_allowed_hosts_env = os.environ.get("DJANGO_ALLOWED_HOSTS", "").strip()
if _allowed_hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_env.split(",") if h.strip()]
elif DEBUG:
    ALLOWED_HOSTS = ["*"]
else:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "brainmart.uz", "www.brainmart.uz", ".brainmart.uz"]

# Всегда гарантируем поддомены brainmart.uz и локальную разработку, даже если DJANGO_ALLOWED_HOSTS переопределен в .env:
for _h in [".brainmart.uz", "brainmart.uz", "www.brainmart.uz", ".localhost", "localhost", "127.0.0.1"]:
    if _h not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_h)

SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "brainmart.uz")



# --- Наши кастомные настройки для ИИ-агента и Instagram ---
# services.py читает их через django.conf.settings
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
META_PAGE_ACCESS_TOKEN = os.environ.get("META_PAGE_ACCESS_TOKEN")
META_WEBHOOK_VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN")

# --- Click.uz (приём оплаты) ---
CLICK_MERCHANT_ID = os.environ.get("CLICK_MERCHANT_ID")
CLICK_SERVICE_ID = os.environ.get("CLICK_SERVICE_ID")
CLICK_SECRET_KEY = os.environ.get("CLICK_SECRET_KEY")

# Публичный адрес сайта — нужен, чтобы построить return_url для Click
# (адрес, куда Click вернёт клиента после оплаты). Для локальной разработки
# через ngrok сюда подставляй текущий https-адрес ngrok.
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000")

# --- Telegram Mini App ---
# Бот не хранит свою логику/данные — он просто открывает наш сайт
# внутри Telegram (см. core/telegram_bot.py)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "")

# --- БЕЗОПАСНОСТЬ: Секретный токен для верификации Telegram-вебхука ---
# При вызове setWebhook передаём secret_token — Telegram присылает его
# в заголовке X-Telegram-Bot-Api-Secret-Token. Если токен не совпадает —
# запрос отклоняется (защита от подделки вебхуков).
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

# --- БЕЗОПАСНОСТЬ: Meta App Secret для проверки подписи Instagram-вебхуков ---
# Meta подписывает каждый webhook-запрос HMAC-SHA256 с App Secret.
# Мы проверяем заголовок X-Hub-Signature-256, чтобы убедиться, что
# запрос пришёл от Meta, а не от хакера.
META_APP_SECRET = os.environ.get("META_APP_SECRET", "")

# --- Яндекс.Карты (выбор адреса доставки кликом на карте) ---
YANDEX_MAPS_API_KEY = os.environ.get("YANDEX_MAPS_API_KEY", "")

# --- BrainMart Marketplace Billing (Оплата подписок продавцами) ---
BRAINMART_BILLING_CARD = os.environ.get("BRAINMART_BILLING_CARD", "6262 4700 9041 1583")
BRAINMART_BILLING_HOLDER = os.environ.get("BRAINMART_BILLING_HOLDER", "Munisa Yakubova")

# --- Вход в панель продавца ---
# Без этого блока Django ищет несуществующие /accounts/login/ и
# /accounts/profile/ — вход в /dashboard/ будет выдавать 404.
LOGIN_URL = "seller-login"
LOGIN_REDIRECT_URL = "seller-dashboard"
LOGOUT_REDIRECT_URL = "seller-login"


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'core.apps.CoreConfig',  # полный путь надёжнее, чем просто 'core'
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # LocaleMiddleware ДОЛЖЕН стоять после SessionMiddleware и до CommonMiddleware —
    # именно он определяет, на каком языке показывать сайт (из сессии/cookie/браузера)
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'src.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.template.context_processors.i18n',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.working_views.context_processors.yandex_maps',
            ],
        },
    },
]

WSGI_APPLICATION = 'src.wsgi.application'


# Database
DB_ENGINE = os.environ.get("DB_ENGINE", "django.db.backends.sqlite3")
if DB_ENGINE == "django.db.backends.postgresql":
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'brainmart'),
            'USER': os.environ.get('DB_USER', 'brainmart_user'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '5432'),
            'CONN_MAX_AGE': 600,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


LANGUAGE_CODE = 'ru'
TIME_ZONE = 'Asia/Tashkent'  # поставил твой часовой пояс — важно для created_at заказов
USE_I18N = True
USE_TZ = True

# --- Мультиязычность сайта (переключатель узб/рус/анг вручную) ---
# Список языков, которые будут доступны в переключателе на сайте.
# Код языка слева ("uz", "ru", "en") — стандарт ISO, справа — название для показа человеку.
LANGUAGES = [
    ("ru", "Русский"),
    ("uz", "O'zbekcha"),
    ("en", "English"),
]

# Папка, где будут лежать переводы всех текстов сайта (кнопки, заголовки и т.д.)
LOCALE_PATHS = [BASE_DIR / "locale"]


STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- Логирование ---
# Нужно, чтобы logger.info(...) в views.py (вебхук Instagram) реально
# выводился в терминал во время разработки — без этого logger молчит.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}


_csrf_origins = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
if SITE_URL and SITE_URL.startswith("http"):
    _csrf_origins.append(SITE_URL.rstrip("/"))
    if SITE_URL.startswith("https://") and not SITE_URL.startswith("https://www."):
        _csrf_origins.append(SITE_URL.replace("https://", "https://www.", 1).rstrip("/"))

CSRF_TRUSTED_ORIGINS = list(dict.fromkeys([
    "https://brainmart.uz",
    "https://www.brainmart.uz",
    "https://*.brainmart.uz",
    "http://brainmart.uz",
    "http://www.brainmart.uz",
    "http://*.brainmart.uz",
    "https://recapture-junction-unplowed.ngrok-free.dev",
    *_csrf_origins,
]))

# --- Celery & Redis Settings ---
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
# По умолчанию выполняем задачи сразу, чтобы система работала стабильно без отдельного Celery воркера
CELERY_TASK_ALWAYS_EAGER = os.environ.get('CELERY_TASK_ALWAYS_EAGER', 'True') == 'True'


# ===========================================================================
# БЕЗОПАСНОСТЬ ДЛЯ ПРОДАКШЕНА
# ===========================================================================
# Эти настройки активируются автоматически когда DEBUG=False (т.е. на сервере).
# Во время разработки (DEBUG=True) они НЕ мешают работе.

if not DEBUG:
    is_https = SITE_URL.startswith("https://")
    # Куки сессии передаются ТОЛЬКО через HTTPS — хакер не перехватит в открытом Wi-Fi
    SESSION_COOKIE_SECURE = is_https
    # CSRF-куки тоже только через HTTPS
    CSRF_COOKIE_SECURE = is_https
    # Защита от кражи сессии через JavaScript (XSS)
    SESSION_COOKIE_HTTPONLY = True
    # Браузер не отправит куки на сторонние сайты (защита от CSRF-атак)
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"
    # Заголовок X-Content-Type-Options: nosniff — браузер не будет «угадывать» тип файла
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # Перенаправлять HTTP → HTTPS автоматически только если сайт работает по HTTPS
    SECURE_SSL_REDIRECT = is_https
    if is_https:
        # HSTS — браузер запомнит, что сайт работает только по HTTPS (на 1 год)
        SECURE_HSTS_SECONDS = 31536000
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_HSTS_PRELOAD = True
    # Для Telegram Mini App и встраивания витрины разрешаем фреймы (полный DENY ломает web.telegram.org)
    X_FRAME_OPTIONS = "SAMEORIGIN"

    # Единая сессия на всех поддоменах: продавец входит один раз и видит свой кабинет на любом поддомене *.brainmart.uz
    site_domain = os.getenv("SITE_DOMAIN", "brainmart.uz").strip().lower()
    if site_domain and not site_domain.startswith("localhost") and not site_domain.startswith("127.0.0.1"):
        SESSION_COOKIE_DOMAIN = f".{site_domain}"
        CSRF_COOKIE_DOMAIN = f".{site_domain}"

    # --- REST Framework: отключаем browsable API на проде ---
    REST_FRAMEWORK = {
        'DEFAULT_RENDERER_CLASSES': [
            'rest_framework.renderers.JSONRenderer',
        ],
    }
