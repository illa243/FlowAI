from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from .controls import AnimatedButton
from .motion import AnimatedDialog


class RunStartDialog(AnimatedDialog):
    """Choose between an immediate run and a GrillMe clarification session."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choice = ""
        self.setWindowTitle("Як запустити Flow?")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        heading = QLabel("Запустити зараз чи спершу уточнити завдання?")
        heading.setObjectName("sectionTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        explanation = QLabel(
            "GrillMe ставить по одному конкретному питанню, а наприкінці "
            "показує, як зміняться промпти завдань."
        )
        explanation.setObjectName("mutedLabel")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        run = AnimatedButton("Запустити зараз", "success", "play")
        grill = AnimatedButton(
            "GrillMe — уточнити перед запуском", "primary", "sparkles"
        )
        run.setMinimumHeight(52)
        grill.setMinimumHeight(52)
        run.clicked.connect(lambda: self._choose("run"))
        grill.clicked.connect(lambda: self._choose("grill"))
        layout.addWidget(run)
        layout.addWidget(grill)

        self.skip = QCheckBox("Більше не питати, завжди запускати одразу")
        layout.addWidget(self.skip)
        cancel = AnimatedButton("Скасувати", "ghost")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        if self.skip.isChecked():
            QSettings("FlowAI", "FlowAI").setValue("run/skip_start_dialog", True)
        self.accept()
