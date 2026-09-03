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

from .calibration import NodeOptimizationReview, parse_report, save_report
from .codex_adapter import CodexAdapter, TurnInterrupted
from .models import (
    AGENT_KINDS,
    DEFAULT_PORT,
    NEVER_SEEDED,
    RESULT_PORTS,
    FlowNode,
    Workflow,
    managed_task_title,
    normalize_managed_tasks,
)
from .project_layout import ARTIFACTS_DIR, REPORTS_DIR, local_output_path
from .quality_control import (
    OperationIntentError,
    QAContractError,
    build_retry_contract,
    normalize_task_review,
    operation_progress_from_activity,
    protected_artifact_regressions,
    task_review_contract_rules,
    validate_operation_intent,
)
from .quality_control import (
    payload_hash as quality_payload_hash,
)
from .runtime_state import (
    JsonArtifactCache,
    atomic_write_json,
    diff_workspace,
    qa_packet,
    snapshot_workspace,
    write_audit_report,
    write_versioned_attempt_manifest,
)
from .runtime_state import (
    file_sha256 as runtime_file_sha256,
)
from .skills import catalogue_text, list_skills, skills_used
from .templating import (
    PLACEHOLDER,
    extract_json,
    render_template,
    resolve_path,
    safe_eval,
    stringify,
)
from .ui_workflow import (
    PhotoshopAutomation,
    PhotoshopAutomationError,
    ReferenceAnalysisCacheError,
    append_ui_learning,
    blocking_defect_ids,
    find_ui_plan,
    find_variants,
    has_non_overridable_issues,
    normalize_confirmation_mode,
    normalize_confirmation_ports,
    normalize_ui_tasks,
    payload_sha256,
    validate_declared_output_paths,
    validate_reference_analysis_cache,
    verify_variant_manifest,
    workspace_child,
)
from .work_review import PROTOCOL_NAME, REPORT_NAME, WorkReviewProtocol

EventCallback = Callable[[dict[str, Any]], None]
LOGGER = logging.getLogger(__name__)

# Запобіжник від нескінченного циклу, якщо лічильники Result налаштовані надто щедро.
MAX_STEPS = 200

# Скільки символів кроків однієї ноди Optimizer отримує на аналіз. Транспорт
# Codex приймає не більше 1 МіБ на весь виклик, а нод в аналізі кілька.
CALIBRATION_STEPS_BUDGET = 150_000
AGENT_INPUT_CHARACTER_LIMIT = 900_000
# Стеля на одне підставлене значення. Промпт складається з багатьох частин,
# і жодна з них не має права з'їсти весь бюджет транспорту: саме так вихід
# однієї ноди колись перетворився на промпт у 3.38 млн символів.
PROMPT_VALUE_CHARACTER_LIMIT = 120_000
# Частка ліміту, після якої Flow попереджає, поки ще є куди рости.
CONTEXT_BUDGET_WARNING_RATIO = 0.6


class WorkflowError(RuntimeError):
    pass


class RunCancelled(RuntimeError):
    pass


