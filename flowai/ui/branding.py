from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication, QIcon

APP_USER_MODEL_ID = "FlowAI.Desktop"
APP_ICON_PATH = Path(__file__).parent / "assets" / "icons" / "app.svg"


def start_menu_shortcut() -> Path:
    """Повернути шлях ярлика в Меню «Пуск», потрібного для тостів."""
    programs = (
        Path.home()
        / "AppData"
        / "Roaming"
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
    )
    return programs / "FlowAI.lnk"


def configure_windows_app_id() -> bool:
    """Give Windows a stable identity instead of grouping FlowAI as Python."""
    if sys.platform != "win32":
        return False
    windll = getattr(ctypes, "windll", None)
    shell32 = getattr(windll, "shell32", None)
    setter = getattr(shell32, "SetCurrentProcessExplicitAppUserModelID", None)
    if setter is None:
        return False
    try:
        return int(setter(APP_USER_MODEL_ID)) == 0
    except (OSError, ValueError):
        return False


def application_icon() -> QIcon:
    """Return FlowAI's scalable application icon."""
    return QIcon(str(APP_ICON_PATH))


def export_windows_icon(target: Path) -> Path:
    """Render the bundled SVG to an ICO used by the Windows shortcut."""
    app = QGuiApplication.instance()
    owns_application = app is None
    if owns_application:
        app = QGuiApplication([])
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    image = application_icon().pixmap(QSize(256, 256)).toImage()
    if image.isNull() or not image.save(str(target), "ICO"):
        raise RuntimeError(f"Не вдалося створити іконку FlowAI: {target}")
    if owns_application:
        app.quit()
    return target


def set_shortcut_app_id(shortcut: Path, app_id: str) -> bool:
    """Прописати System.AppUserModel.ID у властивостях ярлика."""
    if sys.platform != "win32":
        return False
    parent = str(shortcut.parent).replace("'", "''")
    name = shortcut.name.replace("'", "''")
    safe_app_id = app_id.replace("'", "''")
    script = (
        "$shell = New-Object -ComObject Shell.Application; "
        f"$folder = $shell.Namespace('{parent}'); "
        f"$item = $folder.ParseName('{name}'); "
        "$link = $item.GetLink; "
        f"$link.SetProperty('System.AppUserModel.ID', '{safe_app_id}'); "
        "$link.Save()"
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return completed.returncode == 0


def _main(arguments: list[str]) -> int:
    if len(arguments) == 4 and arguments[1] == "--set-app-id":
        return 0 if set_shortcut_app_id(Path(arguments[2]), arguments[3]) else 1
    if len(arguments) != 2:
        print(
            "Usage: python -m flowai.ui.branding <target.ico>\n"
            "       python -m flowai.ui.branding --set-app-id <link> <id>",
            file=sys.stderr,
        )
        return 2
    export_windows_icon(Path(arguments[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
