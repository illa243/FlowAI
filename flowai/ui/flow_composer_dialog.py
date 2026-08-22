from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..codex_adapter import CodexAdapter
from ..codex_auth import available_models
from ..grill import GrillOutcome
from ..mcp.client_config import PROJECT_ROOT, flowai_server_config
from ..models import FlowNode, Workflow
from ..persistence import load_workflow
from ..project_layout import isolated_flow_path
from .attachments import AttachmentListWidget
from .controls import AnimatedButton
from .grill_dialog import GrillDialog
from .log_panel import LogPanel
from .motion import AnimatedDialog

COMPOSER_INSTRUCTIONS = (
    "Ти складаєш Flow для FlowAI через MCP-сервер «flowai». "
    "Порядок роботи обов'язковий: "
    "1) виклич list_guides і прочитай усі довідники — вони описують, як "
    "працюють блоки і які зв'язки коректні; "
    "2) виклич list_node_kinds, щоб знати точні поля конфігів; "
    "3) виклич list_flows на вказаній папці готових Flow і прочитай один-два "
    "готові Flow як еталон стилю формулювань, якщо вони є; "
    "4) прочитай робочу папку користувача, щоб завдання посилались на "
    "реальні шляхи й реальні файли; "
    "вкладення користувача вважай основним контекстом: обов'язково переглянь "
    "їх і додай абсолютні шляхи до attachments тих нод або завдань, яким "
    "ці файли потрібні під час виконання; "
    "5) збери Flow інструментами create_flow / add_node / set_node_config / "
    "set_tasks / connect_nodes, виклич auto_layout, потім validate_flow і "
    "виправ усі помилки; "
    "6) лише після чистої валідації виклич save_flow за точним шляхом із запиту. "
    "Завдання формулюй довгими й конкретними, як в еталонних Flow: що зробити, "
    "з якими файлами, який результат вважається прийнятним."
)

EDITOR_INSTRUCTIONS = (
    "Ти редагуєш конкретний відкритий Flow для FlowAI через MCP-сервер "
    "«flowai». Порядок роботи обов'язковий: "
    "1) виклич list_guides і повністю прочитай усі довідники; "
    "2) виклич read_flow для точного шляху з запиту, щоб зрозуміти поточний "
    "граф, а потім load_flow для цього самого шляху — не створюй новий Flow; "
    "3) виклич list_node_kinds і за потреби describe_node_kind; "
    "4) зміни лише те, про що просить користувач. Зберігай наявні ID, конфіги, "
    "координати та control_points усіх частин, які не потребують перебудови; "
    "5) використовуй set_flow_config, set_node_title, set_node_position, "
    "set_node_config, set_tasks, add_node, remove_node, connect_nodes, "
    "remove_edge, set_edge_config і set_edge_control_points. Викликай auto_layout "
    "лише якщо змінилася топологія або користувач попросив перерозкласти граф; "
    "6) прочитай фінальну чернетку через read_draft, усунь перетини ліній за "
    "правилами довідника, виклич validate_flow і виправ усі помилки; "
    "7) лише після чистої валідації виклич save_flow за тим самим точним шляхом. "
    "Не редагуй JSON напряму й не зберігай результат в інший файл."
)

REASONING_EFFORTS = (
    ("Без міркувань", "none"),
    ("Низька", "low"),
    ("Середня", "medium"),
    ("Висока", "high"),
    ("Дуже висока", "xhigh"),
    ("Максимальна", "max"),
)


class ComposerWorker(QObject):
    activity = Signal(object)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        prompt: str,
        model: str,
        workspace: Path,
        target: Path,
        reasoning_effort: str = "medium",
        attachments: list[Path] | None = None,
        edit_existing: bool = False,
    ) -> None:
        super().__init__()
        self.prompt = prompt
        self.model = model
        self.workspace = workspace
        self.target = target
        self.reasoning_effort = reasoning_effort
        self.attachments = list(attachments or [])
        self.edit_existing = edit_existing
        self._codex: CodexAdapter | None = None

    def cancel(self) -> None:
        if self._codex is not None:
            self._codex.cancel_active()

    @Slot()
    def run(self) -> None:
        try:
            original_contents = (
                self.target.read_bytes()
                if self.edit_existing and self.target.is_file()
                else None
            )
            attachment_list = (
                "\n".join(f"- {path}" for path in self.attachments)
                if self.attachments
                else "Немає"
            )
            with CodexAdapter() as codex:
                self._codex = codex
                codex.run_agent(
                    prompt=(
                        f"# Запит користувача\n{self.prompt}\n\n"
                        f"# Робоча папка\n{self.workspace}\n\n"
                        f"# Папка готових Flow\n{PROJECT_ROOT}\n\n"
                        f"# Вкладення користувача\n{attachment_list}\n\n"
                        f"# Обов'язковий шлях результату\n{self.target}\n\n"
                        + (
                            "Завантаж, зміни, перевір і збережи саме цей Flow "
                            "за тим самим шляхом."
                            if self.edit_existing
                            else "Створи, перевір і збережи Flow саме за цим шляхом."
                        )
                    ),
                    developer_instructions=(
                        EDITOR_INSTRUCTIONS
                        if self.edit_existing
                        else COMPOSER_INSTRUCTIONS
                    ),
                    model=self.model,
                    sandbox="read-only",
                    workspace=self.workspace,
                    reasoning_effort=self.reasoning_effort,
                    attachments=self.attachments,
                    on_activity=self.activity.emit,
                    mcp_servers=flowai_server_config(),
                )
            self._codex = None
            if not self.target.is_file():
                raise FileNotFoundError(
                    f"Агент не зберіг Flow за очікуваним шляхом: {self.target}"
                )
            if (
                original_contents is not None
                and self.target.read_bytes() == original_contents
            ):
                raise ValueError(
                    "AI завершив роботу, але не змінив вибраний Flow. "
                    "Уточніть запит і спробуйте ще раз."
                )
            workflow = load_workflow(self.target)
            errors = workflow.validate()
            if errors:
                raise ValueError("Створений Flow не валідний:\n" + "\n".join(errors))
            self.completed.emit(str(self.target))
        except Exception as exc:  # noqa: BLE001 - worker/UI boundary
            self._codex = None
            self.failed.emit(str(exc))


