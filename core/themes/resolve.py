"""
Сборка итоговой темы витрины для магазина:

    пресет (PRESETS) или генератор «с нуля» (theme_style == 'custom')
      + точечные правки продавца (Store.theme_overrides)
      + раскладка (layout) и шрифт (font)
    → CSS-переменные для :root
"""
import re

from .generator import build_vars, is_hex
from .presets import MARKETPLACE_VARS, PRESETS

LAYOUTS = {
    "luxury": "Luxury — премиум, serif, крупный баннер",
    "minimal": "Minimal — чистый, много воздуха",
    "playful": "Playful — яркий, скруглённый",
    "bold": "Bold — контрастный, стритвир/неон",
    "market": "Market — плотная сетка маркетплейса",
}

# ключ → (CSS font-family, параметр Google Fonts или None)
FONTS = {
    "serif": ("'Cormorant Garamond', 'Playfair Display', Georgia, serif", "Cormorant+Garamond:wght@500;600;700"),
    "sans": ("Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif", None),
    "rounded": ("'Nunito', Inter, sans-serif", "Nunito:wght@600;700;800;900"),
    "grotesk": ("'Space Grotesk', Inter, sans-serif", "Space+Grotesk:wght@500;600;700"),
}
FONT_LABELS = {
    "serif": "Serif — элегантный (Cormorant)",
    "sans": "Sans — нейтральный (Inter)",
    "rounded": "Rounded — дружелюбный (Nunito)",
    "grotesk": "Grotesk — технологичный (Space Grotesk)",
}
LAYOUT_DEFAULT_FONT = {"luxury": "serif", "minimal": "sans", "playful": "rounded", "bold": "grotesk", "market": "sans"}

# устаревшие ключи тем → актуальные пресеты
THEME_ALIASES = {"fashion_kids": "kids_playful"}

ALLOWED_OVERRIDE_KEYS = {
    "primary", "primary-hover", "accent", "bg-app", "surface", "surface-card", "text-main", "text-muted",
    "border", "input-bg", "badge-bg", "header-bg", "on-primary", "btn-gradient", "radius-lg", "radius-md",
}
_SAFE_VALUE = re.compile(r"^[#\w\s.,%()\-]+$")


def _clean_value(v) -> str:
    v = str(v).strip()
    if not v or len(v) > 160 or not _SAFE_VALUE.match(v):
        return ""
    return v


def clean_overrides(data) -> dict:
    """Оставляет только разрешённые ключи и безопасные значения (защита от CSS-инъекций)."""
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if k in ALLOWED_OVERRIDE_KEYS:
            cv = _clean_value(v)
            if cv:
                out[k] = cv
    return out


def resolve_theme(store) -> dict:
    if not store:
        vars_ = dict(MARKETPLACE_VARS)
        mode, layout, name = "dark", "market", "BrainMart"
        badge = ("badge-classic", "✦", "official brand")
    else:
        style = (getattr(store, "theme_style", "") or "default_black")
        if style == "custom":
            vars_, mode = build_vars(
                store.primary_color, getattr(store, "theme_mode", "dark"),
                accent=store.accent_color, bg=getattr(store, "bg_color", ""),
            )
            layout, name, badge = "market", "🎨 Свой дизайн", ("badge-custom", "🎨", "custom brand")
        else:
            preset = PRESETS.get(THEME_ALIASES.get(style, style)) or PRESETS["default_black"]
            vars_ = dict(preset["vars"])
            mode, layout = preset["mode"], preset["layout"]
            name, badge = (preset.get("short") or preset["name"]), preset["badge"]
        vars_.update(clean_overrides(getattr(store, "theme_overrides", None) or {}))
        layout = getattr(store, "layout_style", "") or layout

    if layout not in LAYOUTS:
        layout = "market"
    font = (getattr(store, "font_style", "") if store else "") or LAYOUT_DEFAULT_FONT.get(layout, "sans")
    if font not in FONTS:
        font = "sans"
    font_family, font_param = FONTS[font]

    vars_.setdefault("on-primary", "#FFFFFF")
    vars_.setdefault("primary-hover", vars_.get("primary", "#4F46E5"))
    vars_["font-display"] = font_family
    vars_["font-body"] = FONTS["sans"][0]

    css = "\n".join(f"            --{k}: {v};" for k, v in vars_.items())
    bg = vars_.get("bg-app", "#0B0E14")
    return {
        "vars": vars_,
        "css": css,
        "mode": mode,
        "layout": layout,
        "font": font,
        "font_url": f"https://fonts.googleapis.com/css2?family={font_param}&display=swap" if font_param else "",
        "name": name,
        "badge": badge,
        "bg": bg if is_hex(bg) else "#0B0E14",
        "primary": vars_.get("primary", "#4F46E5"),
    }


def theme_choices():
    """choices для Store.theme_style, собранные из реестра пресетов."""
    items = [(k, v["name"]) for k, v in PRESETS.items() if k != "default_black"]
    items.append(("custom", "Свой дизайн (Фирменные цвета бренда)"))
    items.append(("default_black", PRESETS["default_black"]["name"]))
    return items
