from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..codex_adapter import CodexAdapter
from ..grill import OWN_ANSWER, GrillOutcome, GrillQuestion, GrillSession
from ..models import Workflow, normalize_managed_tasks
from .controls import AnimatedButton
from .motion import AnimatedDialog


class GrillWorker(QObject):
    """Run a GrillSession away from the UI thread."""

    question_ready = Signal(object)
    outcome_ready = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        workflow: Workflow,
        model: str,
        workspace: Path,
        reasoning_effort: str = "medium",
        calibration: Any | None = None,
        generated_files: list[str] | None = None,
        review_feedback: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.workflow = workflow
        self.model = model
        self.workspace = workspace
        self.reasoning_effort = reasoning_effort
        self.calibration = calibration
        self.generated_files = list(generated_files or [])
        self.review_feedback = dict(review_feedback or {})
        self._codex: CodexAdapter | None = None
        self._session: GrillSession | None = None
        self._question: GrillQuestion | None = None

    def _ensure_session(self) -> GrillSession:
        if self._session is None:
            self._codex = CodexAdapter()
            self._codex.__enter__()
            self._session = GrillSession(
                self.workflow,
                self._codex,
                self.model,
                self.workspace,
                reasoning_effort=self.reasoning_effort,
                calibration=self.calibration,
                generated_files=self.generated_files,
                review_feedback=self.review_feedback,
            )
        return self._session

    def _shutdown(self) -> None:
        if self._codex is not None:
            self._codex.__exit__(None, None, None)
        self._codex = None
        self._session = None
        self._question = None

    def cancel(self) -> None:
        if self._codex is not None:
            self._codex.cancel_active()

    @Slot()
    def request_question(self) -> None:
        try:
            question = self._ensure_session().next_question()
            self._question = question
            if question is None:
                self.request_finish()
            else:
                self.question_ready.emit(question)
        except Exception as exc:  # noqa: BLE001 - worker/UI boundary
            self._shutdown()
            self.failed.emit(str(exc))

    @Slot(str)
    def submit_answer(self, answer: str) -> None:
        if self._question is None:
            return
        self._ensure_session().record(self._question.text, answer)
        self._question = None
        self.request_question()

    @Slot()
    def request_finish(self) -> None:
        try:
            outcome = self._ensure_session().finish()
            self._shutdown()
            self.outcome_ready.emit(outcome)
        except Exception as exc:  # noqa: BLE001 - worker/UI boundary
            self._shutdown()
            self.failed.emit(str(exc))


