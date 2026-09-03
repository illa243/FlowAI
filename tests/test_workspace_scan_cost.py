"""Що саме рушій вважає робочим матеріалом проєкту.

Оточення агента — віртуальне середовище й кеш пакетів — не є артефактами.
Поки вони рахуються за такі, кожна read-only нода хешує сотні мегабайтів
чужих файлів, а `uv pip` виглядає як порушення пісочниці.
"""

from __future__ import annotations

import os
from pathlib import Path

from flowai.runtime_state import (
    diff_workspace,
    file_evidence,
    qa_packet,
    snapshot_workspace,
)


def workspace(tmp_path: Path) -> Path:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "board.png").write_bytes(b"board")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "cut_step.py").write_text("pass", encoding="utf-8")
    return tmp_path


def test_the_agent_environment_is_not_part_of_the_snapshot(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    (root / "tools" / ".venv").mkdir()
    (root / "tools" / ".venv" / "python.exe").write_bytes(b"exe")
    (root / "tools" / ".uv-cache").mkdir()
    (root / "tools" / ".uv-cache" / "numpy.whl").write_bytes(b"wheel")

    snapshot = snapshot_workspace(root, ignore_runtime=True)

    assert "artifacts/board.png" in snapshot
    assert "tools/cut_step.py" in snapshot
    assert not any(".venv" in key for key in snapshot)
    assert not any(".uv-cache" in key for key in snapshot)


def test_installing_a_package_is_not_a_generated_artifact(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    before = snapshot_workspace(root, ignore_runtime=True)
    cache = root / "tools" / ".uv-cache"
    cache.mkdir()
    (cache / "pillow.whl").write_bytes(b"wheel")
    (root / "artifacts" / "cutout.png").write_bytes(b"png")

    ledger = diff_workspace(root, before, ignore_runtime=True)

    generated = [entry["relative_path"] for entry in ledger["generated"]]
    assert "artifacts/cutout.png" in generated
    assert not any(".uv-cache" in item for item in generated)


def test_the_same_bytes_keep_the_same_packet_hash_after_a_touch(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)
    target = root / "artifacts" / "board.png"

    first = qa_packet(root, [str(target)], task_id="t1", attempt_id="attempt-001")
    os.utime(target, (1_000_000, 1_000_000))
    second = qa_packet(root, [str(target)], task_id="t1", attempt_id="attempt-001")

    assert first["files"][0]["mtime_ns"] != second["files"][0]["mtime_ns"], (
        "тест має порівнювати саме різні mtime"
    )
    assert first["packet_hash"] == second["packet_hash"]


def test_an_unchanged_result_keeps_its_packet_hash_across_attempts(
    tmp_path: Path,
) -> None:
    root = workspace(tmp_path)
    target = root / "artifacts" / "board.png"

    first = qa_packet(root, [str(target)], task_id="t1", attempt_id="attempt-001")
    second = qa_packet(root, [str(target)], task_id="t1", attempt_id="attempt-002")

    assert first["attempt_id"] != second["attempt_id"]
    assert first["packet_hash"] == second["packet_hash"], (
        "Executor нічого не змінив — QA не має перевіряти те саме заново"
    )


def test_changed_bytes_still_change_the_packet_hash(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    target = root / "artifacts" / "board.png"

    first = qa_packet(root, [str(target)], task_id="t1", attempt_id="attempt-001")
    target.write_bytes(b"board v2")
    second = qa_packet(root, [str(target)], task_id="t1", attempt_id="attempt-001")

    assert first["packet_hash"] != second["packet_hash"]


def test_evidence_still_reports_the_modification_time(tmp_path: Path) -> None:
    root = workspace(tmp_path)

    evidence = file_evidence(root / "artifacts" / "board.png")

    assert evidence["mtime_ns"] > 0
    assert evidence["sha256"]
