"""Система тем витрины BrainMart: пресеты, генератор «с нуля», сборка CSS-переменных."""
from .resolve import resolve_theme, theme_choices, clean_overrides, LAYOUTS, FONTS, FONT_LABELS  # noqa: F401
from .presets import PRESETS  # noqa: F401
from .generator import extract_palette_from_image, build_vars  # noqa: F401
