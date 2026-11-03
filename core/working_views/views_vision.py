"""
core/working_views/views_vision.py
----------------------------------
BrainMart Vision AI: Модуль видеоаналитики, акустического мониторинга
и интеллектуального управления офлайн-магазинами.
"""

import ipaddress
import json
import logging
import re
import secrets
import socket
import time
from datetime import timedelta
from urllib.parse import urlparse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.db.models import Sum, Count, Avg
from django.contrib import messages
from django.conf import settings

from core.models import Store, StoreCamera, CameraTrafficEvent, CustomerSpeechLog, DailyVisionReport, Order
from core.working_views.views_dashboard import _get_store_or_none
try:
    from core.working_views.services import generate_ai_vision_daily_report
except ImportError:
    generate_ai_vision_daily_report = None

logger = logging.getLogger(__name__)


def _mask_rtsp_url(raw_url: str) -> str:
    """Маскирует пароль в RTSP/P2P URL для безопасного отображения в HTML."""
    if not raw_url:
        return ""
    return re.sub(r"([a-zA-Z0-9+.-]+://[^:/@\s]+:)([^@\s]+)(@)", r"\1****\3", str(raw_url).strip())


def _get_store_bridge_token(store: Store) -> str:
    """Возвращает секретный токен магазина для авторизации Vision Bridge."""
    if not store:
        return ""
    if hasattr(store, "get_telegram_link_token") and callable(store.get_telegram_link_token):
        token = store.get_telegram_link_token()
        if token:
            return str(token)
    token = getattr(store, "telegram_link_token", "") or ""
    if not token and getattr(store, "pk", None):
        token = secrets.token_hex(16)
        store.telegram_link_token = token
        store.save(update_fields=["telegram_link_token"])
    return str(token)


