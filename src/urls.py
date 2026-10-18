"""
src/urls.py — главный роутер проекта.

Важное разделение:
- /api/...       — REST API для ИИ, вебхука Instagram, будущего мобильного приложения.
                    Языкового префикса НЕ имеет, т.к. это не для людей, а для программ.
- /ru/, /uz/, /en/... — обычные страницы сайта (каталог, товар, заказ).
                    Обёрнуты в i18n_patterns — Django сам добавляет префикс языка.
- /i18n/setlang/  — служебный адрес, на который отправляется форма переключения языка
                    (встроенный view Django django.views.i18n.set_language)
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
from django.views.generic import RedirectView

urlpatterns = [
    # Favicon для браузеров
    path('favicon.ico', RedirectView.as_view(url='/static/favicon.ico', permanent=True)),
    # Защищенная панель администратора Django (скрытый URL, защита от сканеров)
    path('brainmart-secure-admin-panel/', admin.site.urls),
    path('api/', include('core.urls.urls')),
    # Панель продавца — без языкового префикса, на русском
    path('dashboard/', include('core.urls.urls_dashboard')),
    # Административная панель платформы (для staff / superuser)
    path('platform-admin/', include('core.urls.urls_platform_admin')),
    # POS-терминал для кассиров
    path('pos/', include('core.urls.urls_pos')),
    # set_language — встроенный view Django, который меняет язык и кладёт его в сессию
    path('i18n/', include('django.conf.urls.i18n')),
]

# Все "человеческие" страницы сайта оборачиваем в i18n_patterns —
# Django будет сам добавлять префикс языка: /ru/catalog/, /uz/catalog/, /en/catalog/
urlpatterns += i18n_patterns(
    path('', include('core.urls.urls_web')),
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)