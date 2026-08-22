from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CALIBRATION_FILE = "calibration.json"
EDIT_TARGETS = frozenset(
    {"skill_file", "task_prompt", "node_prompt", "node_instructions"}
)

CALIBRATION_SCHEMA: dict[str, Any] = {
    "summary": "string",
    "root_cause": "string",
    "skills_used": ["string"],
    "skills_missing": ["string"],
    "points": [
        {
            "title": "string",
            "detail": "string",
            "images": [{"path": "string", "note": "string"}],
        }
    ],
    "edits": [
        {
            "target": (
                "skill_file | task_prompt | node_prompt | node_instructions"
            ),
            "path": "абсолютний шлях для skill_file",
            "skill": "ім'я скіла для skill_file",
            "node_id": "id ноди для node_prompt і node_instructions",
            "task_id": "id завдання для task_prompt",
            "label": "string",
            "rationale": "string",
            "before": "точний фрагмент, який зараз у файлі",
            "after": "чим його замінити",
        }
    ],
}


@dataclass(slots=True)
class RejectionImage:
    """Картинка, якою рев'ювер ілюструє свій закид."""

    path: str
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "note": self.note}


@dataclass(slots=True)
class RejectionPoint:
    """Один пункт відхилення разом із баченням користувача."""

    title: str
    detail: str = ""
    images: list[RejectionImage] = field(default_factory=list)
    user_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "detail": self.detail,
            "images": [image.to_dict() for image in self.images],
            "user_note": self.user_note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RejectionPoint:
        images = [
            RejectionImage(
                path=str(item.get("path", "")), note=str(item.get("note", ""))
            )
            for item in raw.get("images") or []
            if isinstance(item, dict) and str(item.get("path", "")).strip()
        ]
        return cls(
            title=str(raw.get("title", "")).strip() or "Без назви",
            detail=str(raw.get("detail", "")),
            images=images,
            user_note=str(raw.get("user_note", "")),
        )


@dataclass(slots=True)
class ProposedEdit:
    """Одна правка: точний фрагмент «було» та його заміна."""

    target: str
    label: str = ""
    rationale: str = ""
    before: str = ""
    after: str = ""
    path: str = ""
    node_id: str = ""
    task_id: str = ""
    skill: str = ""
    accepted: bool = True

    @property
    def display_path(self) -> str:
        """Як цю правку підписати у списку файлів."""
        if self.target == "skill_file":
            name = Path(self.path).name or self.path
            return f"{self.skill} / {name}" if self.skill else name
        if self.target == "task_prompt":
            return "Промпт завдання"
        if self.target == "node_prompt":
            return "Промпт блоку"
        return "Постійні інструкції блоку"

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "label": self.label,
            "rationale": self.rationale,
            "before": self.before,
            "after": self.after,
            "path": self.path,
            "node_id": self.node_id,
            "task_id": self.task_id,
            "skill": self.skill,
            "accepted": self.accepted,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ProposedEdit:
        return cls(
            target=str(raw.get("target", "")),
            label=str(raw.get("label", "")),
            rationale=str(raw.get("rationale", "")),
            before=str(raw.get("before", "")),
            after=str(raw.get("after", "")),
            path=str(raw.get("path", "")),
            node_id=str(raw.get("node_id", "")),
            task_id=str(raw.get("task_id", "")),
            skill=str(raw.get("skill", "")),
            accepted=bool(raw.get("accepted", True)),
        )


