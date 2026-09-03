from __future__ import annotations

import ctypes
import subprocess
import sys
import time

import pytest

from flowai.codex_process import hidden_process_kwargs
from flowai.process_guard import (
    ProcessTreeGuard,
    guard_process_tree,
    guard_subprocess_tree,
)

SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x00000102

CHILD_SOURCE = (
    "import subprocess, sys, time; "
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
    "print(child.pid, flush=True); "
    "time.sleep(120)"
)


def alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def wait_until_gone(pids: list[int], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(alive(pid) for pid in pids):
        time.sleep(0.05)


def visible_top_level_windows(pid: int) -> list[int]:
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def inspect(hwnd: int, _lparam: int) -> bool:
        owner_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(inspect, 0)
    return found


def test_an_unguarded_tree_reports_itself_inactive() -> None:
    guard = guard_process_tree(0)
    assert guard.active is False
    guard.close()


def test_closing_an_empty_guard_is_a_no_op() -> None:
    guard = ProcessTreeGuard()
    guard.close()
    guard.close()
    assert guard.active is False


def test_guard_subprocess_tree_reads_the_pid() -> None:
    class Fake:
        pid = 0

    assert guard_subprocess_tree(Fake()).active is False
    assert guard_subprocess_tree(None).active is False


@pytest.mark.skipif(sys.platform != "win32", reason="job-об'єкти існують у Windows")
def test_closing_the_guard_kills_grandchildren_too() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_SOURCE],
        stdout=subprocess.PIPE,
        text=True,
        **hidden_process_kwargs({}, platform="win32"),
    )
    grandchild_pid = 0
    try:
        guard = guard_subprocess_tree(child)
        assert guard.active is True
        assert child.stdout is not None
        grandchild_pid = int(child.stdout.readline().strip())
        assert alive(grandchild_pid) is True

        guard.close()
        wait_until_gone([child.pid, grandchild_pid])

        assert alive(grandchild_pid) is False
        assert alive(child.pid) is False
    finally:
        if child.poll() is None:
            child.kill()
        if child.stdout is not None:
            child.stdout.close()
        child.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 HWND існує у Windows")
def test_hidden_launcher_has_no_visible_window_and_guard_still_stops_it() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        **hidden_process_kwargs({}, platform="win32"),
    )
    try:
        guard = guard_subprocess_tree(child)
        assert guard.active is True
        time.sleep(0.1)
        assert visible_top_level_windows(child.pid) == []

        guard.close()
        wait_until_gone([child.pid])

        assert alive(child.pid) is False
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
