"""
Генератор темы «с нуля».

Продавец выбирает 1 основной цвет + светлый/тёмный режим (опционально акцент и фон),
а здесь рассчитываются все остальные CSS-переменные витрины:
оттенки поверхностей, приглушённый текст, контрастный цвет текста на кнопке,
градиент кнопки и т.д.
"""
import colorsys
import re

HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def is_hex(value) -> bool:
    return bool(value) and bool(HEX_RE.match(str(value).strip()))


def _rgb(h: str):
    h = h.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(r, g, b) -> str:
    return "#%02X%02X%02X" % tuple(round(max(0.0, min(1.0, x)) * 255) for x in (r, g, b))


def luminance(h: str) -> float:
    """Относительная яркость по WCAG 2.x."""
    def ch(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def shift(h: str, dl: float = 0.0, ds: float = 0.0) -> str:
    """Сдвиг светлоты/насыщенности цвета в HLS."""
    hh, l, s = colorsys.rgb_to_hls(*_rgb(h))
    return _hex(*colorsys.hls_to_rgb(hh, max(0.0, min(1.0, l + dl)), max(0.0, min(1.0, s + ds))))


def rgba(h: str, alpha: float) -> str:
    r, g, b = (round(c * 255) for c in _rgb(h))
    return f"rgba({r}, {g}, {b}, {alpha})"


def on_color(bg: str) -> str:
    """Чёрный или белый текст — что контрастнее на данном фоне."""
    return "#111111" if contrast(bg, "#111111") >= contrast(bg, "#FFFFFF") else "#FFFFFF"


def build_vars(primary: str, mode: str = "dark", accent: str = "", bg: str = "", radius: int = 18) -> dict:
    """Строит полный набор CSS-переменных витрины из 1–3 цветов."""
    primary = primary if is_hex(primary) else "#4F46E5"
    if not primary.startswith("#"):
        primary = "#" + primary
    dark = mode != "light"
    hue = colorsys.rgb_to_hls(*_rgb(primary))[0]

    def tint(l, s):
        return _hex(*colorsys.hls_to_rgb(hue, l, s))

    if is_hex(bg):
        bg = bg if bg.startswith("#") else "#" + bg
        # если фон явно противоречит режиму — режим берём из фона
        dark = luminance(bg) < 0.35
        surface = shift(bg, dl=0.05 if dark else 0.03)
        card = shift(bg, dl=0.08 if dark else 0.045)
        input_bg = shift(bg, dl=0.025 if dark else -0.03)
    elif dark:
        bg, surface, card, input_bg = tint(0.05, 0.10), tint(0.085, 0.09), tint(0.11, 0.08), tint(0.07, 0.09)
    else:
        bg, surface, card, input_bg = tint(0.97, 0.30), "#FFFFFF", "#FFFFFF", tint(0.94, 0.22)

    accent = accent if is_hex(accent) else shift(primary, dl=0.14 if dark else -0.08)
    if not accent.startswith("#"):
        accent = "#" + accent

    text = "#F5F3EF" if dark else "#14110F"
    muted = tint(0.66, 0.08) if dark else tint(0.40, 0.10)

    return {
        "primary": primary,
        "primary-hover": shift(primary, dl=-0.07),
        "accent": accent,
        "bg-app": bg,
        "surface": surface,
        "surface-card": card,
        "text-main": text,
        "text-muted": muted,
        "border": "rgba(255, 255, 255, 0.08)" if dark else "rgba(0, 0, 0, 0.08)",
        "input-bg": input_bg,
        "card-shadow": "0 10px 30px rgba(0, 0, 0, 0.45)" if dark else "0 1px 2px rgba(0,0,0,0.04), 0 10px 28px rgba(0,0,0,0.06)",
        "badge-bg": rgba(card, 0.92),
        "header-bg": rgba(bg, 0.9),
        "radius-lg": f"{radius}px",
        "radius-md": f"{max(6, radius - 6)}px",
        "on-primary": on_color(primary),
        "btn-gradient": f"linear-gradient(135deg, {primary} 0%, {accent} 100%)",
    }, ("dark" if dark else "light")


def extract_palette_from_image(image_file_or_path) -> dict:
    """
    Извлекает гармоничную палитру из логотипа магазина с помощью Pillow:
    находит акцентный/доминантный цвет, подбирает дополнительный и генерирует
    готовые цвета (primary, accent, bg, mode).
    """
    try:
        from PIL import Image
        if hasattr(image_file_or_path, "read"):
            image_file_or_path.seek(0)
            img = Image.open(image_file_or_path)
        else:
            img = Image.open(image_file_or_path)

        img = img.convert("RGBA")
        img.thumbnail((120, 120))

        # Собираем непрозрачные пиксели
        pixels = list(img.getdata())
        candidates = []
        for r, g, b, a in pixels:
            if a < 120:
                continue
            # Исключаем почти чисто белые и почти чисто черные пиксели
            if (r > 240 and g > 240 and b > 240) or (r < 18 and g < 18 and b < 18):
                continue
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            # Приоритет пикселям с высокой насыщенностью
            score = (s * 2.0) + (1.0 - abs(l - 0.5))
            candidates.append(((r, g, b), score))

        if not candidates:
            return {"primary": "#C9A27A", "accent": "#E3C8A3", "bg": "#0B0B0C", "mode": "dark"}

        candidates.sort(key=lambda x: x[1], reverse=True)
        # Кластеризуем лучший цвет
        best_rgb = candidates[0][0]
        primary_hex = "#%02X%02X%02X" % best_rgb

        # Ищем второй гармоничный цвет
        accent_hex = None
        best_h, best_l, best_s = colorsys.rgb_to_hls(*(c / 255 for c in best_rgb))
        for (r, g, b), score in candidates[10:]:
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            # Разница в оттенке хотя бы на 30 градусов
            if abs(h - best_h) > 0.08:
                accent_hex = "#%02X%02X%02X" % (r, g, b)
                break

        if not accent_hex:
            accent_hex = shift(primary_hex, dl=0.15)

        # Рекомендуемый тёмный фон (OLED Black с лёгким оттенком primary)
        tinted_bg = "#%02X%02X%02X" % tuple(
            round(c * 255) for c in colorsys.hls_to_rgb(best_h, 0.045, min(0.12, best_s))
        )

        return {
            "primary": primary_hex,
            "accent": accent_hex,
            "bg": tinted_bg,
            "mode": "dark",
        }
    except Exception:
        return {"primary": "#C9A27A", "accent": "#E3C8A3", "bg": "#0B0B0C", "mode": "dark"}