@dataclass
class CalibrationReport:
    """Усе, що показує вікно калібрації, в одному об'єкті."""

    node_id: str
    node_title: str
    task_id: str
    task_title: str
    workflow_name: str
    attempt: int
    threshold: int
    verdict_reason: str = ""
    must_fix: list[str] = field(default_factory=list)
    summary: str = ""
    root_cause: str = ""
    points: list[RejectionPoint] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    skills_missing: list[str] = field(default_factory=list)
    edits: list[ProposedEdit] = field(default_factory=list)
    analysis_error: str = ""

    def accepted_edits(self) -> list[ProposedEdit]:
        return [edit for edit in self.edits if edit.accepted]

    def user_notes_text(self) -> str:
        """Бачення користувача одним блоком для GrillMe."""
        lines = [
            f"- {point.title}: {point.user_note.strip()}"
            for point in self.points
            if point.user_note.strip()
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_title": self.node_title,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "workflow_name": self.workflow_name,
            "attempt": self.attempt,
            "threshold": self.threshold,
            "verdict_reason": self.verdict_reason,
            "must_fix": list(self.must_fix),
            "summary": self.summary,
            "root_cause": self.root_cause,
            "points": [point.to_dict() for point in self.points],
            "skills_used": list(self.skills_used),
            "skills_missing": list(self.skills_missing),
            "edits": [edit.to_dict() for edit in self.edits],
            "analysis_error": self.analysis_error,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CalibrationReport:
        return cls(
            node_id=str(raw.get("node_id", "")),
            node_title=str(raw.get("node_title", "")),
            task_id=str(raw.get("task_id", "")),
            task_title=str(raw.get("task_title", "")),
            workflow_name=str(raw.get("workflow_name", "")),
            attempt=int(raw.get("attempt", 1)),
            threshold=int(raw.get("threshold", 1)),
            verdict_reason=str(raw.get("verdict_reason", "")),
            must_fix=[str(item) for item in raw.get("must_fix", [])],
            summary=str(raw.get("summary", "")),
            root_cause=str(raw.get("root_cause", "")),
            points=[
                RejectionPoint.from_dict(item)
                for item in raw.get("points", [])
                if isinstance(item, dict)
            ],
            skills_used=[str(item) for item in raw.get("skills_used", [])],
            skills_missing=[str(item) for item in raw.get("skills_missing", [])],
            edits=[
                ProposedEdit.from_dict(item)
                for item in raw.get("edits", [])
                if isinstance(item, dict)
            ],
            analysis_error=str(raw.get("analysis_error", "")),
        )


def _edit_from_payload(raw: dict[str, Any]) -> ProposedEdit | None:
    """Прийняти правку лише для відомої цілі та реальної зміни тексту."""
    target = str(raw.get("target", "")).strip()
    if target not in EDIT_TARGETS:
        return None
    before = str(raw.get("before", ""))
    after = str(raw.get("after", ""))
    if before == after:
        return None
    return ProposedEdit(
        target=target,
        label=str(raw.get("label", "")).strip() or "Правка",
        rationale=str(raw.get("rationale", "")),
        before=before,
        after=after,
        path=str(raw.get("path", "")),
        node_id=str(raw.get("node_id", "")),
        task_id=str(raw.get("task_id", "")),
        skill=str(raw.get("skill", "")),
    )


def parse_report(
    payload: Any,
    *,
    node_id: str,
    node_title: str,
    task_id: str,
    task_title: str,
    workflow_name: str,
    attempt: int,
    threshold: int,
    reason: str,
    must_fix: list[str],
    skills_used: list[str],
) -> CalibrationReport:
    """Скласти звіт із відповіді агента, не даючи їй завалити Flow."""
    report = CalibrationReport(
        node_id=node_id,
        node_title=node_title,
        task_id=task_id,
        task_title=task_title,
        workflow_name=workflow_name,
        attempt=attempt,
        threshold=threshold,
        verdict_reason=reason,
        must_fix=list(must_fix),
        skills_used=list(skills_used),
    )
    if not isinstance(payload, dict):
        report.analysis_error = (
            "Агент відповів не за схемою — показано лише вердикт рев'ювера"
        )
    else:
        report.summary = str(payload.get("summary", "")).strip()
        report.root_cause = str(payload.get("root_cause", "")).strip()
        detected = [
            str(item) for item in payload.get("skills_used", []) if str(item)
        ]
        for name in detected:
            if name not in report.skills_used:
                report.skills_used.append(name)
        report.skills_missing = [
            str(item) for item in payload.get("skills_missing", []) if str(item)
        ]
        report.points = [
            RejectionPoint.from_dict(item)
            for item in payload.get("points", [])
            if isinstance(item, dict)
        ]
        for item in payload.get("edits", []):
            if not isinstance(item, dict):
                continue
            edit = _edit_from_payload(item)
            if edit is not None:
                report.edits.append(edit)
    if not report.points:
        report.points = [
            RejectionPoint(title=str(item)) for item in must_fix if str(item)
        ]
    if not report.points and reason.strip():
        report.points = [RejectionPoint(title=reason.strip())]
    return report


def save_report(report: CalibrationReport, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / CALIBRATION_FILE
    target.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def load_report(directory: Path) -> CalibrationReport | None:
    try:
        payload = json.loads(
            (directory / CALIBRATION_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return CalibrationReport.from_dict(payload)