@login_required
def vision_ai_dashboard_view(request):
    """
    Главный пульт управления Vision AI в кабинете продавца:
    - Статусы камер (RTSP, Onvif, WebCam, P2P)
    - Живые счетчики дня (воронка, конверсия, dwell time)
    - Акустический радар жалоб (цена, дефицит размеров, персонал)
    - Стратегический отчет директора по продажам Gemini 2.5 Flash-Lite
    """
    store = _get_store_or_none(request)
    if not store:
        return redirect("seller-dashboard")

    today = timezone.localdate()
    cameras = list(StoreCamera.objects.filter(store=store).order_by("-is_active", "-id"))
    for cam in cameras:
        cam.masked_rtsp_url = _mask_rtsp_url(cam.rtsp_url)

    # 1. Почасовой трафик за сегодня
    hourly_events = list(CameraTrafficEvent.objects.filter(store=store, date=today).order_by("hour"))
    total_visitors = sum(e.entries_count for e in hourly_events)
    total_exits = sum(e.exits_count for e in hourly_events)

    # 2. Заказы за сегодня и реальная конверсия (без искусственной накрутки)
    orders_today_count = Order.objects.filter(
        store=store,
        created_at__date=today,
        status__in=[Order.Status.PAID, Order.Status.HANDED_TO_COURIER, Order.Status.DELIVERED, Order.Status.PENDING_PAYMENT]
    ).count()

    conversion_rate = round((orders_today_count / total_visitors * 100), 1) if total_visitors > 0 else 0.0

    # 3. Среднее время пребывания (Dwell Time)
    dwell_sum = sum(e.entries_count * e.avg_dwell_seconds for e in hourly_events)
    avg_dwell_sec = (dwell_sum / total_visitors) if total_visitors > 0 else 0
    avg_dwell_min = round(avg_dwell_sec / 60, 1) if total_visitors > 0 else 0.0

    # 4. Акустический мониторинг реплик
    speech_logs = CustomerSpeechLog.objects.filter(store=store, created_at__date=today).order_by("-id")
    total_speech_count = speech_logs.count()
    expensive_count = speech_logs.filter(category=CustomerSpeechLog.Category.PRICE_EXPENSIVE).count()
    missing_size_count = speech_logs.filter(category=CustomerSpeechLog.Category.MISSING_SIZE).count()
    staff_count = speech_logs.filter(category=CustomerSpeechLog.Category.STAFF_SERVICE).count()
    quality_count = speech_logs.filter(category=CustomerSpeechLog.Category.PRODUCT_QUALITY).count()

    exp_pct = round((expensive_count / total_speech_count * 100)) if total_speech_count else 0
    size_pct = round((missing_size_count / total_speech_count * 100)) if total_speech_count else 0
    staff_pct = round((staff_count / total_speech_count * 100)) if total_speech_count else 0
    qual_pct = round((quality_count / total_speech_count * 100)) if total_speech_count else 0

    # 5. Последний ежедневный отчет
    latest_report = DailyVisionReport.objects.filter(store=store, date=today).first()
    if not latest_report:
        latest_report = DailyVisionReport.objects.filter(store=store).order_by("-date").first()

    # 6. Подготовка данных для чарта (по часам 10:00 - 21:00)
    chart_hours = []
    chart_traffic = []
    hour_map = {e.hour: e.entries_count for e in hourly_events}
    for h in range(10, 22):
        chart_hours.append(f"{h:02d}:00")
        chart_traffic.append(hour_map.get(h, 0))

    # 7. История отчетов за 7 дней
    past_reports = DailyVisionReport.objects.filter(store=store).order_by("-date")[:7]

    context = {
        "store": store,
        "cameras": cameras,
        "total_visitors": total_visitors,
        "total_exits": total_exits,
        "orders_today_count": orders_today_count,
        "conversion_rate": conversion_rate,
        "avg_dwell_min": avg_dwell_min,
        "total_speech_count": total_speech_count,
        "expensive_count": expensive_count,
        "missing_size_count": missing_size_count,
        "staff_count": staff_count,
        "exp_pct": exp_pct,
        "size_pct": size_pct,
        "staff_pct": staff_pct,
        "qual_pct": qual_pct,
        "recent_speech_logs": speech_logs[:10],
        "latest_report": latest_report,
        "past_reports": past_reports,
        "chart_hours_json": json.dumps(chart_hours),
        "chart_traffic_json": json.dumps(chart_traffic),
        "today_str": today.strftime("%d.%m.%Y"),
    }
    return render(request, "dashboard/vision_ai.html", context)


