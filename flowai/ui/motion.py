from __future__ import annotations

import math
import time

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
)
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QDialog, QGraphicsOpacityEffect, QWidget

from .design import DURATION
from .platform import apply_dark_titlebar

PULSE_PERIOD = 1.6
RISE_PIXELS = 8


def pulse(moment: float | None = None) -> float:
    """Плавна хвиля 0…1 з періодом 1,6 секунди."""
    value = time.monotonic() if moment is None else moment
    return (math.sin(value * math.tau / PULSE_PERIOD) + 1.0) / 2.0


def fade_in(
    widget: QWidget, duration: int | None = None
) -> QParallelAnimationGroup:
    """Поява вікна: прозорість 0→1 і підйом на 8 px."""
    span = duration or DURATION["base"]
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    effect.setOpacity(0.0)

    opacity = QPropertyAnimation(effect, b"opacity", widget)
    opacity.setDuration(span)
    opacity.setStartValue(0.0)
    opacity.setEndValue(1.0)
    opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

    group = QParallelAnimationGroup(widget)
    group.addAnimation(opacity)
    if not widget.isMaximized() and not widget.isFullScreen():
        end = widget.pos()
        start = QPoint(end.x(), end.y() + RISE_PIXELS)
        move = QPropertyAnimation(widget, b"pos", widget)
        move.setDuration(span)
        move.setStartValue(start)
        move.setEndValue(end)
        move.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(move)
    group.finished.connect(lambda: widget.setGraphicsEffect(None))
    group.start()
    return group


class AnimatedDialog(QDialog):
    """Діалог із темною системною шапкою та плавною появою."""

    def showEvent(self, event: QShowEvent) -> None:
        apply_dark_titlebar(self)
        super().showEvent(event)
        if not getattr(self, "_appeared", False):
            self._appeared = True
            self._appear_animation = fade_in(self)
