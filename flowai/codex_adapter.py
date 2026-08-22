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


class TurnInterrupted(RuntimeError):
    """Хід агента обірвано ззовні — результат неповний і не є успіхом."""


@dataclass(slots=True)
class AgentRun:
    """Результат одного ходу агента разом із його реальними кроками."""

    text: str
    items: list[dict[str, Any]] = field(default_factory=list)
    thread_id: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    context_window: int = 0


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


def paths_from_item(data: dict[str, Any]) -> list[str]:
    """Extract file paths touched by an agent from one normalized item."""
    found: list[str] = []
    changes = data.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            for key in ("path", "file_path", "filePath"):
                value = change.get(key)
                if isinstance(value, str) and value.strip():
                    found.append(value.strip())
    for key in ("path", "file_path", "filePath"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    return list(dict.fromkeys(found))


def _final_response(items: list[Any]) -> str | None:
    """Mirror the SDK's final assistant response selection."""
    last_unknown_phase: str | None = None
    for item in reversed(items):
        payload = getattr(item, "root", item)
        if type(payload).__name__ != "AgentMessageThreadItem":
            continue
        text = str(getattr(payload, "text", "") or "")
        phase = getattr(payload, "phase", None)
        phase_value = str(getattr(phase, "value", phase) or "")
        if phase_value == "final_answer":
            return text
        if phase is None and last_unknown_phase is None:
            last_unknown_phase = text
    return last_unknown_phase


USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def usage_from_turn(result: Any) -> tuple[dict[str, int], int]:
    """Витягти токени останнього ходу та розмір контекстного вікна."""
    usage = getattr(result, "usage", None)
    if usage is None:
        return {}, 0
    breakdown = getattr(usage, "last", None)
    values: dict[str, int] = {}
    for name in USAGE_FIELDS:
        raw = getattr(breakdown, name, None)
        try:
            values[name] = int(raw)
        except (TypeError, ValueError):
            values[name] = 0
    try:
        window = int(getattr(usage, "model_context_window", 0) or 0)
    except (TypeError, ValueError):
        window = 0
    return values, window


def agent_run_from_turn(result: Any, thread_id: str) -> AgentRun:
    """Звести TurnResult до AgentRun, не дозволяючи перерваному ходу пройти далі."""
    status = getattr(result, "status", None)
    status_value = str(getattr(status, "value", status) or "")
    if status_value == "interrupted":
        raise TurnInterrupted(
            "Хід агента перервано до завершення — результат неповний"
        )
    if status_value == "failed":
        error = getattr(result, "error", None)
        message = str(
            getattr(error, "message", "") or "Хід агента завершився помилкою"
        )
        raise CodexUnavailable(message)
    values, window = usage_from_turn(result)
    return AgentRun(
        text=str(getattr(result, "final_response", "") or ""),
        items=normalize_items(getattr(result, "items", None)),
        thread_id=str(thread_id or ""),
        usage=values,
        context_window=window,
    )


class CodexAdapter:
    """Small compatibility layer around the official local Codex Python SDK."""

    def __init__(self) -> None:
        self._client: Any = None
        self._module: Any = None
        self._client_handle: Any = None
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
        # Низькорівневий клієнт уміє довільні JSON-RPC методи skills/*.
        self._client_handle = getattr(self._client, "_client", None)
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
            self._client_handle = None

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

    def list_skills(self, cwd: Path | None = None) -> list[dict[str, Any]]:
        """Повернути каталог скілів очима самого Codex."""
        handle = self._client_handle
        if handle is None or not hasattr(handle, "request"):
            return []
        params: dict[str, Any] = {}
        if cwd is not None:
            params["cwds"] = [str(cwd)]
        try:
            payload = handle.request("skills/list", params)
        except Exception:  # noqa: BLE001 - старий SDK не має цього методу
            LOGGER.info("SDK не підтримує skills/list — читаємо диск")
            return []
        records: list[dict[str, Any]] = []
        entries = payload.get("data") if isinstance(payload, dict) else None
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            for skill in entry.get("skills") or []:
                if isinstance(skill, dict):
                    records.append(skill)
        return records

    def set_skill_enabled(self, name: str, enabled: bool) -> bool:
        """Увімкнути або вимкнути скіл штатним механізмом Codex."""
        handle = self._client_handle
        if handle is None or not hasattr(handle, "request"):
            return False
        try:
            handle.request("skills/config/write", {"name": name, "enabled": enabled})
        except Exception:
            LOGGER.exception("Не вдалося змінити стан скіла %s", name)
            return False
        return True

    def _build_input(
        self,
        prompt: str,
        attachments: list[Path],
        skills: list[dict[str, str]] | None = None,
    ) -> Any:
        """Зібрати мультимодальний вхід, починаючи із закріплених скілів."""
        text_input = getattr(self._module, "TextInput", None)
        image_input = getattr(self._module, "LocalImageInput", None)
        skill_input = getattr(self._module, "SkillInput", None)
        if text_input is None:
            return prompt
        items: list[Any] = []
        if skill_input is not None:
            for skill in skills or []:
                name = str(skill.get("name", "")).strip()
                path = str(skill.get("path", "")).strip()
                if name and path:
                    items.append(skill_input(name=name, path=path))
        items.append(text_input(prompt))
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
        skills: list[dict[str, str]] | None = None,
        resume_thread_id: str = "",
        on_activity: Callable[[dict[str, Any]], None] | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> AgentRun:
        allowed_efforts = {"none", "low", "medium", "high", "xhigh", "max"}
        if reasoning_effort not in allowed_efforts:
            reasoning_effort = "medium"
        attachments = list(attachments or [])
        if os.environ.get("FLOWAI_FAKE_CODEX") == "1":
            return self._fake_run(
                prompt=prompt,
                model=model,
                reasoning_effort=reasoning_effort,
                attachments=attachments,
                skills=list(skills or []),
                resume_thread_id=resume_thread_id,
                on_activity=on_activity,
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
            # A saved Flow owns exactly one writable root: the directory that
            # contains its .flowai.json. Additional folders are source material,
            # never alternate output locations.
            if mcp_servers:
                config.update(mcp_servers)
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

        run_input = self._build_input(prompt, attachments, list(skills or []))
        start_turn = getattr(thread, "turn", None)
        if callable(start_turn):
            turn = start_turn(run_input)
            with self._active_turn_lock:
                self._active_turn = turn
            try:
                result = self._consume_turn(turn, on_activity)
            finally:
                with self._active_turn_lock:
                    if self._active_turn is turn:
                        self._active_turn = None
        else:
            # Compatibility fallback for an older SDK. It cannot be interrupted
            # mid-turn, but the runner still stops before the next node.
            result = thread.run(run_input)
        return agent_run_from_turn(result, str(getattr(thread, "id", "") or ""))

    def _consume_turn(
        self, turn: Any, on_activity: Callable[[dict[str, Any]], None] | None
    ) -> Any:
        """Consume a turn stream while reporting each live item."""
        from openai_codex._run import TurnResult

        stream = turn.stream()
        items: list[Any] = []
        usage = None
        completed = None
        turn_id = str(getattr(turn, "id", "") or "")
        try:
            for event in stream:
                payload = event.payload
                payload_turn_id = str(getattr(payload, "turn_id", "") or "")
                kind = type(payload).__name__
                if payload_turn_id and payload_turn_id != turn_id:
                    continue
                if kind == "ItemStartedNotification" and on_activity is not None:
                    normalized = normalize_items([payload.item])
                    if normalized:
                        entry = normalized[0]
                        on_activity(
                            {
                                "kind": entry["kind"],
                                "summary": entry["summary"],
                                "paths": paths_from_item(entry["detail"]),
                                "phase": "started",
                            }
                        )
                elif kind == "ItemCompletedNotification":
                    items.append(payload.item)
                    normalized = normalize_items([payload.item])
                    if normalized and on_activity is not None:
                        entry = normalized[0]
                        on_activity(
                            {
                                "kind": entry["kind"],
                                "summary": entry["summary"],
                                "paths": paths_from_item(entry["detail"]),
                                "phase": "completed",
                            }
                        )
                elif kind == "ThreadTokenUsageUpdatedNotification":
                    usage = payload.token_usage
                elif kind == "TurnCompletedNotification":
                    completed = payload
        finally:
            stream.close()
        if completed is None:
            raise CodexUnavailable("Хід завершився без події turn/completed")
        turn_data = completed.turn
        return TurnResult(
            id=turn_data.id,
            status=turn_data.status,
            error=turn_data.error,
            started_at=turn_data.started_at,
            completed_at=turn_data.completed_at,
            duration_ms=turn_data.duration_ms,
            final_response=_final_response(items),
            items=items,
            usage=usage,
        )

    @staticmethod
    def _fake_run(
        *,
        prompt: str,
        model: str,
        reasoning_effort: str,
        attachments: list[Path],
        skills: list[dict[str, str]],
        resume_thread_id: str,
        on_activity: Callable[[dict[str, Any]], None] | None,
    ) -> AgentRun:
        thread_id = resume_thread_id or f"fake-thread-{next(_FAKE_THREADS)}"
        call = {
            "prompt": prompt,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "attachments": [str(path) for path in attachments],
            "skills": [str(item.get("name", "")) for item in skills],
            "resumed": bool(resume_thread_id),
            "thread_id": thread_id,
        }
        FAKE_CALLS.append(call)
        if on_activity is not None:
            on_activity(
                {
                    "kind": "fake",
                    "summary": "Тестовий крок",
                    "paths": [],
                    "phase": "completed",
                }
            )
        text = (
            FAKE_RESPONDER(call)
            if FAKE_RESPONDER is not None
            else f"[Тестовий Codex: {model}]\n{prompt}"
        )
        return AgentRun(
            text=text,
            items=[{"kind": "fake", "summary": "Тестовий крок", "detail": {}}],
            thread_id=thread_id,
            usage={
                "input_tokens": len(prompt),
                "cached_input_tokens": 0,
                "output_tokens": len(text),
                "reasoning_output_tokens": 0,
                "total_tokens": len(prompt) + len(text),
            },
            context_window=400000,
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
