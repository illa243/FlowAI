from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QFontDatabase

LOGGER = logging.getLogger(__name__)
FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
UI_FALLBACK = "Segoe UI"
MONO_FALLBACK = "Consolas"


def _load_family(file_names: list[str]) -> str:
    families: list[str] = []
    for name in file_names:
        path = FONTS_DIR / name
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            LOGGER.warning("Не вдалося завантажити шрифт %s", path)
            continue
        families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families[0] if families else ""


def load_fonts() -> tuple[str, str]:
    """Завантажити вбудовані шрифти; повернути родини UI й коду."""
    ui_family = _load_family(
        [
            "Inter-Regular.ttf",
            "Inter-Medium.ttf",
            "Inter-SemiBold.ttf",
            "Inter-Bold.ttf",
        ]
    )
    mono_family = _load_family(["JetBrainsMono-Regular.ttf"])
    return ui_family or UI_FALLBACK, mono_family or MONO_FALLBACK