class RunStopped(RuntimeError):
    """The current node was interrupted, requeued and can be resumed."""



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
    # thread_id → відбитки вкладень і скілів, які вже лежать у цьому треді.
    thread_inputs: dict[str, dict[str, str]] = field(default_factory=dict)
    iterations: dict[str, int] = field(default_factory=dict)
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    protocol_records: dict[str, list[str]] = field(default_factory=dict)
    protocol_path: str = ""
    task_progress: dict[str, dict[str, Any]] = field(default_factory=dict)
    task_attempts: dict[str, int] = field(default_factory=dict)
    task_transition_receipts: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )
    user_requirements: dict[str, list[str]] = field(default_factory=dict)
    calibration_attempts: dict[str, int] = field(default_factory=dict)
    protocol_steps: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ui_plan_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    result_drafts: dict[str, dict[str, Any]] = field(default_factory=dict)
    retry_guards: dict[str, dict[str, Any]] = field(default_factory=dict)
    learning_event_ids: list[str] = field(default_factory=list)
    photoshop_reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    variant_manifests: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkpoint_version: int = 2
    saved_at: str = ""
    run_state: str = "idle"
    active_node_id: str = ""
    active_inputs: dict[str, Any] = field(default_factory=dict)
    active_operation: dict[str, Any] = field(default_factory=dict)
    event_cursor: int = 0
    qa_scores: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    retry_contracts: dict[str, dict[str, Any]] = field(default_factory=dict)
    file_ledgers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    prompt_cache_keys: dict[str, str] = field(default_factory=dict)
    qa_cache_keys: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "steps": self.steps,
            "queue": list(self.queue),
            "pending_inputs": self.pending_inputs,
            "port_counts": dict(self.port_counts),
            "limit_grants": dict(self.limit_grants),
            "thread_ids": dict(self.thread_ids),
            "thread_inputs": {
                str(key): dict(value)
                for key, value in self.thread_inputs.items()
                if isinstance(value, dict)
            },
            "iterations": dict(self.iterations),
            "outputs": self.outputs,
            "history": self.history,
            "protocol_records": self.protocol_records,
            "protocol_path": self.protocol_path,
            "task_progress": self.task_progress,
            "task_attempts": dict(self.task_attempts),
            "task_transition_receipts": {
                str(key): dict(value)
                for key, value in self.task_transition_receipts.items()
                if isinstance(value, dict)
            },
            "user_requirements": {
                str(key): list(values)
                for key, values in self.user_requirements.items()
            },
            "calibration_attempts": dict(self.calibration_attempts),
            "protocol_steps": self.protocol_steps,
            "ui_plan_snapshots": self.ui_plan_snapshots,
            "result_drafts": self.result_drafts,
            "retry_guards": self.retry_guards,
            "learning_event_ids": list(self.learning_event_ids),
            "photoshop_reports": self.photoshop_reports,
            "variant_manifests": self.variant_manifests,
            "checkpoint_version": int(self.checkpoint_version),
            "saved_at": self.saved_at,
            "run_state": self.run_state,
            "active_node_id": self.active_node_id,
            "active_inputs": self.active_inputs,
            "active_operation": self.active_operation,
            "event_cursor": int(self.event_cursor),
            "qa_scores": self.qa_scores,
            "retry_contracts": self.retry_contracts,
            "file_ledgers": self.file_ledgers,
            "prompt_cache_keys": dict(self.prompt_cache_keys),
            "qa_cache_keys": dict(self.qa_cache_keys),
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
            thread_inputs={
                str(key): {
                    str(mark): str(value) for mark, value in dict(marks).items()
                }
                for key, marks in dict(raw.get("thread_inputs") or {}).items()
                if isinstance(marks, dict)
            },
            iterations=dict(raw.get("iterations") or {}),
            outputs=dict(raw.get("outputs") or {}),
            history=dict(raw.get("history") or {}),
            protocol_records=dict(raw.get("protocol_records") or {}),
            protocol_path=str(raw.get("protocol_path", "")),
            task_progress=dict(raw.get("task_progress") or {}),
            task_attempts={
                str(key): int(value)
                for key, value in dict(raw.get("task_attempts") or {}).items()
            },
            task_transition_receipts={
                str(key): dict(value)
                for key, value in dict(
                    raw.get("task_transition_receipts") or {}
                ).items()
                if isinstance(value, dict)
            },
            user_requirements={
                str(key): [
                    str(item).strip()
                    for item in (value if isinstance(value, list) else [value])
                    if str(item).strip()
                ]
                for key, value in dict(
                    raw.get("user_requirements") or {}
                ).items()
            },
            calibration_attempts={
                str(key): int(value)
                for key, value in dict(
                    raw.get("calibration_attempts") or {}
                ).items()
            },
            protocol_steps=dict(raw.get("protocol_steps") or {}),
            ui_plan_snapshots={
                str(key): dict(value)
                for key, value in dict(raw.get("ui_plan_snapshots") or {}).items()
                if isinstance(value, dict)
            },
            result_drafts={
                str(key): dict(value)
                for key, value in dict(raw.get("result_drafts") or {}).items()
                if isinstance(value, dict)
            },
            retry_guards={
                str(key): dict(value)
                for key, value in dict(raw.get("retry_guards") or {}).items()
                if isinstance(value, dict)
            },
            learning_event_ids=[
                str(item) for item in raw.get("learning_event_ids", []) if str(item)
            ],
            photoshop_reports={
                str(key): dict(value)
                for key, value in dict(raw.get("photoshop_reports") or {}).items()
                if isinstance(value, dict)
            },
            variant_manifests={
                str(key): dict(value)
                for key, value in dict(raw.get("variant_manifests") or {}).items()
                if isinstance(value, dict)
            },
            checkpoint_version=max(1, int(raw.get("checkpoint_version", 1))),
            saved_at=str(raw.get("saved_at") or ""),
            run_state=str(raw.get("run_state") or "idle"),
            active_node_id=str(raw.get("active_node_id") or ""),
            active_inputs=dict(raw.get("active_inputs") or {}),
            active_operation=dict(raw.get("active_operation") or {}),
            event_cursor=max(0, int(raw.get("event_cursor", 0))),
            qa_scores={
                str(key): [dict(item) for item in value if isinstance(item, dict)]
                for key, value in dict(raw.get("qa_scores") or {}).items()
                if isinstance(value, list)
            },
            retry_contracts={
                str(key): dict(value)
                for key, value in dict(raw.get("retry_contracts") or {}).items()
                if isinstance(value, dict)
            },
            file_ledgers={
                str(key): [dict(item) for item in value if isinstance(item, dict)]
                for key, value in dict(raw.get("file_ledgers") or {}).items()
                if isinstance(value, list)
            },
            prompt_cache_keys={
                str(key): str(value)
                for key, value in dict(raw.get("prompt_cache_keys") or {}).items()
            },
            qa_cache_keys={
                str(key): str(value)
                for key, value in dict(raw.get("qa_cache_keys") or {}).items()
            },
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
        # М'яка зупинка: на відміну від _stop, вона нічого не перериває, а лише
        # закриває вхід у наступну ноду.
        self._graceful = threading.Event()
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
        self._reference_analysis_receipts: dict[str, dict[str, Any]] = {}
        self._recover_task_transition_receipts()

    def _checkpoint_boundary(self, state: str, *, emit: bool = True) -> None:
        self.checkpoint.run_state = state
        self.checkpoint.saved_at = datetime.now(UTC).isoformat()
        if emit:
            self._emit(
                "checkpoint_updated",
                message="Стан Flow зафіксовано",
                checkpoint_state=state,
                active_node_id=self.checkpoint.active_node_id,
                saved_at=self.checkpoint.saved_at,
                checkpoint=self.checkpoint.to_dict(),
            )

    def _restore_interrupted_active_node(self) -> None:
        node_id = str(self.checkpoint.active_node_id or "")
        if not node_id or self.workflow.find(node_id) is None:
            return
        if node_id not in self.checkpoint.pending_inputs:
            self.checkpoint.pending_inputs[node_id] = dict(
                self.checkpoint.active_inputs or {}
            )
        if node_id not in self.checkpoint.queue:
            self.checkpoint.queue.insert(0, node_id)
        self.checkpoint.active_node_id = ""
        self.checkpoint.active_inputs = {}
        self.checkpoint.active_operation = {}

    def _requeue_active_node(self, node_id: str, inputs: dict[str, Any]) -> None:
        self.checkpoint.pending_inputs[node_id] = dict(inputs)
        if node_id not in self.checkpoint.queue:
            self.checkpoint.queue.insert(0, node_id)
        self.checkpoint.active_node_id = ""
        self.checkpoint.active_inputs = {}
        self.checkpoint.active_operation = {}
        self.checkpoint.steps = max(0, self.checkpoint.steps - 1)

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

    def request_stop(
        self, reason: str = "Flow зупиняє поточну операцію та збереже прогрес"
    ) -> None:
        """Interrupt the current turn, requeue its node and keep a resumable state."""
        if self._stop.is_set() or self._graceful.is_set():
            return
        self._graceful.set()
        # На паузі рушій стоїть у бар'єрі й сам його не зніме. Без цього рядка
        # STOP на паузі не робив би нічого: зупинка чекала б, доки користувач
        # спершу натисне Resume.
        self._resume_event.set()
        with self._control_lock:
            codex = self._active_codex
        if codex is not None:
            threading.Thread(
                target=codex.cancel_active,
                name="FlowAI-Codex-Resumable-Stop",
                daemon=True,
            ).start()
        self._emit("run_stop_requested", message=reason)

    def pause(self, reason: str = "Систему призупинено") -> None:
        """Поставити бар'єр між нодами, не перериваючи активний хід агента."""
        if self._stop.is_set() or not self._resume_event.is_set():
            return
        # Пауза після STOP повернула б бар'єр, який зупинка щойно зняла.
        if self._graceful.is_set():
            return
        with self._control_lock:
            self._pause_generation += 1
            self._resume_event.clear()
        self._emit("run_paused", message=reason)

    def resume(self, reason: str = "Роботу системи відновлено") -> None:
        if self._stop.is_set() or self._resume_event.is_set():
            return
        self._resume_event.set()
        self._emit("run_resumed", message=reason)

    def update_node_config(self, node_id: str, updates: dict[str, Any]) -> bool:
        """Apply explicitly safe live settings to the runner snapshot."""
        allowed = {
            "true_limit",
            "false_limit",
            "task_attempt_limit",
            "wait_for_confirmation",
        }
        clean: dict[str, Any] = {}
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key in {"true_limit", "false_limit", "task_attempt_limit"}:
                try:
                    clean[key] = max(1, int(value))
                except (TypeError, ValueError):
                    continue
            else:
                clean[key] = bool(value)
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

    def _reachable_from(self, sources: list[str]) -> set[str]:
        """Усі ноди, до яких від заданих веде хоч який шлях.

        Порти тут не важать: для стартової черги значення має сам факт, що
        дані колись прийдуть, а не те, якою гілкою.
        """
        seen: set[str] = set()
        stack = list(sources)
        while stack:
            for edge in self.workflow.outgoing(stack.pop()):
                if edge.target not in seen:
                    seen.add(edge.target)
                    stack.append(edge.target)
        return seen

    def _initial_queue(self) -> list[str]:
        """Повернути стартові ноди, явно виключивши Calibration Stop.

        Нода, у яку веде лише Result, теж буває стартовою: так починається
        Flow, зібраний навколо петлі. Але якщо до неї є шлях від справжнього
        входу, вона не стартова, а середина ланцюга — і мусить дочекатися
        своїх даних, а не запуститися з порожніми входами.
        """
        routed = [
            node
            for node in self.workflow.routed_nodes()
            if node.kind not in NEVER_SEEDED
        ]
        downstream = self._reachable_from(
            [node.id for node in routed if not self.workflow.incoming(node.id)]
        )
        return [
            node.id
            for node in routed
            if node.id not in downstream
            and not (
                node.kind == "tasks_manager"
                and str(node.config.get("task_source") or "static") == "input_once"
                and node.id not in self.checkpoint.ui_plan_snapshots
            )
            and not any(
                (source := self.workflow.find(edge.source)) is not None
                and source.kind != "result"
                for edge in self.workflow.incoming(node.id)
            )
        ]

    def run(self) -> RunCheckpoint:
        errors = self.workflow.validate()
        if errors:
            raise WorkflowError("\n".join(errors))
        workspace = self.workflow.resolved_workspace(self.project_path)
        if not workspace.is_dir():
            raise WorkflowError(f"Робоча папка не існує: {workspace}")

        self._restore_interrupted_active_node()
        self._checkpoint_boundary("running", emit=False)
        self._emit("run_started", message=f"Запуск Flow «{self.workflow.name}»")
        self._topo_index = {
            node_id: index
            for index, node_id in enumerate(self.workflow.topological_order())
        }

        reviewer = next(iter(self.workflow.nodes_of_kind("work_reviewer")), None)
        if not self.checkpoint.started:
            self.checkpoint.queue = self._initial_queue()
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
                    self._checkpoint_boundary("stopped_resumable")
                    self._emit("run_stopped", message="Flow зупинено зі збереженням")
                    status = "stopped"
                    break

                # Перевірка саме тут: попередня нода вже завершилась і
                # розіслала результат, тож чекпоінт цілий і придатний до
                # продовження. Наступну ноду просто не беремо.
                if self._graceful.is_set():
                    status = "stopped"
                    self._emit(
                        "run_stopped",
                        message="Flow зупинено — прогрес збережено, можна продовжити",
                    )
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
                self.checkpoint.active_node_id = node_id
                self.checkpoint.active_inputs = dict(inputs)
                self.checkpoint.active_operation = {
                    "node_id": node_id,
                    "node_title": node.title,
                    "task_id": self._active_task_id_for(node),
                    "started_at": datetime.now(UTC).isoformat(),
                }
                self._checkpoint_boundary("running")
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
                except RunStopped:
                    self._requeue_active_node(node_id, inputs)
                    self._checkpoint_boundary("stopped_resumable")
                    self._emit(
                        "run_stopped",
                        node=node,
                        message="Flow зупинено — активну ноду повернуто в чергу",
                    )
                    status = "stopped"
                    break
                except RunCancelled:
                    self._requeue_active_node(node_id, inputs)
                    self._checkpoint_boundary("stopped_resumable")
                    cancelled_turn = NodeResult(
                        node.id,
                        "cancelled",
                        text=(
                            "Активний хід перервано; ноду повернуто в чергу "
                            "для продовження"
                        ),
                    )
                    self._emit(
                        "node_cancelled", node=node, result=cancelled_turn
                    )
                    self._emit(
                        "run_stopped",
                        node=node,
                        message=(
                            "Flow перервано негайно — активну ноду повернуто "
                            "в чергу, прогрес збережено"
                        ),
                    )
                    status = "stopped"
                    break
                except InterventionRequired as exc:
                    # Нода має виконатися ще раз після відповіді — повертаємо
                    # її вхідні дані та місце в черзі.
                    self.checkpoint.pending_inputs[node_id] = inputs
                    self.checkpoint.queue.insert(0, node_id)
                    self.checkpoint.steps -= 1
                    self.checkpoint.active_node_id = ""
                    self.checkpoint.active_inputs = {}
                    self.checkpoint.active_operation = {}
                    self._checkpoint_boundary("attention")
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
                    self.checkpoint.active_node_id = ""
                    self.checkpoint.active_inputs = {}
                    self.checkpoint.active_operation = {}
                    self._checkpoint_boundary("failed")
                    status = "failed"
                    failure = str(exc)
                    break

                self._stage(node, 6, "Збереження та передача результату")
                try:
                    self._wait_until_resumed()
                except RunCancelled:
                    self._requeue_active_node(node_id, inputs)
                    self._checkpoint_boundary("stopped_resumable")
                    self._emit(
                        "run_stopped",
                        node=node,
                        message=(
                            "Flow зупинено перед передачею результату; ноду "
                            "повернуто в чергу"
                        ),
                    )
                    status = "stopped"
                    break
                result.started_at = started_at
                result.finished_at = datetime.now(UTC).isoformat()
                result.duration_seconds = round(time.perf_counter() - started, 3)
                if node.kind == "result":
                    self._commit_task_transition_receipt(node, result)
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
                self.checkpoint.active_node_id = ""
                self.checkpoint.active_inputs = {}
                self.checkpoint.active_operation = {}
                self._checkpoint_boundary("running")

            if self._stop.is_set() and status not in {"cancelled", "stopped"}:
                status = "stopped"
                self._checkpoint_boundary("stopped_resumable")
                self._emit("run_stopped", message="Flow зупинено зі збереженням")
            # Зупинений запуск не підсумовують: Work Reviewer робив би висновки
            # про роботу, яку ще не доведено до кінця.
            if not paused and status not in {"cancelled", "stopped"}:
                if self.protocol is not None:
                    self.protocol.finish(status, self._failed_task_titles())
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
            self._checkpoint_boundary("finished")
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
        found_keys: set[str] = set()
        visited_containers: set[int] = set()

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                identity = id(item)
                if identity in visited_containers:
                    return
                visited_containers.add(identity)
                for nested in item.values():
                    visit(nested)
                return
            if isinstance(item, (list, tuple, set)):
                identity = id(item)
                if identity in visited_containers:
                    return
                visited_containers.add(identity)
                for nested in item:
                    visit(nested)
                return
            if not isinstance(item, str):
                return
            raw_path = item.strip()
            if (
                not raw_path
                or len(raw_path) > 2048
                or any(character in raw_path for character in ("\x00", "\r", "\n"))
            ):
                return
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = workspace / candidate
            try:
                candidate = candidate.absolute()
                exists = candidate.is_file()
            except (OSError, RuntimeError):
                return
            path = str(candidate)
            key = path.casefold()
            if exists and key not in found_keys:
                found_keys.add(key)
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
        transport_recoveries = 0
        while attempt <= retries:
            self._wait_until_resumed()
            with self._control_lock:
                pause_generation = self._pause_generation
            try:
                return self._execute_node(node, inputs, workspace, codex)
            except InterventionRequired:
                raise
            except RunStopped:
                raise
            except RunCancelled:
                raise
            except TurnInterrupted as exc:
                if self._stop.is_set():
                    raise RunCancelled("Flow зупинено")
                if self._graceful.is_set():
                    raise RunStopped("Flow зупинено зі збереженням прогресу") from exc
                if exc.reset_thread:
                    # A crashed app-server cannot safely continue an in-flight turn.
                    # One fresh-thread recovery is automatic even when node retries=0.
                    if transport_recoveries >= 1:
                        raise
                    transport_recoveries += 1
                    self.checkpoint.thread_ids.pop(node.id, None)
                self._wait_until_resumed()
                self._emit(
                    "node_retry",
                    node=node,
                    message=(
                        "Codex-процес перезапущено — повторюємо хід у новому треді"
                        if exc.reset_thread
                        else "Хід агента обірвався — повторюємо в тому ж треді"
                    ),
                )
                continue
            except Exception as exc:
                if self._stop.is_set():
                    raise RunCancelled("Flow зупинено") from exc
                if self._graceful.is_set():
                    raise RunStopped("Flow зупинено зі збереженням прогресу") from exc
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
        if node.kind in AGENT_KINDS and node.kind != "calibrator":
            response = self.intervention_responses.pop(node.id, None)
            if isinstance(response, dict) and response.get("action") == "retry_task":
                note = str(response.get("note") or "").strip()
                if note:
                    inputs = dict(inputs)
                    inputs["user_note"] = note
        context = {
            "inputs": inputs,
            **inputs,
            "workflow": {"name": self.workflow.name},
            "grill_summary": self.workflow.grill_summary
            or "Окремих домовленостей не було.",
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
            return self._execute_tasks_manager(node, inputs, workspace)

        if node.kind == "result":
            self._stage(node, 4, "Перевірка умови розгалуження")
            return self._execute_result(node, inputs, context, workspace)

        if node.kind == "calibrator":
            return self._execute_calibrator(node, inputs, context, workspace, codex)

        if node.kind in AGENT_KINDS:
            return self._execute_agent(node, inputs, context, workspace, codex)

        raise WorkflowError(f"Тип ноди ще не підтримується: {node.kind}")

    def _managed_tasks_for_node(self, node: FlowNode) -> list[dict[str, Any]]:
        snapshot = self.checkpoint.ui_plan_snapshots.get(node.id)
        if isinstance(snapshot, dict) and isinstance(snapshot.get("tasks"), list):
            return normalize_managed_tasks(snapshot["tasks"])
        return normalize_managed_tasks(node.config.get("tasks"))

    def _freeze_ui_plan(
        self,
        node: FlowNode,
        inputs: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        existing = self.checkpoint.ui_plan_snapshots.get(node.id)
        if isinstance(existing, dict) and isinstance(existing.get("tasks"), list):
            return existing

        plan = find_ui_plan(inputs)
        tasks = normalize_ui_tasks(plan.get("tasks") if plan else None)
        if not tasks:
            raise WorkflowError(
                f"Нода «{node.title}» працює в режимі input_once, але не отримала "
                "ui_project_spec.tasks із непорожніми prompt."
            )
        frozen_plan = dict(plan)
        frozen_plan["tasks"] = tasks
        snapshot = {
            "approved_at": datetime.now(UTC).isoformat(),
            "plan_hash": payload_sha256(frozen_plan),
            "plan": frozen_plan,
            "tasks": tasks,
        }
        self.checkpoint.ui_plan_snapshots[node.id] = snapshot

        save_path = str(
            node.config.get("plan_save_path") or "ui_project_spec.json"
        ).strip()
        target = workspace_child(workspace, save_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(frozen_plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._emit(
            "ui_plan_frozen",
            node=node,
            message=f"Погоджений UI-план зафіксовано: {target}",
            plan_hash=snapshot["plan_hash"],
            task_count=len(tasks),
            plan_path=str(target),
        )
        return snapshot

    def _execute_tasks_manager(
        self,
        node: FlowNode,
        inputs: dict[str, Any] | None = None,
        workspace: Path | None = None,
    ) -> NodeResult:
        inputs = inputs or {}
        workspace = workspace or self.workflow.resolved_workspace(self.project_path)
        task_source = str(node.config.get("task_source") or "static").strip()
        snapshot: dict[str, Any] = {}
        if task_source == "input_once":
            snapshot = self._freeze_ui_plan(node, inputs, workspace)
            tasks = normalize_managed_tasks(snapshot.get("tasks"))
        else:
            tasks = normalize_managed_tasks(node.config.get("tasks"))
        valid_ids = {str(task["id"]) for task in tasks}
        progress = self.checkpoint.task_progress.setdefault(
            node.id,
            {
                "active_task_id": "",
                "completed_task_ids": [],
                "failed_task_ids": [],
            },
        )
        times: dict[str, dict[str, float]] = progress.setdefault("times", {})
        now = time.time()

        def _close_task(task_id: str) -> None:
            record = times.get(task_id)
            if not record or record.get("finished"):
                return
            record["finished"] = now
            record["seconds"] = max(
                0.0, now - float(record.get("started", now))
            )

        failed = [
            str(task_id)
            for task_id in progress.get("failed_task_ids", [])
            if str(task_id) in valid_ids
        ]
        completed = [
            str(task_id)
            for task_id in progress.get("completed_task_ids", [])
            if str(task_id) in valid_ids and str(task_id) not in failed
        ]
        active_id = str(progress.get("active_task_id", ""))
        if active_id in failed:
            _close_task(active_id)
        elif active_id in valid_ids and active_id not in completed:
            completed.append(active_id)
            _close_task(active_id)

        finished = set(completed) | set(failed)
        active_task: dict[str, Any] | None = next(
            (task for task in tasks if str(task["id"]) not in finished),
            None,
        )
        active_id = str(active_task["id"]) if active_task is not None else ""
        if active_id and active_id not in times:
            times[active_id] = {"started": now, "finished": 0.0, "seconds": 0.0}
        progress["active_task_id"] = active_id
        progress["completed_task_ids"] = completed
        progress["failed_task_ids"] = failed

        states = []
        for index, task in enumerate(tasks):
            task_id = str(task["id"])
            status = (
                "failed"
                if task_id in failed
                else "completed"
                if task_id in completed
                else "running"
                if task_id == active_id
                else "pending"
            )
            record = times.get(task_id, {})
            if status == "running" and record:
                seconds = max(0.0, now - float(record.get("started", now)))
            else:
                seconds = float(record.get("seconds", 0.0))
            states.append(
                {
                    "id": task_id,
                    "title": managed_task_title(task, index),
                    "status": status,
                    "seconds": round(seconds, 3),
                }
            )

        total_seconds = round(sum(item["seconds"] for item in states), 3)
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
            failed_count=len(failed),
            task_count=len(tasks),
            total_seconds=total_seconds,
        )

        if active_task is None:
            data = {
                "branch": branch,
                "tasks": states,
                "completed_count": len(completed),
                "failed_count": len(failed),
                "task_count": len(tasks),
                "total_seconds": total_seconds,
            }
            if snapshot:
                data["ui_plan_hash"] = str(snapshot.get("plan_hash") or "")
                data["ui_project_spec"] = dict(snapshot.get("plan") or {})
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
            "screen": str(active_task.get("screen") or ""),
            "states": [
                str(item) for item in active_task.get("states", []) if str(item)
            ],
            "acceptance_criteria": [
                str(item)
                for item in active_task.get("acceptance_criteria", [])
                if str(item)
            ],
            "export_profile": str(
                active_task.get("export_profile") or "baseline"
            ),
            "attachments": attachments,
        }
        previous_transition = self._previous_task_transition(node, active_id)
        data = {
            "branch": branch,
            "prompt": prompt,
            "task": task_payload,
            "attachments": attachments,
            "tasks": states,
            "completed_count": len(completed),
            "failed_count": len(failed),
            "task_count": len(tasks),
            "total_seconds": total_seconds,
        }
        if snapshot:
            data["ui_plan_hash"] = str(snapshot.get("plan_hash") or "")
            data["ui_project_spec"] = dict(snapshot.get("plan") or {})
        if previous_transition:
            data["previous_task_transition"] = previous_transition
        return NodeResult(node.id, "success", text=prompt, data=data)

    @staticmethod
    def _transition_receipt_key(manager_id: str, task_id: str) -> str:
        return f"{manager_id}:{task_id}"

    def _tasks_manager_for_result(self, result_id: str) -> FlowNode | None:
        """Return the manager whose task is finalized by this Result node."""
        candidates: list[FlowNode] = []
        for port in ("true", "exhausted"):
            for edge in self.workflow.outgoing(result_id, port):
                target = self.workflow.find(edge.target)
                if target is not None and target.kind == "tasks_manager":
                    candidates.append(target)
        if candidates:
            return next(
                (
                    item
                    for item in candidates
                    if str(
                        self.checkpoint.task_progress.get(item.id, {}).get(
                            "active_task_id", ""
                        )
                    )
                ),
                candidates[0],
            )
        return next(
            (
                item
                for item in self.workflow.nodes_of_kind("tasks_manager")
                if self._is_forward_upstream(item.id, result_id)
            ),
            None,
        )

    def _record_task_transition_receipt(
        self,
        *,
        manager: FlowNode,
        task_id: str,
        result: FlowNode,
        candidate_path: str,
        confirmed_by_user: bool,
        confirmed_at: str | None = None,
        recovered: bool = False,
    ) -> dict[str, Any]:
        key = self._transition_receipt_key(manager.id, task_id)
        existing = self.checkpoint.task_transition_receipts.get(key)
        if isinstance(existing, dict) and existing.get("status") == "approved":
            return dict(existing)
        receipt: dict[str, Any] = {
            "status": "approved",
            "manager_id": manager.id,
            "task_id": task_id,
            "result_node_id": result.id,
            "branch": "true",
            "verdict": True,
            "confirmed_by_user": bool(confirmed_by_user),
            "confirmed_at": confirmed_at or datetime.now().astimezone().isoformat(),
            "candidate_path": candidate_path,
        }
        if recovered:
            receipt["recovered_from_history"] = True
        self.checkpoint.task_transition_receipts[key] = receipt
        self._emit(
            "task_transition_recovered" if recovered else "task_transition_recorded",
            node=result,
            message=(
                "Відновлено підтверджений перехід між завданнями"
                if recovered
                else "Збережено підтверджений перехід між завданнями"
            ),
            receipt=dict(receipt),
        )
        return dict(receipt)

    def _commit_task_transition_receipt(
        self, result_node: FlowNode, result: NodeResult
    ) -> None:
        raw = result.data.get("task_transition_receipt")
        if not isinstance(raw, dict):
            return
        receipt = dict(raw)
        manager_id = str(receipt.get("manager_id") or "")
        task_id = str(receipt.get("task_id") or "")
        if (
            not manager_id
            or not task_id
            or receipt.get("status") != "approved"
            or receipt.get("branch") != "true"
            or receipt.get("verdict") is not True
        ):
            return
        manager = self.workflow.find(manager_id)
        if manager is None or manager.kind != "tasks_manager":
            raise WorkflowError("Task transition receipt посилається на відсутній manager")

        tasks = self._managed_tasks_for_node(manager)
        task_index = next(
            (index for index, item in enumerate(tasks) if str(item["id"]) == task_id),
            -1,
        )
        next_task_id = (
            str(tasks[task_index + 1]["id"])
            if 0 <= task_index < len(tasks) - 1
            else ""
        )
        receipt.update(
            {
                "receipt_id": str(receipt.get("receipt_id") or ""),
                "flow_name": self.workflow.name,
                "project_path": str(self.project_path.resolve())
                if self.project_path
                else "",
                "next_task_id": next_task_id,
                "approved_artifact_hash": str(
                    receipt.get("approved_artifact_hash")
                    or result.data.get("approved_artifact_hash")
                    or ""
                ),
            }
        )
        receipt["receipt_id"] = receipt["receipt_id"] or quality_payload_hash(
            {
                key: receipt.get(key)
                for key in (
                    "manager_id",
                    "task_id",
                    "result_node_id",
                    "confirmed_at",
                    "approved_artifact_hash",
                )
            }
        )[:24]

        workspace = self.workflow.resolved_workspace(self.project_path)
        state_patch = self._apply_task_transition_adapter(
            result_node,
            manager,
            task_id,
            receipt,
            workspace,
        )
        receipt["state_patch"] = state_patch
        receipt["state_patch_hash"] = quality_payload_hash(state_patch)
        receipt_path = atomic_write_json(
            workspace
            / ".flowai"
            / "runtime"
            / "receipts"
            / f"{receipt['receipt_id']}.json",
            receipt,
        )
        receipt["receipt_path"] = str(receipt_path)

        progress = self.checkpoint.task_progress.setdefault(
            manager.id,
            {
                "active_task_id": task_id,
                "completed_task_ids": [],
                "failed_task_ids": [],
            },
        )
        completed = progress.setdefault("completed_task_ids", [])
        failed = progress.setdefault("failed_task_ids", [])
        if task_id not in completed:
            completed.append(task_id)
        if task_id in failed:
            failed.remove(task_id)

        key = self._transition_receipt_key(manager_id, task_id)
        self.checkpoint.task_transition_receipts[key] = receipt
        self._emit(
            "task_transition_recorded",
            node=result_node,
            message="Атомарно збережено підтверджений перехід між завданнями",
            receipt=dict(receipt),
        )

    @staticmethod
    def _deep_merge_json(target: dict[str, Any], patch: dict[str, Any]) -> None:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                WorkflowRunner._deep_merge_json(target[key], value)
            else:
                target[key] = value

    def _apply_task_transition_adapter(
        self,
        result_node: FlowNode,
        manager: FlowNode,
        task_id: str,
        receipt: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        task = next(
            (
                item
                for item in self._managed_tasks_for_node(manager)
                if str(item["id"]) == task_id
            ),
            {},
        )
        tasks = self._managed_tasks_for_node(manager)
        task_index = next(
            (index for index, item in enumerate(tasks) if str(item["id"]) == task_id),
            -1,
        )
        task_adapter = task.get("transition_adapter") if isinstance(task, dict) else None
        configured = result_node.config.get("transition_adapter")
        adapter = (
            dict(task_adapter)
            if isinstance(task_adapter, dict)
            else dict(configured)
            if isinstance(configured, dict)
            else {}
        )
        if not adapter:
            return {"type": "checkpoint_only", "task_id": task_id}
        adapter_type = str(adapter.get("type") or "json_merge")
        if adapter_type != "json_merge":
            raise WorkflowError(f"Невідомий transition adapter: {adapter_type}")
        raw_path = str(adapter.get("path") or "").strip()
        if not raw_path:
            raise WorkflowError("json_merge transition adapter не має path")
        path = workspace_child(workspace, raw_path)
        try:
            current = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"Не вдалося прочитати transition state {path}: {exc}") from exc
        if not isinstance(current, dict):
            raise WorkflowError(f"Transition state має бути JSON object: {path}")

        task_patches = adapter.get("task_patches")
        selected_patch = (
            task_patches.get(task_id)
            if isinstance(task_patches, dict)
            else None
        )
        if not isinstance(selected_patch, dict):
            selected_patch = adapter.get("default_merge") or adapter.get("merge")
        merge = dict(selected_patch) if isinstance(selected_patch, dict) else {}
        replacements = {
            "{task_id}": task_id,
            "{receipt_id}": str(receipt.get("receipt_id") or ""),
            "{approved_artifact_hash}": str(
                receipt.get("approved_artifact_hash") or ""
            ),
        }
        typed_replacements: dict[str, Any] = {
            "{task_index}": task_index,
            "{task_number}": task_index + 1,
            "{completed_position}": max(0, task_index - 1),
            "{next_position}": max(0, task_index),
            "{next_task_number}": task_index + 2,
        }

        def render(value: Any) -> Any:
            if isinstance(value, str):
                if value in typed_replacements:
                    return typed_replacements[value]
                if value.startswith("{state.") and value.endswith("}"):
                    field = value[len("{state.") : -1]
                    if field not in current:
                        raise WorkflowError(
                            f"Transition state {path} не має поля {field}"
                        )
                    return current[field]
                if value.startswith("{receipt.") and value.endswith("}"):
                    field = value[len("{receipt.") : -1]
                    if field not in receipt:
                        raise WorkflowError(
                            f"Task transition receipt не має поля {field}"
                        )
                    return receipt[field]
                for marker, replacement in replacements.items():
                    value = value.replace(marker, replacement)
                return value
            if isinstance(value, dict):
                return {key: render(item) for key, item in value.items()}
            if isinstance(value, list):
                return [render(item) for item in value]
            return value

        merge = render(merge)
        self._deep_merge_json(current, merge)
        task_appends = adapter.get("task_append_unique")
        selected_appends = (
            task_appends.get(task_id) if isinstance(task_appends, dict) else None
        )
        if isinstance(selected_appends, dict):
            appends = selected_appends
        else:
            raw_appends = adapter.get("default_append_unique")
            if not isinstance(raw_appends, dict):
                raw_appends = adapter.get("append_unique")
            appends = raw_appends if isinstance(raw_appends, dict) else {}
        for key, raw_value in appends.items():
            values = current.setdefault(str(key), [])
            if not isinstance(values, list):
                raise WorkflowError(f"Transition field {key} має бути list")
            rendered = render(raw_value)
            source = rendered if isinstance(rendered, list) else [rendered]
            for value in source:
                if value not in values:
                    values.append(value)
        atomic_write_json(path, current)
        return {
            "type": adapter_type,
            "path": str(path),
            "merge": merge,
            "append_unique": render(appends),
            "state_sha256": runtime_file_sha256(path),
        }

    def _recover_task_transition_receipts(self) -> None:
        """Backfill trusted TRUE receipts from pre-receipt checkpoints."""
        try:
            workspace = self.workflow.resolved_workspace(self.project_path)
            receipts_dir = workspace / ".flowai" / "runtime" / "receipts"
        except (OSError, RuntimeError, ValueError):
            receipts_dir = None
        if receipts_dir is not None and receipts_dir.is_dir():
            for path in receipts_dir.glob("*.json"):
                try:
                    receipt = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(receipt, dict):
                    continue
                manager_id = str(receipt.get("manager_id") or "")
                task_id = str(receipt.get("task_id") or "")
                if (
                    not manager_id
                    or not task_id
                    or receipt.get("status") != "approved"
                    or receipt.get("verdict") is not True
                ):
                    continue
                manager = self.workflow.find(manager_id)
                if manager is None or manager.kind != "tasks_manager":
                    continue
                key = self._transition_receipt_key(manager_id, task_id)
                receipt["receipt_path"] = str(path)
                self.checkpoint.task_transition_receipts[key] = dict(receipt)
                progress = self.checkpoint.task_progress.setdefault(
                    manager_id,
                    {
                        "active_task_id": "",
                        "completed_task_ids": [],
                        "failed_task_ids": [],
                    },
                )
                completed = progress.setdefault("completed_task_ids", [])
                if task_id not in completed:
                    completed.append(task_id)
        for result in self.workflow.nodes_of_kind("result"):
            manager = self._tasks_manager_for_result(result.id)
            if manager is None:
                continue
            completed = {
                str(item)
                for item in self.checkpoint.task_progress.get(manager.id, {}).get(
                    "completed_task_ids", []
                )
            }
            if not completed:
                continue
            for raw in self.checkpoint.history.get(result.id, []):
                if not isinstance(raw, dict) or raw.get("status") != "success":
                    continue
                data = raw.get("data")
                if not isinstance(data, dict):
                    continue
                task_id = str(data.get("task_id") or "")
                if (
                    not task_id
                    or task_id not in completed
                    or str(data.get("branch") or "") != "true"
                    or data.get("verdict") is not True
                ):
                    continue
                key = self._transition_receipt_key(manager.id, task_id)
                if key in self.checkpoint.task_transition_receipts:
                    continue
                self._record_task_transition_receipt(
                    manager=manager,
                    task_id=task_id,
                    result=result,
                    candidate_path=str(data.get("candidate_path") or ""),
                    confirmed_by_user=bool(
                        result.config.get("wait_for_confirmation", False)
                    ),
                    confirmed_at=str(raw.get("finished_at") or "") or None,
                    recovered=True,
                )

        # Only legacy receipts without a committed state_patch need backfilling.
        # Replaying a committed patch is unsafe: the project may already stage a
        # later element, so markers such as {state.staged_element_id} would bind
        # to the wrong task and roll the state backward on every Runner open.
        if receipts_dir is None:
            return
        workspace = receipts_dir.parents[2]
        for key, raw_receipt in list(
            self.checkpoint.task_transition_receipts.items()
        ):
            if not isinstance(raw_receipt, dict):
                continue
            receipt = dict(raw_receipt)
            manager_id = str(receipt.get("manager_id") or "")
            task_id = str(receipt.get("task_id") or "")
            result_id = str(receipt.get("result_node_id") or "")
            manager = self.workflow.find(manager_id)
            result = self.workflow.find(result_id)
            if (
                not manager
                or manager.kind != "tasks_manager"
                or not result
                or result.kind != "result"
                or not task_id
                or receipt.get("status") != "approved"
                or receipt.get("verdict") is not True
            ):
                continue
            existing_patch = receipt.get("state_patch")
            if isinstance(existing_patch, dict) and existing_patch.get("type"):
                continue
            receipt["receipt_id"] = str(receipt.get("receipt_id") or "") or (
                quality_payload_hash(
                    {
                        field: receipt.get(field)
                        for field in (
                            "manager_id",
                            "task_id",
                            "result_node_id",
                            "confirmed_at",
                            "candidate_path",
                        )
                    }
                )[:24]
            )
            state_patch = self._apply_task_transition_adapter(
                result, manager, task_id, receipt, workspace
            )
            receipt["state_patch"] = state_patch
            receipt["state_patch_hash"] = quality_payload_hash(state_patch)
            receipt_path = atomic_write_json(
                receipts_dir / f"{receipt['receipt_id']}.json", receipt
            )
            receipt["receipt_path"] = str(receipt_path)
            self.checkpoint.task_transition_receipts[key] = receipt

    def _previous_task_transition(
        self, manager: FlowNode, active_task_id: str
    ) -> dict[str, Any]:
        tasks = self._managed_tasks_for_node(manager)
        active_index = next(
            (
                index
                for index, task in enumerate(tasks)
                if str(task["id"]) == active_task_id
            ),
            -1,
        )
        if active_index <= 0:
            return {}
        previous_id = str(tasks[active_index - 1]["id"])
        key = self._transition_receipt_key(manager.id, previous_id)
        receipt = self.checkpoint.task_transition_receipts.get(key)
        if not isinstance(receipt, dict):
            return {}
        if (
            receipt.get("status") != "approved"
            or receipt.get("manager_id") != manager.id
            or receipt.get("task_id") != previous_id
            or receipt.get("branch") != "true"
            or receipt.get("verdict") is not True
        ):
            return {}
        return dict(receipt)

    def _active_task_transition(self, target: FlowNode) -> dict[str, Any]:
        for manager in self.workflow.nodes_of_kind("tasks_manager"):
            if not self._is_forward_upstream(manager.id, target.id):
                continue
            active_task_id = str(
                self.checkpoint.task_progress.get(manager.id, {}).get(
                    "active_task_id", ""
                )
            )
            receipt = self._previous_task_transition(manager, active_task_id)
            if receipt:
                return receipt
        return {}

    @staticmethod
    def _task_transition_block(receipt: dict[str, Any]) -> str:
        payload = json.dumps(receipt, ensure_ascii=False, indent=2)
        return (
            "# Підтверджений перехід Flow\n"
            "Наведена нижче квитанція є авторитетним системним доказом того, "
            "що попереднє завдання завершило Result TRUE. Вона підтверджує лише "
            "перехід до поточного завдання і не замінює QA поточного результату.\n"
            "FlowAI вже атомарно застосував налаштований state patch. Агентам "
            "заборонено самостійно міняти статус проходження або повторно "
            "підтверджувати попередній крок. Якщо предметний файл суперечить "
            "receipt, повідом про engine_state blocker замість ручного ремонту.\n\n"
            f"```json\n{payload}\n```"
        )

    def _failed_task_titles(self) -> list[str]:
        """Заголовки завдань, що вичерпали власний бюджет спроб."""
        titles: list[str] = []
        for node in self.workflow.nodes_of_kind("tasks_manager"):
            progress = self.checkpoint.task_progress.get(node.id, {})
            failed = {
                str(task_id) for task_id in progress.get("failed_task_ids", [])
            }
            for index, task in enumerate(self._managed_tasks_for_node(node)):
                if str(task["id"]) in failed:
                    titles.append(managed_task_title(task, index))
        return titles

    @staticmethod
    def _requirement_scope_key(
        result_id: str,
        manager_id: str = "",
        task_id: str = "",
    ) -> str:
        if manager_id and task_id:
            return f"task:{manager_id}:{task_id}"
        return f"result:{result_id}"

    @staticmethod
    def _normalized_requirements(values: object) -> list[str]:
        if not isinstance(values, list):
            values = [values] if values is not None else []
        normalized: list[str] = []
        for item in values:
            text = str(item).strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    def _legacy_result_requirements(self, result_id: str) -> list[str]:
        """Recover current-task feedback written before persistent scopes existed."""
        entries = self.checkpoint.history.get(result_id, [])
        boundary = -1
        for index, raw in enumerate(entries):
            data = raw.get("data") if isinstance(raw, dict) else None
            branch = str(data.get("branch") or "") if isinstance(data, dict) else ""
            if branch in {"true", "exhausted"}:
                boundary = index
        notes: list[str] = []
        for raw in entries[boundary + 1 :]:
            data = raw.get("data") if isinstance(raw, dict) else None
            if not isinstance(data, dict):
                continue
            note = str(data.get("user_note") or "").strip()
            if note and note not in notes:
                notes.append(note)
        return notes

    def _requirements_for_result(
        self,
        result: FlowNode,
        manager: FlowNode | None,
        task_id: str,
    ) -> list[str]:
        scope = self._requirement_scope_key(
            result.id,
            manager.id if manager is not None else "",
            task_id,
        )
        requirements = self._normalized_requirements(
            self.checkpoint.user_requirements.get(scope, [])
        )
        if not requirements:
            requirements = self._legacy_result_requirements(result.id)
        if requirements:
            self.checkpoint.user_requirements[scope] = requirements
        return list(requirements)

    def _remember_user_requirement(
        self,
        result: FlowNode,
        manager: FlowNode | None,
        task_id: str,
        note: str,
    ) -> list[str]:
        requirements = self._requirements_for_result(result, manager, task_id)
        value = note.strip()
        if value and value not in requirements:
            requirements.append(value)
        scope = self._requirement_scope_key(
            result.id,
            manager.id if manager is not None else "",
            task_id,
        )
        self.checkpoint.user_requirements[scope] = requirements
        return list(requirements)

    def _active_user_requirements(self, target: FlowNode) -> list[str]:
        requirements: list[str] = []
        for manager in self.workflow.nodes_of_kind("tasks_manager"):
            if not self._is_forward_upstream(manager.id, target.id):
                continue
            progress = self.checkpoint.task_progress.get(manager.id, {})
            task_id = str(progress.get("active_task_id", ""))
            if not task_id:
                continue
            scope = self._requirement_scope_key("", manager.id, task_id)
            for value in self._normalized_requirements(
                self.checkpoint.user_requirements.get(scope, [])
            ):
                if value not in requirements:
                    requirements.append(value)
        for result in self.workflow.nodes_of_kind("result"):
            if not self._is_forward_upstream(target.id, result.id):
                continue
            scope = self._requirement_scope_key(result.id)
            for value in self._normalized_requirements(
                self.checkpoint.user_requirements.get(scope, [])
            ):
                if value not in requirements:
                    requirements.append(value)
        return requirements

    @staticmethod
    def _user_requirements_block(requirements: list[str]) -> str:
        lines = [
            "# Обов'язкові рішення користувача — найвищий пріоритет",
            (
                "Ці рішення діють для поточного завдання на всіх повторних "
                "проходах. Якщо QA, must_fix, критерії, промпт ноди або файл "
                "інструкцій суперечить їм, виконуй рішення користувача й "
                "ігноруй лише суперечливу частину. Не відхиляй результат за "
                "дотримання цих рішень. Пізніше рішення має пріоритет над "
                "ранішим, якщо вони суперечать одне одному."
            ),
        ]
        lines.extend(f"{index}. {value}" for index, value in enumerate(requirements, 1))
        return "\n".join(lines)

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
        with self._control_lock:
            confirmation_mode = normalize_confirmation_mode(
                node.config.get("confirmation_mode")
            )
            confirmation_ports = normalize_confirmation_ports(
                node.config.get("confirmation_ports")
            )
            final_task_result = bool(node.config.get("final_task_result", True))
        ui_plan = find_ui_plan(inputs)
        variants = find_variants(inputs)

        with self._control_lock:
            manager = self._tasks_manager_for_result(node.id)
            exhaustion_manager = self.workflow.exhausted_target(node.id)
        active_task_id = ""
        if manager is not None:
            progress = self.checkpoint.task_progress.get(manager.id, {})
            active_task_id = str(progress.get("active_task_id", ""))
        user_requirements = self._requirements_for_result(
            node, manager, active_task_id
        )

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
        visual_override_review = (
            confirmation_mode == "asset_approval"
            and port == "false"
            and not has_non_overridable_issues(review)
        )
        confirmation_required = wait_for_confirmation and (
            port in confirmation_ports or visual_override_review
        )
        accepted_actions = {
            "continue",
            "continue_with_feedback",
            "add_attempts",
            "force_branch",
            "approve_plan",
            "select_variants",
            "override_visual",
            "retry_task",
        }
        if confirmation_required and action not in accepted_actions:
            draft = dict(self.checkpoint.result_drafts.get(node.id) or {})
            contract_preview = (
                build_retry_contract(
                    review,
                    workspace=workspace,
                    task_id=active_task_id or "flow",
                    source_qa_run_id=str(review.get("qa_run_id") or ""),
                )
                if port == "false"
                else {}
            )
            questions = {
                "plan_approval": "Перевірте, відредагуйте і підтвердьте UI-план",
                "variant_selection": "Виберіть один або декілька PNG-варіантів",
                "asset_approval": "Перевірте PSD, exports і вердикт QA",
            }
            raise InterventionRequired(
                {
                    "node_id": node.id,
                    "node_title": node.title,
                    "type": "result_confirmation",
                    "confirmation_mode": confirmation_mode,
                    "port": port,
                    "verdict": verdict,
                    "reason": reason,
                    "must_fix": must_fix,
                    "score": review.get("score"),
                    "score_status": review.get("score_status", "current"),
                    "score_delta_explanation": review.get(
                        "score_delta_explanation", {}
                    ),
                    "task_id": active_task_id or review.get("task_id", "flow"),
                    "evaluated_artifact_hash": review.get(
                        "evaluated_artifact_hash", ""
                    ),
                    "review": review,
                    "issues": review.get("issues") or [],
                    "failed_checks": contract_preview.get("failed_checks") or [],
                    "protected_passed_checks": contract_preview.get(
                        "protected_passed_checks"
                    )
                    or [],
                    "editable_files": contract_preview.get("editable_files") or [],
                    "evidence_files": review.get("evidence_files") or [],
                    "user_requirements": user_requirements,
                    "candidate_path": candidate_path,
                    "ui_project_spec": ui_plan,
                    "variants": variants,
                    "feedback_draft": str(draft.get("note") or ""),
                    "selected_variant_ids": list(
                        draft.get("selected_variant_ids") or []
                    ),
                    "approved_plan_draft": draft.get("approved_plan") or ui_plan,
                    "approved_plan_text": str(
                        draft.get("approved_plan_text") or ""
                    ),
                    "allow_visual_override": not has_non_overridable_issues(review),
                    "files": self._existing_input_files(
                        {
                            "inputs": inputs,
                            "outputs": [
                                result.data for result in self.outputs.values()
                            ],
                        },
                        workspace,
                    ),
                    "question": questions.get(
                        confirmation_mode,
                        "Перевірте проміжні файли та підтвердьте продовження",
                    ),
                }
            )

        selected_variant_ids: list[str] = []
        selection_mode = "none"
        approved_plan: dict[str, Any] = {}
        approved_artifact_hash = ""
        if isinstance(response, dict):
            if action == "continue":
                user_note = str(response.get("note", "")).strip()
            elif action == "continue_with_feedback":
                user_note = str(response.get("note", "")).strip()
                if user_note:
                    port = "false"
                    forced = True
            elif action == "approve_plan":
                user_note = str(response.get("note", "")).strip()
                candidate = response.get("approved_plan")
                approved_plan = dict(candidate) if isinstance(candidate, dict) else ui_plan
                if not normalize_ui_tasks(approved_plan.get("tasks")):
                    raise WorkflowError(
                        "Погоджений UI-план має містити непорожній список tasks"
                    )
                approved_plan["tasks"] = normalize_ui_tasks(approved_plan.get("tasks"))
                port = "true"
                approved_artifact_hash = payload_sha256(approved_plan)
            elif action == "select_variants":
                available = {
                    str(item.get("variant_id") or "").upper(): item
                    for item in variants
                    if str(item.get("variant_id") or "").strip()
                }
                requested = response.get("selected_variant_ids")
                requested_ids = requested if isinstance(requested, list) else []
                selected_variant_ids = [
                    str(item).upper()
                    for item in requested_ids
                    if str(item).upper() in available
                ]
                selected_variant_ids = list(dict.fromkeys(selected_variant_ids))
                user_note = str(response.get("note", "")).strip()
                selection_mode = (
                    "multiple"
                    if len(selected_variant_ids) > 1
                    else "single"
                    if selected_variant_ids
                    else "none"
                )
                if selected_variant_ids:
                    port = "true"
                    selected_payload = [
                        {
                            "variant_id": variant_id,
                            "path": available[variant_id].get("path", ""),
                            "sha256": available[variant_id].get("sha256", ""),
                        }
                        for variant_id in selected_variant_ids
                    ]
                    approved_artifact_hash = payload_sha256(selected_payload)
                    if selection_mode == "single":
                        candidate_path = str(
                            available[selected_variant_ids[0]].get("path") or candidate_path
                        )
                elif user_note:
                    port = "false"
                    forced = True
                else:
                    raise WorkflowError(
                        "Виберіть хоча б один PNG або опишіть правки для нового раунду"
                    )
            elif action == "override_visual":
                if has_non_overridable_issues(review):
                    raise WorkflowError(
                        "Технічний або обов'язковий дефект не можна прийняти через override"
                    )
                user_note = str(response.get("note", "")).strip()
                port = "true"
                forced = True
            elif action == "retry_task":
                user_note = str(response.get("note", "")).strip()
                port = "false"
                forced = True
            elif action == "add_attempts":
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

        if isinstance(response, dict):
            self.checkpoint.result_drafts.pop(node.id, None)

        if user_note and port == "false":
            user_requirements = self._remember_user_requirement(
                node, manager, active_task_id, user_note
            )

        guard_key = f"{node.id}:{active_task_id or 'flow'}"
        with self._control_lock:
            retry_guard_enabled = bool(node.config.get("retry_guard_enabled", False))
            retry_guard_threshold = max(
                2, int(node.config.get("retry_guard_threshold", 2))
            )
        if port == "true":
            self.checkpoint.retry_guards.pop(guard_key, None)
        elif retry_guard_enabled and not forced and action != "retry_task":
            defect_ids = blocking_defect_ids(review)
            previous_guard = dict(self.checkpoint.retry_guards.get(guard_key) or {})
            previous_ids = {
                str(item) for item in previous_guard.get("defect_ids", []) if str(item)
            }
            repeated_ids = [item for item in defect_ids if item in previous_ids]
            previous_count = int(previous_guard.get("repeat_count", 0) or 0)
            repeat_count = previous_count + 1 if repeated_ids else 1
            score_value: float | None = None
            try:
                if review.get("score") is not None:
                    score_value = float(review.get("score"))
            except (TypeError, ValueError):
                score_value = None
            previous_score = previous_guard.get("score")
            seen_before = {
                str(item)
                for item in previous_guard.get("seen_defect_ids", [])
                if str(item)
            }
            # Регресія — це повернення дефекту, якого минулого разу вже не
            # було, тобто вважався виправленим. Саме лише падіння score нею
            # не є: бали просідають і від нових, ще не бачених зауважень.
            regressed_ids = [
                item for item in defect_ids if item in seen_before - previous_ids
            ]
            regression = bool(regressed_ids)
            guard = {
                "defect_ids": defect_ids,
                "repeated_defect_ids": repeated_ids,
                "regressed_defect_ids": regressed_ids,
                "repeat_count": repeat_count,
                "score": score_value,
                "previous_score": previous_score,
                "regression": regression,
                "seen_defect_ids": sorted(seen_before | set(defect_ids)),
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self.checkpoint.retry_guards[guard_key] = guard
            if (repeated_ids and repeat_count >= retry_guard_threshold) or regression:
                raise InterventionRequired(
                    {
                        "node_id": node.id,
                        "node_title": node.title,
                        "type": "retry_attention",
                        "port": "false",
                        "reason": reason,
                        "review": review,
                        "must_fix": must_fix,
                        "defect_ids": defect_ids,
                        "repeated_defect_ids": repeated_ids,
                        "regressed_defect_ids": regressed_ids,
                        "repeat_count": repeat_count,
                        "score": score_value,
                        "previous_score": previous_score,
                        "regression": regression,
                        "question": (
                            "Той самий QA-дефект повторився двічі"
                            if repeated_ids
                            else "Раніше виправлений дефект повернувся"
                        ),
                    }
                )

        failed_task_id = ""
        if (
            port == "false"
            and exhaustion_manager is not None
            and active_task_id
            and not forced
        ):
            attempt_key = f"{node.id}:{active_task_id}"
            used_attempts = self.checkpoint.task_attempts.get(attempt_key, 0) + 1
            self.checkpoint.task_attempts[attempt_key] = used_attempts
            with self._control_lock:
                attempt_limit = max(
                    1, int(node.config.get("task_attempt_limit", 2))
                )
            if used_attempts >= attempt_limit:
                port = "exhausted"
                failed_task_id = active_task_id
                progress = self.checkpoint.task_progress.setdefault(
                    exhaustion_manager.id,
                    {
                        "active_task_id": "",
                        "completed_task_ids": [],
                        "failed_task_ids": [],
                    },
                )
                failed_ids = progress.setdefault("failed_task_ids", [])
                if active_task_id not in failed_ids:
                    failed_ids.append(active_task_id)
                self._emit(
                    "task_exhausted",
                    node=node,
                    message=(
                        f"Завдання вичерпало {attempt_limit} спроби — "
                        "переходимо до наступного"
                    ),
                    task_id=active_task_id,
                    attempts=used_attempts,
                )

        key = f"{node.id}:{port}"
        used = self.checkpoint.port_counts.get(key, 0)
        with self._control_lock:
            configured_limit = self.workflow.result_port_limit(node, port)
        limit = configured_limit + self.checkpoint.limit_grants.get(key, 0)
        if port != "exhausted" and not forced and used + 1 > limit:
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

        if not approved_artifact_hash and candidate_path:
            try:
                candidate_file = self._resolved_file(candidate_path, workspace)
                candidate_file.relative_to(workspace.resolve())
                if candidate_file.is_file():
                    approved_artifact_hash = self._file_sha256(candidate_file)
            except (OSError, RuntimeError, ValueError):
                approved_artifact_hash = ""

        transition_receipt: dict[str, Any] = {}
        if (
            port == "true"
            and (verdict is True or action == "override_visual")
            and final_task_result
            and manager is not None
            and active_task_id
        ):
            transition_receipt = {
                "status": "approved",
                "manager_id": manager.id,
                "task_id": active_task_id,
                "result_node_id": node.id,
                "branch": "true",
                "verdict": True,
                "confirmed_by_user": bool(confirmation_required and action),
                "confirmed_at": datetime.now().astimezone().isoformat(),
                "candidate_path": candidate_path,
                "approved_artifact_hash": approved_artifact_hash,
            }

        saved_to = ""
        if port == "true":
            text = render_template(
                str(node.config.get("template", "{{inputs}}")), context
            )
            save_path = str(node.config.get("save_path", "")).strip()
            if save_path:
                target = local_output_path(save_path, workspace, ARTIFACTS_DIR)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                saved_to = str(target)
        elif port == "exhausted":
            text = reason or "Завдання вичерпало ліміт спроб"
        else:
            text = reason or "Результат відправлено на переробку"

        retry_contract: dict[str, Any] = {}
        contract_key = f"{node.id}:{active_task_id or 'flow'}"
        if port == "false" and bool(
            node.config.get("retry_contract_enabled", True)
        ):
            previous_contract = self.checkpoint.retry_contracts.get(contract_key)
            retry_contract = build_retry_contract(
                review,
                workspace=workspace,
                task_id=active_task_id or "flow",
                source_qa_run_id=str(review.get("qa_run_id") or ""),
                previous_contract=(
                    previous_contract if isinstance(previous_contract, dict) else None
                ),
            )
            retry_contract.update(
                {
                    "reason": reason,
                    "must_fix": must_fix,
                    "candidate_path": candidate_path,
                    "user_requirements": user_requirements,
                    "instruction": (
                        "Виправ тільки failed_checks; не змінюй protected PASS "
                        "артефакти; регулярно зберігай versioned best candidate."
                    ),
                }
            )
            retry_contract["retry_contract_hash"] = quality_payload_hash(
                retry_contract
            )
            self.checkpoint.retry_contracts[contract_key] = dict(retry_contract)
            contract_name = "".join(
                character
                if character.isalnum() or character in "-_"
                else "-"
                for character in (active_task_id or "flow")
            ).strip("-") or "flow"
            contract_path = atomic_write_json(
                workspace
                / ".flowai"
                / "runtime"
                / "retry-contracts"
                / contract_name
                / f"attempt-{self.checkpoint.iterations.get(node.id, 0) + 1:03d}.json",
                retry_contract,
            )
            retry_contract["contract_path"] = str(contract_path)
            manifest_path, current_path = write_versioned_attempt_manifest(
                workspace,
                task_id=active_task_id or "flow",
                attempt_number=self.checkpoint.iterations.get(node.id, 0) + 1,
                manifest={
                    "result_node_id": node.id,
                    "branch": "false",
                    "candidate_path": candidate_path,
                    "review_contract_hash": review.get("qa_contract_hash", ""),
                    "retry_contract_path": str(contract_path),
                    "retry_contract_hash": retry_contract["retry_contract_hash"],
                },
            )
            retry_contract["attempt_manifest_path"] = str(manifest_path)
            retry_contract["current_attempt_path"] = str(current_path)
            self.checkpoint.retry_contracts[contract_key] = dict(retry_contract)
            self._emit(
                "retry_contract_created",
                node=node,
                message="Створено цільовий контракт повторної спроби",
                task_id=active_task_id or "flow",
                failed_checks=list(retry_contract.get("failed_checks") or []),
                protected_passed_checks=list(
                    retry_contract.get("protected_passed_checks") or []
                ),
                contract_path=str(contract_path),
            )
        elif port == "true":
            self.checkpoint.retry_contracts.pop(contract_key, None)

        data: dict[str, Any] = {
            "verdict": verdict,
            "branch": port,
            "confirmation_mode": confirmation_mode,
            "action": action,
            "result": text,
            "saved_to": saved_to,
            "reason": reason,
            "must_fix": must_fix,
            "candidate_path": candidate_path,
            "review": review,
            "forced": forced,
            "task_id": active_task_id,
            "user_requirements": user_requirements,
            "selected_variant_ids": selected_variant_ids,
            "selection_mode": selection_mode,
            "approved_artifact_hash": approved_artifact_hash,
        }
        if approved_plan:
            data["approved_plan"] = approved_plan
            data["ui_project_spec"] = approved_plan
        if variants:
            data["variants"] = variants
        if transition_receipt:
            data["task_transition_receipt"] = transition_receipt
        retry_variant_ids: list[str] = []
        frozen_variants: list[dict[str, Any]] = []
        if confirmation_mode == "variant_selection" and port == "false" and variants:
            raw_retry_ids = review.get("retry_variant_ids")
            requested_retry_ids = (
                raw_retry_ids if isinstance(raw_retry_ids, list) else []
            )
            available_ids = {
                str(item.get("variant_id") or "").upper() for item in variants
            }
            retry_variant_ids = [
                str(item).upper()
                for item in requested_retry_ids
                if str(item).upper() in available_ids
            ]
            if not retry_variant_ids:
                retry_variant_ids = sorted(available_ids)
            retry_variant_ids = list(dict.fromkeys(retry_variant_ids))
            frozen_variants = [
                dict(item)
                for item in variants
                if str(item.get("variant_id") or "").upper()
                not in set(retry_variant_ids)
            ]
        if retry_contract:
            retry_contract["previous_review"] = review
            retry_contract["retry_variant_ids"] = retry_variant_ids
            retry_contract["frozen_variants"] = frozen_variants
            data["retry_contract"] = retry_contract
            data["retry_context"] = retry_contract
        else:
            data["retry_context"] = {
                "reason": reason,
                "must_fix": must_fix,
                "candidate_path": candidate_path,
                "previous_review": review,
                "user_requirements": user_requirements,
                "retry_variant_ids": retry_variant_ids,
                "frozen_variants": frozen_variants,
                "instruction": (
                    "Спочатку виконай обов'язкові рішення користувача. Із must_fix "
                    "виконай усе, що їм не суперечить. Не змінюй прийняті області. "
                    "Після перевірки атомарно онови candidate_path."
                ),
            }
        if user_note:
            data["user_note"] = user_note
        if failed_task_id:
            data["task_outcome"] = "failed"
            data["failed_task_id"] = failed_task_id
        if bool(node.config.get("learning_enabled", False)):
            event = {
                "event_type": "result_review",
                "workflow": self.workflow.name,
                "node_id": node.id,
                "node_title": node.title,
                "result_iteration": self.checkpoint.iterations.get(node.id, 0) + 1,
                "task_id": active_task_id,
                "accepted": port == "true",
                "verdict": verdict,
                "action": action,
                "user_note": user_note,
                "candidate_path": candidate_path,
                "selected_variant_ids": selected_variant_ids,
                "review": review,
            }
            event_id = payload_sha256(event)
            if event_id not in self.checkpoint.learning_event_ids:
                try:
                    log_path, profile_path = append_ui_learning(
                        workspace,
                        event,
                        log_path=str(
                            node.config.get("learning_log_path")
                            or "learnings/ui_learnings.jsonl"
                        ),
                        profile_path=str(
                            node.config.get("project_profile_path")
                            or "learnings/ui_project_profile.md"
                        ),
                    )
                except (OSError, ValueError) as exc:
                    raise WorkflowError(
                        f"Не вдалося оновити локальне UI-навчання: {exc}"
                    ) from exc
                self.checkpoint.learning_event_ids.append(event_id)
                data["learning"] = {
                    "event_id": event_id,
                    "log_path": str(log_path),
                    "profile_path": str(profile_path),
                }
                self._emit(
                    "ui_learning_updated",
                    node=node,
                    message=f"Локальні UI-знання оновлено: {profile_path}",
                    event_id=event_id,
                    log_path=str(log_path),
                    profile_path=str(profile_path),
                )
        return NodeResult(node.id, "success", text=text, data=data)

    def _upstream_node_of_kind(
        self, node_id: str, kind: str
    ) -> FlowNode | None:
        """Знайти найближчу ноду вказаного типу вгору по графу."""
        seen: set[str] = set()
        stack = [edge.source for edge in self.workflow.incoming(node_id)]
        while stack:
            current = stack.pop(0)
            if current in seen:
                continue
            seen.add(current)
            node = self.workflow.find(current)
            if node is None:
                continue
            if node.kind == kind:
                return node
            stack.extend(edge.source for edge in self.workflow.incoming(current))
        return None

    def _execute_calibrator(
        self,
        node: FlowNode,
        inputs: dict[str, Any],
        context: dict[str, Any],
        workspace: Path,
        codex: CodexAdapter | None,
    ) -> NodeResult:
        """Після K-го відхилення зупинити Flow і зібрати рекомендації."""
        manager = next(iter(self.workflow.nodes_of_kind("tasks_manager")), None)
        task_id = ""
        task_title = ""
        if manager is not None:
            progress = self.checkpoint.task_progress.get(manager.id, {})
            task_id = str(progress.get("active_task_id", ""))
            tasks = self._managed_tasks_for_node(manager)
            for index, task in enumerate(tasks):
                if str(task["id"]) == task_id:
                    task_title = managed_task_title(task, index)
                    break

        with self._control_lock:
            auto_skip = bool(node.config.get("auto_skip", False))
        retry_context = self._retry_context_from(inputs)
        if auto_skip:
            self._emit(
                "calibration_skipped",
                node=node,
                message="AutoSkip увімкнено — аналіз ефективності пропущено",
            )
            return NodeResult(
                node.id,
                "success",
                text="Optimizer пропущено через AutoSkip",
                data={
                    "action": "auto_skip",
                    "task_id": task_id,
                    "retry_context": retry_context,
                },
            )

        response = self.intervention_responses.pop(node.id, None)
        if isinstance(response, dict):
            action = str(response.get("action", "continue"))
            if action == "retry_task":
                self.checkpoint.calibration_attempts.pop(
                    f"{node.id}:{task_id}", None
                )
                result_node = self._upstream_node_of_kind(node.id, "result")
                if result_node is not None and task_id:
                    self.checkpoint.task_attempts.pop(
                        f"{result_node.id}:{task_id}", None
                    )
            return NodeResult(
                node.id,
                "success",
                text="Калібрацію завершено",
                data={
                    "action": action,
                    "task_id": task_id,
                    "retry_context": retry_context,
                },
            )

        key = f"{node.id}:{task_id}"
        attempt = self.checkpoint.calibration_attempts.get(key, 0) + 1
        self.checkpoint.calibration_attempts[key] = attempt
        with self._control_lock:
            threshold = max(1, int(node.config.get("false_threshold", 2)))
        if attempt < threshold:
            self._emit(
                "calibration_skipped",
                node=node,
                message=(
                    f"Відхилення {attempt} з {threshold} — даємо Flow "
                    "спробувати ще раз"
                ),
            )
            return NodeResult(
                node.id,
                "success",
                text=f"Відхилення {attempt} з {threshold}",
                data={
                    "action": "wait",
                    "attempt": attempt,
                    "retry_context": retry_context,
                },
            )

        review = self._review_payload_from(inputs)
        reason = self._reason_from(inputs)
        must_fix = review.get("must_fix")
        if not isinstance(must_fix, list):
            must_fix = []

        reviewer = self._upstream_node_of_kind(node.id, "task_reviewer")
        executor = self._upstream_node_of_kind(node.id, "executor")
        if reviewer is not None:
            node.config["thread_source"] = reviewer.id
            node.config["reviewer_node"] = reviewer.id

        configured_ids = [
            str(item)
            for item in node.config.get("reviewed_nodes", [])
            if str(item)
        ]
        reviewed_nodes: list[FlowNode] = []
        for node_id in configured_ids:
            reviewed = self.workflow.find(node_id)
            if reviewed is not None and reviewed not in reviewed_nodes:
                reviewed_nodes.append(reviewed)
        if not reviewed_nodes:
            reviewed_nodes = [
                item for item in (executor, reviewer) if item is not None
            ]

        used: list[str] = []
        used_by_node: dict[str, list[str]] = {}
        for reviewed in reviewed_nodes:
            names = skills_used(self.outputs_steps_for(reviewed))
            used_by_node[reviewed.id] = names
            for name in names:
                if name not in used:
                    used.append(name)
        catalogue = catalogue_text(list_skills(codex))
        generated: list[str] = []
        if executor is not None:
            previous = self.checkpoint.outputs.get(executor.id, {})
            data = previous.get("data")
            if isinstance(data, dict):
                generated = [
                    str(path) for path in data.get("_generated_files", [])
                ]

        task_prompt = ""
        if manager is not None and task_id:
            for task in self._managed_tasks_for_node(manager):
                if str(task["id"]) == task_id:
                    task_prompt = str(task.get("prompt", ""))
                    break

        reviewed_payload: list[dict[str, Any]] = []
        for reviewed in reviewed_nodes:
            previous = self.checkpoint.outputs.get(reviewed.id, {})
            previous_data = previous.get("data")
            reviewed_files = (
                [
                    str(path)
                    for path in previous_data.get("_generated_files", [])
                ]
                if isinstance(previous_data, dict)
                else []
            )
            reviewed_payload.append(
                {
                    "node_id": reviewed.id,
                    "node_title": reviewed.title,
                    "kind": reviewed.kind,
                    "model": reviewed.config.get("model", ""),
                    "reasoning_effort": reviewed.config.get(
                        "reasoning_effort", ""
                    ),
                    "prompt_source": reviewed.config.get("prompt_source", ""),
                    "prompt": reviewed.config.get("prompt", ""),
                    "instructions": reviewed.config.get("instructions", ""),
                    "duration_seconds": previous.get("duration_seconds", 0.0),
                    "steps": self.bounded_steps_for(reviewed),
                    "generated_files": reviewed_files,
                    "skills_used": used_by_node.get(reviewed.id, []),
                }
            )

        protocol_path = Path(self.checkpoint.protocol_path) if (
            self.checkpoint.protocol_path
        ) else None
        protocol_attachments = (
            [protocol_path]
            if protocol_path is not None and protocol_path.is_file()
            else []
        )
        analysis_context = dict(context)
        analysis_context.update(
            {
                "skills_used": "\n".join(f"- {name}" for name in used)
                or "Агент не відкрив жодного скіла",
                "skills_catalogue": catalogue or "Каталог порожній",
                "task_prompt": task_prompt,
                "node_instructions": str(
                    executor.config.get("instructions", "") if executor else ""
                ),
                "node_prompt": str(
                    executor.config.get("prompt", "") if executor else ""
                ),
                "generated_files": "\n".join(
                    f"- {path}" for path in generated
                )
                or "Файлів не зафіксовано",
                "reviewed_nodes": json.dumps(
                    reviewed_payload, ensure_ascii=False, indent=2
                ),
                "protocol_path": str(protocol_path or ""),
            }
        )

        self._emit(
            "calibration_started",
            node=node,
            message="Аналізую скіли й готую рекомендації",
        )
        payload: Any = None
        analysis_error = ""
        try:
            analysis = self._execute_agent(
                node,
                inputs,
                analysis_context,
                workspace,
                codex,
                extra_attachments=protocol_attachments,
            )
            payload = analysis.data
            if (
                isinstance(payload, dict)
                and "response" in payload
                and not any(
                    key in payload
                    for key in ("summary", "node_reviews", "points", "edits")
                )
            ):
                payload = payload["response"]
        except RunCancelled:
            raise
        except Exception as exc:
            LOGGER.exception("Аналіз калібрації не вдався")
            analysis_error = str(exc)
            # Мовчазний збій тут коштував запуску: користувач бачив звіт без
            # жодної рекомендації і не знав, що аналізу взагалі не було.
            self._emit(
                "calibration_failed",
                node=node,
                message=f"Аналіз Optimizer не виконано: {exc}",
            )

        report = parse_report(
            payload,
            node_id=node.id,
            node_title=node.title,
            task_id=task_id,
            task_title=task_title or "Активне завдання",
            workflow_name=self.workflow.name,
            attempt=attempt,
            threshold=threshold,
            reason=reason,
            must_fix=[str(item) for item in must_fix],
            skills_used=used,
        )
        if analysis_error:
            report.analysis_error = analysis_error
        if report.root_cause_category in {"engine_state", "tool_failure"}:
            # Prompt patches cannot safely repair scheduler/state/tool defects.
            report.edits = []
        if configured_ids:
            allowed = {reviewed.id: reviewed for reviewed in reviewed_nodes}
            by_id = {
                review.node_id: review
                for review in report.node_reviews
                if review.node_id in allowed
            }
            normalized_reviews: list[NodeOptimizationReview] = []
            for reviewed in reviewed_nodes:
                node_review = by_id.get(reviewed.id) or NodeOptimizationReview(
                    node_id=reviewed.id,
                    node_title=reviewed.title,
                    summary="Аналіз не повернув рекомендацій для цієї ноди.",
                )
                node_review.node_title = reviewed.title
                normalized_reviews.append(node_review)
            report.node_reviews = normalized_reviews

            safe_edits = []
            for edit in report.edits:
                target = allowed.get(edit.node_id)
                if target is None:
                    continue
                allowed_targets = {
                    "executor": {"node_instructions"},
                    "task_reviewer": {"node_instructions", "node_prompt"},
                }.get(target.kind, set())
                if edit.target not in allowed_targets:
                    continue
                safe_edits.append(edit)
            report.edits = safe_edits
        elif executor is not None:
            for edit in report.edits:
                if edit.target in {"node_prompt", "node_instructions"}:
                    edit.node_id = edit.node_id or executor.id
                if edit.target == "task_prompt":
                    edit.task_id = edit.task_id or task_id

        report_path = save_report(report, self._protocol_directory())
        feedback_triggered = bool(review.get("forced")) and bool(
            str(review.get("user_note") or "").strip()
        )
        question = (
            f"Користувач надіслав правки до «{report.task_title}» — "
            "перегляньте рекомендації Optimizer"
            if feedback_triggered
            else (
                f"Рев'ювер відхилив «{report.task_title}» — "
                "перегляньте рекомендації"
            )
        )
        raise InterventionRequired(
            {
                "node_id": node.id,
                "node_title": node.title,
                "type": "calibration",
                "trigger": "user_feedback" if feedback_triggered else "qa_rejection",
                "question": question,
                "report": report.to_dict(),
                "report_path": str(report_path),
            }
        )

    def outputs_steps_for(self, node: FlowNode | None) -> list[dict[str, Any]]:
        """Кроки останнього ходу ноди, де видно відкриті скіли."""
        if node is None:
            return []
        return list(self.checkpoint.protocol_steps.get(node.id, []))

    def bounded_steps_for(self, node: FlowNode | None) -> list[dict[str, Any]]:
        """Кроки ноди в межах бюджета аналізу.

        Транспорт Codex відмовляє на вході понад 1 МіБ, і робив це мовчки:
        Optimizer лишався без жодної рекомендації. Свіжі кроки цінніші за
        давні, тож бюджет витрачаємо з кінця.
        """
        steps = self.outputs_steps_for(node)
        kept: list[dict[str, Any]] = []
        budget = CALIBRATION_STEPS_BUDGET
        for step in reversed(steps):
            size = len(json.dumps(step, ensure_ascii=False))
            if size > budget and kept:
                break
            budget -= size
            kept.append(step)
        kept.reverse()
        dropped = len(steps) - len(kept)
        if dropped:
            kept.insert(
                0,
                {
                    "kind": "notice",
                    "summary": (
                        f"Ранніх кроків не показано: {dropped} — "
                        "не вміщалися в ліміт транспорту"
                    ),
                    "detail": {},
                },
            )
        return kept

    def _record_file_ledger(
        self, node_id: str, mutations: list[dict[str, Any]]
    ) -> None:
        """Зберегти журнал мутацій останнього проходу ноди.

        Раніше кожен прохід дописувався до попередніх, тож нода, яка створює
        тисячі файлів, роздувала чекпоінт без межі — а сам ledger читається
        лише як аудит останньої роботи.
        """
        self.checkpoint.file_ledgers[node_id] = list(mutations)

    @staticmethod
    def _template_variables(template: str) -> set[str]:
        """Кореневі імена, які шаблон уже підставляє сам.

        `{{work}}` і `{{work.candidate_path}}` однаково означають, що `work`
        у промпті вже є.
        """
        return {
            match.split(".", 1)[0]
            for match in PLACEHOLDER.findall(template)
            if match.split(".", 1)[0]
        }

    @staticmethod
    def _expects_json(node: FlowNode) -> bool:
        """Чи справді нода обіцяла структуровану відповідь.

        Це та сама умова, за якою `_compose_agent_prompt` вимагає JSON у
        промпті. Розбір відповіді має йти рівно за нею, інакше текстова нода
        втрачає свій `response`.
        """
        if str(node.config.get("output_format", "text")) == "json":
            return True
        return bool(node.config.get("output_schema"))

    @staticmethod
    def _compact_review_inputs(
        inputs: dict[str, Any], *, file_sample_limit: int = 20
    ) -> dict[str, Any]:
        """Keep QA evidence useful without serializing the full mutation ledger."""

        def compact(value: Any) -> Any:
            if isinstance(value, dict):
                output: dict[str, Any] = {}
                for key, item in value.items():
                    if key == "_file_ledger" and isinstance(item, list):
                        output[key] = {
                            "omitted_from_prompt": True,
                            "entry_count": len(item),
                            "note": "Повний ledger збережено в runtime checkpoint.",
                        }
                    elif key in {"_generated_files", "_modified_files"} and isinstance(
                        item, list
                    ):
                        output[key] = {
                            "count": len(item),
                            "sample": [str(path) for path in item[:file_sample_limit]],
                        }
                    else:
                        output[key] = compact(item)
                return output
            if isinstance(value, list):
                return [compact(item) for item in value]
            return value

        return compact(inputs)

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
        # `photoshop_required` — повний контракт Builder-ноди (preflight плюс
        # validate_psd кандидата). `photoshop_preflight` — лише «Photoshop є»:
        # ним користуються концепт-ноди, які малюють через COM, але віддають
        # variant manifest, а не один кінцевий .psd.
        if bool(node.config.get("photoshop_required", False)) or bool(
            node.config.get("photoshop_preflight", False)
        ):
            try:
                PhotoshopAutomation(workspace).preflight()
            except PhotoshopAutomationError as exc:
                raise InterventionRequired(
                    {
                        "node_id": node.id,
                        "node_title": node.title,
                        "type": "photoshop_attention",
                        "reason": str(exc),
                        "question": (
                            "Photoshop недоступний. Виправте встановлення або "
                            f"ресурси й повторіть ноду «{node.title}»."
                        ),
                    }
                ) from exc
        artifact_before = self._required_artifact_state(node, workspace)

        self._stage(node, 2, "Формування промпту та вкладень")
        context = dict(context)
        user_requirements = self._active_user_requirements(node)
        context["user_requirements"] = user_requirements
        task_transition = self._active_task_transition(node)
        context["previous_task_transition"] = task_transition
        reference_receipt = self._reference_analysis_for_node(node, workspace)
        if reference_receipt:
            context["reference_analysis_receipt"] = {
                key: reference_receipt[key]
                for key in ("analysis_path", "file_count", "library_sha256")
            }
        if node.kind == "prompt_reviewer":
            if bool(node.config.get("compact_flow_context", True)):
                downstream_ids = self._reachable_from([node.id])
                context["flow_chain"] = "\n".join(
                    f"- {item.kind} [{item.short_id}]: {item.title}; "
                    f"output={item.config.get('output_format', 'control')}"
                    for item in self.workflow.nodes
                    if item.id in downstream_ids and item.id != node.id
                ) or "Наступних блоків немає"
            else:
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

        task_id = self._active_task_id_for(node)
        qa_packet_payload: dict[str, Any] = {}
        if node.kind == "task_reviewer" and bool(
            node.config.get("deterministic_qa_enabled", False)
        ):
            qa_files = self._existing_input_files(inputs, workspace)
            qa_packet_payload = qa_packet(
                workspace,
                qa_files,
                task_id=task_id,
                attempt_id=(
                    f"attempt-{self.checkpoint.iterations.get(node.id, 0) + 1:03d}"
                ),
                validator_version=str(
                    node.config.get("deterministic_validator_version", "1")
                ),
            )
            packet_cache = JsonArtifactCache(workspace, "qa-packets")
            packet_path = packet_cache.save(
                f"{node.id}-{qa_packet_payload['packet_hash']}", qa_packet_payload
            )
            attachments.append(packet_path)
            context["qa_packet"] = {
                "path": str(packet_path),
                "packet_hash": qa_packet_payload["packet_hash"],
                "file_count": len(qa_packet_payload["files"]),
                "missing_files": list(qa_packet_payload["missing_files"]),
            }

        # Перелік файлів роздуває промпт однаково для будь-якої ноди, не лише
        # для QA: агентові потрібні кількість і зразок, а не тисяча шляхів.
        # Стискається саме вигляд у промпті — самі дані течуть по ребрах
        # повними.
        if bool(node.config.get("compact_review_inputs", True)):
            compact_inputs = self._compact_review_inputs(
                inputs,
                file_sample_limit=max(
                    0, int(node.config.get("review_file_sample_limit", 20))
                ),
            )
            context["inputs"] = compact_inputs
            for key in inputs:
                if key in context:
                    context[key] = compact_inputs.get(key)

        additional_workspaces = self._resolved_agent_folders(node, workspace)
        prompt = self._compose_agent_prompt(
            node, context, workspace, additional_workspaces, attachments
        )
        instructions = self._compose_agent_instructions(node, workspace)
        instruction_sections = [instructions]
        if node.kind == "task_reviewer":
            instruction_sections.append(
                "# Правила перевірки QA-відповіді у FlowAI\n"
                + task_review_contract_rules(
                    max(0, min(100, int(node.config.get("pass_threshold", 80))))
                )
            )
        if task_transition and node.kind in {
            "prompt_reviewer",
            "executor",
            "task_reviewer",
        }:
            instruction_sections.append(
                self._task_transition_block(task_transition)
            )
        if user_requirements:
            instruction_sections.append(
                self._user_requirements_block(user_requirements)
            )
        if reference_receipt:
            compact_receipt = {
                key: reference_receipt[key]
                for key in ("analysis_path", "file_count", "library_sha256")
            }
            instruction_sections.append(
                "# Перевірений кеш UI-референсів\n"
                f"Усі {reference_receipt['file_count']} референсів уже були "
                "проаналізовані один раз. SHA-256 бібліотеки: "
                f"{reference_receipt['library_sha256']}. Не запускай повторний "
                "аналіз усієї теки й не перечитуй усі вихідні картинки. "
                "Прочитай записаний analysis_path як основу стилю відповідно до "
                "закріпленого skill modern-ui. Вихідні "
                "референси дозволено відкривати лише точково для конкретного "
                "порівняння; тека джерела доступна тільки для читання. Сам текст "
                "аналізу навмисно не дублюється в контекст кожного ходу.\n\n"
                "reference_analysis_receipt:\n"
                + json.dumps(compact_receipt, ensure_ascii=False, indent=2)
            )
        retry_contract = self._retry_context_from(inputs)
        if (
            retry_contract
            and not retry_contract.get("retry_contract_hash")
            and node.kind == "executor"
            and bool(node.config.get("legacy_retry_upgrade_enabled", False))
            and isinstance(retry_contract.get("previous_review"), dict)
        ):
            legacy_review = normalize_task_review(
                retry_contract["previous_review"],
                pass_threshold=80,
                strict=False,
            )
            upgraded = build_retry_contract(
                legacy_review,
                workspace=workspace,
                task_id=task_id or "flow",
                source_qa_run_id=str(legacy_review.get("qa_run_id") or "legacy"),
            )
            upgraded.update(
                {
                    "reason": str(retry_contract.get("reason") or ""),
                    "must_fix": [
                        str(issue.get("must_fix") or issue.get("description") or "")
                        for issue in upgraded.get("issues", [])
                        if isinstance(issue, dict)
                    ],
                    "candidate_path": str(
                        retry_contract.get("candidate_path") or ""
                    ),
                    "user_requirements": retry_contract.get("user_requirements")
                    or [],
                    "previous_review": legacy_review,
                    "instruction": (
                        "Виправ лише artifact issues. system_issues належать "
                        "рушію FlowAI; не редагуй progress/status вручну."
                    ),
                    "upgraded_from_legacy_retry": True,
                }
            )
            configured_checks = [
                str(item)
                for item in node.config.get("legacy_retry_protected_checks", [])
                if str(item)
            ]
            configured_files: list[str] = []
            configured_hashes: dict[str, str] = {}
            for raw_path in node.config.get("legacy_retry_protected_files", []):
                try:
                    protected_path = self._resolved_file(str(raw_path), workspace)
                    protected_path.relative_to(workspace.resolve())
                except (OSError, RuntimeError, ValueError):
                    continue
                if not protected_path.is_file():
                    continue
                protected_text = str(protected_path)
                configured_files.append(protected_text)
                configured_hashes[protected_text] = runtime_file_sha256(
                    protected_path
                )
            upgraded["protected_passed_checks"] = list(
                dict.fromkeys(
                    [
                        *upgraded.get("protected_passed_checks", []),
                        *configured_checks,
                    ]
                )
            )
            upgraded["immutable_files"] = list(
                dict.fromkeys(
                    [*upgraded.get("immutable_files", []), *configured_files]
                )
            )
            upgraded["immutable_hashes"].update(configured_hashes)
            upgraded["retry_contract_hash"] = quality_payload_hash(upgraded)
            task_slug = "".join(
                character if character.isalnum() or character in "-_" else "-"
                for character in (task_id or "flow")
            ).strip("-") or "flow"
            contract_path = atomic_write_json(
                workspace
                / ".flowai"
                / "runtime"
                / "retry-contracts"
                / task_slug
                / "recovered-legacy-retry.json",
                upgraded,
            )
            upgraded["contract_path"] = str(contract_path)
            self.checkpoint.retry_contracts[
                f"legacy:{task_id or 'flow'}"
            ] = dict(upgraded)
            retry_contract = upgraded
            self._emit(
                "retry_contract_upgraded",
                node=node,
                message=(
                    "Старі QA-правки перетворено на машинний retry contract; "
                    "engine_state пункти вилучено з роботи Executor"
                ),
                task_id=task_id or "flow",
                contract_path=str(contract_path),
                failed_checks=list(upgraded.get("failed_checks") or []),
                system_issue_count=len(upgraded.get("system_issues") or []),
            )
        if retry_contract and retry_contract.get("retry_contract_hash"):
            regressions = protected_artifact_regressions(retry_contract, workspace)
            if regressions:
                raise InterventionRequired(
                    {
                        "node_id": node.id,
                        "node_title": node.title,
                        "type": "protected_artifact_regression",
                        "task_id": task_id,
                        "regressions": regressions,
                        "question": (
                            "Захищений артефакт, який уже пройшов QA, змінився. "
                            "Flow зупинено до повторного запуску Executor."
                        ),
                    }
                )
            instruction_sections.append(
                "# Машинний контракт повторної спроби\n"
                "Працюй лише над failed_checks і лише з editable_files/regions. "
                "Не змінюй protected_passed_checks та immutable_files. Перед "
                "дорогою ітеративною операцією назви target check, output і metric. "
                "Зупини алгоритм при досягненні acceptance threshold або коли "
                "немає meaningful improvement; регулярно зберігай best candidate.\n\n"
                + json.dumps(retry_contract, ensure_ascii=False, indent=2)
            )
        operation_policy = node.config.get("operation_policy")
        if node.kind == "executor" and isinstance(operation_policy, dict):
            instruction_sections.append(
                "# Політика ітеративних операцій\n"
                "Жорсткого ліміту часу немає, але кожен локальний пошук має "
                "дотримуватись цього operation budget, підтримувати early-stop, "
                "no-improvement patience, cancel і versioned best checkpoint:\n"
                + json.dumps(operation_policy, ensure_ascii=False, indent=2)
            )
        operation_intent_path: Path | None = None
        if (
            node.kind == "executor"
            and retry_contract.get("retry_contract_hash")
            and bool(node.config.get("operation_intent_required", False))
        ):
            task_slug = "".join(
                character if character.isalnum() or character in "-_" else "-"
                for character in (task_id or "flow")
            ).strip("-") or "flow"
            operation_intent_path = (
                workspace
                / ".flowai"
                / "runtime"
                / "operation-intents"
                / node.id
                / f"{task_slug}-{retry_contract['retry_contract_hash'][:16]}.json"
            )
            operation_intent_path.parent.mkdir(parents=True, exist_ok=True)
            instruction_sections.append(
                "# Обов'язковий operation intent\n"
                "Перед запуском Python-скрипта для ітеративного пошуку створи "
                "цей JSON через файловий інструмент (не запускай shell для його "
                "створення). Рушій перевірить його до старту дорогої команди:\n"
                f"{operation_intent_path}\n\n"
                "Поля: target_check (лише з failed_checks), input_files, "
                "output_files, metric, acceptable_threshold, max_operations, "
                "no_improvement_patience, checkpoint_every і "
                "retry_contract_hash. Output не може бути immutable."
            )
        instructions = "\n\n".join(instruction_sections)
        input_character_limit = max(
            1,
            int(
                node.config.get(
                    "input_character_limit", AGENT_INPUT_CHARACTER_LIMIT
                )
            ),
        )
        input_character_count = len(prompt) + len(instructions)
        if input_character_count > input_character_limit:
            raise WorkflowError(
                f"Вхід ноди «{node.title}» має {input_character_count} символів; "
                f"ліміт {input_character_limit}. Скоротіть source_path/шаблон або "
                "увімкніть compact_review_inputs, не запускаючи завеликий виклик."
            )
        # Про переповнення контексту треба знати до того, як транспорт
        # відмовить: тоді ще є куди рости і що скоротити.
        if input_character_count > input_character_limit * CONTEXT_BUDGET_WARNING_RATIO:
            self._emit(
                "context_budget_warning",
                node=node,
                message=(
                    f"Вхід ноди зайняв {input_character_count} символів із "
                    f"{input_character_limit} — контекст росте"
                ),
                task_id=task_id,
                input_characters=input_character_count,
                prompt_characters=len(prompt),
                instruction_characters=len(instructions),
                input_character_limit=input_character_limit,
            )

        memory = str(node.config.get("memory", "thread"))
        thread_source = str(node.config.get("thread_source", "")) or node.id
        thread_scope = thread_source
        if memory == "task_thread":
            thread_scope = f"{thread_source}:{task_id or 'flow'}"
        resume_id = (
            self.checkpoint.thread_ids.get(thread_scope, "")
            if memory in {"thread", "task_thread"}
            else ""
        )
        delivered = (
            dict(self.checkpoint.thread_inputs.get(resume_id, {}))
            if resume_id
            else {}
        )

        attachment_fingerprints: list[dict[str, Any]] = []
        for attachment in attachments:
            try:
                stat = attachment.stat()
                attachment_fingerprints.append(
                    {
                        "path": str(attachment),
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sha256": runtime_file_sha256(attachment),
                    }
                )
            except OSError:
                attachment_fingerprints.append(
                    {"path": str(attachment), "missing": True}
                )
        cache_identity = quality_payload_hash(
            {
                "node_id": node.id,
                "task_id": task_id,
                "prompt": prompt,
                "instructions": instructions,
                "attachments": attachment_fingerprints,
                "qa_packet_hash": qa_packet_payload.get("packet_hash", ""),
                "schema": node.config.get("output_schema") or {},
            }
        )
        cache: JsonArtifactCache | None = None
        if node.kind == "prompt_reviewer" and bool(
            node.config.get("prompt_cache_enabled", False)
        ):
            cache = JsonArtifactCache(workspace, "prompt-cache")
        elif node.kind == "task_reviewer" and bool(
            node.config.get("qa_cache_enabled", False)
        ):
            cache = JsonArtifactCache(workspace, "qa-cache")
        if cache is not None:
            cached = cache.load(f"{node.id}-{cache_identity}")
            if isinstance(cached, dict) and isinstance(cached.get("data"), dict):
                cached_data = dict(cached["data"])
                if node.kind == "task_reviewer":
                    try:
                        cached_data = normalize_task_review(
                            cached_data,
                            pass_threshold=max(
                                0, min(100, int(node.config.get("pass_threshold", 80)))
                            ),
                            strict=bool(
                                node.config.get("strict_review_contract", True)
                            ),
                        )
                    except QAContractError:
                        cached_data = {}
                if cached_data:
                    if node.kind == "prompt_reviewer":
                        self.checkpoint.prompt_cache_keys[node.id] = cache_identity
                    elif node.kind == "task_reviewer":
                        self.checkpoint.qa_cache_keys[node.id] = cache_identity
                    self._emit(
                        "agent_cache_hit",
                        node=node,
                        message=(
                            "Використано перевірений QA-кеш для незмінних файлів"
                            if node.kind == "task_reviewer"
                            else "Використано підготовлений prompt для незмінного Task"
                        ),
                        cache_key=cache_identity,
                    )
                    if node.kind == "task_reviewer":
                        artifact_hash = str(
                            qa_packet_payload.get("packet_hash") or cache_identity
                        )
                        cached_data.update(
                            {
                                "task_id": task_id or "flow",
                                "evaluated_artifact_hash": artifact_hash,
                                "score_status": "current",
                            }
                        )
                        score_record = self._record_qa_score(
                            node,
                            cached_data,
                            task_id=task_id,
                            artifact_hash=artifact_hash,
                            cached=True,
                        )
                        cached_data.update(
                            {
                                "qa_run_id": score_record["qa_run_id"],
                                "attempt_id": score_record["attempt_id"],
                                "score_delta_explanation": score_record[
                                    "score_delta_explanation"
                                ],
                            }
                        )
                    return NodeResult(
                        node.id,
                        "success",
                        text=str(cached.get("text") or ""),
                        data=cached_data,
                    )

        sandbox = str(node.config.get("sandbox", "read-only"))
        audit_before = snapshot_workspace(
            workspace,
            hash_all=sandbox == "read-only" and bool(
                node.config.get("read_only_audit", True)
            ),
            ignore_runtime=True,
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
            prompt_characters=len(prompt),
            instruction_characters=len(instructions),
            input_characters=input_character_count,
            input_character_limit=input_character_limit,
        )

        self._stage(node, 4, "Виконання агентом")

        validated_operation_intent: dict[str, Any] = {}

        def report(activity: dict[str, Any]) -> None:
            nonlocal validated_operation_intent
            summary = str(activity.get("summary", "")).strip()
            if not summary:
                return
            detail = (
                dict(activity.get("detail") or {})
                if isinstance(activity.get("detail"), dict)
                else {}
            )
            command = str(detail.get("command") or summary).strip()
            command_folded = command.casefold()
            kind_folded = str(activity.get("kind") or "").casefold()
            iterative_command = (
                str(activity.get("phase") or "") == "started"
                and (
                    "command" in kind_folded
                    or "execution" in kind_folded
                    or "tool" in kind_folded
                )
                and (
                    ("python" in command_folded and ".py" in command_folded)
                    or any(
                        marker in command_folded
                        for marker in ("optimize", "optimizer", "iteration-fit")
                    )
                )
            )
            if iterative_command and operation_intent_path is not None:
                try:
                    raw_intent = json.loads(
                        operation_intent_path.read_text(encoding="utf-8")
                    )
                    validated_operation_intent = validate_operation_intent(
                        raw_intent,
                        contract=retry_contract,
                        workspace=workspace,
                        policy=(
                            operation_policy
                            if isinstance(operation_policy, dict)
                            else None
                        ),
                    )
                except (OSError, ValueError, OperationIntentError) as exc:
                    codex.cancel_active()
                    errors = (
                        list(exc.errors)
                        if isinstance(exc, OperationIntentError)
                        else [str(exc)]
                    )
                    raise InterventionRequired(
                        {
                            "node_id": node.id,
                            "node_title": node.title,
                            "type": "operation_intent_rejected",
                            "task_id": task_id,
                            "command": command,
                            "intent_path": str(operation_intent_path),
                            "errors": errors,
                            "question": (
                                "Ітеративну команду зупинено до виконання: її "
                                "ціль не підтверджена активним retry contract."
                            ),
                        }
                    ) from exc
                atomic_write_json(operation_intent_path, validated_operation_intent)
                self.checkpoint.active_operation.update(
                    {
                        "operation": command,
                        "operation_intent": validated_operation_intent,
                        "operation_intent_path": str(operation_intent_path),
                    }
                )
                self._emit(
                    "operation_started",
                    node=node,
                    message=(
                        "Запущено перевірену операцію для "
                        f"{validated_operation_intent['target_check']}"
                    ),
                    task_id=task_id,
                    operation=command,
                    operation_intent=validated_operation_intent,
                )

            progress = operation_progress_from_activity(
                {"summary": summary, "detail": detail}
            )
            if progress:
                self.checkpoint.active_operation.update(progress)
                self.checkpoint.active_operation["last_progress_at"] = (
                    datetime.now(UTC).isoformat()
                )
                self._emit(
                    "operation_progress",
                    node=node,
                    message=(
                        f"Ітерація {progress['iteration']}"
                        + (
                            f"/{progress['max_iterations']}"
                            if progress.get("max_iterations")
                            else ""
                        )
                        + (
                            f" · best {progress['best_metric']}"
                            if progress.get("best_metric")
                            else ""
                        )
                    ),
                    task_id=task_id,
                    operation_intent=validated_operation_intent,
                    **progress,
                )
                self._checkpoint_boundary("running")
            self._emit(
                "agent_activity",
                node=node,
                message=summary,
                kind=str(activity.get("kind", "")),
                phase=str(activity.get("phase", "")),
                paths=[str(item) for item in activity.get("paths", [])],
                detail=detail,
            )

        run = codex.run_agent(
            prompt=prompt,
            developer_instructions=instructions,
            model=str(node.config.get("model", "gpt-5.6-terra")),
            sandbox=sandbox,
            workspace=workspace,
            additional_workspaces=additional_workspaces,
            reasoning_effort=str(node.config.get("reasoning_effort", "medium")),
            attachments=attachments,
            skills=[
                {
                    "name": str(item.get("name", "")),
                    "path": str(item.get("path", "")),
                }
                for item in node.config.get("skills", [])
                if isinstance(item, dict)
            ],
            resume_thread_id=resume_id,
            delivered_inputs=delivered,
            on_activity=report,
        )
        if self._stop.is_set():
            raise RunCancelled("Flow зупинено")
        self._stage(node, 5, "Обробка відповіді агента")
        self._last_steps = run.items
        self.checkpoint.protocol_steps[node.id] = list(run.items)
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
        if run.thread_id and memory in {"thread", "task_thread"}:
            self.checkpoint.thread_ids[thread_scope] = run.thread_id
            self.checkpoint.thread_inputs[run.thread_id] = dict(run.delivered_inputs)
            # Тред, який більше нікому не належить, нічого не пам'ятає.
            live = set(self.checkpoint.thread_ids.values())
            self.checkpoint.thread_inputs = {
                key: value
                for key, value in self.checkpoint.thread_inputs.items()
                if key in live
            }

        input_tokens = 0
        if isinstance(run.usage, dict):
            for key in ("input_tokens", "inputTokens"):
                try:
                    input_tokens = int(run.usage.get(key, 0) or 0)
                except (TypeError, ValueError):
                    input_tokens = 0
                if input_tokens:
                    break
        soft_limit = max(1, int(node.config.get("context_soft_limit", 80_000)))
        if (
            input_tokens > soft_limit
            and memory in {"thread", "task_thread"}
            and run.thread_id
        ):
            self.checkpoint.thread_ids.pop(thread_scope, None)
            self.checkpoint.thread_inputs.pop(run.thread_id, None)
            self._emit(
                "context_compacted",
                node=node,
                message=(
                    f"Контекст {input_tokens} токенів перевищив soft limit "
                    f"{soft_limit}; наступний хід почнеться у чистому task thread"
                ),
                task_id=task_id,
                input_tokens=input_tokens,
                soft_limit=soft_limit,
            )

        # Текстова нода віддає доказ далі через `data.response`. Розбирати її
        # відповідь як JSON не можна: будь-який фрагмент на кшталт
        # `{"element_id": "E19"}` усередині звіту підмінив би собою всю
        # відповідь, і наступна нода лишилася б без матеріалу.
        parsed = extract_json(run.text) if self._expects_json(node) else None
        text = run.text
        if node.kind == "task_reviewer":
            if not isinstance(parsed, dict) or "verdict" not in parsed:
                contract_error = QAContractError(
                    ["QA має повернути JSON із полем verdict"], parsed or run.text[:500]
                )
            else:
                try:
                    parsed = normalize_task_review(
                        parsed,
                        pass_threshold=max(
                            0, min(100, int(node.config.get("pass_threshold", 80)))
                        ),
                        strict=bool(node.config.get("strict_review_contract", True)),
                    )
                    contract_error = None
                except QAContractError as exc:
                    contract_error = exc

            correction_limit = max(
                0, int(node.config.get("qa_correction_attempts", 1))
            )
            correction_attempt = 0
            while contract_error is not None and correction_attempt < correction_limit:
                correction_attempt += 1
                self._emit(
                    "qa_contract_correction",
                    node=node,
                    message="QA-відповідь суперечлива — виправляємо лише JSON contract",
                    errors=list(contract_error.errors),
                    attempt=correction_attempt,
                )
                correction_prompt = (
                    "Виправ лише структуру та внутрішню узгодженість своєї "
                    "попередньої QA-відповіді. Не перечитуй файли й не змінюй "
                    "оцінку без логічної потреби. Поверни тільки JSON. Правила: "
                    + task_review_contract_rules(
                        max(0, min(100, int(node.config.get("pass_threshold", 80))))
                    )
                    + "\n\nПомилки contract:\n- "
                    + "\n- ".join(contract_error.errors)
                    + "\n\nПопередня відповідь:\n"
                    + run.text
                )
                correction = codex.run_agent(
                    prompt=correction_prompt,
                    developer_instructions=instructions,
                    model=str(node.config.get("model", "gpt-5.6-terra")),
                    sandbox=sandbox,
                    workspace=workspace,
                    additional_workspaces=additional_workspaces,
                    reasoning_effort=str(
                        node.config.get("reasoning_effort", "medium")
                    ),
                    attachments=[],
                    skills=[],
                    resume_thread_id=run.thread_id,
                    delivered_inputs=run.delivered_inputs,
                    on_activity=report,
                )
                run.items.extend(correction.items)
                run.text = correction.text
                run.thread_id = correction.thread_id or run.thread_id
                run.delivered_inputs = dict(correction.delivered_inputs)
                if correction.usage:
                    run.usage = dict(correction.usage)
                    run.context_window = correction.context_window
                parsed = extract_json(correction.text)
                try:
                    parsed = normalize_task_review(
                        parsed,
                        pass_threshold=max(
                            0, min(100, int(node.config.get("pass_threshold", 80)))
                        ),
                        strict=bool(node.config.get("strict_review_contract", True)),
                    )
                    contract_error = None
                    text = correction.text
                except QAContractError as exc:
                    contract_error = exc
            if contract_error is not None:
                raise InterventionRequired(
                    {
                        "node_id": node.id,
                        "node_title": node.title,
                        "type": "invalid_qa_contract",
                        "errors": list(contract_error.errors),
                        "invalid_response": run.text[:4000],
                        "question": (
                            "QA повернув некоректну або суперечливу відповідь "
                            f"після {correction_attempt + 1} спроб. "
                            "Flow призупинено, щоб не передати хибну оцінку далі."
                        ),
                    }
                )
            data: Any = parsed
        elif parsed is not None:
            data = parsed
        else:
            data = {"response": run.text}

        self._last_steps = run.items
        self.checkpoint.protocol_steps[node.id] = list(run.items)

        ledger = diff_workspace(
            workspace, audit_before, hash_changed=True, ignore_runtime=True
        )
        mutations = [
            {"kind": kind, **entry}
            for kind in ("generated", "modified", "deleted")
            for entry in ledger[kind]
        ]
        if sandbox == "read-only" and mutations:
            report_path = write_audit_report(
                workspace,
                node_id=node.id,
                mutation={
                    "node_id": node.id,
                    "node_title": node.title,
                    "sandbox": sandbox,
                    "mutations": mutations,
                },
            )
            raise InterventionRequired(
                {
                    "node_id": node.id,
                    "node_title": node.title,
                    "type": "read_only_mutation",
                    "mutations": mutations,
                    "audit_path": str(report_path),
                    "question": (
                        "Read-only нода змінила файли. Flow зупинено; зміни не "
                        "прийняті та перелічені в audit report."
                    ),
                }
            )

        if bool(node.config.get("variant_contract_enabled", False)):
            incoming_contract = inputs.get("prompt")
            retry_contract = (
                dict(incoming_contract)
                if isinstance(incoming_contract, dict)
                else self._retry_context_from(inputs)
            )
            try:
                data = verify_variant_manifest(
                    data,
                    workspace,
                    previous=self.checkpoint.variant_manifests.get(node.id),
                    retry_context=retry_contract,
                )
            except (TypeError, ValueError) as exc:
                raise WorkflowError(
                    f"Concept contract порушено нодою «{node.title}»: {exc}"
                ) from exc
            self.checkpoint.variant_manifests[node.id] = dict(data)

        if bool(node.config.get("enforce_project_outputs", False)):
            try:
                declared_outputs = validate_declared_output_paths(data, workspace)
            except ValueError as exc:
                raise WorkflowError(
                    f"Нода «{node.title}» порушила межі проєкту: {exc}"
                ) from exc
            if isinstance(data, dict) and declared_outputs:
                data = dict(data)
                data["_declared_output_files"] = declared_outputs

        data = self._verify_required_artifact(
            node, data, workspace, before_state=artifact_before
        )

        generated_files = [entry["path"] for entry in ledger["generated"]]
        modified_files = [entry["path"] for entry in ledger["modified"]]
        if isinstance(data, dict) and (generated_files or modified_files):
            data = dict(data)
            data["_generated_files"] = generated_files
            data["_modified_files"] = modified_files
            # Повний ledger лишається у чекпоінті як аудит. У `data` його
            # немає навмисно: його ніхто не читає, зате він тече в промпти,
            # події та журнал запуску — саме так виріс промпт на 3.38 млн
            # символів. Хто працює з файлами, бере `_generated_files`.
            self._record_file_ledger(node.id, mutations)
            if node.kind == "executor":
                self._mark_qa_score_pending(node, task_id)

        if retry_contract and retry_contract.get("retry_contract_hash"):
            regressions = protected_artifact_regressions(retry_contract, workspace)
            if regressions:
                raise InterventionRequired(
                    {
                        "node_id": node.id,
                        "node_title": node.title,
                        "type": "protected_artifact_regression",
                        "task_id": task_id,
                        "regressions": regressions,
                        "question": (
                            "Executor змінив артефакт, який уже пройшов QA. "
                            "Повний Visual QA не запущено; виправте regression."
                        ),
                    }
                )

        if run.usage and isinstance(data, dict):
            data = dict(data)
            data["usage"] = {**run.usage, "context_window": run.context_window}

        if node.kind == "prompt_reviewer" and isinstance(data, dict):
            improved = data.get("improved_prompt")
            if isinstance(improved, str) and improved.strip():
                text = improved

        if node.kind == "task_reviewer" and isinstance(data, dict):
            data = dict(data)
            artifact_hash = str(
                qa_packet_payload.get("packet_hash")
                or data.get("approved_artifact_hash")
                or cache_identity
            )
            data.update(
                {
                    "task_id": task_id or "flow",
                    "evaluated_artifact_hash": artifact_hash,
                    "score_status": "current",
                }
            )
            score_record = self._record_qa_score(
                node,
                data,
                task_id=task_id,
                artifact_hash=artifact_hash,
            )
            data.update(
                {
                    "qa_run_id": score_record["qa_run_id"],
                    "attempt_id": score_record["attempt_id"],
                    "score_delta_explanation": score_record[
                        "score_delta_explanation"
                    ],
                }
            )

        if cache is not None and isinstance(data, dict):
            cache.save(
                f"{node.id}-{cache_identity}",
                {"node_id": node.id, "text": text, "data": data},
            )
            if node.kind == "prompt_reviewer":
                self.checkpoint.prompt_cache_keys[node.id] = cache_identity
            elif node.kind == "task_reviewer":
                self.checkpoint.qa_cache_keys[node.id] = cache_identity

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
        required = local_output_path(required_raw, workspace, ARTIFACTS_DIR)
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
        photoshop_required = bool(node.config.get("photoshop_required", False))
        if not required_raw and not protected_raw and not photoshop_required:
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

        if not required_raw and not photoshop_required:
            return enriched

        if required_raw:
            required = local_output_path(required_raw, workspace, ARTIFACTS_DIR)
        else:
            reported_raw = str(
                enriched.get("candidate_path") or enriched.get("output_path") or ""
            ).strip()
            if not reported_raw:
                raise WorkflowError(
                    f"Нода «{node.title}» має повернути candidate_path створеного PSD"
                )
            try:
                required = workspace_child(workspace, reported_raw)
            except ValueError as exc:
                raise WorkflowError(str(exc)) from exc
            if required.suffix.casefold() != ".psd":
                raise WorkflowError(
                    f"Нода «{node.title}» має повернути справжній .psd: {required}"
                )
        if protected is not None and required == protected:
            raise WorkflowError("Вихідний артефакт не може перезаписувати оригінал")
        reported_raw = str(
            enriched.get("candidate_path") or enriched.get("output_path") or ""
        ).strip()
        if reported_raw:
            reported = local_output_path(reported_raw, workspace, ARTIFACTS_DIR)
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
        if photoshop_required:
            try:
                validation = PhotoshopAutomation(workspace).validate_psd(required)
            except PhotoshopAutomationError as exc:
                raise InterventionRequired(
                    {
                        "node_id": node.id,
                        "node_title": node.title,
                        "type": "photoshop_attention",
                        "reason": str(exc),
                        "candidate_path": str(required),
                        "question": (
                            "PSD не пройшов повторне відкриття у Photoshop. "
                            "Перевірте файл і повторіть PSD Builder."
                        ),
                    }
                ) from exc
            enriched["photoshop_validation"] = validation
            self.checkpoint.photoshop_reports[node.id] = dict(validation)
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

    @staticmethod
    def _retry_context_from(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            retry_context = value.get("retry_context")
            if isinstance(retry_context, dict):
                return dict(retry_context)
            for nested in value.values():
                found = WorkflowRunner._retry_context_from(nested)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = WorkflowRunner._retry_context_from(nested)
                if found:
                    return found
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
                    for item in self._managed_tasks_for_node(manager)
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

    def _active_task_id_for(self, target: FlowNode) -> str:
        for manager in self.workflow.nodes_of_kind("tasks_manager"):
            if not self._is_forward_upstream(manager.id, target.id):
                continue
            task_id = str(
                self.checkpoint.task_progress.get(manager.id, {}).get(
                    "active_task_id", ""
                )
            )
            if task_id:
                return task_id
        return ""

    def _record_qa_score(
        self,
        node: FlowNode,
        review: dict[str, Any],
        *,
        task_id: str,
        artifact_hash: str,
        cached: bool = False,
    ) -> dict[str, Any]:
        scope = task_id or "flow"
        issue_ids = [
            str(item.get("defect_id") or "")
            for item in review.get("issues", [])
            if isinstance(item, dict) and str(item.get("defect_id") or "")
        ]
        history = self.checkpoint.qa_scores.setdefault(scope, [])
        previous = history[-1] if history else None
        previous_ids = {
            str(item)
            for item in (previous or {}).get("issue_ids", [])
            if str(item)
        }
        current_ids = set(issue_ids)
        seen_earlier = {
            str(item)
            for old in history[:-1]
            for item in old.get("issue_ids", [])
            if isinstance(old, dict) and str(item)
        }
        explanation = {
            "kind": "same_task_retry" if previous else "first_score_for_task",
            "previous_score": (
                int(previous.get("score", 0)) if isinstance(previous, dict) else None
            ),
            "score_delta": (
                int(review.get("score", 0) or 0)
                - int(previous.get("score", 0) or 0)
                if isinstance(previous, dict)
                else None
            ),
            "fixed_defect_ids": sorted(previous_ids - current_ids),
            "new_defect_ids": sorted(current_ids - previous_ids),
            "regressed_defect_ids": sorted(
                (current_ids - previous_ids) & seen_earlier
            ),
            "unchanged_defect_ids": sorted(current_ids & previous_ids),
        }
        record = {
            "task_id": scope,
            "reviewer_node_id": node.id,
            "qa_run_id": f"{node.id}:{self.checkpoint.iterations.get(node.id, 0) + 1}",
            "attempt_id": f"attempt-{self.checkpoint.iterations.get(node.id, 0) + 1:03d}",
            "score": int(review.get("score", 0) or 0),
            "verdict": bool(review.get("verdict", False)),
            "evaluated_artifact_hash": artifact_hash,
            "status": "current",
            "cached": bool(cached),
            "evaluated_at": datetime.now(UTC).isoformat(),
            "issue_ids": issue_ids,
            "score_delta_explanation": explanation,
        }
        if not history or any(
            history[-1].get(key) != record.get(key)
            for key in ("score", "verdict", "evaluated_artifact_hash")
        ):
            history.append(record)
        self._emit(
            "qa_score_recorded",
            node=node,
            message=(
                f"QA для Task {scope}: {record['score']}/100"
                + (" (кеш)" if cached else "")
            ),
            **record,
        )
        return record

    def _mark_qa_score_pending(self, node: FlowNode, task_id: str) -> None:
        if not task_id:
            return
        changed = False
        for record in self.checkpoint.qa_scores.get(task_id, []):
            if record.get("status") == "current":
                record["status"] = "stale"
                changed = True
        if changed:
            self._emit(
                "qa_score_pending",
                node=node,
                message=f"Task {task_id}: артефакт змінено, очікується повторний QA",
                task_id=task_id,
                score_status="pending",
            )

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
            incoming_prompt = inputs.get("prompt")
            if incoming_prompt is None:
                managed = next(
                    (
                        value
                        for value in inputs.values()
                        if isinstance(value, dict)
                        and str(value.get("prompt", "")).strip()
                    ),
                    None,
                )
                if managed is not None:
                    incoming_prompt = managed.get("prompt")
            if incoming_prompt is None:
                retry = self._retry_context_from(inputs)
                if retry:
                    incoming_prompt = retry
            if incoming_prompt is None:
                raise WorkflowError(
                    f"Нода «{node.title}» очікує вхідну змінну «prompt». "
                    "Передайте text попередньої ноди у prompt."
                )
            prompt = (
                incoming_prompt.strip()
                if isinstance(incoming_prompt, str)
                else stringify(
                    incoming_prompt, limit=PROMPT_VALUE_CHARACTER_LIMIT
                ).strip()
            )
            if isinstance(incoming_prompt, dict):
                prompt = (
                    "Це структурований контракт повторного проходу. Спочатку "
                    "виконай обов'язкові рішення користувача, потім усі сумісні "
                    "must_fix і збережи вже прийняті частини.\n\n"
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
            template = str(node.config.get("prompt", ""))
            prompt = render_template(
                template, context, value_limit=PROMPT_VALUE_CHARACTER_LIMIT
            ).strip()
            # Те, що шаблон уже підставив через {{…}}, не має лягати другим
            # примірником у «# Вхідні дані»: на великому вході це подвоювало
            # весь промпт.
            substituted = self._template_variables(template)
            visible_inputs = {
                key: value
                for key, value in inputs.items()
                if key not in {"attachments", "user_note"} and key not in substituted
            }

        output_format = str(node.config.get("output_format", "text"))
        schema = node.config.get("output_schema") or {}

        sections = ["# Завдання\n" + prompt]
        requirements = self._normalized_requirements(
            context.get("user_requirements", [])
        )
        user_note = str(inputs.get("user_note", "")).strip()
        if user_note and user_note not in requirements:
            requirements.append(user_note)
        if requirements:
            sections.append(self._user_requirements_block(requirements))
        if visible_inputs:
            sections.append(
                "# Вхідні дані\n"
                + stringify(visible_inputs, limit=PROMPT_VALUE_CHARACTER_LIMIT)
            )
        if workspace is not None:
            folder_lines = [f"- Проєктна (єдина для запису): {workspace}"]
            folder_lines.extend(
                f"- Джерело (лише читання): {path}"
                for path in additional_workspaces or []
            )
            sections.append("# Доступні робочі папки\n" + "\n".join(folder_lines))
            required_raw = str(node.config.get("required_output_path", "")).strip()
            if required_raw:
                required = local_output_path(
                    required_raw, workspace, ARTIFACTS_DIR
                )
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

    def _reference_analysis_for_node(
        self, node: FlowNode, workspace: Path
    ) -> dict[str, Any]:
        raw = node.config.get("reference_cache")
        if not isinstance(raw, dict) or not raw:
            return {}
        key = payload_sha256(raw)
        cached = self._reference_analysis_receipts.get(key)
        if cached is not None:
            return cached
        try:
            receipt = validate_reference_analysis_cache(raw, workspace)
        except ReferenceAnalysisCacheError as exc:
            raise InterventionRequired(
                {
                    "node_id": node.id,
                    "node_title": node.title,
                    "type": "reference_analysis_attention",
                    "reason": str(exc),
                    "source_dir": str(raw.get("source_dir") or ""),
                    "analysis_path": str(raw.get("analysis_path") or ""),
                    "question": (
                        "Кеш аналізу UI-референсів відсутній або застарів. "
                        "Flow поставлено на Pause · Attention: виконайте один "
                        "контрольований повторний аналіз бібліотеки й оновіть "
                        "manifest перед продовженням."
                    ),
                }
            ) from exc
        self._reference_analysis_receipts[key] = receipt
        self._emit(
            "reference_analysis_cache_hit",
            node=node,
            message=(
                f"Використано готовий аналіз {receipt['file_count']} UI-референсів"
            ),
            source_dir=receipt["source_dir"],
            analysis_path=receipt["analysis_path"],
            library_sha256=receipt["library_sha256"],
        )
        return receipt

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
        sections: list[str] = [
            (
                "# Правило файлової архітектури Flow\n"
                f"Тека цього Flow-проєкту: {workspace}. Усі нові файли, теки, "
                "скрипти, тимчасові матеріали, звіти й фінальні артефакти створюй "
                "лише всередині цієї теки. Додаткові робочі папки та абсолютні "
                "шляхи поза нею є лише джерелами для читання: не змінюй їх і не "
                "створюй там нічого. Якщо старий текст завдання вимагає зовнішній "
                "output path, збережи еквівалент у підтеці artifacts цього проєкту "
                "та поверни фактичний локальний шлях. Для службових скриптів "
                "використовуй tools, для результатів — artifacts."
            )
        ]
        written = str(node.config.get("instructions", "")).strip()
        if written:
            sections.append(written)
        # Закріплений скіл Codex відкриває сам. Той самий SKILL.md, вписаний ще
        # й у файли інструкцій, приносить у контекст другий примірник того
        # самого тексту — на кожному ході.
        pinned: set[Path] = set()
        for skill in node.config.get("skills", []):
            raw = str(skill.get("path", "")).strip() if isinstance(skill, dict) else ""
            if raw:
                pinned.add(Path(raw).expanduser().resolve())
        for raw_path in node.config.get("instruction_files", []):
            path = Path(str(raw_path)).expanduser()
            if not path.is_absolute():
                path = workspace / path
            path = path.resolve()
            if path.suffix.casefold() not in {".md", ".markdown"}:
                raise WorkflowError(f"Файл інструкцій має бути Markdown: {path}")
            if not path.is_file():
                raise WorkflowError(f"Файл постійних інструкцій не знайдено: {path}")
            if path in pinned:
                continue
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
            local_output_path(report_path, workspace, REPORTS_DIR)
            if report_path
            else protocol_path.with_name(REPORT_NAME)
        )
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
        self.checkpoint.event_cursor += 1
        event: dict[str, Any] = {
            "type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "event_cursor": self.checkpoint.event_cursor,
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
