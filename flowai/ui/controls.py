from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QEnterEvent,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
)
from PySide6.QtWidgets import QSizePolicy, QToolButton, QWidget

from .design import COLORS, CONTROL_HEIGHT, DURATION, RADII, SPACE
from .icons import icon as load_icon

VARIANTS: dict[str, dict[str, str]] = {
    "primary": {"bg": COLORS["accent"], "hover": COLORS["accent_hover"], "text": COLORS["accent_text"], "border": COLORS["accent_hover"]},
    "secondary": {"bg": COLORS["surface_raised"], "hover": COLORS["border_strong"], "text": COLORS["text"], "border": COLORS["border"]},
    "ghost": {"bg": "transparent", "hover": COLORS["surface_raised"], "text": COLORS["text_muted"], "border": "transparent"},
    "success": {"bg": COLORS["success"], "hover": "#16A34A", "text": "#04140A", "border": COLORS["success"]},
    "danger": {"bg": COLORS["danger"], "hover": "#DC2626", "text": "#FFFFFF", "border": COLORS["danger"]},
}


def _blend(start: str | QColor, end: str | QColor, amount: float) -> QColor:
    first, second = QColor(start), QColor(end)
    if not first.isValid():
        return second
    ratio = max(0.0, min(1.0, amount))
    return QColor(
        round(first.red() + (second.red() - first.red()) * ratio),
        round(first.green() + (second.green() - first.green()) * ratio),
        round(first.blue() + (second.blue() - first.blue()) * ratio),
        round(first.alpha() + (second.alpha() - first.alpha()) * ratio),
    )


class AnimatedButton(QToolButton):
    """Кнопка з плавним hover/press, яких Qt Style Sheets не підтримують."""

    def __init__(
        self,
        text: str = "",
        variant: str = "secondary",
        icon_name: str = "",
        color: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.variant = variant if variant in VARIANTS else "secondary"
        self.button_color = color if QColor(color).isValid() else ""
        self._hover = 0.0
        self._press = 0.0
        self._button_text = text
        self._icon_name = icon_name
        self.setText(text)
        self.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            if text
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.setMinimumHeight(CONTROL_HEIGHT)
        self.setIconSize(QSize(16, 16))
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoRaise(True)
        self._apply_icon()
        self.setMinimumWidth(self.minimumSizeHint().width())

        self._hover_animation = QPropertyAnimation(self, b"hover_progress", self)
        self._hover_animation.setDuration(DURATION["fast"])
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._press_animation = QPropertyAnimation(self, b"press_progress", self)
        self._press_animation.setDuration(DURATION["fast"])
        self._press_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def setDefaultAction(self, action: QAction) -> None:
        super().setDefaultAction(action)
        self.setText(self._button_text)
        self._apply_icon()
        self.setMinimumWidth(self.minimumSizeHint().width())

    def _button_palette(self) -> dict[str, str]:
        if not self.button_color:
            return VARIANTS[self.variant]
        return {
            "bg": self.button_color,
            "hover": _blend(self.button_color, "#FFFFFF", 0.14).name(),
            "text": "#FFFFFF",
            "border": _blend(self.button_color, "#FFFFFF", 0.20).name(),
        }

    def _apply_icon(self) -> None:
        if self._icon_name:
            self.setIcon(load_icon(self._icon_name, self._button_palette()["text"]))

    def _content_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def sizeHint(self) -> QSize:
        base = super().sizeHint()
        width = SPACE["md"] * 2
        if not self.icon().isNull():
            width += self.iconSize().width()
        if self._button_text:
            if not self.icon().isNull():
                width += SPACE["sm"]
            width += QFontMetrics(self._content_font()).horizontalAdvance(
                self._button_text
            )
        return QSize(max(base.width(), width), max(base.height(), CONTROL_HEIGHT))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def get_hover_progress(self) -> float:
        return self._hover

    def set_hover_progress(self, value: float) -> None:
        self._hover = float(value)
        self.update()

    hover_progress = Property(float, get_hover_progress, set_hover_progress)

    def get_press_progress(self) -> float:
        return self._press

    def set_press_progress(self, value: float) -> None:
        self._press = float(value)
        self.update()

    press_progress = Property(float, get_press_progress, set_press_progress)

    def _animate(self, animation: QPropertyAnimation, target: float) -> None:
        animation.stop()
        animation.setStartValue(
            self._hover if animation is self._hover_animation else self._press
        )
        animation.setEndValue(target)
        animation.start()

    def enterEvent(self, event: QEnterEvent) -> None:
        self._animate(self._hover_animation, 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._animate(self._hover_animation, 0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._animate(self._press_animation, 1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._animate(self._press_animation, 0.0)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        palette = self._button_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, RADII["sm"], RADII["sm"])

        if self.isEnabled():
            background = _blend(palette["bg"], palette["hover"], self._hover)
            background = _blend(background, COLORS["surface_sunken"], self._press * 0.35)
            text_color = QColor(palette["text"])
            border_color = QColor(palette["border"])
        else:
            background = QColor(COLORS["surface"])
            text_color = QColor(COLORS["text_dim"])
            border_color = QColor(COLORS["border"])

        painter.fillPath(path, background)
        painter.setPen(border_color)
        painter.drawPath(path)
        content = self.rect().adjusted(SPACE["md"], 0, -SPACE["md"], 0)
        if not self.icon().isNull():
            size = self.iconSize()
            icon_rect = QRect(0, 0, size.width(), size.height())
            icon_rect.moveLeft(content.left())
            icon_rect.moveTop(content.center().y() - size.height() // 2)
            self.icon().paint(painter, icon_rect)
            content.setLeft(icon_rect.right() + SPACE["sm"])

        if self.text():
            painter.setFont(self._content_font())
            painter.setPen(text_color)
            alignment = Qt.AlignmentFlag.AlignCenter
            if not self.icon().isNull():
                alignment = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
            painter.drawText(content, alignment, self.text())
        painter.end()
