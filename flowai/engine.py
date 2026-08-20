from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .codex_adapter import CodexAdapter
from .models import (
    AGENT_KINDS,
    DEFAULT_PORT,
    RESULT_PORTS,
    FlowNode,
    Workflow,
    managed_task_title,
    normalize_managed_tasks,
)
from .templating import (
    extract_json,
    render_template,
    resolve_path,
    safe_eval,
    stringify,
)
from .work_review import PROTOCOL_NAME, REPORT_NAME, WorkReviewProtocol

EventCallback = Callable[[dict[str, Any]], None]
LOGGER = logging.getLogger(__name__)

# Запобіжник від нескінченного циклу, якщо лічильники Result налаштовані надто щедро.
MAX_STEPS = 200


class WorkflowError(RuntimeError):
    pass


class RunCancelled(RuntimeError):
    pass


class InterventionRequired(RuntimeError):
    def __init__(self, request: dict[str, Any]) -> None:
        super().__init__(
            str(request.get("question") or "Потрібна відповідь користувача")
        )
        self.request = request


@dataclass(slots=True)
class NodeResult:
    node_id: str
    status: str
    text: str = ""
    data: Any = field(default_factory=dict)
    error: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "text": self.text,
            "data": self.data,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NodeResult:
        return cls(
            node_id=str(raw.get("node_id", "")),
            status=str(raw.get("status", "")),
            text=str(raw.get("text", "")),
            data=raw.get("data") or {},
            error=str(raw.get("error", "")),
            started_at=str(raw.get("started_at", "")),
            finished_at=str(raw.get("finished_at", "")),
            duration_seconds=float(raw.get("duration_seconds", 0.0)),
        )


@dataclass
class RunCheckpoint:
    """Повний стан запуску: дозволяє продовжити Flow після втручання.

    Стара модель «нода виконана — більше не чіпаємо» не працює з циклами,
    тому зберігається саме стан планувальника, а не лише результати.
    """

    started: bool = False
    steps: int = 0
    queue: list[str] = field(default_factory=list)
    pending_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    port_counts: dict[str, int] = field(default_factory=dict)
    limit_grants: dict[str, int] = field(default_factory=dict)
    thread_ids: dict[str, str] = field(default_factory=dict)
    iterations: dict[str, int] = field(default_factory=dict)
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    protocol_records: dict[str, list[str]] = field(default_factory=dict)
    protocol_path: str = ""
    task_progress: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "steps": self.steps,
            "queue": list(self.queue),
            "pending_inputs": self.pending_inputs,
            "port_counts": dict(self.port_counts),
            "limit_grants": dict(self.limit_grants),
            "thread_ids": dict(self.thread_ids),
            "iterations": dict(self.iterations),
            "outputs": self.outputs,
            "history": self.history,
            "protocol_records": self.protocol_records,
            "protocol_path": self.protocol_path,
            "task_progress": self.task_progress,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RunCheckpoint:
        return cls(
            started=bool(raw.get("started", False)),
            steps=int(raw.get("steps", 0)),
            queue=[str(item) for item in raw.get("queue", [])],
            pending_inputs=dict(raw.get("pending_inputs") or {}),
            port_counts=dict(raw.get("port_counts") or {}),
            limit_grants=dict(raw.get("limit_grants") or {}),
            thread_ids=dict(raw.get("thread_ids") or {}),
            iterations=dict(raw.get("iterations") or {}),
            outputs=dict(raw.get("outputs") or {}),
            history=dict(raw.get("history") or {}),
            protocol_records=dict(raw.get("protocol_records") or {}),
            protocol_path=str(raw.get("protocol_path", "")),
            task_progress=dict(raw.get("task_progress") or {}),
        )

    def node_results(self) -> dict[str, NodeResult]:
        return {
            node_id: NodeResult.from_dict(raw) for node_id, raw in self.outputs.items()
        }


