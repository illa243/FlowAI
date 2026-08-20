from __future__ import annotations

import inspect
import logging
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from types import TracebackType
from typing import Any, Self

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})

# Максимальна довжина одного нормалізованого кроку в протоколі роботи.
STEP_DETAIL_LIMIT = 4000
LOGGER = logging.getLogger(__name__)


class CodexUnavailable(RuntimeError):
    pass


@dataclass(slots=True)
class AgentRun:
    """Результат одного ходу агента разом із його реальними кроками."""

    text: str
    items: list[dict[str, Any]] = field(default_factory=list)
    thread_id: str = ""


# У тестовому режимі сюди пишеться кожен виклик — так тести перевіряють,
# що друга ітерація циклу продовжила той самий тред.
FAKE_CALLS: list[dict[str, Any]] = []
FAKE_RESPONDER: Callable[[dict[str, Any]], str] | None = None
_FAKE_THREADS = count(1)


def _clip(value: str) -> str:
    if len(value) <= STEP_DETAIL_LIMIT:
        return value
    return value[:STEP_DETAIL_LIMIT] + "…"


def _summarize_item(payload: Any, data: dict[str, Any]) -> str:
    for key in ("text", "summary", "command", "title", "name", "plan", "status"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value.strip())
        if isinstance(value, list) and value:
            return _clip("; ".join(str(item) for item in value))
    return type(payload).__name__


def normalize_items(items: Any) -> list[dict[str, Any]]:
    """Звести ThreadItem-и SDK до простих дикт для журналу і MD-протоколу."""
    normalized: list[dict[str, Any]] = []
    for item in items or []:
        payload = getattr(item, "root", item)
        data: dict[str, Any] = {}
        dump = getattr(payload, "model_dump", None)
        if callable(dump):
            try:
                data = dump(mode="json", exclude_none=True)
            except (TypeError, ValueError):
                data = {}
        elif isinstance(payload, dict):
            data = dict(payload)
        kind = data.get("type") or getattr(payload, "type", None)
        normalized.append(
            {
                "kind": str(kind or type(payload).__name__),
                "summary": _summarize_item(payload, data),
                "detail": data,
            }
        )
    return normalized


