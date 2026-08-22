from __future__ import annotations

import logging
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from PySide6.QtCore import QObject, Signal

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ToastAction:
    """Кнопка тоста, id якої повертається при натисканні."""

    id: str
    label: str


def build_toast_xml(
    title: str, body: str, actions: list[ToastAction], *, tag: str
) -> str:
    """Скласти XML тоста з аргументами активації «tag|action»."""
    buttons = "".join(
        "<action activationType='foreground' "
        f"content={quoteattr(action.label)} "
        f"arguments={quoteattr(f'{tag}|{action.id}')} />"
        for action in actions
    )
    return (
        f"<toast launch={quoteattr(f'{tag}|open')} activationType='foreground'>"
        "<visual><binding template='ToastGeneric'>"
        f"<text>{escape(title)}</text>"
        f"<text>{escape(body)}</text>"
        "</binding></visual>"
        f"<actions>{buttons}</actions>"
        "</toast>"
    )


class Toaster(QObject):
    """Windows-тост, що повертає False, коли WinRT недоступний."""

    activated = Signal(str, str)

    def __init__(self, app_id: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.app_id = app_id
        self._notifier: object | None = None
        self._available = False
        self._documents: object | None = None
        self._toast_type: object | None = None
        self._load()

    def _load(self) -> None:
        try:
            from winrt.windows.data.xml.dom import XmlDocument
            from winrt.windows.ui.notifications import (
                ToastNotification,
                ToastNotificationManager,
            )
        except (ImportError, OSError):
            LOGGER.info("WinRT недоступний — залишаємось на трей-балоні")
            return
        try:
            self._notifier = ToastNotificationManager.create_toast_notifier(
                self.app_id
            )
        except TypeError:
            # pywinrt 3.x відкриває лише overload без аргументів. Після
            # configure_windows_app_id Windows бере AUMID поточного процесу.
            try:
                self._notifier = (
                    ToastNotificationManager.create_toast_notifier()
                )
            except OSError:
                LOGGER.info(
                    "Не вдалося створити ToastNotifier для %s", self.app_id
                )
                return
        except OSError:
            LOGGER.info("Не вдалося створити ToastNotifier для %s", self.app_id)
            return
        self._documents = XmlDocument
        self._toast_type = ToastNotification
        self._available = True

    def available(self) -> bool:
        return self._available

    def show(
        self,
        title: str,
        body: str,
        *,
        tag: str,
        actions: list[ToastAction],
    ) -> bool:
        """Показати тост; False означає відкат на трей-балон."""
        if not self._available or self._notifier is None:
            return False
        xml = build_toast_xml(title, body, actions, tag=tag)
        try:
            document = self._documents()  # type: ignore[misc]
            document.load_xml(xml)
            toast = self._toast_type(document)  # type: ignore[misc]
            toast.add_activated(
                lambda _sender, args: self._handle_argument(
                    str(getattr(args, "arguments", "") or tag)
                )
            )
            self._notifier.show(toast)  # type: ignore[attr-defined]
        except OSError:
            LOGGER.exception("Тост не показався")
            return False
        return True

    def _handle_argument(self, argument: str) -> None:
        tag, _separator, action = argument.partition("|")
        self.activated.emit(tag, action or "open")
