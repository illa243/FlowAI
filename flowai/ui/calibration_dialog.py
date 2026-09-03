from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..calibration import (
    CalibrationReport,
    NodeOptimizationReview,
    ProposedEdit,
    RejectionPoint,
)
from .controls import AnimatedButton
from .design import COLORS, SPACE
from .diff_view import DiffView
from .motion import AnimatedDialog
from .paths import is_image, open_file, path_menu

THUMBNAIL_WIDTH = 320
EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"]


class _BannerLabel(QLabel):
    """Банер звіту з явним станом видимості навіть до показу діалогу."""

    def isVisible(self) -> bool:
        return not self.isHidden()


class RejectionPointCard(QFrame):
    """Один пункт відхилення з полем для бачення користувача."""

    def __init__(
        self, point: RejectionPoint, index: int, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.point = point
        self.image_rows: list[QLabel] = []
        self.setObjectName("rejectionCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"]
        )
        layout.setSpacing(SPACE["xs"])

        title = QLabel(f"{index}. {point.title}")
        title.setObjectName("sectionTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        if point.detail.strip():
            detail = QLabel(point.detail)
            detail.setWordWrap(True)
            layout.addWidget(detail)

        for image in point.images:
            row = QLabel(f"{Path(image.path).name} — {image.note}")
            row.setObjectName("mutedLabel")
            row.setToolTip(image.path)
            row.setWordWrap(True)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            row.customContextMenuRequested.connect(
                lambda position, path=image.path, widget=row: path_menu(
                    path, widget
                ).exec(widget.mapToGlobal(position))
            )
            row.mouseDoubleClickEvent = (  # type: ignore[method-assign]
                lambda _event, path=image.path: open_file(path)
            )
            layout.addWidget(row)
            self.image_rows.append(row)
            if is_image(image.path) and Path(image.path).is_file():
                pixmap = QPixmap(image.path)
                if not pixmap.isNull():
                    preview = QLabel()
                    preview.setPixmap(
                        pixmap.scaledToWidth(
                            THUMBNAIL_WIDTH,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    layout.addWidget(preview)

        self.note = QPlainTextEdit(point.user_note)
        self.note.setPlaceholderText("Ваше бачення виправлення")
        self.note.setMaximumHeight(80)
        layout.addWidget(self.note)

    def commit(self) -> None:
        self.point.user_note = self.note.toPlainText()


class CalibrationDialog(AnimatedDialog):
    """Пояснення відхилення та пропоновані правки на двох вкладках."""

    def __init__(
        self,
        report: CalibrationReport,
        parent: QWidget | None = None,
        *,
        models: list[str],
        default_model: str,
        default_effort: str,
        node_titles: dict[str, str] | None = None,
        node_order: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.decision = ""
        self.model = default_model
        self.effort = default_effort
        self.node_titles = dict(node_titles or {})
        self.node_order = list(node_order or [])
        self.point_cards: list[RejectionPointCard] = []
        self.diff_views: list[DiffView] = []
        self.skill_boxes: list[QCheckBox] = []
        self.node_section_titles: dict[str, QLabel] = {}
        self.node_section_frames: dict[str, QFrame] = {}

        self.setWindowTitle(f"Відхилено: {report.task_title}")
        self.setMinimumSize(1040, 720)

        root = QVBoxLayout(self)
        heading = QLabel(
            f"«{report.task_title}» — спроба {report.attempt} із порогом "
            f"{report.threshold}"
        )
        heading.setObjectName("sectionTitle")
        root.addWidget(heading)

        self.error_banner = _BannerLabel("")
        self.error_banner.setWordWrap(True)
        self.error_banner.setStyleSheet(f"color: {COLORS['warning']};")
        self.error_banner.setVisible(bool(report.analysis_error))
        if report.analysis_error:
            self.error_banner.setText(
                f"Аналіз скілів не завершився: {report.analysis_error}"
            )
        root.addWidget(self.error_banner)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_points_tab(), "Чому відхилено")
        self.tabs.addTab(
            self._build_edits_tab(),
            f"Пропоновані правки ({len(report.edits)})",
        )
        self.tabs.setCurrentIndex(0)
        root.addWidget(self.tabs, 1)
        root.addLayout(self._build_buttons(models))

    def _scrolled(self) -> tuple[QScrollArea, QVBoxLayout]:
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(SPACE["md"])
        area.setWidget(inner)
        return area, layout

    def _build_points_tab(self) -> QWidget:
        area, layout = self._scrolled()
        self.points_layout = layout
        if self.report.verdict_reason.strip():
            reason = QLabel(self.report.verdict_reason)
            reason.setWordWrap(True)
            reason.setObjectName("mutedLabel")
            layout.addWidget(reason)
        for index, point in enumerate(self.report.points, start=1):
            card = RejectionPointCard(point, index)
            self.point_cards.append(card)
            layout.addWidget(card)
        layout.addStretch()
        return area

    def _build_edits_tab(self) -> QWidget:
        area, layout = self._scrolled()
        self.edits_layout = layout
        if self.report.root_cause.strip():
            cause = QLabel(f"Причина: {self.report.root_cause}")
            cause.setWordWrap(True)
            layout.addWidget(cause)
        if self.report.skills_used:
            used = QLabel(
                "Агент працював зі скілами: "
                + ", ".join(self.report.skills_used)
            )
            used.setObjectName("mutedLabel")
            used.setWordWrap(True)
            layout.addWidget(used)
        if self.report.skills_missing:
            missing = QLabel("Рев'ювер радить закріпити за блоком:")
            layout.addWidget(missing)
            for name in self.report.skills_missing:
                box = QCheckBox(name)
                self.skill_boxes.append(box)
                layout.addWidget(box)
        reviews = {review.node_id: review for review in self.report.node_reviews}
        node_ids: list[str] = []
        for node_id in self.node_order:
            if node_id and node_id not in node_ids:
                node_ids.append(node_id)
        for review in self.report.node_reviews:
            if review.node_id and review.node_id not in node_ids:
                node_ids.append(review.node_id)
        for edit in self.report.edits:
            if (
                edit.target in {"node_prompt", "node_instructions"}
                and edit.node_id
                and edit.node_id not in node_ids
            ):
                node_ids.append(edit.node_id)

        for node_id in node_ids:
            outside_review_scope = bool(
                self.node_order and node_id not in self.node_order
            )
            stale_node = bool(
                self.node_titles and node_id not in self.node_titles
            )
            if outside_review_scope or stale_node:
                for edit in self.report.edits:
                    if edit.node_id == node_id:
                        edit.accepted = False
            self._add_node_section(
                layout,
                node_id,
                reviews.get(node_id),
                [edit for edit in self.report.edits if edit.node_id == node_id],
            )

        remaining = [
            edit
            for edit in self.report.edits
            if edit.target not in {"node_prompt", "node_instructions"}
            or not edit.node_id
        ]
        if remaining:
            heading = QLabel("Інші правки")
            heading.setObjectName("sectionTitle")
            layout.addWidget(heading)
            for edit in remaining:
                view = DiffView(edit)
                self.diff_views.append(view)
                layout.addWidget(view)

        if not node_ids and not remaining:
            empty = QLabel("Рев'ювер не запропонував конкретних правок.")
            empty.setObjectName("mutedLabel")
            layout.addWidget(empty)
        layout.addStretch()
        return area

    def _add_node_section(
        self,
        layout: QVBoxLayout,
        node_id: str,
        review: NodeOptimizationReview | None,
        edits: list[ProposedEdit],
    ) -> None:
        frame = QFrame()
        frame.setObjectName("rejectionCard")
        section = QVBoxLayout(frame)
        section.setContentsMargins(
            SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"]
        )
        section.setSpacing(SPACE["xs"])

        fallback = review.node_title if review is not None else node_id
        title_text = self.node_titles.get(node_id, fallback or "Невідома нода")
        title = QLabel(title_text)
        title.setObjectName("sectionTitle")
        section.addWidget(title)
        self.node_section_titles[node_id] = title
        self.node_section_frames[node_id] = frame

        if review is not None:
            score = QLabel(f"Оцінка ефективності: {review.score}/100")
            score.setObjectName("mutedLabel")
            section.addWidget(score)
            if review.summary:
                summary = QLabel(review.summary)
                summary.setWordWrap(True)
                section.addWidget(summary)
            for finding in review.findings:
                parts = [f"[{finding.assessment}] {finding.action}".strip()]
                if finding.evidence:
                    parts.append(f"Доказ: {finding.evidence}")
                if finding.better_alternative:
                    parts.append(f"Краще: {finding.better_alternative}")
                if finding.expected_gain:
                    parts.append(f"Виграш: {finding.expected_gain}")
                finding_label = QLabel("\n".join(parts))
                finding_label.setWordWrap(True)
                finding_label.setObjectName("mutedLabel")
                section.addWidget(finding_label)
            if review.recommendations:
                recommendations = QLabel(
                    "Рекомендації:\n"
                    + "\n".join(f"• {item}" for item in review.recommendations)
                )
                recommendations.setWordWrap(True)
                section.addWidget(recommendations)

        if edits:
            for edit in edits:
                view = DiffView(edit)
                self.diff_views.append(view)
                section.addWidget(view)
        else:
            empty = QLabel("Правки не рекомендовані")
            empty.setObjectName("mutedLabel")
            section.addWidget(empty)
        layout.addWidget(frame)

    def _build_buttons(self, models: list[str]) -> QHBoxLayout:
        row = QHBoxLayout()
        self.apply_button = AnimatedButton("Застосувати правки", "primary")
        self.apply_button.setEnabled(bool(self.report.accepted_edits()))
        self.apply_button.clicked.connect(lambda: self._decide("apply"))
        row.addWidget(self.apply_button)

        self.retry_button = AnimatedButton("Хай спробує сам", "secondary")
        self.retry_button.clicked.connect(lambda: self._decide("retry"))
        row.addWidget(self.retry_button)
        row.addStretch()

        self.model_combo = QComboBox()
        self.model_combo.addItems(models)
        self.model_combo.setCurrentText(self.model)
        row.addWidget(QLabel("Модель"))
        row.addWidget(self.model_combo)

        self.effort_combo = QComboBox()
        self.effort_combo.addItems(EFFORTS)
        self.effort_combo.setCurrentText(self.effort)
        row.addWidget(QLabel("Складність"))
        row.addWidget(self.effort_combo)

        self.regenerate_button = AnimatedButton("Regenerate Prompt", "primary")
        self.regenerate_button.clicked.connect(
            lambda: self._decide("regenerate")
        )
        row.addWidget(self.regenerate_button)

        stop = AnimatedButton("Зупинити Flow", "ghost")
        stop.clicked.connect(lambda: self._decide("stop"))
        row.addWidget(stop)
        return row

    @property
    def pinned_skills(self) -> list[str]:
        return [box.text() for box in self.skill_boxes if box.isChecked()]

    def commit_notes(self) -> None:
        """Перенести написане користувачем у звіт перед закриттям."""
        for card in self.point_cards:
            card.commit()

    def _decide(self, decision: str) -> None:
        self.commit_notes()
        self.decision = decision
        self.model = self.model_combo.currentText()
        self.effort = self.effort_combo.currentText()
        if decision == "stop":
            self.reject()
        else:
            self.accept()

    def reject(self) -> None:
        self.commit_notes()
        if not self.decision:
            self.decision = "stop"
        super().reject()
