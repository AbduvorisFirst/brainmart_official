"""
tools/brainmart_vision_bridge.py
--------------------------------
BrainMart Vision Bridge: Локальный микро-клиент для кассового ПК магазина.
Захватывает реальный видеопоток с локальной камеры (RTSP или USB WebCam) через OpenCV,
производит детекцию движения и людей (HOGDescriptor / frame diff) и отправляет
агрегированные числовые метрики и текст на сервер brainmart.uz с проверкой bridge_token.
Для тестирования без физической камеры предусмотрен флаг --demo.
"""

import argparse
import datetime
import json
import os
import random
import sys
import time
import requests

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

SERVER_ENDPOINT = "https://brainmart.uz/dashboard/api/vision/push/"


def send_telemetry(server_url, store_id, camera_id, bridge_token, entries, exits, dwell_sec, speech=None):
    payload = {
        "store_id": store_id,
        "camera_id": camera_id,
        "bridge_token": bridge_token,
        "entries": int(entries),
        "exits": int(exits),
        "dwell": int(dwell_sec),
        "dwell_sec": int(dwell_sec),
    }
    if speech:
        payload.update(speech)
    headers = {"X-Bridge-Token": bridge_token} if bridge_token else {}
    return requests.post(server_url, json=payload, headers=headers, timeout=6)


def run_opencv_stream(source, args):
    if not HAS_OPENCV:
        print("[!] Ошибка: библиотека opencv-python не установлена (выполните: pip install opencv-python).")
        print("[*] Для запуска тестового режима без камеры используйте флаг --demo.")
        sys.exit(1)

    print(f"[*] Подключение к видеопотоку OpenCV: {source} ...")
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"[!] Не удалось открыть видеопоток: {source}")
        print("[*] Проверьте IP-адрес камеры в локальной сети магазина или используйте --demo для теста.")
        sys.exit(1)

    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    prev_gray = None
    entries_acc = 0
    exits_acc = 0
    dwell_samples = []
    last_push = time.monotonic()
    last_detection_ts = 0.0

    print("[+] Видеопоток активен! Детектор движения и силуэтов (OpenCV HOG) запущен.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.5)
                continue

            resized = cv2.resize(frame, (640, 360))
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21, 21), 0)

            motion_pixels = 0
            if prev_gray is not None:
                frame_delta = cv2.absdiff(prev_gray, gray)
                thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]
                motion_pixels = int(cv2.countNonZero(thresh))
            prev_gray = gray

            now = time.monotonic()
            if motion_pixels > 2500 and (now - last_detection_ts) >= 3.0:
                rects, _ = hog.detectMultiScale(
                    resized, winStride=(8, 8), padding=(8, 8), scale=1.05
                )
                if len(rects) > 0:
                    entries_acc += len(rects)
                    dwell_samples.append(420)
                    last_detection_ts = now

            if (now - last_push) >= args.interval:
                if entries_acc > 0 or exits_acc > 0:
                    avg_dwell = int(sum(dwell_samples) / len(dwell_samples)) if dwell_samples else 420
                    try:
                        res = send_telemetry(
                            args.server,
                            args.store_id,
                            args.camera_id,
                            args.token,
                            entries_acc,
                            exits_acc,
                            avg_dwell,
                        )
                        if res.status_code == 200:
                            ts = datetime.datetime.now().strftime("%H:%M:%S")
                            print(f"[{ts}] ✓ Отправлено с камеры: +{entries_acc} вошло, -{exits_acc} вышло")
                            entries_acc = 0
                            exits_acc = 0
                            dwell_samples.clear()
                        else:
                            print(f"[!] Сервер ответил кодом {res.status_code}: {res.text}")
                    except Exception as e:
                        print(f"[!] Ошибка отправки на сервер: {e}")
                last_push = now
    finally:
        cap.release()