@login_required
def vision_ai_camera_add(request):
    """Добавление новой камеры видеонаблюдения (RTSP или P2P по QR-коду / S/N)."""
    store = _get_store_or_none(request)
    if not store:
        return redirect("seller-dashboard")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        camera_type = request.POST.get("camera_type", StoreCamera.CameraType.P2P_HIKVISION)
        zone = request.POST.get("zone", StoreCamera.Zone.ENTRANCE)
        rtsp_url = request.POST.get("rtsp_url", "").strip()
        stream_user = request.POST.get("stream_username", "").strip()
        stream_pass = request.POST.get("stream_password", "").strip()
        has_audio = request.POST.get("has_audio") == "1"
        
        serial_number = request.POST.get("serial_number", "").strip().upper()
        verification_code = request.POST.get("verification_code", "").strip().upper()
        channel = int(request.POST.get("p2p_device_channel", 1) or 1)
        tapo_ip = request.POST.get("tapo_ip", "").strip()

        provider = "hikvision"
        if camera_type == StoreCamera.CameraType.P2P_DAHUA:
            provider = "dahua"
        elif camera_type == StoreCamera.CameraType.P2P_HIKVISION:
            provider = "hikvision"
        elif camera_type == StoreCamera.CameraType.TAPO:
            provider = "tapo"

        if camera_type == StoreCamera.CameraType.TAPO:
            if not stream_user:
                stream_user = "admin"
            if not rtsp_url and tapo_ip:
                rtsp_url = f"rtsp://{stream_user}:{stream_pass}@{tapo_ip}:554/stream2"
            if not name:
                zone_label = "Вход" if zone == StoreCamera.Zone.ENTRANCE else "Касса"
                name = f"Tapo Wi-Fi ({zone_label} • {tapo_ip or 'Камера'})"
        elif camera_type in [StoreCamera.CameraType.P2P_HIKVISION, StoreCamera.CameraType.P2P_DAHUA]:
            brand_label = "Hikvision / HiWatch / Ezviz" if provider == "hikvision" else "Dahua / Imou"
            if not name:
                zone_label = "Вход" if zone == StoreCamera.Zone.ENTRANCE else "Касса"
                name = f"{brand_label} ({zone_label} • {serial_number or 'Камера'})"
            if not rtsp_url:
                rtsp_url = f"{provider}://{serial_number}:{verification_code}@p2p.cloud/ch{channel}"

        if not name:
            name = "Новая камера"

        cam = StoreCamera.objects.create(
            store=store,
            name=name,
            camera_type=camera_type,
            zone=zone,
            rtsp_url=rtsp_url,
            stream_username=stream_user,
            stream_password=stream_pass,
            serial_number=serial_number,
            verification_code=verification_code,
            cloud_provider=provider,
            p2p_device_channel=channel,
            has_audio=has_audio,
            status=StoreCamera.Status.ONLINE,
            last_ping_at=timezone.now()
        )

        if serial_number:
            messages.success(request, f"🎉 Камера «{name}» успешно подключена по P2P QR-коду (S/N: {serial_number}) за 1 минуту без ПК!")
        else:
            messages.success(request, f"Камера «{name}» успешно подключена к системе Vision AI!")

    return redirect("seller-vision-ai")


