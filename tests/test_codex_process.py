from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from flowai.codex_process import create_codex, hidden_process_kwargs


class FakeStartupInfo:
    def __init__(self) -> None:
        self.dwFlags = 0
        self.wShowWindow = 9


class FakeSubprocess:
    CREATE_NO_WINDOW = 0x08000000
    STARTF_USESHOWWINDOW = 0x00000001
    SW_HIDE = 0
    PIPE = object()
    STARTUPINFO = FakeStartupInfo

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def Popen(self, *args: Any, **kwargs: Any) -> object:
        self.calls.append((args, kwargs))
        return object()


def test_hidden_process_kwargs_adds_windows_flags_without_losing_values() -> None:
    module = FakeSubprocess()
    original_startup = FakeStartupInfo()
    original_startup.dwFlags = 0x20
    values = {
        "creationflags": 0x04,
        "startupinfo": original_startup,
        "stdin": "stdin",
        "stdout": "stdout",
        "stderr": "stderr",
        "cwd": "C:/project",
        "env": {"A": "B"},
        "text": True,
        "encoding": "utf-8",
        "bufsize": 1,
    }

    hidden = hidden_process_kwargs(
        values, platform="win32", subprocess_module=module
    )

    assert hidden["creationflags"] == 0x04 | module.CREATE_NO_WINDOW
    assert hidden["startupinfo"] is not original_startup
    assert hidden["startupinfo"].dwFlags == 0x20 | module.STARTF_USESHOWWINDOW
    assert hidden["startupinfo"].wShowWindow == module.SW_HIDE
    assert original_startup.dwFlags == 0x20
    for key in (
        "stdin",
        "stdout",
        "stderr",
        "cwd",
        "env",
        "text",
        "encoding",
        "bufsize",
    ):
        assert hidden[key] == values[key]


def test_hidden_process_kwargs_does_nothing_off_windows() -> None:
    values = {"creationflags": 7, "text": True}
    hidden = hidden_process_kwargs(
        values, platform="linux", subprocess_module=FakeSubprocess()
    )
    assert hidden == values
    assert hidden is not values


def test_create_codex_hides_only_transport_popen_and_restores_module() -> None:
    original = FakeSubprocess()
    transport = SimpleNamespace(subprocess=original)
    received: dict[str, Any] = {}

    class Module:
        @staticmethod
        def Codex(config: Any = None) -> Any:
            received["config"] = config
            received["transport_during_constructor"] = transport.subprocess
            return transport.subprocess.Popen(
                ["codex.exe", "app-server"],
                stdin="stdin",
                stdout="stdout",
                stderr="stderr",
                cwd="C:/project",
                env={"A": "B"},
                text=True,
                encoding="utf-8",
                bufsize=1,
                creationflags=0x10,
            )

    config = object()
    client = create_codex(
        config,
        codex_module=Module,
        transport_module=transport,
        platform="win32",
    )

    assert client is not None
    assert received["config"] is config
    assert received["transport_during_constructor"] is not original
    assert transport.subprocess is original
    assert len(original.calls) == 1
    _args, kwargs = original.calls[0]
    assert kwargs["creationflags"] == 0x10 | original.CREATE_NO_WINDOW
    assert kwargs["startupinfo"].dwFlags & original.STARTF_USESHOWWINDOW
    assert kwargs["startupinfo"].wShowWindow == original.SW_HIDE
    assert kwargs["stdin"] == "stdin"
    assert kwargs["stdout"] == "stdout"
    assert kwargs["stderr"] == "stderr"


def test_create_codex_restores_transport_after_constructor_error() -> None:
    original = FakeSubprocess()
    transport = SimpleNamespace(subprocess=original)

    class Module:
        @staticmethod
        def Codex() -> Any:
            assert transport.subprocess is not original
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        create_codex(
            codex_module=Module,
            transport_module=transport,
            platform="win32",
        )

    assert transport.subprocess is original


def test_create_codex_serializes_concurrent_constructor_patches() -> None:
    original = FakeSubprocess()
    transport = SimpleNamespace(subprocess=original)
    state_lock = threading.Lock()
    active = 0
    maximum = 0

    class Module:
        @staticmethod
        def Codex() -> object:
            nonlocal active, maximum
            with state_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with state_lock:
                active -= 1
            return object()

    threads = [
        threading.Thread(
            target=create_codex,
            kwargs={
                "codex_module": Module,
                "transport_module": transport,
                "platform": "win32",
            },
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum == 1
    assert transport.subprocess is original


def test_create_codex_falls_back_when_transport_is_unavailable() -> None:
    calls: list[Any] = []

    class Module:
        @staticmethod
        def Codex(config: Any = None) -> object:
            calls.append(config)
            return object()

    config = object()
    client = create_codex(
        config,
        codex_module=Module,
        transport_module=SimpleNamespace(),
        platform="win32",
    )

    assert client is not None
    assert calls == [config]