def run_demo_stream(args):
    sample_quotes = [
        ("Qimmat ekan, arzonrog'i bormi?", "price_expensive", "negative", "uz"),
        ("L razmer bormi qora rangda?", "missing_size", "neutral", "uz"),
        ("Kiyimlar juda sifatli va chiroyli ekan", "product_quality", "positive", "uz"),
        ("Сколько стоит этот костюм?", "general", "neutral", "ru"),
        ("Качество отличное, но цена немного высокая", "price_expensive", "negative", "ru"),
    ]
    counter = 0
    while True:
        time.sleep(args.interval)
        counter += 1
        entries = random.choice([0, 1, 1, 2, 0])
        exits = random.choice([0, 1, 0, 1])
        dwell_sec = random.randint(320, 580)

        speech = None
        if counter % 3 == 0:
            txt, cat, sent, lang = random.choice(sample_quotes)
            speech = {
                "transcript": txt,
                "category": cat,
                "sentiment": sent,
                "language": lang,
            }

        try:
            res = send_telemetry(
                args.server,
                args.store_id,
                args.camera_id,
                args.token,
                entries,
                exits,
                dwell_sec,
                speech=speech,
            )
            if res.status_code == 200:
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                speech_info = f" | Речь: «{speech['transcript']}»" if speech else ""
                print(f"[{ts}] ✓ [DEMO] Отправлено: +{entries} вошло, -{exits} вышло{speech_info}")
            else:
                print(f"[!] Сервер ответил кодом {res.status_code}: {res.text}")
        except Exception as e:
            print(f"[!] Ошибка отправки на сервер: {e}")


def main():
    parser = argparse.ArgumentParser(description="BrainMart Vision Bridge Client")
    parser.add_argument("--store-id", type=int, default=1, help="ID магазина в системе BrainMart")
    parser.add_argument("--camera-id", type=int, default=1, help="ID камеры")
    parser.add_argument("--token", type=str, default=os.environ.get("BRAINMART_BRIDGE_TOKEN", ""), help="Секретный токен магазина (bridge_token)")
    parser.add_argument("--rtsp", type=str, default="", help="RTSP URL (например: rtsp://admin:pass@192.168.1.64:554/live)")
    parser.add_argument("--server", type=str, default=SERVER_ENDPOINT, help="BrainMart Server Endpoint")
    parser.add_argument("--webcam", type=int, default=-1, help="Индекс USB веб-камеры (обычно 0)")
    parser.add_argument("--interval", type=int, default=15, help="Интервал отправки телеметрии (сек)")
    parser.add_argument("--demo", action="store_true", help="Явный запуск демонстрационного режима без физической камеры")
    args = parser.parse_args()

    print("=" * 65)
    print("🚀 BRAINMART VISION AI — LOCAL EDGE BRIDGE CLIENT")
    print(f"Магазин ID: {args.store_id} | Камера ID: {args.camera_id}")
    print(f"Сервер: {args.server}")
    print("=" * 65)

    if not args.token:
        print("[!] ВНИМАНИЕ: Не указан --token (или переменная окружения BRAINMART_BRIDGE_TOKEN).")
        print("    Скачайте персональный скрипт в разделе Vision AI кабинета продавца.")

    try:
        if args.demo:
            print("[*] Режим работы: DEMO_MODE (генерация тестовых событий)")
            run_demo_stream(args)
        else:
            source = args.rtsp if args.rtsp else (args.webcam if args.webcam >= 0 else None)
            if source is None:
                print("[!] Не указан источник видеопотока (--rtsp <URL> или --webcam 0).")
                print("    Для запуска в демонстрационном режиме без камеры добавьте флаг --demo.")
                sys.exit(1)
            print(f"[*] Источник видеопотока: {source}")
            run_opencv_stream(source, args)
    except KeyboardInterrupt:
        print("\n[*] Завершение работы BrainMart Vision Bridge.")


if __name__ == "__main__":
    main()
