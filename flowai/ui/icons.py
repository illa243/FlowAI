from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .design import COLORS

ICONS_DIR = Path(__file__).parent / "assets" / "icons"


@lru_cache(maxsize=256)
def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    """SVG-іконка, підфарбована кольором теми."""
    path = ICONS_DIR / f"{name}.svg"
    if not path.is_file():
        return QIcon()
    markup = path.read_text(encoding="utf-8").replace(
        "currentColor", color or COLORS["text"]
    )
    renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
