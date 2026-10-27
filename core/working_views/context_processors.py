"""
context_processors.py
------------------------
Django context processor — функция, результат которой автоматически
добавляется в контекст КАЖДОГО шаблона, без необходимости передавать
вручную в каждом view. Подключается в settings.py -> TEMPLATES -> OPTIONS.
"""

from django.conf import settings


def yandex_maps(request):
    """Делает ключ Яндекс.Карт доступным в любом шаблоне как {{ YANDEX_MAPS_API_KEY }}."""
    return {"YANDEX_MAPS_API_KEY": getattr(settings, "YANDEX_MAPS_API_KEY", "")}
