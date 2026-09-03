from __future__ import annotations

import copy
import importlib
import subprocess
import sys
import threading
from types import ModuleType
from typing import Any

_CODEX_CONSTRUCTOR_LOCK = threading.RLock()


def hidden_process_kwargs(
    kwargs: dict[str, Any],
    *,
    platform: str | None = None,
    subprocess_module: Any = subprocess,
) -> dict[str, Any]:
    """Return Popen kwargs that cannot create a visible console on Windows."""
    values = dict(kwargs)
    if (platform or sys.platform) != "win32":
        return values

    create_no_window = int(
        getattr(subprocess_module, "CREATE_NO_WINDOW", 0x08000000)
    )
    values["creationflags"] = int(values.get("creationflags", 0) or 0) | (
        create_no_window
    )

    startupinfo = values.get("startupinfo")
    if startupinfo is None:
        startupinfo_type = getattr(subprocess_module, "STARTUPINFO", None)
        if startupinfo_type is not None:
            startupinfo = startupinfo_type()
    else:
        try:
            startupinfo = copy.copy(startupinfo)
        except (TypeError, copy.Error):
            pass
    if startupinfo is not None:
        use_show_window = int(
            getattr(subprocess_module, "STARTF_USESHOWWINDOW", 0x00000001)
        )
        startupinfo.dwFlags = int(getattr(startupinfo, "dwFlags", 0) or 0) | (
            use_show_window
        )
        startupinfo.wShowWindow = int(
            getattr(subprocess_module, "SW_HIDE", 0)
        )
        values["startupinfo"] = startupinfo
    return values


class _HiddenSubprocessProxy:
    """Delegate an SDK transport module while hardening only its Popen call."""

    def __init__(self, delegate: Any, *, platform: str | None = None) -> None:
        self._delegate = delegate
        self._platform = platform

    def Popen(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate.Popen(
            *args,
            **hidden_process_kwargs(
                kwargs,
                platform=self._platform,
                subprocess_module=self._delegate,
            ),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def _transport_for(codex_module: Any) -> ModuleType | Any | None:
    module_name = str(getattr(codex_module, "__name__", ""))
    if not module_name:
        return None
    try:
        return importlib.import_module(f"{module_name}.client")
    except (ImportError, ModuleNotFoundError):
        return None


def create_codex(
    config: Any = None,
    *,
    codex_module: Any = None,
    transport_module: Any = None,
    platform: str | None = None,
) -> Any:
    """Construct Codex while hiding the SDK app-server console on Windows.

    The installed SDK does not currently expose Popen kwargs. Its constructor
    starts the transport synchronously, so the narrow module proxy is installed
    only for that constructor and is always restored before this function exits.
    """
    if codex_module is None:
        import openai_codex as codex_module

    constructor = codex_module.Codex
    selected_platform = platform or sys.platform
    if selected_platform != "win32":
        return constructor(config=config) if config is not None else constructor()

    transport = transport_module or _transport_for(codex_module)
    if transport is None:
        return constructor(config=config) if config is not None else constructor()

    with _CODEX_CONSTRUCTOR_LOCK:
        original = getattr(transport, "subprocess", None)
        if original is None or not hasattr(original, "Popen"):
            return (
                constructor(config=config)
                if config is not None
                else constructor()
            )
        proxy = _HiddenSubprocessProxy(original, platform=selected_platform)
        transport.subprocess = proxy
        try:
            return (
                constructor(config=config)
                if config is not None
                else constructor()
            )
        finally:
            transport.subprocess = original