class GrillDialog(AnimatedDialog):
    """One-question-at-a-time clarification UI with a final prompt diff."""

    question_requested = Signal()
    answer_submitted = Signal(str)
    finish_requested = Signal()
    transcript_changed = Signal(object)

    def __init__(
        self,
        workflow: Workflow,
        model: str,
        workspace: Path,
        parent: QWidget | None = None,
        *,
        reasoning_effort: str = "medium",
        calibration: Any | None = None,
        generated_files: list[str] | None = None,
        review_feedback: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self.workflow = workflow
        self.model = model
        self.workspace = workspace
        self.reasoning_effort = reasoning_effort
        self.calibration = calibration
        self.generated_files = list(generated_files or [])
        self.review_feedback = dict(review_feedback or {})
        self.outcome: GrillOutcome | None = None
        saved_transcript = self.review_feedback.get("grill_transcript")
        self.transcript: list[dict[str, str]] = (
            [
                {
                    "question": str(item.get("question") or "").strip(),
                    "answer": str(item.get("answer") or "").strip(),
                }
                for item in saved_transcript
                if isinstance(item, dict)
                and (item.get("question") or item.get("answer"))
            ]
            if isinstance(saved_transcript, list)
            else []
        )
        self.decision = ""
        self.option_buttons: list[AnimatedButton] = []
        self._question_count = 0
        self._current_question: GrillQuestion | None = None
        self._thread: QThread | None = None
        self._worker: GrillWorker | None = None
        self._started = False

        if self.review_feedback:
            self.setWindowTitle("Обговорити правки — GrillMe")
        else:
            self.setWindowTitle(
                "Regenerate Prompt — GrillMe" if calibration is not None else "GrillMe"
            )
        self.setMinimumSize(760, 620)
        root = QVBoxLayout(self)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self._build_waiting_page()
        self._build_question_page()
        self._build_ready_page()
        self.pages.setCurrentWidget(self.waiting_page)

    def _build_waiting_page(self) -> None:
        self.waiting_page = QWidget()
        layout = QVBoxLayout(self.waiting_page)
        layout.addStretch()
        heading = QLabel("Агент формулює питання…")
        heading.setObjectName("sectionTitle")
        heading.setAlignment(heading.alignment() | heading.alignment().AlignHCenter)
        layout.addWidget(heading)
        note = QLabel("GrillMe шукає неоднозначності й пропущені рішення")
        note.setObjectName("mutedLabel")
        note.setAlignment(note.alignment() | note.alignment().AlignHCenter)
        layout.addWidget(note)
        layout.addStretch()
        self.pages.addWidget(self.waiting_page)

    def _build_question_page(self) -> None:
        self.question_page = QWidget()
        layout = QVBoxLayout(self.question_page)
        self.counter = QLabel("Питання 1")
        self.counter.setObjectName("mutedLabel")
        layout.addWidget(self.counter)
        self.rationale = QLabel("")
        self.rationale.setObjectName("mutedLabel")
        self.rationale.setWordWrap(True)
        layout.addWidget(self.rationale)
        self.question_text = QLabel("")
        self.question_text.setObjectName("sectionTitle")
        self.question_text.setWordWrap(True)
        layout.addWidget(self.question_text)

        self.options_widget = QWidget()
        self.options_layout = QVBoxLayout(self.options_widget)
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.options_widget)

        self.custom_widget = QWidget()
        custom_layout = QHBoxLayout(self.custom_widget)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_answer = QLineEdit()
        self.custom_answer.setPlaceholderText("Ваша відповідь")
        send = AnimatedButton("Відповісти", "primary")
        send.clicked.connect(self._submit_custom)
        self.custom_answer.returnPressed.connect(self._submit_custom)
        custom_layout.addWidget(self.custom_answer, 1)
        custom_layout.addWidget(send)
        self.custom_widget.hide()
        layout.addWidget(self.custom_widget)
        layout.addStretch()

        controls = QHBoxLayout()
        finish = AnimatedButton("Досить — збирай промпт", "secondary")
        cancel = AnimatedButton("Скасувати", "ghost")
        finish.clicked.connect(self._finish_early)
        cancel.clicked.connect(self.reject)
        controls.addWidget(finish)
        controls.addStretch()
        controls.addWidget(cancel)
        layout.addLayout(controls)
        self.pages.addWidget(self.question_page)

    def _build_ready_page(self) -> None:
        self.ready_page = QWidget()
        layout = QVBoxLayout(self.ready_page)
        heading = QLabel("Все готово")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.diff_scroll = QScrollArea()
        self.diff_scroll.setWidgetResizable(True)
        self.diff_widget = QWidget()
        self.diff_layout = QVBoxLayout(self.diff_widget)
        self.diff_scroll.setWidget(self.diff_widget)
        layout.addWidget(self.diff_scroll, 1)
        controls = QHBoxLayout()
        self.run_button = AnimatedButton("Запустити", "primary", "play")
        self.edit_button = AnimatedButton("Edit", "secondary")
        cancel = AnimatedButton("Скасувати", "ghost")
        self.run_button.clicked.connect(lambda: self._decide("run"))
        self.edit_button.clicked.connect(lambda: self._decide("edit"))
        cancel.clicked.connect(self.reject)
        controls.addWidget(self.run_button)
        controls.addWidget(self.edit_button)
        controls.addStretch()
        controls.addWidget(cancel)
        layout.addLayout(controls)
        self.pages.addWidget(self.ready_page)

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def show_question(self, question: GrillQuestion) -> None:
        self._question_count += 1
        self._current_question = question
        self.counter.setText(f"Питання {self._question_count}")
        self.rationale.setText(question.rationale)
        self.rationale.setVisible(bool(question.rationale))
        self.question_text.setText(question.text)
        self.custom_widget.hide()
        self.custom_answer.clear()
        self._clear_layout(self.options_layout)
        self.option_buttons = []
        for option in question.options:
            button = AnimatedButton(option, "secondary")
            button.setMinimumHeight(46)
            if option == OWN_ANSWER:
                button.clicked.connect(self._show_custom_answer)
            else:
                button.clicked.connect(
                    lambda _checked=False, value=option: self._submit(value)
                )
            self.options_layout.addWidget(button)
            self.option_buttons.append(button)
        self.pages.setCurrentWidget(self.question_page)

    def _show_custom_answer(self) -> None:
        self.custom_widget.show()
        self.custom_answer.setFocus()

    def _submit_custom(self) -> None:
        answer = self.custom_answer.text().strip()
        if answer:
            self._submit(answer)

    def _submit(self, answer: str) -> None:
        question = self._current_question
        if question is not None:
            self.transcript.append({"question": question.text, "answer": answer})
            self.transcript_changed.emit(list(self.transcript))
            self._current_question = None
        self.pages.setCurrentWidget(self.waiting_page)
        self.answer_submitted.emit(answer)

    def _finish_early(self) -> None:
        self.pages.setCurrentWidget(self.waiting_page)
        self.finish_requested.emit()

    def _old_prompts(self) -> dict[str, str]:
        prompts: dict[str, str] = {}
        for node in self.workflow.nodes:
            if node.kind == "entry":
                prompts["entry"] = str(node.config.get("text", ""))
            elif node.kind == "tasks_manager":
                prompts.update(
                    {
                        str(task["id"]): str(task.get("prompt", ""))
                        for task in normalize_managed_tasks(node.config.get("tasks"))
                    }
                )
        return prompts

    def show_outcome(self, outcome: GrillOutcome) -> None:
        self.outcome = outcome
        self.summary.setText(outcome.summary or "Домовленості зібрано.")
        self._clear_layout(self.diff_layout)
        if self.review_feedback:
            title = QLabel("Підсумкові правки для виконавчої ноди")
            title.setObjectName("sectionTitle")
            self.diff_layout.addWidget(title)
            self.feedback_editor = QPlainTextEdit(outcome.feedback)
            self.feedback_editor.setPlaceholderText(
                "Остаточні правки, які отримає нода"
            )
            self.diff_layout.addWidget(self.feedback_editor, 1)
            self.run_button.setText("Відправити правки")
            self.edit_button.hide()
            self.pages.setCurrentWidget(self.ready_page)
            self._stop_thread()
            return
        old = self._old_prompts()
        changes = dict(outcome.rewritten_tasks)
        if outcome.rewritten_entry:
            changes["entry"] = outcome.rewritten_entry
        if not changes:
            empty = QLabel("Промпти не потребують змін.")
            empty.setObjectName("mutedLabel")
            self.diff_layout.addWidget(empty)
        for task_id, new_prompt in changes.items():
            title = QLabel("Entry" if task_id == "entry" else f"Завдання {task_id}")
            title.setObjectName("sectionTitle")
            self.diff_layout.addWidget(title)
            row = QHBoxLayout()
            before = QPlainTextEdit(old.get(task_id, ""))
            after = QPlainTextEdit(new_prompt)
            before.setReadOnly(True)
            after.setReadOnly(True)
            before.setPlaceholderText("Було")
            after.setPlaceholderText("Стало")
            before.setMinimumHeight(110)
            after.setMinimumHeight(110)
            row.addWidget(before)
            row.addWidget(after)
            self.diff_layout.addLayout(row)
        self.diff_layout.addStretch()
        self.pages.setCurrentWidget(self.ready_page)
        self._stop_thread()

    def diff_text(self) -> str:
        if self.outcome is None:
            return ""
        if self.review_feedback:
            editor = getattr(self, "feedback_editor", None)
            return editor.toPlainText() if editor is not None else self.outcome.feedback
        old = self._old_prompts()
        lines: list[str] = []
        for task_id, prompt in self.outcome.rewritten_tasks.items():
            lines.extend([old.get(task_id, ""), prompt])
        if self.outcome.rewritten_entry:
            lines.extend([old.get("entry", ""), self.outcome.rewritten_entry])
        return "\n".join(lines)

    def _decide(self, decision: str) -> None:
        if self.review_feedback and self.outcome is not None:
            editor = getattr(self, "feedback_editor", None)
            if editor is not None:
                self.outcome.feedback = editor.toPlainText().strip()
        self.decision = decision
        self.accept()

    def _start_worker(self) -> None:
        if self._started:
            return
        self._started = True
        thread = QThread(self)
        worker = GrillWorker(
            self.workflow,
            self.model,
            self.workspace,
            reasoning_effort=self.reasoning_effort,
            calibration=self.calibration,
            generated_files=self.generated_files,
            review_feedback=self.review_feedback,
        )
        worker.moveToThread(thread)
        self.question_requested.connect(worker.request_question)
        self.answer_submitted.connect(worker.submit_answer)
        self.finish_requested.connect(worker.request_finish)
        worker.question_ready.connect(self.show_question)
        worker.outcome_ready.connect(self.show_outcome)
        worker.failed.connect(self._worker_failed)
        thread.started.connect(worker.request_question)
        thread.finished.connect(worker.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _stop_thread(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()

    @Slot(str)
    def _worker_failed(self, message: str) -> None:
        self._stop_thread()
        QMessageBox.critical(self, "GrillMe не завершився", message)
        self.reject()

    def reject(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        self._stop_thread()
        super().reject()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._start_worker()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None:
            self._worker.cancel()
        self._stop_thread()
        super().closeEvent(event)