class NewFlowChoiceDialog(AnimatedDialog):
    """Choose the source for a new Flow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choice = ""
        self.setWindowTitle("Новий Flow")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        heading = QLabel("З чого почати?")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        options = (
            ("Порожній", "blank", "plus", "secondary"),
            ("Готова схема", "starter", "chart", "secondary"),
            ("Скласти з AI", "ai", "sparkles", "primary"),
        )
        for title, value, icon_name, variant in options:
            button = AnimatedButton(title, variant, icon_name)
            button.setMinimumHeight(50)
            button.clicked.connect(
                lambda _checked=False, selected=value: self._choose(selected)
            )
            layout.addWidget(button)
        cancel = AnimatedButton("Скасувати", "ghost")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()


class FlowComposerDialog(AnimatedDialog):
    """Compose a validated Flow through the bundled MCP server."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        edit_path: Path | None = None,
        initial_workspace: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.edit_path = edit_path.resolve() if edit_path is not None else None
        self.edit_mode = self.edit_path is not None
        self.saved_path = ""
        self._thread: QThread | None = None
        self._worker: ComposerWorker | None = None
        self.setWindowTitle(
            "Edit Flow — AI" if self.edit_mode else "Скласти Flow за допомогою AI"
        )
        self.setMinimumSize(860, 720)
        root = QVBoxLayout(self)

        form = QFormLayout()
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(
            "Опишіть, що саме потрібно змінити у вибраному Flow"
            if self.edit_mode
            else "Опишіть, для чого потрібен Flow, які файли він читає і що створює"
        )
        self.prompt.setMinimumHeight(150)
        form.addRow("Запит", self.prompt)

        self.attachments = AttachmentListWidget()
        self.attachments.setMaximumHeight(190)
        attachment_box = QWidget()
        attachment_layout = QVBoxLayout(attachment_box)
        attachment_layout.setContentsMargins(0, 0, 0, 0)
        attachment_buttons = QHBoxLayout()
        self.add_attachments_button = AnimatedButton(
            "Додати файли…", "secondary", "plus"
        )
        self.remove_attachments_button = AnimatedButton(
            "Прибрати", "ghost", "trash"
        )
        self.add_attachments_button.clicked.connect(self._choose_attachments)
        self.remove_attachments_button.clicked.connect(
            self.attachments.remove_selected_paths
        )
        attachment_buttons.addWidget(self.add_attachments_button)
        attachment_buttons.addWidget(self.remove_attachments_button)
        attachment_buttons.addStretch()
        attachment_layout.addWidget(self.attachments)
        attachment_layout.addLayout(attachment_buttons)
        form.addRow("Файли і картинки", attachment_box)

        self.model = QComboBox()
        self.model.addItems(available_models())
        form.addRow("Модель", self.model)
        self.reasoning = QComboBox()
        for label, value in REASONING_EFFORTS:
            self.reasoning.addItem(label, value)
        self.reasoning.setCurrentIndex(self.reasoning.findData("medium"))
        self.reasoning.setToolTip(
            "Вища сила дає агенту більше часу на аналіз, але Flow складається довше"
        )
        form.addRow(
            "Складність моделі" if self.edit_mode else "Сила міркування",
            self.reasoning,
        )
        self.grill = QCheckBox("Спершу уточнити запит через GrillMe")
        self.grill.setChecked(not self.edit_mode)
        form.addRow(self.grill)

        workspace_box = QWidget()
        workspace_layout = QHBoxLayout(workspace_box)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace = QLineEdit(str(initial_workspace or Path.cwd()))
        browse = AnimatedButton("Огляд…", "secondary", "folder")
        browse.clicked.connect(self._choose_workspace)
        workspace_layout.addWidget(self.workspace, 1)
        workspace_layout.addWidget(browse)
        form.addRow("Робоча папка", workspace_box)
        root.addLayout(form)

        self.log = LogPanel()
        root.addWidget(self.log, 1)
        controls = QHBoxLayout()
        self.compose_button = AnimatedButton(
            "Змінити Flow" if self.edit_mode else "Скласти",
            "primary",
            "sparkles",
        )
        cancel = AnimatedButton("Скасувати", "ghost")
        self.compose_button.clicked.connect(self._compose)
        cancel.clicked.connect(self.reject)
        controls.addWidget(self.compose_button)
        controls.addStretch()
        controls.addWidget(cancel)
        root.addLayout(controls)

    def _choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Робоча папка", self.workspace.text() or str(Path.cwd())
        )
        if selected:
            self.workspace.setText(selected)

    def _choose_attachments(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "Прикріпити файли і картинки",
            self.workspace.text() or str(Path.cwd()),
            "Усі файли (*);;Картинки "
            "(*.png *.jpg *.jpeg *.webp *.gif *.bmp *.tif *.tiff)",
        )
        self.attachments.add_paths([str(Path(path).resolve()) for path in paths])

    def _clarified_prompt(self, prompt: str, workspace: Path) -> str | None:
        if not self.grill.isChecked():
            return prompt
        entry = FlowNode.create("entry")
        entry.config["text"] = prompt
        entry.config["attachments"] = self.attachments.paths()
        workflow = Workflow(
            name=(
                "Запит на редагування Flow"
                if self.edit_mode
                else "Запит на новий Flow"
            ),
            nodes=[entry],
        )
        dialog = GrillDialog(
            workflow,
            self.model.currentText(),
            workspace,
            self,
            reasoning_effort=str(self.reasoning.currentData() or "medium"),
        )
        dialog.exec()
        if dialog.decision not in {"run", "edit"} or dialog.outcome is None:
            return None
        outcome: GrillOutcome = dialog.outcome
        clarified = outcome.rewritten_entry or prompt
        if outcome.summary:
            clarified += f"\n\nДомовленості GrillMe:\n{outcome.summary}"
        if dialog.decision == "edit":
            self.prompt.setPlainText(clarified)
            self.prompt.setFocus()
            return None
        return clarified

    def _target_path(self, workspace: Path) -> Path:
        if self.edit_path is not None:
            return self.edit_path
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        target = isolated_flow_path(
            workspace / f"ai-flow-{stamp}.flowai.json"
        )
        counter = 2
        while target.exists():
            target = isolated_flow_path(
                workspace / f"ai-flow-{stamp}-{counter}.flowai.json"
            )
            counter += 1
        return target

    def _compose(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        prompt = self.prompt.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(self, "Немає запиту", "Опишіть потрібний Flow.")
            return
        workspace = Path(self.workspace.text().strip() or Path.cwd()).expanduser()
        if not workspace.is_dir():
            QMessageBox.warning(
                self, "Папку не знайдено", f"Робочої папки не існує:\n{workspace}"
            )
            return
        attachments = [
            Path(path).expanduser().resolve() for path in self.attachments.paths()
        ]
        missing = [path for path in attachments if not path.is_file()]
        if missing:
            QMessageBox.warning(
                self,
                "Вкладення не знайдено",
                "Ці вкладення більше не існують:\n"
                + "\n".join(str(path) for path in missing),
            )
            return
        clarified = self._clarified_prompt(prompt, workspace.resolve())
        if clarified is None:
            return

        self.compose_button.setEnabled(False)
        self.log.set_activity("Підготовка агента-складача…", "")
        target = self._target_path(workspace.resolve())
        thread = QThread(self)
        worker = ComposerWorker(
            clarified,
            self.model.currentText(),
            workspace.resolve(),
            target,
            reasoning_effort=str(self.reasoning.currentData() or "medium"),
            attachments=attachments,
            edit_existing=self.edit_mode,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.activity.connect(self._activity)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _activity(self, activity: object) -> None:
        if not isinstance(activity, dict):
            return
        message = str(activity.get("summary", "")).strip()
        paths = [str(item) for item in activity.get("paths", []) if str(item)]
        self.log.set_activity(message, "")
        if activity.get("phase") == "completed" and message:
            self.log.append_entry(
                {
                    "timestamp": datetime.now().astimezone().strftime("%H:%M:%S"),
                    "text": message,
                    "color": "",
                    "file_paths": paths,
                }
            )

    @Slot(str)
    def _completed(self, path: str) -> None:
        self.saved_path = path
        self.log.set_activity("", "")
        self.accept()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self.log.set_activity("", "")
        QMessageBox.critical(self, "Не вдалося скласти Flow", message)

    @Slot()
    def _thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self.compose_button.setEnabled(True)

    def reject(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        super().reject()