class CodexAdapter:
    """Small compatibility layer around the official local Codex Python SDK."""

    def __init__(self) -> None:
        self._client: Any = None
        self._module: Any = None
        self._active_turn: Any = None
        self._active_turn_lock = threading.Lock()

    def __enter__(self) -> Self:
        if os.environ.get("FLOWAI_FAKE_CODEX") == "1":
            return self
        try:
            import openai_codex
        except ImportError as exc:
            raise CodexUnavailable(
                "Не встановлено openai-codex. Запустіть install.ps1 або "
                "pip install openai-codex."
            ) from exc
        self._module = openai_codex
        self._client = openai_codex.Codex()
        self._client.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        with self._active_turn_lock:
            self._active_turn = None
        if self._client is not None:
            self._client.__exit__(exc_type, exc, traceback)
            self._client = None

    def cancel_active(self) -> bool:
        """Interrupt the currently running Codex turn, if the SDK exposes it."""
        with self._active_turn_lock:
            turn = self._active_turn
        if turn is None or not hasattr(turn, "interrupt"):
            return False
        try:
            turn.interrupt()
        except Exception:
            LOGGER.exception("Could not interrupt the active Codex turn")
            return False
        return True

    def _build_input(self, prompt: str, attachments: list[Path]) -> Any:
        """Зібрати мультимодальний вхід; якщо SDK не вміє — лишити рядок."""
        text_input = getattr(self._module, "TextInput", None)
        image_input = getattr(self._module, "LocalImageInput", None)
        if text_input is None:
            return prompt
        items: list[Any] = [text_input(prompt)]
        if image_input is not None:
            for path in attachments:
                if path.suffix.casefold() in IMAGE_SUFFIXES and path.is_file():
                    items.append(image_input(str(path)))
        if len(items) == 1:
            return prompt
        return items

    def run_agent(
        self,
        *,
        prompt: str,
        developer_instructions: str,
        model: str,
        sandbox: str,
        workspace: Path,
        additional_workspaces: list[Path] | None = None,
        reasoning_effort: str = "medium",
        attachments: list[Path] | None = None,
        resume_thread_id: str = "",
    ) -> AgentRun:
        allowed_efforts = {"none", "low", "medium", "high", "xhigh", "max"}
        if reasoning_effort not in allowed_efforts:
            reasoning_effort = "medium"
        attachments = list(attachments or [])
        if os.environ.get("FLOWAI_FAKE_CODEX") == "1":
            return self._fake_run(
                prompt=prompt,
                model=model,
                attachments=attachments,
                resume_thread_id=resume_thread_id,
            )
        if self._client is None or self._module is None:
            raise CodexUnavailable("Codex SDK не запущено")

        sandbox_map = {
            "read-only": self._module.Sandbox.read_only,
            "workspace-write": self._module.Sandbox.workspace_write,
            "full-access": self._module.Sandbox.full_access,
        }
        kwargs: dict[str, Any] = {
            "model": model,
            "sandbox": sandbox_map.get(sandbox, self._module.Sandbox.read_only),
        }

        # The stable SDK guarantees model/sandbox. Newer SDKs also expose cwd and
        # reasoning configuration; pass them only when the installed signature does.
        starter = self._client.thread_start
        resuming = bool(resume_thread_id) and hasattr(self._client, "thread_resume")
        if resuming:
            starter = self._client.thread_resume
        parameters = inspect.signature(starter).parameters
        if "cwd" in parameters:
            kwargs["cwd"] = str(workspace)
        if "developer_instructions" in parameters and developer_instructions.strip():
            kwargs["developer_instructions"] = developer_instructions.strip()
        if "config" in parameters:
            config: dict[str, Any] = {"model_reasoning_effort": reasoning_effort}
            writable_roots = [
                str(path)
                for path in additional_workspaces or []
                if path.resolve() != workspace.resolve()
            ]
            if sandbox == "workspace-write" and writable_roots:
                config["sandbox_workspace_write"] = {"writable_roots": writable_roots}
            kwargs["config"] = config
        if "model_reasoning_effort" in parameters:
            kwargs["model_reasoning_effort"] = reasoning_effort
        elif "reasoning_effort" in parameters:
            kwargs["reasoning_effort"] = reasoning_effort

        if resuming:
            try:
                thread = starter(resume_thread_id, **kwargs)
            except Exception:  # noqa: BLE001 - тред міг зникнути, починаємо новий
                thread = self._client.thread_start(**kwargs)
        else:
            thread = self._client.thread_start(**kwargs)

        run_input = self._build_input(prompt, attachments)
        start_turn = getattr(thread, "turn", None)
        if callable(start_turn):
            turn = start_turn(run_input)
            with self._active_turn_lock:
                self._active_turn = turn
            try:
                result = turn.run()
            finally:
                with self._active_turn_lock:
                    if self._active_turn is turn:
                        self._active_turn = None
        else:
            # Compatibility fallback for an older SDK. It cannot be interrupted
            # mid-turn, but the runner still stops before the next node.
            result = thread.run(run_input)
        return AgentRun(
            text=str(result.final_response or ""),
            items=normalize_items(getattr(result, "items", None)),
            thread_id=str(getattr(thread, "id", "") or ""),
        )

    @staticmethod
    def _fake_run(
        *,
        prompt: str,
        model: str,
        attachments: list[Path],
        resume_thread_id: str,
    ) -> AgentRun:
        thread_id = resume_thread_id or f"fake-thread-{next(_FAKE_THREADS)}"
        call = {
            "prompt": prompt,
            "model": model,
            "attachments": [str(path) for path in attachments],
            "resumed": bool(resume_thread_id),
            "thread_id": thread_id,
        }
        FAKE_CALLS.append(call)
        text = (
            FAKE_RESPONDER(call)
            if FAKE_RESPONDER is not None
            else f"[Тестовий Codex: {model}]\n{prompt}"
        )
        return AgentRun(
            text=text,
            items=[{"kind": "fake", "summary": "Тестовий крок", "detail": {}}],
            thread_id=thread_id,
        )


def codex_command() -> str | None:
    try:
        from codex_cli_bin import bundled_codex_path

        bundled = bundled_codex_path()
        if bundled and Path(bundled).exists():
            return str(bundled)
    except (ImportError, OSError):
        pass
    return shutil.which("codex") or shutil.which("codex.exe")


def login_status() -> tuple[bool, str]:
    command = codex_command()
    if not command:
        return False, "Команду codex не знайдено"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            [command, "login", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    message = (completed.stdout or completed.stderr).strip()
    return completed.returncode == 0, message or "Статус недоступний"


def start_chatgpt_login() -> None:
    command = codex_command()
    if not command:
        raise CodexUnavailable("Команду codex не знайдено. Спочатку встановіть Codex")
    if sys.platform == "win32":
        subprocess.Popen(
            ["cmd.exe", "/k", command, "login"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        subprocess.Popen([command, "login"])
