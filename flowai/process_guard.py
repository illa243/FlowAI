"""Прив'язка дерева дочірніх процесів до життя самого FlowAI.

Codex app-server лишає по одному ``node_repl.exe`` на кожен хід агента, а
``Popen.terminate()`` у Windows убиває лише сам app-server — його нащадки
переживають і зупинку клієнта, і аварійне вбивство FlowAI. Job-об'єкт із
прапорцем ``KILL_ON_JOB_CLOSE`` знімає обидва випадки: дескриптор закриється
або явно, або самою системою, коли процес FlowAI зникне.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

LOGGER = logging.getLogger(__name__)

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001

_DWORD = ctypes.c_uint32
_HANDLE = ctypes.c_void_p


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", _DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", _DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", _DWORD),
        ("SchedulingClass", _DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32() -> Any:
    """kernel32 з явними сигнатурами — інакше ctypes обріже 64-бітні дескриптори."""
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    library.CreateJobObjectW.restype = _HANDLE
    library.SetInformationJobObject.argtypes = [
        _HANDLE,
        _DWORD,
        ctypes.c_void_p,
        _DWORD,
    ]
    library.SetInformationJobObject.restype = ctypes.c_int
    library.OpenProcess.argtypes = [_DWORD, ctypes.c_int, _DWORD]
    library.OpenProcess.restype = _HANDLE
    library.AssignProcessToJobObject.argtypes = [_HANDLE, _HANDLE]
    library.AssignProcessToJobObject.restype = ctypes.c_int
    library.CloseHandle.argtypes = [_HANDLE]
    library.CloseHandle.restype = ctypes.c_int
    return library


class ProcessTreeGuard:
    """Тримає job-об'єкт; ``close()`` знімає все дерево разом із нащадками."""

    def __init__(self, job_handle: int = 0) -> None:
        self._job_handle = job_handle

    @property
    def active(self) -> bool:
        return bool(self._job_handle)

    def close(self) -> None:
        if not self._job_handle:
            return
        handle, self._job_handle = self._job_handle, 0
        try:
            _kernel32().CloseHandle(_HANDLE(handle))
        except Exception:
            # Прибирання не має валити зупинку Flow.
            LOGGER.exception("Не вдалося закрити job-об'єкт дерева процесів")


def guard_process_tree(pid: int) -> ProcessTreeGuard:
    """Забрати процес ``pid`` та його майбутніх нащадків у kill-on-close job."""
    if sys.platform != "win32" or pid <= 0:
        return ProcessTreeGuard()
    try:
        kernel32 = _kernel32()
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            LOGGER.warning(
                "CreateJobObject не вдався (код %s)", ctypes.get_last_error()
            )
            return ProcessTreeGuard()
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = kernel32.SetInformationJobObject(
            _HANDLE(job),
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        process = kernel32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, int(pid)
        )
        assigned = 0
        if configured and process:
            assigned = kernel32.AssignProcessToJobObject(_HANDLE(job), _HANDLE(process))
        if process:
            kernel32.CloseHandle(_HANDLE(process))
        if not assigned:
            LOGGER.warning(
                "Процес %s не вдалося прив'язати до job-об'єкта (код %s); "
                "дочірні процеси доведеться прибирати вручну",
                pid,
                ctypes.get_last_error(),
            )
            kernel32.CloseHandle(_HANDLE(job))
            return ProcessTreeGuard()
        return ProcessTreeGuard(int(job))
    except Exception:
        # Захист не має заважати запуску агента.
        LOGGER.exception("Не вдалося створити job-об'єкт для процесу %s", pid)
        return ProcessTreeGuard()


def guard_subprocess_tree(process: Any) -> ProcessTreeGuard:
    """Те саме, але приймає об'єкт із атрибутом ``pid`` (``Popen`` тощо)."""
    pid = int(getattr(process, "pid", 0) or 0)
    return guard_process_tree(pid)
