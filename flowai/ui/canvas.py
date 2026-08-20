from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from ..models import (
    DEFAULT_PORT,
    NODE_COLORS,
    SIDECAR_KINDS,
    FlowEdge,
    FlowNode,
    Workflow,
    managed_task_title,
    normalize_managed_tasks,
)

NODE_WIDTH = 220.0
NODE_HEIGHT = 130.0

PORT_COLORS = {
    DEFAULT_PORT: "#A78BFA",
    "true": "#22C55E",
    "false": "#EF4444",
    "next": "#3B82F6",
    "done": "#22C55E",
}


def _connection_path(
    start: QPointF, end: QPointF, control_points: list[QPointF] | None = None
) -> QPainterPath:
    if control_points:
        anchors = [start, *control_points, end]
        path = QPainterPath(start)
        for index in range(len(anchors) - 1):
            previous = anchors[index - 1] if index > 0 else anchors[index]
            current = anchors[index]
            following = anchors[index + 1]
            after = (
                anchors[index + 2] if index + 2 < len(anchors) else anchors[index + 1]
            )
            first_control = QPointF(
                current.x() + (following.x() - previous.x()) / 6.0,
                current.y() + (following.y() - previous.y()) / 6.0,
            )
            second_control = QPointF(
                following.x() - (after.x() - current.x()) / 6.0,
                following.y() - (after.y() - current.y()) / 6.0,
            )
            path.cubicTo(first_control, second_control, following)
        return path

    distance = max(70.0, abs(end.x() - start.x()) * 0.5)
    path = QPainterPath(start)
    path.cubicTo(
        QPointF(start.x() + distance, start.y()),
        QPointF(end.x() - distance, end.y()),
        end,
    )
    return path


def _nearest_path_percent(path: QPainterPath, position: QPointF) -> float:
    samples = max(80, min(400, round(path.length() / 5.0)))
    best_percent = 0.0
    best_distance = math.inf
    for index in range(samples + 1):
        percent = index / samples
        point = path.pointAtPercent(percent)
        distance = (point.x() - position.x()) ** 2 + (point.y() - position.y()) ** 2
        if distance < best_distance:
            best_distance = distance
            best_percent = percent
    step = 1.0 / samples
    for _ in range(4):
        candidates = (
            max(0.0, best_percent - step),
            best_percent,
            min(1.0, best_percent + step),
        )
        best_percent = min(
            candidates,
            key=lambda percent: (
                (path.pointAtPercent(percent).x() - position.x()) ** 2
                + (path.pointAtPercent(percent).y() - position.y()) ** 2
            ),
        )
        step /= 2.0
    return best_percent


