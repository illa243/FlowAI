from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FINISH_EVENTS = frozenset({"node_finished", "work_review_finished"})
FAIL_EVENTS = frozenset({"node_failed", "work_review_failed"})


@dataclass(slots=True)
class NodeStat:
    node_id: str
    title: str = ""
    kind: str = ""
    color: str = "#CBD5E1"
    runs: int = 0
    attempts: list[float] = field(default_factory=list)
    total_seconds: float = 0.0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    context_window: int = 0
    peak_tokens: int = 0
    failures: int = 0

    @property
    def average_seconds(self) -> float:
        if not self.attempts:
            return 0.0
        return round(self.total_seconds / len(self.attempts), 3)

    @property
    def context_percent(self) -> float:
        if not self.context_window:
            return 0.0
        return round(self.peak_tokens / self.context_window * 100, 1)


@dataclass(slots=True)
class RunStats:
    nodes: list[NodeStat] = field(default_factory=list)
    total_seconds: float = 0.0
    tasks_total_seconds: float = 0.0
    run_count: int = 1


def _stat_for(bucket: dict[str, NodeStat], node_id: str) -> NodeStat:
    if node_id not in bucket:
        bucket[node_id] = NodeStat(node_id=node_id)
    return bucket[node_id]


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def collect_stats(
    events: list[dict[str, Any]], colors: dict[str, str] | None = None
) -> RunStats:
    """Звести події одного запуску в статистику по блоках."""
    palette = colors or {}
    bucket: dict[str, NodeStat] = {}
    tasks_total = 0.0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", ""))
        node_id = str(event.get("node_id") or "")
        if event_type == "tasks_progress":
            try:
                tasks_total = max(tasks_total, float(event.get("total_seconds", 0.0)))
            except (TypeError, ValueError):
                pass
            continue
        if not node_id or event_type not in FINISH_EVENTS | FAIL_EVENTS:
            continue
        stat = _stat_for(bucket, node_id)
        stat.title = str(event.get("node_title") or stat.title or node_id[:6])
        stat.kind = str(event.get("node_kind") or stat.kind)
        stat.color = palette.get(node_id, stat.color)
        result = event.get("result")
        result = result if isinstance(result, dict) else {}
        try:
            seconds = float(result.get("duration_seconds", 0.0) or 0.0)
        except (TypeError, ValueError):
            seconds = 0.0
        stat.runs += 1
        stat.attempts.append(round(seconds, 3))
        stat.total_seconds = round(stat.total_seconds + seconds, 3)
        if event_type in FAIL_EVENTS:
            stat.failures += 1
        data = result.get("data")
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            turn_tokens = _safe_int(usage.get("total_tokens"))
            stat.total_tokens += turn_tokens
            stat.reasoning_tokens += _safe_int(usage.get("reasoning_output_tokens"))
            stat.peak_tokens = max(stat.peak_tokens, turn_tokens)
            stat.context_window = max(
                stat.context_window, _safe_int(usage.get("context_window"))
            )
    nodes = sorted(bucket.values(), key=lambda item: item.total_seconds, reverse=True)
    return RunStats(
        nodes=nodes,
        total_seconds=round(sum(item.total_seconds for item in nodes), 3),
        tasks_total_seconds=round(tasks_total, 3),
    )


def merge_stats(items: list[RunStats]) -> RunStats:
    """Скласти кілька запусків в один звіт."""
    bucket: dict[str, NodeStat] = {}
    for stats in items:
        for node in stats.nodes:
            target = _stat_for(bucket, node.node_id)
            target.title = node.title or target.title
            target.kind = node.kind or target.kind
            target.color = node.color if node.color != "#CBD5E1" else target.color
            target.runs += node.runs
            target.attempts.extend(node.attempts)
            target.total_seconds = round(target.total_seconds + node.total_seconds, 3)
            target.total_tokens += node.total_tokens
            target.reasoning_tokens += node.reasoning_tokens
            target.failures += node.failures
            target.peak_tokens = max(target.peak_tokens, node.peak_tokens)
            target.context_window = max(target.context_window, node.context_window)
    nodes = sorted(bucket.values(), key=lambda item: item.total_seconds, reverse=True)
    return RunStats(
        nodes=nodes,
        total_seconds=round(sum(item.total_seconds for item in nodes), 3),
        tasks_total_seconds=round(
            sum(item.tasks_total_seconds for item in items), 3
        ),
        run_count=len(items),
    )
