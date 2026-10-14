import os
from celery import Celery

# Устанавливаем модуль настроек Django по умолчанию для Celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'src.settings')

app = Celery('jm_ecommerce')

# Загружаем настройки из объекта settings в Django,
# все настройки Celery должны начинаться с префикса 'CELERY_'
app.config_from_object('django.conf:settings', namespace='CELERY')

# Автоматически находим задачи в файлах tasks.py внутри приложений
app.autodiscover_tasks()