class PortItem(QGraphicsObject):
    def __init__(
        self,
        node_item: NodeItem,
        port_type: str,
        name: str = DEFAULT_PORT,
        label: str = "",
    ) -> None:
        super().__init__(node_item)
        self.node_item = node_item
        self.port_type = port_type
        self.name = name
        self.label = label
        self.setAcceptHoverEvents(True)
        self._hovered = False
        self._connection_target = False
        self._press_scene_pos: QPointF | None = None
        self._dragging_connection = False
        self.setCursor(Qt.CursorShape.CrossCursor)

    def boundingRect(self) -> QRectF:
        if self.label:
            return QRectF(-8, -12, 74, 24)
        return QRectF(-11, -11, 22, 22)

    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.port_type == "output":
            color = QColor(PORT_COLORS.get(self.name, PORT_COLORS[DEFAULT_PORT]))
        else:
            color = QColor("#60A5FA")
        if self._connection_target:
            glow = QColor(color)
            glow.setAlpha(120)
            painter.setPen(QPen(glow, 5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(-8, -8, 16, 16))
        painter.setPen(
            QPen(QColor("#FFFFFF"), 2.5)
            if self._connection_target
            else QPen(QColor("#E5E7EB"), 1.5)
        )
        painter.setBrush(
            color.lighter(175)
            if self._connection_target
            else color.lighter(130)
            if self._hovered
            else color
        )
        painter.drawEllipse(QRectF(-6, -6, 12, 12))
        if self.label:
            painter.setPen(color.lighter(115))
            font = painter.font()
            font.setPointSize(7)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QRectF(10, -10, 62, 20),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.label,
            )

    def set_label(self, label: str) -> None:
        if label != self.label:
            self.prepareGeometryChange()
            self.label = label
            self.update()

    def set_connection_target(self, active: bool) -> None:
        if self._connection_target != active:
            self._connection_target = active
            self.update()

    def hoverEnterEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        scene = self.scene()
        if (
            isinstance(scene, FlowScene)
            and event.button() == Qt.MouseButton.LeftButton
            and self.port_type == "output"
        ):
            self._press_scene_pos = event.scenePos()
            self._dragging_connection = False
            scene.begin_connection_drag(self)
            event.accept()
            return
        if isinstance(scene, FlowScene) and event.button() == Qt.MouseButton.LeftButton:
            scene.port_clicked(self)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        scene = self.scene()
        if isinstance(scene, FlowScene) and self._press_scene_pos is not None:
            position = event.scenePos()
            delta = position - self._press_scene_pos
            if math.hypot(delta.x(), delta.y()) >= 4.0:
                self._dragging_connection = True
            scene.update_connection_drag(position)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        scene = self.scene()
        if (
            isinstance(scene, FlowScene)
            and event.button() == Qt.MouseButton.LeftButton
            and self._press_scene_pos is not None
        ):
            if self._dragging_connection:
                scene.finish_connection_drag(event.scenePos())
            else:
                scene.cancel_connection_preview()
                scene.port_clicked(self)
            self._press_scene_pos = None
            self._dragging_connection = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class NodeItem(QGraphicsObject):
    def __init__(self, model: FlowNode) -> None:
        super().__init__()
        self.model = model
        self.edges: list[EdgeItem] = []
        self.status = "idle"
        self.duration_seconds = 0.0
        self.duration_history: list[float] = []
        self.running_started_at: float | None = None
        self.stage_current = 0
        self.stage_total = 0
        self.stage_name = ""
        self.attention = False
        self.blink_on = False
        self.task_states = self._configured_task_states()
        self.node_height = self._desired_height()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setPos(model.x, model.y)

        self.input_port: PortItem | None = None
        self.output_ports: dict[str, PortItem] = {}
        if model.kind in SIDECAR_KINDS:
            return

        self.input_port = PortItem(self, "input")
        self.input_port.setPos(0, self.node_height / 2)
        if model.kind == "result":
            true_port = PortItem(self, "output", "true", "TRUE 0/1")
            true_port.setPos(NODE_WIDTH, NODE_HEIGHT / 3)
            false_port = PortItem(self, "output", "false", "FALSE 0/3")
            false_port.setPos(NODE_WIDTH, NODE_HEIGHT * 2 / 3)
            self.output_ports = {"true": true_port, "false": false_port}
            self.refresh_port_labels()
        elif model.kind == "tasks_manager":
            next_port = PortItem(self, "output", "next", "NEXT")
            next_port.setPos(NODE_WIDTH, self.node_height / 3)
            done_port = PortItem(self, "output", "done", "DONE")
            done_port.setPos(NODE_WIDTH, self.node_height * 2 / 3)
            self.output_ports = {"next": next_port, "done": done_port}
        else:
            port = PortItem(self, "output", DEFAULT_PORT)
            port.setPos(NODE_WIDTH, self.node_height / 2)
            self.output_ports = {DEFAULT_PORT: port}

    def _configured_task_states(self) -> list[dict[str, str]]:
        if self.model.kind != "tasks_manager":
            return []
        return [
            {
                "id": str(task["id"]),
                "title": managed_task_title(task, index),
                "status": "pending",
            }
            for index, task in enumerate(
                normalize_managed_tasks(self.model.config.get("tasks"))
            )
        ]

    def _desired_height(self) -> float:
        if self.model.kind != "tasks_manager":
            return NODE_HEIGHT
        return max(150.0, 86.0 + len(self.task_states) * 22.0)

    def refresh_task_config(self) -> None:
        if self.model.kind != "tasks_manager":
            return
        states_by_id = {str(item.get("id")): item for item in self.task_states}
        configured = self._configured_task_states()
        for item in configured:
            previous = states_by_id.get(item["id"])
            if previous is not None:
                item["status"] = str(previous.get("status", "pending"))
        height = max(150.0, 86.0 + len(configured) * 22.0)
        if height != self.node_height:
            self.prepareGeometryChange()
            self.node_height = height
        self.task_states = configured
        self._layout_ports()
        for edge in self.edges:
            edge.update_path()
        self.update()

    def _layout_ports(self) -> None:
        if self.input_port is not None:
            self.input_port.setPos(0, self.node_height / 2)
        if self.model.kind == "tasks_manager":
            if next_port := self.output_ports.get("next"):
                next_port.setPos(NODE_WIDTH, self.node_height / 3)
            if done_port := self.output_ports.get("done"):
                done_port.setPos(NODE_WIDTH, self.node_height * 2 / 3)
        elif self.model.kind == "result":
            if true_port := self.output_ports.get("true"):
                true_port.setPos(NODE_WIDTH, self.node_height / 3)
            if false_port := self.output_ports.get("false"):
                false_port.setPos(NODE_WIDTH, self.node_height * 2 / 3)
        else:
            for port in self.output_ports.values():
                port.setPos(NODE_WIDTH, self.node_height / 2)

    def output_port_item(self, name: str) -> PortItem | None:
        if name in self.output_ports:
            return self.output_ports[name]
        return next(iter(self.output_ports.values()), None)

    def refresh_port_labels(self, counts: dict[str, int] | None = None) -> None:
        if self.model.kind != "result":
            return
        counts = counts or {}
        for name in ("true", "false"):
            port = self.output_ports.get(name)
            if port is None:
                continue
            limit = int(self.model.config.get(f"{name}_limit", 1))
            scene = self.scene()
            if isinstance(scene, FlowScene):
                limit = scene.workflow.result_port_limit(self.model, name)
            port.set_label(f"{name.upper()} {counts.get(name, 0)}/{limit}")

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, NODE_WIDTH, self.node_height)

    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QColor("#172033")
        if self.attention and self.blink_on:
            border = QColor("#FBBF24")
            width = 3
        elif self.status == "running":
            pulse = self._running_pulse()
            border = QColor(34, 197, 94, round(125 + 120 * pulse))
            width = 1.5 + 1.5 * pulse
            background = self._blend_color(
                QColor("#172033"), QColor("#14532D"), 0.12 + 0.20 * pulse
            )
        elif self.isSelected():
            border = QColor("#A78BFA")
            width = 2
        else:
            border = QColor("#334155")
            width = 1
        painter.setPen(QPen(border, width))
        painter.setBrush(background)
        painter.drawRoundedRect(self.boundingRect(), 10, 10)

        header = QRectF(0, 0, NODE_WIDTH, 36)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(NODE_COLORS.get(self.model.kind, "#64748B")))
        painter.drawRoundedRect(header, 10, 10)
        painter.drawRect(QRectF(0, 26, NODE_WIDTH, 10))

        font = painter.font()
        font.setBold(False)
        font.setPointSize(7)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 190))
        progress = ""
        if self.status in {"running", "waiting"} and self.stage_total:
            progress = f" · {self.stage_current}/{self.stage_total}"
        painter.drawText(
            QRectF(NODE_WIDTH - 104, 0, 92, 36),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            f"{self.model.short_id}{progress}",
        )

        painter.setPen(QColor("#FFFFFF"))
        font.setBold(True)
        font.setPointSize(10)
        painter.setFont(font)
        painter.drawText(
            QRectF(14, 0, NODE_WIDTH - 118, 36),
            Qt.AlignmentFlag.AlignVCenter,
            self.model.title,
        )

        if self.model.kind == "tasks_manager":
            self._paint_tasks(painter)
            return

        painter.setPen(QColor("#94A3B8"))
        font.setBold(False)
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(
            QRectF(14, 42, NODE_WIDTH - 28, 32),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            self._subtitle(),
        )

        status_colors = {
            "idle": "#64748B",
            "running": "#22C55E",
            "success": "#22C55E",
            "failed": "#EF4444",
            "cancelled": "#EF4444",
            "skipped": "#94A3B8",
            "waiting": "#FBBF24",
        }
        painter.setBrush(QColor(status_colors.get(self.status, "#64748B")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(14, 78, 8, 8))
        stage_active = self.status == "running" and bool(self.stage_name)
        stage_color = QColor("#86EFAC") if stage_active else QColor("#94A3B8")
        if stage_active:
            stage_color.setAlpha(round(145 + 110 * self._running_pulse()))
        painter.setPen(stage_color)
        painter.drawText(
            QRectF(28, 70, NODE_WIDTH - 40, 24),
            Qt.AlignmentFlag.AlignVCenter,
            self.stage_name if stage_active else self.status,
        )
        painter.setPen(QColor("#CBD5E1"))
        for index, line in enumerate(self._time_lines()):
            painter.drawText(
                QRectF(28, 92 + index * 15, NODE_WIDTH - 40, 15),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                line,
            )

    def _paint_tasks(self, painter: QPainter) -> None:
        font = painter.font()
        font.setBold(False)
        font.setPointSize(8)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        for index, task in enumerate(self.task_states):
            y = 44.0 + index * 22.0
            status = str(task.get("status", "pending"))
            if status == "completed":
                painter.setPen(QPen(QColor("#4ADE80"), 2.2))
                painter.drawLine(QPointF(14, y + 8), QPointF(18, y + 12))
                painter.drawLine(QPointF(18, y + 12), QPointF(25, y + 4))
            elif status == "running":
                painter.setPen(QPen(QColor("#60A5FA"), 2.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                angle = -round(time.monotonic() * 300 * 16)
                painter.drawArc(QRectF(13, y + 2, 13, 13), angle, 250 * 16)
            else:
                painter.setPen(QPen(QColor("#64748B"), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QRectF(16, y + 5, 7, 7))
            painter.setPen(
                QColor("#E5E7EB") if status == "running" else QColor("#CBD5E1")
            )
            title = metrics.elidedText(
                str(task.get("title", f"Завдання {index + 1}")),
                Qt.TextElideMode.ElideRight,
                round(NODE_WIDTH - 48),
            )
            painter.drawText(
                QRectF(32, y, NODE_WIDTH - 44, 18),
                Qt.AlignmentFlag.AlignVCenter,
                title,
            )

        completed = sum(
            1 for task in self.task_states if task.get("status") == "completed"
        )
        footer_y = self.node_height - 28
        painter.setPen(QColor("#94A3B8"))
        painter.drawText(
            QRectF(14, footer_y, NODE_WIDTH - 90, 18),
            Qt.AlignmentFlag.AlignVCenter,
            f"Виконано {completed}/{len(self.task_states)}",
        )
        painter.setPen(QColor("#CBD5E1"))
        painter.drawText(
            QRectF(NODE_WIDTH - 92, footer_y, 78, 18),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            self._time_lines()[-1],
        )

    @staticmethod
    def _blend_color(start: QColor, end: QColor, amount: float) -> QColor:
        ratio = max(0.0, min(1.0, amount))
        return QColor(
            round(start.red() + (end.red() - start.red()) * ratio),
            round(start.green() + (end.green() - start.green()) * ratio),
            round(start.blue() + (end.blue() - start.blue()) * ratio),
        )

    @staticmethod
    def _running_pulse(now: float | None = None) -> float:
        """Плавна хвиля 0..1 з періодом приблизно 1,6 секунди."""
        moment = time.monotonic() if now is None else now
        return (math.sin(moment * math.tau / 1.6) + 1.0) / 2.0

    def elapsed_seconds(self, now: float | None = None) -> float:
        elapsed = self.duration_seconds
        if self.running_started_at is not None:
            moment = time.monotonic() if now is None else now
            elapsed += max(0.0, moment - self.running_started_at)
        return elapsed

    def formatted_duration(self, now: float | None = None) -> str:
        if self.duration_seconds <= 0 and self.running_started_at is None:
            return "—"
        seconds = self.elapsed_seconds(now)
        if seconds < 60:
            return f"{seconds:.1f} с"
        minutes, remainder = divmod(seconds, 60)
        if minutes < 60:
            return f"{int(minutes):02d}:{remainder:04.1f}"
        hours, minutes = divmod(int(minutes), 60)
        return f"{hours}:{minutes:02d}:{int(remainder):02d}"

    def _time_lines(self, now: float | None = None) -> list[str]:
        values = list(self.duration_history)
        if self.running_started_at is not None:
            values.append(self.elapsed_seconds(now))
        elif not values and self.duration_seconds > 0:
            values.append(self.duration_seconds)
        if not values:
            return ["Час: —"]
        start = max(0, len(values) - 2)
        lines = [
            f"Час {index + 1}: {self._format_seconds(value)}"
            for index, value in enumerate(values[start:], start=start)
        ]
        if start:
            lines[0] = "…  " + lines[0]
        return lines

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f} с"
        minutes, remainder = divmod(seconds, 60)
        if minutes < 60:
            return f"{int(minutes):02d}:{remainder:04.1f}"
        hours, minutes = divmod(int(minutes), 60)
        return f"{hours}:{minutes:02d}:{int(remainder):02d}"

    def _subtitle(self) -> str:
        if self.model.is_agent:
            model = str(self.model.config.get("model", "Codex"))
            reasoning = str(self.model.config.get("reasoning_effort", "medium"))
            sandbox = str(self.model.config.get("sandbox", "read-only"))
            return f"{model} · Міркування: {reasoning}\n{sandbox}"
        descriptions = {
            "entry": "Вхідний промпт і вкладення",
            "tasks_manager": "Черга послідовних завдань",
            "result": "Розгалуження True/False",
        }
        return descriptions.get(self.model.kind, self.model.kind)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any) -> Any:
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            position = self.pos()
            self.model.x = round(position.x(), 2)
            self.model.y = round(position.y(), 2)
            for edge in self.edges:
                edge.update_path()
            scene = self.scene()
            if isinstance(scene, FlowScene) and not scene.loading:
                scene.model_changed.emit()
        return super().itemChange(change, value)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self.attention and event.button() == Qt.MouseButton.LeftButton:
            scene = self.scene()
            if isinstance(scene, FlowScene):
                scene.attention_clicked.emit(self.model.id)
        super().mousePressEvent(event)

    def set_status(self, status: str) -> None:
        self.status = status
        self.update()

    def set_runtime(
        self,
        duration_seconds: float,
        running_started_at: float | None = None,
        history: list[float] | None = None,
    ) -> None:
        self.duration_seconds = max(0.0, float(duration_seconds))
        self.running_started_at = running_started_at
        if history is not None:
            self.duration_history = [max(0.0, float(item)) for item in history]
        self.update()

    def set_stage(self, current: int, total: int, name: str) -> None:
        self.stage_current = max(0, int(current))
        self.stage_total = max(0, int(total))
        self.stage_name = str(name)
        self.update()

    def set_attention(self, attention: bool) -> None:
        self.attention = attention
        self.blink_on = attention
        self.update()

    def set_task_states(self, states: list[dict[str, Any]]) -> None:
        if self.model.kind != "tasks_manager":
            return
        known = {str(item.get("id")): item for item in self.task_states}
        merged: list[dict[str, str]] = []
        for index, raw in enumerate(states):
            task_id = str(raw.get("id", ""))
            previous = known.get(task_id, {})
            merged.append(
                {
                    "id": task_id,
                    "title": str(
                        raw.get("title")
                        or previous.get("title")
                        or f"Завдання {index + 1}"
                    ),
                    "status": str(raw.get("status", "pending")),
                }
            )
        if merged:
            self.task_states = merged
        self.update()

    def has_active_task(self) -> bool:
        return any(task.get("status") == "running" for task in self.task_states)


class EdgeControlPointItem(QGraphicsObject):
    def __init__(self, edge: EdgeItem, index: int, position: QPointF) -> None:
        super().__init__(edge)
        self.edge = edge
        self.index = index
        self._hovered = False
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(2)
        self.setPos(position)

    def boundingRect(self) -> QRectF:
        return QRectF(-9, -9, 18, 18)

    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = (
            QColor("#C4B5FD")
            if self._hovered or self.isSelected()
            else QColor("#8B5CF6")
        )
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.setBrush(color)
        painter.drawEllipse(QRectF(-6, -6, 12, 12))

    def hoverEnterEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        event.accept()

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any) -> Any:
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and not self.edge._syncing_control_points
            and 0 <= self.index < len(self.edge.model.control_points)
        ):
            position = self.pos()
            self.edge.model.control_points[self.index] = {
                "x": round(position.x(), 2),
                "y": round(position.y(), 2),
            }
            self.edge.update_path()
            scene = self.scene()
            if isinstance(scene, FlowScene) and not scene.loading:
                scene.model_changed.emit()
        return super().itemChange(change, value)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, model: FlowEdge, source: NodeItem, target: NodeItem) -> None:
        super().__init__()
        self.model = model
        self.source = source
        self.target = target
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(-1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label_item = QGraphicsSimpleTextItem(self)
        self.label_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.label_item.setBrush(QColor("#CBD5E1"))
        self._syncing_control_points = True
        self.control_point_items: list[EdgeControlPointItem] = []
        for index, point in enumerate(self.model.control_points):
            position = QPointF(float(point["x"]), float(point["y"]))
            self.control_point_items.append(EdgeControlPointItem(self, index, position))
        self._syncing_control_points = False
        self.source.edges.append(self)
        self.target.edges.append(self)
        self.update_path()

    def _color(self) -> QColor:
        if self.isSelected():
            return QColor("#A78BFA")
        if self.model.source_port in {"true", "false"}:
            return QColor(PORT_COLORS[self.model.source_port]).darker(115)
        return QColor("#64748B")

    def update_path(self) -> None:
        port = self.source.output_port_item(self.model.source_port)
        start = port.scenePos() if port else self.source.scenePos()
        end = (
            self.target.input_port.scenePos()
            if self.target.input_port
            else self.target.scenePos()
        )
        control_points = [
            QPointF(float(point["x"]), float(point["y"]))
            for point in self.model.control_points
        ]
        path = _connection_path(start, end, control_points)
        self.setPath(path)
        label = (
            self.model.label
            or f"{self.model.source_path} → {self.model.target_variable}"
        )
        self.label_item.setText(label)
        middle = path.pointAtPercent(0.5)
        bounds = self.label_item.boundingRect()
        self.label_item.setPos(
            middle.x() - bounds.width() / 2, middle.y() - bounds.height() - 6
        )

    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        color = self._color()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color, 3 if self.isSelected() else 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())

        end = self.path().pointAtPercent(1.0)
        before = self.path().pointAtPercent(0.97)
        angle = math.atan2(end.y() - before.y(), end.x() - before.x())
        arrow_size = 9
        p1 = end - QPointF(
            math.cos(angle - math.pi / 6) * arrow_size,
            math.sin(angle - math.pi / 6) * arrow_size,
        )
        p2 = end - QPointF(
            math.cos(angle + math.pi / 6) * arrow_size,
            math.sin(angle + math.pi / 6) * arrow_size,
        )
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([end, p1, p2]))

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(16.0)
        return stroker.createStroke(self.path())

    def boundingRect(self) -> QRectF:
        return self.path().boundingRect().adjusted(-8.0, -8.0, 8.0, 8.0)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.add_control_point(event.scenePos())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def add_control_point(self, position: QPointF) -> EdgeControlPointItem:
        percent = _nearest_path_percent(self.path(), position)
        point_position = self.path().pointAtPercent(percent)
        existing = [
            _nearest_path_percent(
                self.path(), QPointF(float(point["x"]), float(point["y"]))
            )
            for point in self.model.control_points
        ]
        index = next(
            (
                current_index
                for current_index, current_percent in enumerate(existing)
                if current_percent > percent
            ),
            len(existing),
        )
        self.model.control_points.insert(
            index,
            {"x": round(point_position.x(), 2), "y": round(point_position.y(), 2)},
        )
        self._syncing_control_points = True
        item = EdgeControlPointItem(self, index, point_position)
        self.control_point_items.insert(index, item)
        self._reindex_control_points()
        self._syncing_control_points = False
        self.update_path()
        scene = self.scene()
        if isinstance(scene, FlowScene):
            scene.model_changed.emit()
            scene.message.emit("Точку зв'язку створено — перетягніть її для вигину")
        return item

    def remove_control_point(self, item: EdgeControlPointItem) -> None:
        if item not in self.control_point_items:
            return
        index = self.control_point_items.index(item)
        self.control_point_items.pop(index)
        self.model.control_points.pop(index)
        item.setParentItem(None)
        scene = item.scene()
        if scene is not None:
            scene.removeItem(item)
        self._reindex_control_points()
        self.update_path()

    def _reindex_control_points(self) -> None:
        for index, point in enumerate(self.control_point_items):
            point.index = index

    def detach(self) -> None:
        if self in self.source.edges:
            self.source.edges.remove(self)
        if self in self.target.edges:
            self.target.edges.remove(self)


