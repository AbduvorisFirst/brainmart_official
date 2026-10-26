# -*- coding: utf-8 -*-
import logging
from django.conf import settings
from django.contrib.auth import login
from core.models import Store

logger = logging.getLogger(__name__)


class DynamicCookieDomainMiddleware:
    """
    Гарантирует, что куки сессии и CSRF принимаются браузером на ЛЮБОМ домене:
    1. Если запрос пришёл с ?tg_auth=<token> — моментально авторизует владельца магазина без ввода пароля.
    2. Если запрос пришёл на *.brainmart.uz — куки работают на всех поддоменах.
    3. Если запрос пришёл по прямому IP сервера (например, 45.9.230.5) или localhost/ngrok —
       убирает domain=.brainmart.uz, чтобы браузер смартфона не отклонял куки сессии!
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. Мгновенная авторизация из Telegram WebApp (?tg_auth=<token>)
        tg_auth = request.GET.get("tg_auth")
        if tg_auth and not request.user.is_authenticated:
            try:
                store = Store.objects.filter(telegram_link_token=tg_auth).select_related("owner").first()
                if store and store.owner:
                    login(request, store.owner)
                    request.session.save()
                    logger.info("Успешный вход по tg_auth для магазина '%s' (user: %s)", store.name, store.owner.username)
            except Exception as e:
                logger.warning("Ошибка авторизации по tg_auth: %s", e)

        response = self.get_response(request)

        # 2. Исправление Domain в Set-Cookie для IP-адресов и сторонних доменов
        try:
            host = request.get_host().split(":")[0].lower()
            base_domain = getattr(settings, "SITE_DOMAIN", "brainmart.uz").lower()

            # Если хост не оканчивается на base_domain (например, вход по прямому IP 45.9.230.5)
            if not host.endswith(base_domain):
                session_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
                csrf_name = getattr(settings, "CSRF_COOKIE_NAME", "csrftoken")

                for cookie_name in [session_name, csrf_name]:
                    if cookie_name in response.cookies:
                        c = response.cookies[cookie_name]
                        domain_val = c.get("domain", "")
                        if domain_val and not host.endswith(domain_val.lstrip(".")):
                            response.set_cookie(
                                cookie_name,
                                c.value,
                                max_age=c.get("max-age"),
                                expires=c.get("expires"),
                                path=c.get("path", "/"),
                                domain=None,
                                secure=c.get("secure", False),
                                httponly=c.get("httponly", False),
                                samesite=c.get("samesite", "Lax")
                            )
        except Exception:
            pass

        return response
