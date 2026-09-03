"""Чи справді read-only забороняє запис, а не просить про нього.

Sandbox задає лише базову політику. Якщо не передати approval_mode, SDK бере
ApprovalMode.auto_review: агент упирається в стіну, просить ескалацію — і її
схвалює автоматичний рецензент, без людини й без відома FlowAI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import openai_codex
import pytest

from flowai import codex_adapter


@pytest.fixture(autouse=True)
def _real_codex_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тут потрібен справжній шлях run_agent, а не заглушка.

    test_grill_ui ставить FLOWAI_FAKE_CODEX при імпорті модуля й не прибирає,
    тож у повному прогоні змінна витікає сюди.
    """

    monkeypatch.delenv("FLOWAI_FAKE_CODEX", raising=False)


class _Turn:
    status = None
    final_response = "готово"
    items: ClassVar[list[Any]] = []
    usage = None


class _Thread:
    id = "thread-1"

    def run(self, _run_input: object) -> _Turn:
        return _Turn()


class RecordingClient:
    """Клієнт із сигнатурою реального SDK, який лише запамʼятовує kwargs."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def thread_start(
        self,
        *,
        model: Any = None,
        sandbox: Any = None,
        approval_mode: Any = None,
        cwd: Any = None,
        developer_instructions: Any = None,
        config: Any = None,
    ) -> _Thread:
        self.kwargs = {
            "model": model,
            "sandbox": sandbox,
            "approval_mode": approval_mode,
            "cwd": cwd,
        }
        return _Thread()


class LegacyClient:
    """Старий SDK без approval_mode — його не можна ламати зайвим kwarg."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def thread_start(self, *, model: Any = None, sandbox: Any = None) -> _Thread:
        self.kwargs = {"model": model, "sandbox": sandbox}
        return _Thread()


def _run(client: object, sandbox: str) -> None:
    adapter = codex_adapter.CodexAdapter()
    adapter._module = openai_codex
    adapter._client = client
    adapter.run_agent(
        prompt="Перевір результат",
        developer_instructions="",
        model="gpt-5.6-sol",
        sandbox=sandbox,
        workspace=Path.cwd(),
    )


def test_a_read_only_node_cannot_ask_for_an_escalation() -> None:
    client = RecordingClient()

    _run(client, "read-only")

    assert client.kwargs["sandbox"] == openai_codex.Sandbox.read_only
    assert client.kwargs["approval_mode"] == openai_codex.ApprovalMode.deny_all


def test_a_writing_node_keeps_the_automatic_reviewer() -> None:
    client = RecordingClient()

    _run(client, "workspace-write")

    assert client.kwargs["sandbox"] == openai_codex.Sandbox.workspace_write
    assert client.kwargs["approval_mode"] == openai_codex.ApprovalMode.auto_review


def test_an_unknown_sandbox_value_is_treated_as_read_only() -> None:
    """Невідоме значення вже падало в read_only — межа має падати разом із ним."""

    client = RecordingClient()

    _run(client, "")

    assert client.kwargs["sandbox"] == openai_codex.Sandbox.read_only
    assert client.kwargs["approval_mode"] == openai_codex.ApprovalMode.deny_all


def test_an_sdk_without_approval_mode_is_left_alone() -> None:
    client = LegacyClient()

    _run(client, "read-only")

    assert "approval_mode" not in client.kwargs
    assert client.kwargs["sandbox"] == openai_codex.Sandbox.read_only