class ConnectionPreviewItem(QGraphicsPathItem):
    """Тимчасова крива, яка слідує за курсором під час з'єднання."""

    def __init__(self, start: QPointF, port_name: str) -> None:
        super().__init__()
        self.start = start
        color = QColor(PORT_COLORS.get(port_name, PORT_COLORS[DEFAULT_PORT]))
        color.setAlpha(220)
        pen = QPen(color, 2.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setZValue(-0.5)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.update_end(start)

    def update_end(self, end: QPointF) -> None:
        self.setPath(_connection_path(self.start, end))


class FlowScene(QGraphicsScene):
    selection_object_changed = Signal(object)
    model_changed = Signal()
    message = Signal(str)
    attention_clicked = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.workflow = Workflow()
        self.node_items: dict[str, NodeItem] = {}
        self.edge_items: dict[str, EdgeItem] = {}
        self.pending_source: tuple[NodeItem, str] | None = None
        self.connection_preview: ConnectionPreviewItem | None = None
        self._preview_source: tuple[NodeItem, str] | None = None
        self._highlighted_input: PortItem | None = None
        self.loading = False
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self.selectionChanged.connect(self._selection_changed)
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(550)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._running_timer = QTimer(self)
        self._running_timer.setInterval(100)
        self._running_timer.timeout.connect(self._update_running_nodes)

    def set_workflow(self, workflow: Workflow) -> None:
        self.loading = True
        self.cancel_connection_preview()
        self.clear()
        self.workflow = workflow
        self.node_items.clear()
        self.edge_items.clear()
        self.pending_source = None
        self._blink_timer.stop()
        self._running_timer.stop()
        for node in workflow.nodes:
            item = NodeItem(node)
            self.node_items[node.id] = item
            self.addItem(item)
        for edge in workflow.edges:
            if edge.source in self.node_items and edge.target in self.node_items:
                item = EdgeItem(
                    edge, self.node_items[edge.source], self.node_items[edge.target]
                )
                self.edge_items[edge.id] = item
                self.addItem(item)
        self.loading = False
        self.selection_object_changed.emit(None)

    def add_node(self, kind: str, position: QPointF) -> FlowNode:
        node = FlowNode.create(kind, position.x(), position.y())
        self.workflow.nodes.append(node)
        item = NodeItem(node)
        self.node_items[node.id] = item
        self.addItem(item)
        self.clearSelection()
        item.setSelected(True)
        self.model_changed.emit()
        return node

    def port_clicked(self, port: PortItem) -> None:
        if port.port_type == "output":
            self.cancel_connection_preview()
            self.pending_source = (port.node_item, port.name)
            suffix = f" ({port.name.upper()})" if port.name != DEFAULT_PORT else ""
            self.message.emit(
                f"Вибрано вихід «{port.node_item.model.title}»{suffix}. "
                "Натисніть вхід іншої ноди."
            )
            return
        if self.pending_source is None:
            self.message.emit("Спочатку натисніть вихідний порт ноди")
            return
        source, port_name = self.pending_source
        target = port.node_item
        self.pending_source = None
        self._create_connection(source, port_name, target)

    def begin_connection_drag(self, port: PortItem) -> None:
        if port.port_type != "output":
            return
        self.cancel_connection_preview()
        self.pending_source = None
        self._preview_source = (port.node_item, port.name)
        self.connection_preview = ConnectionPreviewItem(port.scenePos(), port.name)
        self.addItem(self.connection_preview)

    def update_connection_drag(self, position: QPointF) -> None:
        if self.connection_preview is not None:
            self.connection_preview.update_end(position)
            self._set_highlighted_input(self._valid_input_port_at(position))

    def finish_connection_drag(self, position: QPointF) -> None:
        source_info = self._preview_source
        target_port = self._input_port_at(position)
        self.cancel_connection_preview()
        if source_info is None:
            return
        if target_port is None:
            self.message.emit("З'єднання скасовано: відпустіть лінію на вхідному порті")
            return
        source, port_name = source_info
        self._create_connection(source, port_name, target_port.node_item)

    def cancel_connection_preview(self) -> None:
        self._set_highlighted_input(None)
        preview = self.connection_preview
        self.connection_preview = None
        self._preview_source = None
        if preview is not None and preview.scene() is self:
            self.removeItem(preview)

    def cancel_connection(self) -> None:
        self.pending_source = None
        self.cancel_connection_preview()

    def _input_port_at(self, position: QPointF) -> PortItem | None:
        for item in self.items(position):
            if isinstance(item, PortItem) and item.port_type == "input":
                return item
        return None

    def _valid_input_port_at(self, position: QPointF) -> PortItem | None:
        target_port = self._input_port_at(position)
        if target_port is None or self._preview_source is None:
            return None
        source, port_name = self._preview_source
        target = target_port.node_item
        if source is target:
            return None
        if any(
            edge.source == source.model.id
            and edge.target == target.model.id
            and edge.source_port == port_name
            for edge in self.workflow.edges
        ):
            return None
        return target_port

    def _set_highlighted_input(self, port: PortItem | None) -> None:
        if self._highlighted_input is port:
            return
        if self._highlighted_input is not None:
            self._highlighted_input.set_connection_target(False)
        self._highlighted_input = port
        if port is not None:
            port.set_connection_target(True)

    def _create_connection(
        self, source: NodeItem, port_name: str, target: NodeItem
    ) -> bool:
        if source is target:
            self.message.emit("Ноду не можна з'єднати саму із собою")
            return False
        if any(
            edge.source == source.model.id
            and edge.target == target.model.id
            and edge.source_port == port_name
            for edge in self.workflow.edges
        ):
            self.message.emit("Таке з'єднання вже існує")
            return False
        edge = FlowEdge.create(source.model.id, target.model.id, port_name)
        self.workflow.edges.append(edge)
        item = EdgeItem(edge, source, target)
        self.edge_items[edge.id] = item
        self.addItem(item)
        self.clearSelection()
        item.setSelected(True)
        self.model_changed.emit()
        self.message.emit("З'єднання створено")
        return True

    def delete_selection(self) -> None:
        selected = list(self.selectedItems())
        if not selected:
            return
        for item in selected:
            if isinstance(item, EdgeControlPointItem):
                item.edge.remove_control_point(item)
        for item in selected:
            if isinstance(item, EdgeItem):
                self._remove_edge(item.model.id)
        for item in selected:
            if isinstance(item, NodeItem):
                self._remove_node(item.model.id)
        self.model_changed.emit()
        self.selection_object_changed.emit(None)

    def _remove_edge(self, edge_id: str) -> None:
        item = self.edge_items.pop(edge_id, None)
        if item:
            item.detach()
            self.removeItem(item)
        self.workflow.edges = [
            edge for edge in self.workflow.edges if edge.id != edge_id
        ]

    def _remove_node(self, node_id: str) -> None:
        for edge in list(self.workflow.edges):
            if edge.source == node_id or edge.target == node_id:
                self._remove_edge(edge.id)
        item = self.node_items.pop(node_id, None)
        if item:
            self.removeItem(item)
        self.workflow.remove_node(node_id)

    def _selection_changed(self) -> None:
        selected = self.selectedItems()
        if len(selected) == 1:
            item = selected[0]
            if isinstance(item, NodeItem):
                if item.attention:
                    self.attention_clicked.emit(item.model.id)
                self.selection_object_changed.emit(item.model)
                return
            if isinstance(item, EdgeItem):
                self.selection_object_changed.emit(item.model)
                return
            if isinstance(item, EdgeControlPointItem):
                self.selection_object_changed.emit(item.edge.model)
                return
        self.selection_object_changed.emit(None)

    def refresh_item(self, model: FlowNode | FlowEdge) -> None:
        if isinstance(model, FlowNode):
            item = self.node_items.get(model.id)
            if item:
                item.refresh_task_config()
                item.refresh_port_labels()
                item.update()
            if model.kind == "tasks_manager":
                for result in self.workflow.nodes_of_kind("result"):
                    result_item = self.node_items.get(result.id)
                    if result_item is not None:
                        result_item.refresh_port_labels()
        else:
            item = self.edge_items.get(model.id)
            if item:
                item.update_path()
                item.update()

    def reset_statuses(self) -> None:
        for item in self.node_items.values():
            item.set_status("idle")
            item.set_runtime(0.0, history=[])
            item.set_stage(0, 0, "")
            item.set_attention(False)
            item.refresh_task_config()
            if item.model.kind == "tasks_manager":
                item.set_task_states(item._configured_task_states())
            item.refresh_port_labels()
        self._blink_timer.stop()
        self._running_timer.stop()

    def set_node_status(self, node_id: str, status: str) -> None:
        item = self.node_items.get(node_id)
        if item:
            item.set_status(status)
        self._sync_running_timer()

    def node_statuses(self) -> dict[str, str]:
        return {node_id: item.status for node_id, item in self.node_items.items()}

    def apply_node_statuses(self, statuses: dict[str, str]) -> None:
        for node_id, status in statuses.items():
            self.set_node_status(node_id, status)

    def set_node_runtime(
        self,
        node_id: str,
        duration_seconds: float,
        running_started_at: float | None = None,
        history: list[float] | None = None,
    ) -> None:
        item = self.node_items.get(node_id)
        if item:
            item.set_runtime(duration_seconds, running_started_at, history)

    def apply_node_runtimes(
        self,
        durations: dict[str, float],
        running_started_at: dict[str, float] | None = None,
        histories: dict[str, list[float]] | None = None,
    ) -> None:
        starts = running_started_at or {}
        all_histories = histories or {}
        for node_id, item in self.node_items.items():
            item.set_runtime(
                float(durations.get(node_id, 0.0)),
                starts.get(node_id),
                all_histories.get(node_id, []),
            )
        self._sync_running_timer()

    def set_node_stage(self, node_id: str, current: int, total: int, name: str) -> None:
        item = self.node_items.get(node_id)
        if item:
            item.set_stage(current, total, name)

    def apply_node_stages(self, stages: dict[str, tuple[int, int, str]]) -> None:
        for node_id, stage in stages.items():
            if len(stage) == 3:
                self.set_node_stage(node_id, stage[0], stage[1], stage[2])

    def set_attention(self, node_id: str, attention: bool) -> None:
        item = self.node_items.get(node_id)
        if item is None:
            return
        item.set_attention(attention)
        if attention:
            self._blink_timer.start()
        elif not any(node.attention for node in self.node_items.values()):
            self._blink_timer.stop()

    def apply_port_counts(self, counts: dict[str, int]) -> None:
        """counts: ключі виду "<node_id>:<port>" із чекпоінта запуску."""
        grouped: dict[str, dict[str, int]] = {}
        for key, value in counts.items():
            node_id, _, port = key.rpartition(":")
            grouped.setdefault(node_id, {})[port] = int(value)
        for node_id, item in self.node_items.items():
            item.refresh_port_labels(grouped.get(node_id, {}))

    def set_task_states(self, node_id: str, states: list[dict[str, Any]]) -> None:
        item = self.node_items.get(node_id)
        if item is not None:
            item.set_task_states(states)
        self._sync_running_timer()

    def apply_task_states(self, task_states: dict[str, list[dict[str, Any]]]) -> None:
        for node_id, states in task_states.items():
            self.set_task_states(node_id, states)

    def _toggle_blink(self) -> None:
        for item in self.node_items.values():
            if item.attention:
                item.blink_on = not item.blink_on
                item.update()

    def _sync_running_timer(self) -> None:
        running = any(
            item.status == "running" or item.has_active_task()
            for item in self.node_items.values()
        )
        if running and not self._running_timer.isActive():
            self._running_timer.start()
        elif not running:
            self._running_timer.stop()

    def _update_running_nodes(self) -> None:
        for item in self.node_items.values():
            if item.status == "running" or item.has_active_task():
                item.update()


class FlowView(QGraphicsView):
    rename_requested = Signal(object)

    def __init__(self, scene: FlowScene) -> None:
        super().__init__(scene)
        self._panning = False
        self._pan_start = QPoint()
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate
        )
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#0B1220"))
        self.setFrameShape(QFrame.Shape.NoFrame)

    def wheelEvent(self, event: Any) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        current = self.transform().m11()
        if 0.25 < current * factor < 2.5:
            self.scale(factor, factor)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._panning:
            position = event.position().toPoint()
            delta = position - self._pan_start
            self._pan_start = position
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._panning:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        scene = self.scene()
        if event.key() == Qt.Key.Key_F2 and isinstance(scene, FlowScene):
            selected_nodes = [
                item for item in scene.selectedItems() if isinstance(item, NodeItem)
            ]
            if len(selected_nodes) == 1:
                self.rename_requested.emit(selected_nodes[0].model)
                event.accept()
                return
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace} and isinstance(
            scene, FlowScene
        ):
            scene.delete_selection()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and isinstance(scene, FlowScene):
            scene.cancel_connection()
            scene.clearSelection()
            event.accept()
            return
        super().keyPressEvent(event)

    def center_position(self) -> QPointF:
        return self.mapToScene(self.viewport().rect().center()) - QPointF(
            NODE_WIDTH / 2, NODE_HEIGHT / 2
        )
