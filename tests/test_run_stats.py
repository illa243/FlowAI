from __future__ import annotations

import json
from pathlib import Path

from flowai.run_history import load_runs
from flowai.run_stats import collect_stats, merge_stats


def _finished(node_id: str, title: str, seconds: float, tokens: int) -> dict:
    return {
        "type": "node_finished",
        "node_id": node_id,
        "node_title": title,
        "result": {
            "duration_seconds": seconds,
            "status": "success",
            "data": {
                "usage": {
                    "total_tokens": tokens,
                    "reasoning_output_tokens": 5,
                    "context_window": 1000,
                }
            },
        },
    }


def test_collect_stats_aggregates_attempts() -> None:
    events = [
        _finished("n1", "Task Executor", 2.0, 100),
        _finished("n1", "Task Executor", 3.0, 300),
        _finished("n2", "Task Reviewer", 1.0, 50),
    ]
    stats = collect_stats(events, {"n1": "#7C3AED", "n2": "#D97706"})
    executor = next(item for item in stats.nodes if item.node_id == "n1")
    assert executor.runs == 2
    assert executor.attempts == [2.0, 3.0]
    assert executor.total_seconds == 5.0
    assert executor.average_seconds == 2.5
    assert executor.total_tokens == 400
    assert executor.context_percent == 30.0
    assert stats.total_seconds == 6.0


def test_merge_stats_sums_runs() -> None:
    first = collect_stats([_finished("n1", "Виконавець", 2.0, 100)], {})
    second = collect_stats([_finished("n1", "Виконавець", 4.0, 100)], {})
    merged = merge_stats([first, second])
    node = merged.nodes[0]
    assert node.runs == 2
    assert node.total_seconds == 6.0
    assert merged.run_count == 2


def test_load_runs_reads_newest_first(tmp_path: Path) -> None:
    for name, workflow in (("20260101-000000", "A"), ("20260102-000000", "B")):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "flowai-run.json").write_text(
            json.dumps({"workflow": workflow, "status": "success", "events": []}),
            encoding="utf-8",
        )
    runs = load_runs(tmp_path)
    assert len(runs) == 2
