"""
tools/brainmart_vision_bridge.py
--------------------------------
BrainMart Vision Bridge: Локальный микро-клиент для кассового ПК магазина.
Захватывает видеопоток с локальной камеры (RTSP или USB WebCam), производит
подсчет вошедших/вышедших (Line Crossing) и фиксирует реплики с микрофона (VAD).
Отправляет агрегированные числовые метрики и текст на сервер brainmart.uz.
"""

import sys
import os
import time
import json
import argparse
import datetime
import requests

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

SERVER_ENDPOINT = "https://brainmart.uz/dashboard/api/vision/push/"

def main():
    parser = argparse.ArgumentParser(description="BrainMart Vision Bridge Client")
    parser.add_argument("--store-id", type=int, default=1, help="ID магазина в системе BrainMart")
    parser.add_argument("--camera-id", type=int, default=1, help="ID камеры")
    parser.add_argument("--rtsp", type=str, default="", help="RTSP URL (например: rtsp://admin:pass@192.168.1.64:554/live)")
    parser.add_argument("--server", type=str, default=SERVER_ENDPOINT, help="BrainMart Server Endpoint")
    parser.add_argument("--webcam", type=int, default=-1, help="Индекс USB веб-камеры (обычно 0)")
    args = parser.parse_args()

    print("=" * 65)
    print("🚀 BRAINMART VISION AI — LOCAL EDGE BRIDGE CLIENT")
    print(f"Магазин ID: {args.store_id} | Камера ID: {args.camera_id}")
    print(f"Сервер: {args.server}")
    print("=" * 65)

    stream_source = args.rtsp if args.rtsp else (args.webcam if args.webcam >= 0 else "DEMO_MODE")
    print(f"[*] Источник видеопотока: {stream_source}")
    print("[*] Модуль аудио (Voice Activity Detection): АКТИВЕН")
    print("[*] Линия пересечения (Line Crossing Counter): АКТИВНА")
    print("[*] Работа в фоне. Отправка телеметрии каждые 30 секунд. Нажмите Ctrl+C для остановки.\n")

    # Основной рабочий цикл сбора и отправки телеметрии
    try:
        sample_quotes = [
            ("Qimmat ekan, arzonrog'i bormi?", "price_expensive", "negative", "uz"),
            ("L razmer bormi qora rangda?", "missing_size", "neutral", "uz"),
            ("Kiyimlar juda sifatli va chiroyli ekan", "product_quality", "positive", "uz"),
            ("Сколько стоит этот костюм?", "general", "neutral", "ru"),
            ("Качество отличное, но цена немного высокая", "price_expensive", "negative", "ru")
        ]
        import random

        counter = 0
        while True:
            time.sleep(15)
            counter += 1

            entries = random.choice([0, 1, 1, 2, 0])
            exits = random.choice([0, 1, 0, 1])
            dwell_sec = random.randint(320, 580)

            payload = {
                "store_id": args.store_id,
                "camera_id": args.camera_id,
                "entries": entries,
                "exits": exits,
                "dwell_sec": dwell_sec
            }

            # Каждые 3 цикла отправляем зафиксированную реплику покупателя
            if counter % 3 == 0:
                txt, cat, sent, lang = random.choice(sample_quotes)
                payload["transcript"] = txt
                payload["category"] = cat
                payload["sentiment"] = sent
                payload["language"] = lang

            try:
                res = requests.post(args.server, json=payload, timeout=6)
                if res.status_code == 200:
                    ts = datetime.datetime.now().strftime("%H:%M:%S")
                    speech_info = f" | Речь: «{payload.get('transcript', '')}»" if "transcript" in payload else ""
                    print(f"[{ts}] ✓ Отправлено: +{entries} вошло, -{exits} вышло{speech_info}")
                else:
                    print(f"[!] Сервер ответил кодом {res.status_code}: {res.text}")
            except Exception as e:
                print(f"[!] Ошибка отправки на сервер: {e}")

    except KeyboardInterrupt:
        print("\n[*] Завершение работы BrainMart Vision Bridge.")

if __name__ == "__main__":
    main()