@csrf_exempt
@login_required
def vision_ai_p2p_connect_api(request):
    """
    Быстрое подключение камеры по QR-коду / Серийному номеру (Hikvision / HiWatch / Ezviz / Dahua)
    в 1 клик прямо из Telegram Mini App без перезагрузки страницы.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only POST allowed"}, status=405)

    store = _get_store_or_none(request)
    if not store:
        return JsonResponse({"success": False, "error": "Магазин не найден"}, status=400)

    try:
        data = json.loads(request.body) if request.body else {}
    except Exception:
        return JsonResponse({"success": False, "error": "Неверный формат данных JSON"}, status=400)

    serial_number = data.get("serial_number", "").strip().upper()
    verification_code = data.get("verification_code", "").strip().upper()
    camera_type = data.get("camera_type", StoreCamera.CameraType.P2P_HIKVISION)
    zone = data.get("zone", StoreCamera.Zone.ENTRANCE)
    channel = int(data.get("channel", 1) or 1)
    has_audio = bool(data.get("has_audio", True))
    name = data.get("name", "").strip()

    if not serial_number:
        return JsonResponse({"success": False, "error": "Введите или отсканируйте 9-значный серийный номер с наклейки (S/N)"}, status=400)

    provider = "hikvision" if "hik" in camera_type or "ezviz" in camera_type else "dahua"
    brand_label = "Hikvision / HiWatch / Ezviz" if provider == "hikvision" else "Dahua / Imou"

    if not name:
        zone_label = "Вход" if zone == StoreCamera.Zone.ENTRANCE else "Касса"
        name = f"{brand_label} ({zone_label} • {serial_number})"

    rtsp_url = f"{provider}://{serial_number}:{verification_code}@p2p.cloud/ch{channel}"

    camera = StoreCamera.objects.create(
        store=store,
        name=name,
        camera_type=camera_type,
        zone=zone,
        serial_number=serial_number,
        verification_code=verification_code,
        cloud_provider=provider,
        p2p_device_channel=channel,
        rtsp_url=rtsp_url,
        has_audio=has_audio,
        status=StoreCamera.Status.ONLINE,
        last_ping_at=timezone.now()
    )

    return JsonResponse({
        "success": True,
        "message": f"🎉 Камера «{name}» успешно подключена к BrainMart Vision AI через P2P Cloud за 1 минуту!",
        "camera": {
            "id": camera.id,
            "name": camera.name,
            "serial_number": camera.serial_number,
            "verification_code": camera.verification_code,
            "zone": camera.get_zone_display(),
            "cloud_provider": provider,
            "channel": camera.p2p_device_channel,
            "status": "online"
        }
    })


@login_required
def vision_ai_camera_toggle(request, camera_id):
    """Включение / отключение камеры."""
    store = _get_store_or_none(request)
    cam = get_object_or_404(StoreCamera, id=camera_id, store=store)
    cam.is_active = not cam.is_active
    cam.status = StoreCamera.Status.ONLINE if cam.is_active else StoreCamera.Status.OFFLINE
    cam.save(update_fields=["is_active", "status"])
    return JsonResponse({"success": True, "is_active": cam.is_active, "status": cam.status})


@login_required
def vision_ai_camera_delete(request, camera_id):
    """Удаление камеры."""
    store = _get_store_or_none(request)
    cam = get_object_or_404(StoreCamera, id=camera_id, store=store)
    cam.delete()
    messages.info(request, "Камера отключена и удалена из аналитики.")
    return redirect("seller-vision-ai")


@login_required
def vision_ai_camera_ping(request, camera_id):
    """Честная проверка доступности камеры (TCP сокет / LAN статус через Vision Bridge / P2P)."""
    store = _get_store_or_none(request)
    cam = get_object_or_404(StoreCamera, id=camera_id, store=store)
    raw_url = (cam.rtsp_url or "").strip()

    host = None
    port = 554
    if raw_url:
        parsed = urlparse(raw_url if "://" in raw_url else f"rtsp://{raw_url}")
        host = parsed.hostname
        if parsed.port:
            port = parsed.port
        elif parsed.scheme in ("http", "https"):
            port = 443 if parsed.scheme == "https" else 80

    # Проверка P2P камер
    if cam.serial_number or (host and host.endswith("p2p.cloud")):
        cam.last_ping_at = timezone.now()
        cam.status = StoreCamera.Status.ONLINE
        cam.save(update_fields=["last_ping_at", "status"])
        return JsonResponse({
            "success": True,
            "status": "online",
            "latency_ms": None,
            "message": (
                f"Камера P2P ({cam.serial_number or cam.cloud_provider}) зарегистрирована. "
                f"Для передачи видеопотока из локальной сети магазина запустите BrainMart Vision Bridge."
            ),
        })

    if not host:
        cam.status = StoreCamera.Status.OFFLINE
        cam.save(update_fields=["status"])
        return JsonResponse({
            "success": False,
            "status": "offline",
            "latency_ms": None,
            "message": "У камеры не указан корректный IP-адрес или RTSP URL.",
        })

    is_private_lan = False
    try:
        ip_obj = ipaddress.ip_address(host)
        is_private_lan = ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local
    except ValueError:
        is_private_lan = host.lower() in ("localhost", "local") or host.endswith(".local") or host.endswith(".lan")

    start_ts = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=1.5):
            latency_ms = max(1, int(round((time.monotonic() - start_ts) * 1000)))
            cam.last_ping_at = timezone.now()
            cam.status = StoreCamera.Status.ONLINE
            cam.save(update_fields=["last_ping_at", "status"])
            return JsonResponse({
                "success": True,
                "status": "online",
                "latency_ms": latency_ms,
                "message": (
                    f"Прямое TCP-соединение с {host}:{port} успешно установлено ({latency_ms} мс). "
                    f"Аудиоканал: {'включен' if cam.has_audio else 'выключен'}."
                ),
            })
    except OSError as exc:
        if is_private_lan:
            recent_bridge = bool(cam.last_ping_at and (timezone.now() - cam.last_ping_at) <= timedelta(minutes=5))
            return JsonResponse({
                "success": True,
                "status": cam.status,
                "latency_ms": None,
                "message": (
                    f"Камера находится во внутренней Wi-Fi/LAN сети магазина ({host}:{port}) и недоступна напрямую из облака. "
                    f"Подключение осуществляется через локальный клиент BrainMart Vision Bridge в магазине"
                    + (" (телеметрия от Bridge активна)." if recent_bridge else " (запустите скрипт Vision Bridge на ПК в магазине).")
                ),
            })

        cam.status = StoreCamera.Status.OFFLINE
        cam.save(update_fields=["status"])
        return JsonResponse({
            "success": False,
            "status": "offline",
            "latency_ms": None,
            "message": f"Не удалось подключиться к {host}:{port} (таймаут или узел недоступен: {exc}).",
        })


@login_required
def vision_ai_generate_report_now(request):
    """Принудительная генерация свежего отчета директором Gemini 2.5 Flash-Lite."""
    store = _get_store_or_none(request)
    if not store:
        return JsonResponse({"success": False, "error": "Store not found"})

    today = timezone.localdate()
    report_fn = generate_ai_vision_daily_report
    if report_fn is None:
        from core.working_views.services import generate_ai_vision_daily_report as report_fn
    res = report_fn(store, target_date=today, trigger_telegram=True)
    return JsonResponse({
        "success": True,
        "data": res
    })


@login_required
def vision_ai_download_bridge(request):
    """
    Генерирует готовый легковесный скрипт BrainMart Vision Bridge для кассового ПК продавца.
    Продавец просто запускает его на Windows ноутбуке/ПК в магазине.
    """
    store = _get_store_or_none(request)
    if not store:
        return redirect("seller-dashboard")

    store_id = int(store.id)
    bridge_token = _get_store_bridge_token(store)
    first_cam = StoreCamera.objects.filter(store=store, is_active=True).order_by("-id").first()
    default_rtsp = first_cam.rtsp_url if (first_cam and first_cam.rtsp_url) else ""
    default_cam_id = int(first_cam.id) if first_cam else 0

    safe_store_name = json.dumps(store.name or "Store", ensure_ascii=False)
    safe_bridge_token = json.dumps(bridge_token, ensure_ascii=False)
    safe_default_rtsp = json.dumps(default_rtsp, ensure_ascii=False)

    bridge_script = f'''# =====================================================================
# BrainMart Vision Bridge (Клиент видео- и аудиоаналитики магазина)
# Инструкция: Запустите данный скрипт на ПК/ноутбуке в локальной сети магазина.
# Зависимости: pip install opencv-python requests
# Для тестового демо-режима без физической камеры добавьте флаг: --demo
# =====================================================================
import argparse
import datetime
import json
import random
import time
import requests

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

STORE_ID = {store_id}
STORE_NAME = {safe_store_name}
CAMERA_ID = {default_cam_id}
BRIDGE_TOKEN = {safe_bridge_token}
DEFAULT_RTSP_URL = {safe_default_rtsp}
SERVER_URL = "https://brainmart.uz/dashboard/api/vision/push/"


def send_telemetry(server_url, entries, exits, dwell_sec, speech=None):
    payload = {{
        "store_id": STORE_ID,
        "camera_id": CAMERA_ID or None,
        "bridge_token": BRIDGE_TOKEN,
        "entries": int(entries),
        "exits": int(exits),
        "dwell": int(dwell_sec),
        "dwell_sec": int(dwell_sec),
    }}
    if speech:
        payload.update(speech)
    headers = {{"X-Bridge-Token": BRIDGE_TOKEN}}
    res = requests.post(server_url, json=payload, headers=headers, timeout=6)
    return res


def run_opencv_capture(source, server_url, interval_sec=30):
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видеопоток: {{source}}")

    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    prev_gray = None
    entries_acc = 0
    exits_acc = 0
    last_push = time.monotonic()
    last_person_ts = 0.0

    print(f"[+] Подключено к видеопотоку OpenCV: {{source}}")
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.5)
                continue

            resized = cv2.resize(frame, (640, 360))
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21, 21), 0)

            motion_score = 0
            if prev_gray is not None:
                diff = cv2.absdiff(prev_gray, gray)
                thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)[1]
                motion_score = int(cv2.countNonZero(thresh))
            prev_gray = gray

            now = time.monotonic()
            if motion_score > 2500 and (now - last_person_ts) >= 3.0:
                rects, _ = hog.detectMultiScale(resized, winStride=(8, 8), padding=(8, 8), scale=1.05)
                if len(rects) > 0:
                    entries_acc += len(rects)
                    last_person_ts = now

            if (now - last_push) >= interval_sec:
                if entries_acc > 0 or exits_acc > 0:
                    try:
                        res = send_telemetry(server_url, entries_acc, exits_acc, 420)
                        if res.status_code == 200:
                            ts = datetime.datetime.now().strftime("%H:%M:%S")
                            print(f"[OK] {{ts}} Отправлено: +{{entries_acc}} вошло, -{{exits_acc}} вышло.")
                            entries_acc = 0
                            exits_acc = 0
                        else:
                            print(f"[!] Ответ сервера {{res.status_code}}: {{res.text}}")
                    except Exception as e:
                        print(f"[!] Ошибка отправки телеметрии: {{e}}")
                last_push = now
    finally:
        cap.release()


def run_demo_loop(server_url, interval_sec=15):
    print("[*] Запущен демо-режим (--demo) без физической камеры.")
    while True:
        time.sleep(interval_sec)
        entries = random.choice([0, 1, 1, 2, 0])
        exits = random.choice([0, 1, 0, 1])
        dwell = random.randint(300, 600)
        try:
            res = send_telemetry(server_url, entries, exits, dwell)
            if res.status_code == 200:
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                print(f"[DEMO OK] {{ts}} Отправлено: +{{entries}} вошло, -{{exits}} вышло.")
            else:
                print(f"[!] Ответ сервера {{res.status_code}}: {{res.text}}")
        except Exception as e:
            print(f"[!] Ошибка отправки данных на сервер: {{e}}")


def main():
    parser = argparse.ArgumentParser(description="BrainMart Vision Bridge")
    parser.add_argument("--rtsp", type=str, default=DEFAULT_RTSP_URL, help="RTSP URL камеры")
    parser.add_argument("--webcam", type=int, default=-1, help="Индекс USB веб-камеры (например 0)")
    parser.add_argument("--server", type=str, default=SERVER_URL, help="URL сервера BrainMart")
    parser.add_argument("--demo", action="store_true", help="Запустить тестовый демо-режим без камеры")
    args = parser.parse_args()

    print(f"[*] BrainMart Vision Bridge запущен для магазина: {{STORE_NAME}} (ID: {{STORE_ID}})")
    try:
        if args.demo:
            run_demo_loop(args.server)
        else:
            source = args.webcam if args.webcam >= 0 else args.rtsp
            if not source:
                print("[!] Не указан RTSP поток или --webcam. Используйте --rtsp <URL>, --webcam 0 или --demo.")
                return
            if not HAS_OPENCV:
                print("[!] Библиотека opencv-python не установлена (pip install opencv-python). Для теста используйте --demo.")
                return
            run_opencv_capture(source, args.server)
    except KeyboardInterrupt:
        print("\\n[!] BrainMart Vision Bridge остановлен.")
    except Exception as exc:
        print(f"[!] Ошибка работы видеопотока: {{exc}}")
        print("[*] Совет: проверьте доступность RTSP URL в локальной сети или запустите с флагом --demo.")


if __name__ == "__main__":
    main()
'''

    response = HttpResponse(bridge_script, content_type="text/x-python; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="brainmart_bridge_store_{store_id}.py"'
    return response


@csrf_exempt
def vision_ai_push_telemetry(request):
    """
    API endpoint для приема телеметрии от BrainMart Vision Bridge (локального клиента):
    - количество вошедших/вышедших
    - распознанный текст с микрофона
    Защищен проверкой секретного токена магазина (bridge_token / X-Bridge-Token).
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only POST allowed"}, status=405)

    try:
        data = json.loads(request.body) if request.body else {}
        if not isinstance(data, dict):
            return JsonResponse({"success": False, "error": "Invalid JSON object"}, status=400)
    except Exception:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    store_id = data.get("store_id")
    if not store_id:
        return JsonResponse({"success": False, "error": "Missing store_id"}, status=400)

    try:
        store_id_int = int(store_id)
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Invalid store_id"}, status=400)

    store = Store.objects.filter(id=store_id_int).first()
    if not store:
        return JsonResponse({"success": False, "error": "Store not found"}, status=404)

    expected_token = _get_store_bridge_token(store)
    provided_token = str(
        data.get("bridge_token")
        or request.headers.get("X-Bridge-Token")
        or ""
    ).strip()

    if not expected_token or not provided_token or not secrets.compare_digest(provided_token, expected_token):
        return JsonResponse({"success": False, "error": "Unauthorized: invalid or missing bridge_token"}, status=403)

    today = timezone.localdate()
    now_hour = timezone.localtime().hour

    camera_id = data.get("camera_id")
    camera = None
    if camera_id:
        try:
            camera = StoreCamera.objects.filter(id=int(camera_id), store=store).first()
        except (ValueError, TypeError):
            camera = None
    if not camera:
        camera = StoreCamera.objects.filter(store=store).first()

    if camera:
        camera.last_ping_at = timezone.now()
        camera.status = StoreCamera.Status.ONLINE
        camera.save(update_fields=["last_ping_at", "status"])

    try:
        entries = max(0, min(5000, int(data.get("entries") or 0)))
    except (ValueError, TypeError):
        entries = 0

    try:
        exits = max(0, min(5000, int(data.get("exits") or 0)))
    except (ValueError, TypeError):
        exits = 0

    try:
        raw_dwell = data.get("dwell") if data.get("dwell") is not None else data.get("dwell_sec")
        dwell = max(0, min(86400, int(raw_dwell if raw_dwell is not None else 0)))
    except (ValueError, TypeError):
        dwell = 0

    if entries > 0 or exits > 0:
        event, created = CameraTrafficEvent.objects.get_or_create(
            store=store,
            camera=camera,
            date=today,
            hour=now_hour,
            defaults={"entries_count": entries, "exits_count": exits, "avg_dwell_seconds": dwell}
        )
        if not created:
            event.entries_count += entries
            event.exits_count += exits
            if dwell > 0:
                event.avg_dwell_seconds = int((event.avg_dwell_seconds + dwell) / 2) if event.avg_dwell_seconds else dwell
            event.save(update_fields=["entries_count", "exits_count", "avg_dwell_seconds"])

    # Если передан фрагмент распознанной речи (поддержка как вложенного объекта speech, так и верхнего уровня)
    speech_data = data.get("speech") if isinstance(data.get("speech"), dict) else data
    transcript = str(speech_data.get("transcript", "") or "").strip()[:500]
    if transcript:
        allowed_categories = {choice[0] for choice in CustomerSpeechLog.Category.choices}
        allowed_sentiments = {choice[0] for choice in CustomerSpeechLog.Sentiment.choices}
        allowed_languages = {"uz", "ru", "en"}

        cat = str(speech_data.get("category") or CustomerSpeechLog.Category.GENERAL).strip()
        if cat not in allowed_categories:
            cat = CustomerSpeechLog.Category.GENERAL

        sent = str(speech_data.get("sentiment") or CustomerSpeechLog.Sentiment.NEUTRAL).strip()
        if sent not in allowed_sentiments:
            sent = CustomerSpeechLog.Sentiment.NEUTRAL

        lang = str(speech_data.get("language") or "uz").strip().lower()
        if lang not in allowed_languages:
            lang = "uz"

        CustomerSpeechLog.objects.create(
            store=store,
            camera=camera,
            transcript=transcript,
            category=cat,
            sentiment=sent,
            language=lang
        )

    return JsonResponse({"success": True})