class WorkflowRunner:
    def __init__(
        self,
        workflow: Workflow,
        *,
        project_path: Path | None = None,
        on_event: EventCallback | None = None,
        checkpoint: RunCheckpoint | None = None,
        intervention_responses: dict[str, Any] | None = None,
        run_directory: Path | None = None,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self.workflow = workflow
        self.project_path = project_path
        self.on_event = on_event or (lambda event: None)
        self.checkpoint = checkpoint or RunCheckpoint()
        self.intervention_responses = dict(intervention_responses or {})
        self.run_directory = run_directory
        self.max_steps = max_steps
        self.protocol: WorkReviewProtocol | None = None
        self.outputs: dict[str, NodeResult] = self.checkpoint.node_results()
        self._stop = threading.Event()
        self._topo_index: dict[str, int] = {}
        self._last_steps: list[dict[str, Any]] = []
        self._last_resumed = False
        self._last_prompt = ""
        self._last_instructions = ""
        self._last_attachments: list[str] = []
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._control_lock = threading.RLock()
        self._pause_generation = 0
        self._active_codex: CodexAdapter | None = None

    def cancel(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._resume_event.set()
        with self._control_lock:
            codex = self._active_codex
        if codex is not None:
            threading.Thread(
                target=codex.cancel_active,
                name="FlowAI-Codex-Interrupt",
                daemon=True,
            ).start()
        self._emit("run_cancel_requested", message="Запит на зупинку отримано")

    def pause(self, reason: str = "Систему призупинено") -> None:
        if self._stop.is_set() or not self._resume_event.is_set():
            return
        with self._control_lock:
            self._pause_generation += 1
            self._resume_event.clear()
            codex = self._active_codex
        if codex is not None:
            threading.Thread(
                target=codex.cancel_active,
                name="FlowAI-Codex-Pause",
                daemon=True,
            ).start()
        self._emit("run_paused", message=reason)

    def resume(self, reason: str = "Роботу системи відновлено") -> None:
        if self._stop.is_set() or self._resume_event.is_set():
            return
        self._resume_event.set()
        self._emit("run_resumed", message=reason)

    def update_node_config(self, node_id: str, updates: dict[str, Any]) -> bool:
        """Apply explicitly safe live settings to the runner snapshot."""
        allowed = {"true_limit", "false_limit", "wait_for_confirmation"}
        clean = {key: value for key, value in updates.items() if key in allowed}
        node = self.workflow.find(node_id)
        if node is None or node.kind != "result" or not clean:
            return False
        with self._control_lock:
            node.config.update(clean)
        self._emit(
            "node_config_updated",
            node=node,
            message="Параметри Result оновлено під час виконання",
            updates=clean,
        )
        return True

    def _wait_until_resumed(self) -> None:
        while not self._resume_event.wait(0.25):
            if self._stop.is_set():
                raise RunCancelled("Flow зупинено")
        if self._stop.is_set():
            raise RunCancelled("Flow зупинено")

    def _stage(self, node: FlowNode, current: int, name: str) -> None:
        self._emit(
            "node_stage",
            node=node,
            message=name,
            stage=current,
            stage_total=6,
            stage_name=name,
        )

    # ------------------------------------------------------------------
    # Основний цикл
    # ------------------------------------------------------------------

    def run(self) -> RunCheckpoint:
        errors = self.workflow.validate()
        if errors:
            raise WorkflowError("\n".join(errors))
        workspace = self.workflow.resolved_workspace(self.project_path)
        if not workspace.is_dir():
            raise WorkflowError(f"Робоча папка не існує: {workspace}")

        self._emit("run_started", message=f"Запуск Flow «{self.workflow.name}»")
        self._topo_index = {
            node_id: index
            for index, node_id in enumerate(self.workflow.topological_order())
        }

        reviewer = next(iter(self.workflow.nodes_of_kind("work_reviewer")), None)
        if not self.checkpoint.started:
            self.checkpoint.queue = [
                node.id
                for node in self.workflow.routed_nodes()
                if not any(
                    (source := self.workflow.find(edge.source)) is not None
                    and source.kind != "result"
                    for edge in self.workflow.incoming(node.id)
                )
            ]
            self.checkpoint.started = True
        if reviewer is not None:
            self._start_protocol(reviewer)

        needs_codex = any(node.kind in AGENT_KINDS for node in self.workflow.nodes)
        codex_context = CodexAdapter() if needs_codex else nullcontext(None)

        status = "success"
        paused = False
        failure = ""
        with codex_context as codex:
            with self._control_lock:
                self._active_codex = codex
            while self.checkpoint.queue:
                try:
                    self._wait_until_resumed()
                except RunCancelled:
                    self._emit("run_cancelled", message="Flow зупинено")
                    status = "cancelled"
                    break

                node_id = self._take_next()
                node = self.workflow.node(node_id)
                self.checkpoint.steps += 1
                if self.checkpoint.steps > self.max_steps:
                    raise WorkflowError(
                        f"Перевищено ліміт кроків ({self.max_steps}). "
                        "Схоже, Flow зациклився — перевірте ліміти блоку Result."
                    )

                inputs = self.checkpoint.pending_inputs.pop(node_id, {})
                iteration = self.checkpoint.iterations.get(node_id, 0) + 1
                self._emit(
                    "node_started", node=node, inputs=inputs, iteration=iteration
                )
                self._stage(node, 1, "Підготовка вхідних даних")
                started = time.perf_counter()
                started_at = datetime.now(UTC).isoformat()
                self._last_steps = []
                self._last_resumed = False

                try:
                    result = self._execute_with_retries(
                        node=node, inputs=inputs, workspace=workspace, codex=codex
                    )
                except RunCancelled:
                    result = NodeResult(
                        node_id=node.id,
                        status="cancelled",
                        text="Flow зупинено користувачем",
                        started_at=started_at,
                        finished_at=datetime.now(UTC).isoformat(),
                        duration_seconds=round(time.perf_counter() - started, 3),
                    )
                    self._store(node, result, iteration=iteration, count=False)
                    self._emit("node_cancelled", node=node, result=result)
                    self._emit("run_cancelled", message="Flow зупинено")
                    status = "cancelled"
                    break
                except InterventionRequired as exc:
                    # Нода має виконатися ще раз після відповіді — повертаємо
                    # її вхідні дані та місце в черзі.
                    self.checkpoint.pending_inputs[node_id] = inputs
                    self.checkpoint.queue.insert(0, node_id)
                    self.checkpoint.steps -= 1
                    result = NodeResult(
                        node_id=node.id,
                        status="waiting",
                        text=str(exc),
                        data={"request": exc.request},
                        started_at=started_at,
                        finished_at=datetime.now(UTC).isoformat(),
                        duration_seconds=round(time.perf_counter() - started, 3),
                    )
                    self._store(node, result, iteration=iteration, count=False)
                    self._emit("node_waiting", node=node, result=result)
                    self._emit(
                        "intervention_required",
                        node=node,
                        message=str(exc),
                        request=exc.request,
                    )
                    paused = True
                    break
                except Exception as exc:
                    LOGGER.exception(
                        "Node failed: workflow=%r node=%r id=%s iteration=%s",
                        self.workflow.name,
                        node.title,
                        node.id,
                        iteration,
                    )
                    result = NodeResult(
                        node_id=node.id,
                        status="failed",
                        error=str(exc),
                        started_at=started_at,
                        finished_at=datetime.now(UTC).isoformat(),
                        duration_seconds=round(time.perf_counter() - started, 3),
                    )
                    self._store(node, result, iteration=iteration)
                    self._record_protocol(node, result, iteration)
                    self._emit("node_failed", node=node, result=result)
                    self._emit(
                        "run_failed", message=f"Помилка в ноді «{node.title}»: {exc}"
                    )
                    status = "failed"
                    failure = str(exc)
                    break

                self._stage(node, 6, "Збереження та передача результату")
                try:
                    self._wait_until_resumed()
                except RunCancelled:
                    result.status = "cancelled"
                    result.text = "Flow зупинено користувачем"
                    result.finished_at = datetime.now(UTC).isoformat()
                    result.duration_seconds = round(time.perf_counter() - started, 3)
                    self._store(node, result, iteration=iteration, count=False)
                    self._emit("node_cancelled", node=node, result=result)
                    self._emit("run_cancelled", message="Flow зупинено")
                    status = "cancelled"
                    break
                result.started_at = started_at
                result.finished_at = datetime.now(UTC).isoformat()
                result.duration_seconds = round(time.perf_counter() - started, 3)
                self._store(node, result, iteration=iteration)
                self._record_protocol(node, result, iteration)
                self._emit(
                    "node_finished",
                    node=node,
                    result=result,
                    iteration=iteration,
                    port_counts=dict(self.checkpoint.port_counts),
                )
                self._dispatch(node, result)

            if self._stop.is_set() and status != "cancelled":
                status = "cancelled"
                self._emit("run_cancelled", message="Flow зупинено")
            if not paused and status != "cancelled":
                if self.protocol is not None:
                    self.protocol.finish(status)
                if reviewer is not None and codex is not None:
                    try:
                        self._run_work_reviewer(reviewer, workspace, codex, status)
                    except RunCancelled:
                        status = "cancelled"
                        cancelled = NodeResult(
                            reviewer.id,
                            "cancelled",
                            text="Work Reviewer зупинено користувачем",
                        )
                        self._emit("node_cancelled", node=reviewer, result=cancelled)
                        self._emit("run_cancelled", message="Flow зупинено")
            with self._control_lock:
                self._active_codex = None

        if not paused and status == "success":
            self._emit("run_finished", message="Flow виконано успішно")
        elif failure:
            pass
        return self.checkpoint

    def _take_next(self) -> str:
        """Взяти ноду з найменшим топологічним індексом.

        Це вирішує проблему з'єднання: якщо нода чекає дані одразу з кількох
        гілок, вона не запуститься раніше за своїх попередників.
        """
        limit = len(self._topo_index)
        node_id = min(
            self.checkpoint.queue,
            key=lambda item: self._topo_index.get(item, limit),
        )
        self.checkpoint.queue.remove(node_id)
        return node_id

    def _store(
        self, node: FlowNode, result: NodeResult, *, iteration: int, count: bool = True
    ) -> None:
        self.outputs[node.id] = result
        self.checkpoint.outputs[node.id] = result.to_dict()
        self.checkpoint.history.setdefault(node.id, []).append(result.to_dict())
        if count:
            self.checkpoint.iterations[node.id] = iteration

    def _dispatch(self, node: FlowNode, result: NodeResult) -> None:
        port = DEFAULT_PORT
        note = ""
        if node.kind in {"result", "tasks_manager"} and isinstance(result.data, dict):
            port = str(result.data.get("branch") or DEFAULT_PORT)
            note = str(result.data.get("user_note") or "")

        source = result.to_dict()
        all_outputs = {key: value.to_dict() for key, value in self.outputs.items()}
        for edge in self.workflow.outgoing(node.id, port):
            value = resolve_path(source, edge.source_path, default=None)
            context = {"source": source, "value": value, "outputs": all_outputs}
            if edge.condition.strip() and not bool(safe_eval(edge.condition, context)):
                continue
            bucket = self.checkpoint.pending_inputs.setdefault(edge.target, {})
            if edge.transform.strip():
                value = render_template(edge.transform, {**context, "inputs": bucket})
            self._assign_input(bucket, edge.target_variable, value)
            if note:
                bucket["user_note"] = note
            if edge.target not in self.checkpoint.queue:
                self.checkpoint.queue.append(edge.target)

    @staticmethod
    def _assign_input(inputs: dict[str, Any], name: str, value: Any) -> None:
        key = name.strip()
        if key not in inputs:
            inputs[key] = value
        elif isinstance(inputs[key], list):
            inputs[key].append(value)
        else:
            inputs[key] = [inputs[key], value]

    @staticmethod
    def _existing_input_files(value: Any, workspace: Path) -> list[str]:
        found: list[str] = []

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                for nested in item.values():
                    visit(nested)
                return
            if isinstance(item, (list, tuple, set)):
                for nested in item:
                    visit(nested)
                return
            if not isinstance(item, str) or not item.strip() or len(item) > 2048:
                return
            candidate = Path(item.strip()).expanduser()
            if not candidate.is_absolute():
                candidate = workspace / candidate
            try:
                resolved = candidate.resolve()
                exists = resolved.is_file()
            except OSError:
                return
            path = str(resolved)
            if exists and path not in found:
                found.append(path)

        visit(value)
        return found

    def _execute_with_retries(
        self,
        *,
        node: FlowNode,
        inputs: dict[str, Any],
        workspace: Path,
        codex: CodexAdapter | None,
    ) -> NodeResult:
        retries = max(0, int(node.config.get("retries", 0)))
        attempt = 0
        while attempt <= retries:
            self._wait_until_resumed()
            with self._control_lock:
                pause_generation = self._pause_generation
            try:
                return self._execute_node(node, inputs, workspace, codex)
            except InterventionRequired:
                raise
            except RunCancelled:
                raise
            except Exception as exc:
                if self._stop.is_set():
                    raise RunCancelled("Flow зупинено") from exc
                with self._control_lock:
                    interrupted_by_system = pause_generation != self._pause_generation
                if interrupted_by_system:
                    self._wait_until_resumed()
                    self._emit(
                        "node_retry",
                        node=node,
                        message="З'єднання відновлено після сну або блокування ПК",
                    )
                    continue
                if attempt >= retries:
                    raise
                attempt += 1
                self._emit(
                    "node_retry",
                    node=node,
                    message=f"Повторна спроба {attempt + 1} з {retries + 1}",
                )
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------
    # Виконавці нод
    # ------------------------------------------------------------------

    def _execute_node(
        self,
        node: FlowNode,
        inputs: dict[str, Any],
        workspace: Path,
        codex: CodexAdapter | None,
    ) -> NodeResult:
        context = {
            "inputs": inputs,
            **inputs,
            "workflow": {"name": self.workflow.name},
            "node_iteration": self.checkpoint.iterations.get(node.id, 0) + 1,
        }

        if node.kind == "entry":
            self._stage(node, 4, "Обробка початкових даних")
            configured = node.config.get("json")
            data = dict(configured) if isinstance(configured, dict) else {}
            text = str(node.config.get("text", ""))
            data.setdefault("text", text)
            data.setdefault("task", text)
            data["attachments"] = [
                str(item) for item in node.config.get("attachments", []) if str(item)
            ]
            return NodeResult(node.id, "success", text=text, data=data)

        if node.kind == "tasks_manager":
            self._stage(node, 4, "Вибір наступного завдання")
            return self._execute_tasks_manager(node)

        if node.kind == "result":
            self._stage(node, 4, "Перевірка умови розгалуження")
            return self._execute_result(node, inputs, context, workspace)

        if node.kind in AGENT_KINDS:
            return self._execute_agent(node, inputs, context, workspace, codex)

        raise WorkflowError(f"Тип ноди ще не підтримується: {node.kind}")

    def _execute_tasks_manager(self, node: FlowNode) -> NodeResult:
        tasks = normalize_managed_tasks(node.config.get("tasks"))
        valid_ids = {str(task["id"]) for task in tasks}
        progress = self.checkpoint.task_progress.setdefault(
            node.id,
            {"active_task_id": "", "completed_task_ids": []},
        )
        completed = [
            str(task_id)
            for task_id in progress.get("completed_task_ids", [])
            if str(task_id) in valid_ids
        ]
        active_id = str(progress.get("active_task_id", ""))
        if active_id in valid_ids and active_id not in completed:
            completed.append(active_id)

        active_task: dict[str, Any] | None = next(
            (task for task in tasks if str(task["id"]) not in completed),
            None,
        )
        active_id = str(active_task["id"]) if active_task is not None else ""
        progress["active_task_id"] = active_id
        progress["completed_task_ids"] = completed

        states = []
        for index, task in enumerate(tasks):
            task_id = str(task["id"])
            status = (
                "completed"
                if task_id in completed
                else "running"
                if task_id == active_id
                else "pending"
            )
            states.append(
                {
                    "id": task_id,
                    "title": managed_task_title(task, index),
                    "status": status,
                }
            )

        branch = "next" if active_task is not None else "done"
        message = (
            f"Активовано: {managed_task_title(active_task, tasks.index(active_task))}"
            if active_task is not None
            else "Усі завдання виконано"
        )
        self._emit(
            "tasks_progress",
            node=node,
            message=message,
            task_states=states,
            active_task_id=active_id,
            completed_count=len(completed),
            task_count=len(tasks),
        )

        if active_task is None:
            data = {
                "branch": branch,
                "tasks": states,
                "completed_count": len(completed),
                "task_count": len(tasks),
            }
            return NodeResult(node.id, "success", text=message, data=data)

        task_index = tasks.index(active_task)
        prompt = str(active_task.get("prompt", ""))
        attachments = [
            str(path) for path in active_task.get("attachments", []) if str(path)
        ]
        task_payload = {
            "id": active_id,
            "index": task_index,
            "number": task_index + 1,
            "title": managed_task_title(active_task, task_index),
            "prompt": prompt,
            "attachments": attachments,
        }
        data = {
            "branch": branch,
            "prompt": prompt,
            "task": task_payload,
            "attachments": attachments,
            "tasks": states,
            "completed_count": len(completed),
            "task_count": len(tasks),
        }
        return NodeResult(node.id, "success", text=prompt, data=data)

    def _execute_result(
        self,
        node: FlowNode,
        inputs: dict[str, Any],
        context: dict[str, Any],
        workspace: Path,
    ) -> NodeResult:
        verdict = self._verdict_from(inputs)
        reason = self._reason_from(inputs)
        review = self._review_payload_from(inputs)
        must_fix = review.get("must_fix")
        if not isinstance(must_fix, list):
            must_fix = []
        candidate_path = self._candidate_path_from(inputs)
        port = "true" if verdict else "false"

        user_note = ""
        forced = False
        response = self.intervention_responses.pop(node.id, None)
        action = ""
        if isinstance(response, dict):
            action = str(response.get("action", ""))

        with self._control_lock:
            wait_for_confirmation = bool(
                node.config.get("wait_for_confirmation", False)
            )
        if wait_for_confirmation and action not in {
            "continue",
            "add_attempts",
            "force_branch",
        }:
            raise InterventionRequired(
                {
                    "node_id": node.id,
                    "node_title": node.title,
                    "type": "result_confirmation",
                    "port": port,
                    "verdict": verdict,
                    "reason": reason,
                    "must_fix": must_fix,
                    "candidate_path": candidate_path,
                    "files": self._existing_input_files(
                        {
                            "inputs": inputs,
                            "outputs": [
                                result.data for result in self.outputs.values()
                            ],
                        },
                        workspace,
                    ),
                    "question": "Перевірте проміжні файли та підтвердьте продовження",
                }
            )

        if isinstance(response, dict):
            if action == "add_attempts":
                grant = max(1, int(response.get("count", 1)))
                key = f"{node.id}:{port}"
                self.checkpoint.limit_grants[key] = (
                    self.checkpoint.limit_grants.get(key, 0) + grant
                )
                user_note = str(response.get("note", "")).strip()
            elif action == "force_branch":
                branch = str(response.get("branch", "")).strip()
                if branch in RESULT_PORTS:
                    port = branch
                    forced = True
                user_note = str(response.get("note", "")).strip()

        key = f"{node.id}:{port}"
        used = self.checkpoint.port_counts.get(key, 0)
        with self._control_lock:
            configured_limit = self.workflow.result_port_limit(node, port)
        limit = configured_limit + self.checkpoint.limit_grants.get(key, 0)
        if not forced and used + 1 > limit:
            raise InterventionRequired(
                {
                    "node_id": node.id,
                    "node_title": node.title,
                    "type": "result_limit",
                    "port": port,
                    "used": used,
                    "limit": limit,
                    "verdict": verdict,
                    "reason": reason,
                    "must_fix": must_fix,
                    "candidate_path": candidate_path,
                    "question": (
                        f"Гілка {port.upper()} вичерпала ліміт проходів: "
                        f"{used} з {limit}"
                    ),
                }
            )
        if not forced:
            self.checkpoint.port_counts[key] = used + 1

        saved_to = ""
        if port == "true":
            text = render_template(
                str(node.config.get("template", "{{inputs}}")), context
            )
            save_path = str(node.config.get("save_path", "")).strip()
            if save_path:
                target = Path(save_path).expanduser()
                if not target.is_absolute():
                    target = workspace / target
                target = target.resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                saved_to = str(target)
        else:
            text = reason or "Результат відправлено на переробку"

        data: dict[str, Any] = {
            "verdict": verdict,
            "branch": port,
            "result": text,
            "saved_to": saved_to,
            "reason": reason,
            "must_fix": must_fix,
            "candidate_path": candidate_path,
            "review": review,
            "forced": forced,
        }
        data["retry_context"] = {
            "reason": reason,
            "must_fix": must_fix,
            "candidate_path": candidate_path,
            "previous_review": review,
            "instruction": (
                "Виправ лише перелічені must_fix. Не змінюй прийняті області. "
                "Після перевірки атомарно онови candidate_path."
            ),
        }
        if user_note:
            data["user_note"] = user_note
        return NodeResult(node.id, "success", text=text, data=data)

    def _execute_agent(
        self,
        node: FlowNode,
        inputs: dict[str, Any],
        context: dict[str, Any],
        workspace: Path,
        codex: CodexAdapter | None,
        *,
        extra_attachments: list[Path] | None = None,
    ) -> NodeResult:
        if codex is None:
            raise WorkflowError("Codex не ініціалізовано")
        artifact_before = self._required_artifact_state(node, workspace)

        self._stage(node, 2, "Формування промпту та вкладень")
        context = dict(context)
        if node.kind == "prompt_reviewer":
            context["flow_chain"] = self.workflow.describe_chain(node.id)
            if not str(context.get("entry_prompt", "")).strip():
                managed_input = next(
                    (
                        value
                        for key in ("task", "input", "data")
                        if isinstance((value := inputs.get(key)), dict)
                        and str(value.get("prompt", "")).strip()
                    ),
                    None,
                )
                context["entry_prompt"] = (
                    str(managed_input["prompt"])
                    if managed_input is not None
                    else stringify(inputs)
                    if inputs
                    else ""
                )
        elif (
            node.kind == "task_reviewer"
            and not str(context.get("criteria", "")).strip()
        ):
            context["criteria"] = self._resolve_criteria(node)

        attachments = self._collect_attachments(node, inputs, workspace)
        attachments.extend(extra_attachments or [])

        additional_workspaces = self._resolved_agent_folders(node, workspace)
        prompt = self._compose_agent_prompt(
            node, context, workspace, additional_workspaces, attachments
        )
        instructions = self._compose_agent_instructions(node, workspace)

        memory = str(node.config.get("memory", "thread"))
        resume_id = (
            self.checkpoint.thread_ids.get(node.id, "") if memory == "thread" else ""
        )

        self._stage(node, 3, "Підключення до моделі")
        self._emit(
            "agent_prompt",
            node=node,
            message="Сформований промпт",
            prompt=prompt,
            instructions=instructions,
            workspace=str(workspace),
            additional_workspaces=[str(path) for path in additional_workspaces],
            attachments=[str(path) for path in attachments],
            resumed=bool(resume_id),
        )

        self._stage(node, 4, "Виконання агентом")
        run = codex.run_agent(
            prompt=prompt,
            developer_instructions=instructions,
            model=str(node.config.get("model", "gpt-5.6-terra")),
            sandbox=str(node.config.get("sandbox", "read-only")),
            workspace=workspace,
            additional_workspaces=additional_workspaces,
            reasoning_effort=str(node.config.get("reasoning_effort", "medium")),
            attachments=attachments,
            resume_thread_id=resume_id,
        )
        if self._stop.is_set():
            raise RunCancelled("Flow зупинено")
        self._stage(node, 5, "Обробка відповіді агента")
        self._last_steps = run.items
        for step in run.items:
            kind = str(step.get("kind", "крок"))
            summary = str(step.get("summary", "")).strip()
            if summary:
                self._emit(
                    "agent_step",
                    node=node,
                    message=f"{kind}: {summary}",
                )
        self._last_resumed = bool(resume_id)
        if run.thread_id and memory == "thread":
            self.checkpoint.thread_ids[node.id] = run.thread_id

        parsed = extract_json(run.text)
        text = run.text
        if node.kind == "task_reviewer":
            if not isinstance(parsed, dict) or "verdict" not in parsed:
                raise WorkflowError(
                    f"Блок «{node.title}» має повернути JSON із полем verdict. "
                    f"Отримано: {run.text[:200]}"
                )
            parsed["verdict"] = bool(parsed.get("verdict"))
            data: Any = parsed
        elif parsed is not None:
            data = parsed
        else:
            data = {"response": run.text}

        data = self._verify_required_artifact(
            node, data, workspace, before_state=artifact_before
        )

        generated_files = self._existing_input_files(run.items, workspace)
        if generated_files and isinstance(data, dict):
            data = dict(data)
            data["_generated_files"] = generated_files

        if node.kind == "prompt_reviewer" and isinstance(data, dict):
            improved = data.get("improved_prompt")
            if isinstance(improved, str) and improved.strip():
                text = improved

        return NodeResult(node.id, "success", text=text, data=data)

    # ------------------------------------------------------------------
    # Допоміжне
    # ------------------------------------------------------------------

    @staticmethod
    def _resolved_file(raw_path: str, workspace: Path) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = workspace / path
        return path.resolve()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().upper()

    def _required_artifact_state(
        self, node: FlowNode, workspace: Path
    ) -> tuple[int, int, int] | None:
        required_raw = str(node.config.get("required_output_path", "")).strip()
        if not required_raw:
            return None
        required = self._resolved_file(required_raw, workspace)
        if not required.is_file():
            return None
        stat = required.stat()
        return stat.st_mtime_ns, stat.st_size, stat.st_ino

    def _verify_required_artifact(
        self,
        node: FlowNode,
        data: Any,
        workspace: Path,
        *,
        before_state: tuple[int, int, int] | None = None,
    ) -> Any:
        """Verify and fingerprint an agent artifact before downstream dispatch.

        A configured ``required_output_path`` turns the reported path into a strict
        handoff contract: the exact file must exist when the node completes. This
        prevents a reviewer from accidentally checking an older or renamed file.
        """
        required_raw = str(node.config.get("required_output_path", "")).strip()
        protected_raw = str(node.config.get("protected_source_path", "")).strip()
        if not required_raw and not protected_raw:
            return data
        if not isinstance(data, dict):
            raise WorkflowError(
                f"Нода «{node.title}» має повернути JSON з інформацією про артефакт"
            )

        enriched = dict(data)
        protected: Path | None = None
        if protected_raw:
            protected = self._resolved_file(protected_raw, workspace)
            if not protected.is_file():
                raise WorkflowError(f"Захищений вихідний файл зник: {protected}")
            expected_hash = str(node.config.get("protected_source_sha256", "")).strip()
            if expected_hash:
                actual_hash = self._file_sha256(protected)
                if actual_hash.casefold() != expected_hash.casefold():
                    raise WorkflowError(
                        f"Нода «{node.title}» змінила захищений оригінал: {protected}"
                    )

        if not required_raw:
            return enriched

        required = self._resolved_file(required_raw, workspace)
        if protected is not None and required == protected:
            raise WorkflowError("Вихідний артефакт не може перезаписувати оригінал")
        reported_raw = str(
            enriched.get("candidate_path") or enriched.get("output_path") or ""
        ).strip()
        if reported_raw:
            reported = self._resolved_file(reported_raw, workspace)
            if reported != required:
                raise WorkflowError(
                    f"Нода «{node.title}» повідомила неправильний шлях артефакта: "
                    f"{reported}. Очікується: {required}"
                )
        if not required.is_file():
            raise WorkflowError(
                f"Нода «{node.title}» не створила обов'язковий артефакт: {required}"
            )

        try:
            stat = required.stat()
            artifact_hash = self._file_sha256(required)
        except OSError as exc:
            raise WorkflowError(
                f"Не вдалося зафіксувати артефакт «{required}»: {exc}"
            ) from exc
        current_state = stat.st_mtime_ns, stat.st_size, stat.st_ino
        if before_state is not None and current_state == before_state:
            raise WorkflowError(
                f"Нода «{node.title}» не оновила артефакт цього проходу: {required}"
            )
        enriched["candidate_path"] = str(required)
        enriched["output_path"] = str(required)
        enriched["artifact"] = {
            "path": str(required),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": artifact_hash,
        }
        return enriched

    @staticmethod
    def _verdict_from(inputs: dict[str, Any]) -> bool:
        if "verdict" in inputs:
            return bool(inputs["verdict"])
        for key in ("review", "task_review", "verdict_data"):
            candidate = resolve_path(inputs, f"{key}.verdict", default=None)
            if candidate is not None:
                return bool(candidate)
        for value in inputs.values():
            if isinstance(value, dict) and "verdict" in value:
                return bool(value["verdict"])
        raise WorkflowError(
            "Блок Result не отримав вердикт. Проведіть у нього з'єднання від "
            "Task Reviewer (data → review або data.verdict → verdict)."
        )

    @staticmethod
    def _review_payload_from(inputs: dict[str, Any]) -> dict[str, Any]:
        for key in ("review", "task_review", "verdict_data"):
            candidate = inputs.get(key)
            if isinstance(candidate, dict) and "verdict" in candidate:
                return dict(candidate)
        for value in inputs.values():
            if isinstance(value, dict) and "verdict" in value:
                return dict(value)
        return {}

    def _candidate_path_from(self, inputs: dict[str, Any]) -> str:
        def inspect(value: Any) -> str:
            if not isinstance(value, dict):
                return ""
            for key in ("candidate_path", "output_path"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            artifact = value.get("artifact")
            if isinstance(artifact, dict):
                candidate = artifact.get("path")
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            for nested in value.values():
                candidate = inspect(nested)
                if candidate:
                    return candidate
            return ""

        candidate = inspect(inputs)
        if candidate:
            return candidate
        for result in reversed(list(self.outputs.values())):
            candidate = inspect(result.data)
            if candidate:
                return candidate
        return ""

    @staticmethod
    def _reason_from(inputs: dict[str, Any]) -> str:
        for key in ("reason", "review", "task_review"):
            if key == "reason" and isinstance(inputs.get(key), str):
                return str(inputs[key])
            candidate = resolve_path(inputs, f"{key}.reason", default=None)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        for value in inputs.values():
            if isinstance(value, dict) and isinstance(value.get("reason"), str):
                return str(value["reason"])
        return ""

    def _nearest_upstream(
        self, node_id: str, kinds: tuple[str, ...]
    ) -> FlowNode | None:
        seen: set[str] = set()
        frontier = [edge.source for edge in self.workflow.incoming(node_id)]
        found: dict[str, FlowNode] = {}
        while frontier:
            following: list[str] = []
            for current in frontier:
                if current in seen:
                    continue
                seen.add(current)
                node = self.workflow.find(current)
                if node is None:
                    continue
                found.setdefault(node.kind, node)
                following.extend(
                    edge.source for edge in self.workflow.incoming(current)
                )
            for kind in kinds:
                if kind in found:
                    return found[kind]
            frontier = following
        for kind in kinds:
            if kind in found:
                return found[kind]
        return None

    def _resolve_criteria(self, node: FlowNode) -> str:
        reference = str(node.config.get("criteria_node", "")).strip()
        target = (
            self.workflow.find(reference)
            if reference
            else self._nearest_upstream(node.id, ("prompt_reviewer", "entry"))
        )
        if target is None:
            return ""
        result = self.outputs.get(target.id)
        if result is None:
            return str(target.config.get("text", ""))
        if isinstance(result.data, dict):
            improved = result.data.get("improved_prompt")
            if isinstance(improved, str) and improved.strip():
                return improved
        return result.text

    def _collect_attachments(
        self, node: FlowNode, inputs: dict[str, Any], workspace: Path
    ) -> list[Path]:
        raw: list[str] = [
            str(item) for item in node.config.get("attachments", []) if str(item)
        ]
        incoming = inputs.get("attachments")
        if isinstance(incoming, str):
            raw.append(incoming)
        elif isinstance(incoming, list):
            raw.extend(str(item) for item in incoming if str(item))
        raw.extend(self._active_managed_task_attachments(node))

        resolved: list[Path] = []
        for item in raw:
            path = Path(item).expanduser()
            if not path.is_absolute():
                path = workspace / path
            if path not in resolved:
                resolved.append(path)
        return resolved

    def _active_managed_task_attachments(self, target: FlowNode) -> list[str]:
        attachments: list[str] = []
        for manager in self.workflow.nodes_of_kind("tasks_manager"):
            if not self._is_forward_upstream(manager.id, target.id):
                continue
            progress = self.checkpoint.task_progress.get(manager.id, {})
            active_id = str(progress.get("active_task_id", ""))
            task = next(
                (
                    item
                    for item in normalize_managed_tasks(manager.config.get("tasks"))
                    if str(item["id"]) == active_id
                ),
                None,
            )
            if task is None:
                continue
            for path in task.get("attachments", []):
                value = str(path)
                if value and value not in attachments:
                    attachments.append(value)
        return attachments

    def _is_forward_upstream(self, source_id: str, target_id: str) -> bool:
        seen: set[str] = set()
        stack = [edge.target for edge in self.workflow.outgoing(source_id)]
        while stack:
            current = stack.pop()
            if current == target_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            current_node = self.workflow.find(current)
            if current_node is not None and current_node.kind == "result":
                continue
            stack.extend(edge.target for edge in self.workflow.outgoing(current))
        return False

    def _compose_agent_prompt(
        self,
        node: FlowNode,
        context: dict[str, Any],
        workspace: Path | None = None,
        additional_workspaces: list[Path] | None = None,
        attachments: list[Path] | None = None,
    ) -> str:
        inputs = context.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}
        if node.config.get("prompt_source") == "input":
            if "prompt" not in inputs:
                raise WorkflowError(
                    f"Нода «{node.title}» очікує вхідну змінну «prompt». "
                    "Передайте text попередньої ноди у prompt."
                )
            incoming_prompt = inputs.get("prompt")
            prompt = (
                incoming_prompt.strip()
                if isinstance(incoming_prompt, str)
                else stringify(incoming_prompt).strip()
            )
            if isinstance(incoming_prompt, dict):
                prompt = (
                    "Це структурований контракт повторного проходу. Виконай лише "
                    "перелічені must_fix і збережи всі вже прийняті частини.\n\n"
                    + prompt
                )
            if not prompt:
                raise WorkflowError(f"Нода «{node.title}» отримала порожній промпт")
            visible_inputs = {
                key: value
                for key, value in inputs.items()
                if key not in {"prompt", "attachments", "user_note"}
            }
        else:
            prompt = render_template(
                str(node.config.get("prompt", "")), context
            ).strip()
            visible_inputs = {
                key: value
                for key, value in inputs.items()
                if key not in {"attachments", "user_note"}
            }

        output_format = str(node.config.get("output_format", "text"))
        schema = node.config.get("output_schema") or {}

        sections = ["# Завдання\n" + prompt]
        user_note = str(inputs.get("user_note", "")).strip()
        if user_note:
            sections.append(
                "# Додаткова вимога від користувача\n"
                "Це вказівка людини після невдалих спроб — виконай її обов'язково.\n"
                + user_note
            )
        if visible_inputs:
            sections.append("# Вхідні дані\n" + stringify(visible_inputs))
        if workspace is not None:
            folder_lines = [f"- Основна: {workspace}"]
            folder_lines.extend(
                f"- Додаткова: {path}" for path in additional_workspaces or []
            )
            sections.append("# Доступні робочі папки\n" + "\n".join(folder_lines))
            required_raw = str(node.config.get("required_output_path", "")).strip()
            if required_raw:
                required = self._resolved_file(required_raw, workspace)
                sections.append(
                    "# Обов'язковий вихідний артефакт\n"
                    f"- Єдиний дозволений фінальний шлях: {required}\n"
                    "- Не використовуй альтернативні назви на кшталт final_.png.\n"
                    "- Запиши результат у тимчасовий файл у цій самій папці, "
                    "перевір його, а потім атомарно заміни ним фінальний шлях.\n"
                    "- Крок не буде прийнято, якщо цього файла немає після відповіді."
                )
            protected_raw = str(node.config.get("protected_source_path", "")).strip()
            if protected_raw:
                protected = self._resolved_file(protected_raw, workspace)
                sections.append(
                    "# Захищений оригінал\n"
                    f"Не змінюй і не перезаписуй: {protected}. "
                    "FlowAI перевірить його SHA-256 після виконання."
                )
        if attachments:
            sections.append(
                "# Прикріплені локальні файли\n"
                + "\n".join(f"- {item}" for item in attachments)
            )
        if output_format == "json" or schema:
            sections.append(
                "# Формат відповіді\n"
                "Поверни лише валідний JSON без Markdown-огорожі. Схема або приклад:\n"
                + json.dumps(
                    schema or {"result": "string"}, ensure_ascii=False, indent=2
                )
            )
        return "\n\n".join(sections)

    def _resolved_agent_folders(self, node: FlowNode, workspace: Path) -> list[Path]:
        candidates = self.workflow.resolved_additional_folders(self.project_path)
        raw_node_paths = [
            str(item) for item in node.config.get("additional_folders", []) if str(item)
        ]
        legacy_workspace = str(node.config.get("workspace", "")).strip()
        if legacy_workspace:
            raw_node_paths.insert(0, legacy_workspace)
        for raw_path in raw_node_paths:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = workspace / path
            path = path.resolve()
            if path != workspace and path not in candidates:
                candidates.append(path)
        for path in candidates:
            if not path.is_dir():
                raise WorkflowError(f"Додаткова робоча папка не існує: {path}")
        return candidates

    @staticmethod
    def _compose_agent_instructions(node: FlowNode, workspace: Path) -> str:
        sections: list[str] = []
        written = str(node.config.get("instructions", "")).strip()
        if written:
            sections.append(written)
        for raw_path in node.config.get("instruction_files", []):
            path = Path(str(raw_path)).expanduser()
            if not path.is_absolute():
                path = workspace / path
            path = path.resolve()
            if path.suffix.casefold() not in {".md", ".markdown"}:
                raise WorkflowError(f"Файл інструкцій має бути Markdown: {path}")
            if not path.is_file():
                raise WorkflowError(f"Файл постійних інструкцій не знайдено: {path}")
            try:
                content = path.read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeError) as exc:
                raise WorkflowError(
                    f"Не вдалося прочитати файл інструкцій {path}: {exc}"
                ) from exc
            if content:
                sections.append(f"# Інструкції з {path.name}\n{content}")
        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Work Reviewer
    # ------------------------------------------------------------------

    def _protocol_directory(self) -> Path:
        if self.run_directory is not None:
            return self.run_directory
        base = self.project_path.parent if self.project_path else Path.cwd()
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        return base / "runs" / stamp

    def _start_protocol(self, reviewer: FlowNode) -> None:
        if self.checkpoint.protocol_path:
            path = Path(self.checkpoint.protocol_path)
        else:
            path = self._protocol_directory() / PROTOCOL_NAME
            self.checkpoint.protocol_path = str(path)
        self.protocol = WorkReviewProtocol(path, self.workflow.name)
        monitored = WorkReviewProtocol.monitored_ids(self.workflow, reviewer)
        self.protocol.begin(
            self.workflow, monitored, records=self.checkpoint.protocol_records
        )

    def _record_protocol(
        self, node: FlowNode, result: NodeResult, iteration: int
    ) -> None:
        if self.protocol is None:
            return
        extra: dict[str, Any] = {}
        if node.kind == "result" and isinstance(result.data, dict):
            extra["Гілка"] = str(result.data.get("branch", "")).upper()
            extra["Вердикт"] = result.data.get("verdict")
            if result.data.get("reason"):
                extra["Причина"] = result.data["reason"]
        self.protocol.record(
            node=node,
            iteration=iteration,
            result_status=result.status,
            duration_seconds=result.duration_seconds,
            prompt=self._last_prompt if node.is_agent else "",
            instructions=self._last_instructions if node.is_agent else "",
            attachments=self._last_attachments if node.is_agent else [],
            steps=self._last_steps,
            text=result.text,
            error=result.error,
            thread_id=self.checkpoint.thread_ids.get(node.id, ""),
            resumed=self._last_resumed,
            extra=extra,
        )
        self.checkpoint.protocol_records = self.protocol.snapshot()

    def _run_work_reviewer(
        self,
        reviewer: FlowNode,
        workspace: Path,
        codex: CodexAdapter,
        status: str,
    ) -> None:
        if self.protocol is None:
            return
        protocol_path = self.protocol.path
        context = {
            "inputs": {},
            "workflow": {"name": self.workflow.name},
            "protocol_path": str(protocol_path),
            "run_status": status,
        }
        self._emit(
            "work_review_started",
            node=reviewer,
            message="Work Reviewer аналізує протокол роботи",
        )
        self._stage(reviewer, 1, "Підготовка протоколу роботи")
        started = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()
        try:
            result = self._execute_agent(
                reviewer,
                {},
                context,
                workspace,
                codex,
                extra_attachments=[protocol_path],
            )
        except RunCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - аналіз не має валити весь запуск
            self._emit(
                "work_review_failed",
                node=reviewer,
                message=f"Не вдалося проаналізувати протокол: {exc}",
            )
            return

        result.started_at = started_at
        result.finished_at = datetime.now(UTC).isoformat()
        result.duration_seconds = round(time.perf_counter() - started, 3)
        iteration = self.checkpoint.iterations.get(reviewer.id, 0) + 1
        self._store(reviewer, result, iteration=iteration)

        self._stage(reviewer, 6, "Збереження звіту Work Reviewer")
        report_path = str(reviewer.config.get("report_path", "")).strip()
        target = (
            Path(report_path).expanduser()
            if report_path
            else protocol_path.with_name(REPORT_NAME)
        )
        if not target.is_absolute():
            target = workspace / target
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.text, encoding="utf-8")
        self._emit(
            "work_review_finished",
            node=reviewer,
            result=result,
            message=f"Аналіз роботи збережено: {target}",
            report_path=str(target),
            protocol_path=str(protocol_path),
        )

    def _emit(
        self,
        event_type: str,
        *,
        node: FlowNode | None = None,
        result: NodeResult | None = None,
        **payload: Any,
    ) -> None:
        event: dict[str, Any] = {
            "type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            **payload,
        }
        if event_type == "agent_prompt":
            self._last_prompt = str(payload.get("prompt", ""))
            self._last_instructions = str(payload.get("instructions", ""))
            self._last_attachments = list(payload.get("attachments", []))
        if node:
            event["node_id"] = node.id
            event["node_title"] = node.title
        if result:
            event["result"] = result.to_dict()
        LOGGER.info(
            "Engine event workflow=%r type=%s node=%s",
            self.workflow.name,
            event_type,
            node.id if node is not None else "-",
        )
        self.on_event(event)
