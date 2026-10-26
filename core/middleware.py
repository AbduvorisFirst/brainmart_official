# -*- coding: utf-8 -*-
"""
core/middleware.py
------------------
Обеспечивает 100% стабильную работу сессий, кук и авторизации:
1. DynamicCookieDomainMiddleware — гарантирует Host-Only куки (domain=None) с SameSite=None; Secure
   для бесшовной работы внутри Telegram Mini App, iOS Safari (ITP) и всех браузеров.
   Устраняет бесконечный редирект на /dashboard/login/.
2. TelegramAuthMiddleware — мгновенная 1-клик авторизация по ссылке бота (?tg_auth=<token>).
"""

import logging
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from core.models import Store, StoreManager

logger = logging.getLogger(__name__)


class TelegramAuthMiddleware:
    """
    Авторизует пользователя при переходе по ссылке из Telegram с параметром ?tg_auth=<token>.
    Выполняется ДО вызова view, благодаря чему @login_required пропускает пользователя сразу.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tg_auth = request.GET.get("tg_auth")
        if tg_auth and not request.user.is_authenticated:
            try:
                # 1. Поиск магазина по telegram_link_token
                store = Store.objects.filter(telegram_link_token=tg_auth).select_related("owner").first()
                if store and store.owner:
                    login(request, store.owner)
                    request.session.save()
                    logger.info("Успешный вход по tg_auth для магазина '%s' (user: %s)", store.name, store.owner.username)
                else:
                    # 2. Поиск суперпользователя платформы
                    from core.models import PlatformSettings
                    ps = PlatformSettings.objects.filter(superadmin_telegram_link_token=tg_auth).first()
                    if ps:
                        super_user = User.objects.filter(is_superuser=True).first()
                        if super_user:
                            login(request, super_user)
                            request.session.save()
                            logger.info("Успешный вход суперпользователя по superadmin token (user: %s)", super_user.username)
                    else:
                        # 3. Поиск менеджера магазина
                        manager = StoreManager.objects.filter(telegram_link_token=tg_auth).select_related("store", "store__owner").first()
                        if manager and manager.store and manager.store.owner:
                            login(request, manager.store.owner)
                            request.session.save()
                            logger.info("Успешный вход менеджера по tg_auth для магазина '%s'", manager.store.name)
            except Exception as e:
                logger.warning("Ошибка авторизации по tg_auth: %s", e)

        return self.get_response(request)


class DynamicCookieDomainMiddleware:
    """
    Внешний слой для обработки HTTP-ответа (Outermost middleware):
    1. Перехватывает все Set-Cookie заголовки (sessionid, csrftoken, messages).
    2. Удаляет Domain attribute (Host-Only cookie) — это защищает куки от блокировки
       Apple Intelligent Tracking Prevention (ITP) в Telegram Mini App и Safari.
    3. При HTTPS принудительно устанавливает SameSite="None" и Secure=True,
       чтобы Telegram Mini App (embedded WebView / iframe) отправлял куки сессии на редиректах.
    4. При HTTP (локальная разработка) выставляет SameSite="Lax" и Secure=False.
    5. Добавляет Content-Security-Policy для разрешения встраивания в Telegram WebApp.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Определяем, работает ли запрос по HTTPS
        is_https = (
            request.is_secure()
            or request.headers.get("x-forwarded-proto", "").lower() == "https"
            or (not getattr(settings, "DEBUG", True) and getattr(settings, "SITE_URL", "").startswith("https"))
        )

        try:
            # Обрабатываем все установленные куки
            for cookie_name, morsel in list(response.cookies.items()):
                # Удаляем явный domain attribute -> браузер трактует куку как Host-Only
                # (Host-Only куки никогда не блокируются Safari ITP)
                if "domain" in morsel:
                    del morsel["domain"]

                # Выставляем SameSite и Secure в зависимости от HTTPS
                if is_https:
                    morsel["samesite"] = "None"
                    morsel["secure"] = True
                else:
                    morsel["samesite"] = "Lax"
                    morsel["secure"] = False
        except Exception as e:
            logger.debug("Ошибка модификации кук в DynamicCookieDomainMiddleware: %s", e)

        # Разрешаем отображение страниц внутри Telegram WebApp (iOS / Android / Desktop)
        try:
            csp_frame = "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org https://*.t.me https://t.me"
            if "Content-Security-Policy" in response.headers:
                csp_val = response.headers["Content-Security-Policy"]
                if "frame-ancestors" not in csp_val:
                    response.headers["Content-Security-Policy"] = f"{csp_val}; {csp_frame}"
            else:
                response.headers["Content-Security-Policy"] = csp_frame

            # Убираем строгий X-Frame-Options, если он блокирует Telegram WebApp
            if response.has_header("X-Frame-Options"):
                del response["X-Frame-Options"]
        except Exception:
            pass

        return response
