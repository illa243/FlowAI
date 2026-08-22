from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .design import COLORS, DURATION, SPACE
from .paths import IMAGE_SUFFIXES, open_file, path_menu

FILE_SCHEME = "flowai-file"
THUMBNAIL_HEIGHT = 120
ACTIVITY_INTERVAL_MS = 100


def _path_url(raw_path: str) -> QUrl:
    url = QUrl.fromLocalFile(str(Path(raw_path).resolve()))
    url.setScheme(FILE_SCHEME)
    return url


class LogView(QTextBrowser):
    """Execution log where file paths are interactive links."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("logView")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.document().setMaximumBlockCount(10_000)
        self.anchorClicked.connect(self._anchor_clicked)

    @staticmethod
    def _path_from(url: QUrl) -> str:
        if url.scheme() != FILE_SCHEME:
            return ""
        path = url.path()
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path

    def _anchor_clicked(self, url: QUrl) -> None:
        path = self._path_from(url)
        if path:
            open_file(path)

    def contextMenuEvent(self, event: Any) -> None:
        anchor = self.anchorAt(event.pos())
        if anchor:
            path = self._path_from(QUrl(anchor))
            if path:
                path_menu(path, self).exec(event.globalPos())
                return
        super().contextMenuEvent(event)


class LogPanel(QWidget):
    """Execution history with a throttled live activity line."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["xs"])

        self.view = LogView(self)
        layout.addWidget(self.view, 1)

        self.activity_label = QLabel("", self)
        self.activity_label.setObjectName("activityLine")
        self.activity_label.setWordWrap(False)
        self.activity_label.setTextFormat(Qt.TextFormat.PlainText)
        self.activity_label.hide()
        layout.addWidget(self.activity_label)

        self._effect = QGraphicsOpacityEffect(self.activity_label)
        self.activity_label.setGraphicsEffect(self._effect)
        self._breath = QPropertyAnimation(self._effect, b"opacity", self)
        self._breath.setDuration(DURATION["slow"] * 4)
        self._breath.setStartValue(0.55)
        self._breath.setKeyValueAt(0.5, 1.0)
        self._breath.setEndValue(0.55)
        self._breath.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._breath.setLoopCount(-1)

        self._pending_activity: tuple[str, str] | None = None
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(ACTIVITY_INTERVAL_MS)
        self._activity_timer.timeout.connect(self.flush_activity)
        self._activity_timer.start()

    def set_activity(self, text: str, color: str) -> None:
        """Queue activity; UI updates are limited to ten per second."""
        self._pending_activity = (text, color)

    def flush_activity(self) -> None:
        if self._pending_activity is None:
            return
        text, color = self._pending_activity
        self._pending_activity = None
        if not text:
            self._breath.stop()
            self._effect.setOpacity(1.0)
            self.activity_label.hide()
            return
        self.activity_label.setText(f"⟳  {text}")
        self.activity_label.setStyleSheet(
            f"color: {color or COLORS['text_muted']};"
        )
        self.activity_label.show()
        if self._breath.state() != QPropertyAnimation.State.Running:
            self._breath.start()

    def clear(self) -> None:
        self.view.clear()

    def render_entries(self, entries: list[dict[str, Any]]) -> None:
        self.view.clear()
        for entry in entries:
            self.append_entry(entry)

    def append_entry(self, entry: dict[str, Any]) -> None:
        color = str(entry.get("color") or COLORS["text_muted"])
        timestamp = html.escape(str(entry.get("timestamp", "")))
        text = str(entry.get("text", ""))
        file_paths = [str(item) for item in entry.get("file_paths", []) if str(item)]
        image_paths = [
            str(item) for item in entry.get("image_paths", []) if str(item)
        ]
        body = self._linkify(html.escape(text), file_paths)
        block = (
            f'<div style="color:{color}; margin:2px 0;">'
            f'<span style="color:{COLORS["text_dim"]};">[{timestamp}]</span> '
            f"{body}</div>"
        )
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(block)
        for raw_path in dict.fromkeys([*file_paths, *image_paths]):
            self._insert_thumbnail(raw_path)
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _linkify(escaped_text: str, file_paths: list[str]) -> str:
        for raw_path in sorted(file_paths, key=len, reverse=True):
            needle = html.escape(raw_path)
            if needle not in escaped_text:
                continue
            href = html.escape(_path_url(raw_path).toString(), quote=True)
            anchor = (
                f'<a href="{href}" '
                f'style="color:{COLORS["focus"]}; text-decoration:underline;">'
                f"{html.escape(Path(raw_path).name)}</a>"
            )
            escaped_text = escaped_text.replace(needle, anchor)
        return escaped_text.replace("\n", "<br/>")

    def _insert_thumbnail(self, raw_path: str) -> None:
        path = Path(raw_path)
        if path.suffix.casefold() not in IMAGE_SUFFIXES or not path.is_file():
            return
        image = QImage(str(path))
        if image.isNull():
            return
        if image.height() > THUMBNAIL_HEIGHT:
            image = image.scaledToHeight(
                THUMBNAIL_HEIGHT, Qt.TransformationMode.SmoothTransformation
            )
        url = _path_url(raw_path)
        self.view.document().addResource(
            QTextDocument.ResourceType.ImageResource, url, image
        )
        href = html.escape(url.toString(), quote=True)
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(
            f'<div style="margin:4px 0 8px 0;"><a href="{href}">'
            f'<img src="{href}"/></a></div>'
        )
