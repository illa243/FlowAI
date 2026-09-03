from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .quality_control import normalize_issue

CONFIRMATION_MODES = frozenset(
    {"standard", "plan_approval", "variant_selection", "asset_approval"}
)
QA_BLOCKING_CATEGORIES = frozenset(
    {"visual_mismatch", "technical_blocker", "missing_requirement"}
)
REFERENCE_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
)


class PhotoshopAutomationError(RuntimeError):
    """Photoshop cannot create or validate the requested PSD."""


class ReferenceAnalysisCacheError(RuntimeError):
    """The one-time UI reference analysis is missing or no longer current."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest().upper()


def _external_or_workspace_path(raw: Any, workspace: Path) -> Path:
    raw_text = str(raw or "").strip()
    if not raw_text:
        raise ReferenceAnalysisCacheError("Не вказано шлях кешу UI-референсів")
    path = Path(raw_text).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _reference_library_sha256(files: list[dict[str, Any]]) -> str:
    digest = sha256()
    for item in files:
        digest.update(str(item["relative_path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest().lower()


def validate_reference_analysis_cache(
    config: dict[str, Any], workspace: Path
) -> dict[str, Any]:
    """Verify a cached corpus analysis by file-level SHA-256.

    The expensive visual interpretation is deliberately not repeated. Reading and
    hashing the source files only proves that the written analysis still describes
    the exact same corpus.
    """

    source_dir = _external_or_workspace_path(config.get("source_dir"), workspace)
    manifest_path = _external_or_workspace_path(
        config.get("manifest_path"), workspace
    )
    analysis_path = _external_or_workspace_path(
        config.get("analysis_path"), workspace
    )
    if not source_dir.is_dir():
        raise ReferenceAnalysisCacheError(
            f"Тека UI-референсів не існує: {source_dir}"
        )
    if not manifest_path.is_file():
        raise ReferenceAnalysisCacheError(
            f"Manifest аналізу UI-референсів не знайдено: {manifest_path}"
        )
    if not analysis_path.is_file():
        raise ReferenceAnalysisCacheError(
            f"Записаний аналіз UI-референсів не знайдено: {analysis_path}"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReferenceAnalysisCacheError(
            f"Manifest UI-референсів пошкоджений: {manifest_path}"
        ) from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ReferenceAnalysisCacheError("Manifest UI-референсів має невірну схему")

    current_paths = sorted(
        (
            path
            for path in source_dir.rglob("*")
            if path.is_file() and path.suffix.casefold() in REFERENCE_IMAGE_SUFFIXES
        ),
        key=lambda path: path.relative_to(source_dir).as_posix().casefold(),
    )
    manifest_files = {
        str(item.get("relative_path") or ""): item
        for item in manifest["files"]
        if isinstance(item, dict) and str(item.get("relative_path") or "")
    }
    current_names = [path.relative_to(source_dir).as_posix() for path in current_paths]
    if set(current_names) != set(manifest_files):
        added = sorted(set(current_names) - set(manifest_files))
        removed = sorted(set(manifest_files) - set(current_names))
        details: list[str] = []
        if added:
            details.append("додано: " + ", ".join(added[:5]))
        if removed:
            details.append("видалено: " + ", ".join(removed[:5]))
        raise ReferenceAnalysisCacheError(
            "Набір UI-референсів змінився (" + "; ".join(details) + ")"
        )

    verified: list[dict[str, Any]] = []
    changed: list[str] = []
    for path, relative in zip(current_paths, current_names, strict=True):
        actual = _file_sha256(path)
        expected = str(manifest_files[relative].get("sha256") or "").lower()
        if not expected or actual != expected:
            changed.append(relative)
        verified.append({"relative_path": relative, "sha256": actual})
    if changed:
        raise ReferenceAnalysisCacheError(
            "Вміст UI-референсів змінився: " + ", ".join(changed[:5])
        )

    actual_library_hash = _reference_library_sha256(verified)
    manifest_library_hash = str(manifest.get("library_sha256") or "").lower()
    configured_hash = str(config.get("library_sha256") or "").lower()
    if actual_library_hash != manifest_library_hash or (
        configured_hash and actual_library_hash != configured_hash
    ):
        raise ReferenceAnalysisCacheError(
            "SHA-256 бібліотеки UI-референсів не відповідає записаному аналізу"
        )
    try:
        analysis = analysis_path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise ReferenceAnalysisCacheError(
            f"Не вдалося прочитати аналіз UI-референсів: {analysis_path}"
        ) from exc
    if not analysis or actual_library_hash not in analysis.casefold():
        raise ReferenceAnalysisCacheError(
            "Файл аналізу не містить SHA-256 поточної бібліотеки референсів"
        )
    return {
        "source_dir": str(source_dir),
        "manifest_path": str(manifest_path),
        "analysis_path": str(analysis_path),
        "file_count": len(verified),
        "library_sha256": actual_library_hash,
    }


def normalize_confirmation_mode(value: Any) -> str:
    mode = str(value or "standard").strip().lower()
    return mode if mode in CONFIRMATION_MODES else "standard"


def normalize_confirmation_ports(value: Any) -> list[str]:
    if isinstance(value, str):
        source = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        source = value
    else:
        source = ("true", "false")
    ports: list[str] = []
    for item in source:
        port = str(item).strip().lower()
        if port in {"true", "false", "exhausted"} and port not in ports:
            ports.append(port)
    return ports or ["true", "false"]


def find_ui_plan(value: Any) -> dict[str, Any]:
    """Find an approved/UI plan inside a nested Result or agent payload."""

    seen: set[int] = set()

    def visit(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            identity = id(item)
            if identity in seen:
                return {}
            seen.add(identity)
            for key in ("approved_plan", "ui_project_spec"):
                candidate = item.get(key)
                if isinstance(candidate, dict) and isinstance(candidate.get("tasks"), list):
                    return dict(candidate)
            if isinstance(item.get("tasks"), list) and any(
                isinstance(task, dict) and str(task.get("prompt", "")).strip()
                for task in item["tasks"]
            ):
                return dict(item)
            for nested in item.values():
                candidate = visit(nested)
                if candidate:
                    return candidate
        elif isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in seen:
                return {}
            seen.add(identity)
            for nested in item:
                candidate = visit(nested)
                if candidate:
                    return candidate
        return {}

    return visit(value)


def normalize_ui_tasks(raw: Any) -> list[dict[str, Any]]:
    """Keep UI task metadata while enforcing the generic managed-task contract."""

    source = raw if isinstance(raw, list) else []
    result: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, item in enumerate(source, 1):
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt", "")).strip()
        if not prompt:
            continue
        task_id = str(item.get("id") or f"ui-task-{index:02d}").strip()
        if not task_id or task_id in used_ids:
            task_id = f"ui-task-{index:02d}-{payload_sha256(item)[:8].lower()}"
        used_ids.add(task_id)
        task = dict(item)
        task.update(
            {
                "id": task_id,
                "title": str(item.get("title") or "").strip(),
                "prompt": prompt,
                "screen": str(item.get("screen") or "").strip(),
                "states": [
                    str(state).strip()
                    for state in item.get("states", [])
                    if str(state).strip()
                ],
                "acceptance_criteria": [
                    str(rule).strip()
                    for rule in item.get("acceptance_criteria", [])
                    if str(rule).strip()
                ],
                "attachments": [
                    str(path).strip()
                    for path in item.get("attachments", [])
                    if str(path).strip()
                ],
                "export_profile": str(
                    item.get("export_profile") or "baseline"
                ).strip(),
            }
        )
        result.append(task)
    return result


def find_variants(value: Any) -> list[dict[str, Any]]:
    """Return a stable V01..V04 manifest from nested executor/QA data."""

    seen: set[int] = set()

    def visit(item: Any) -> list[dict[str, Any]]:
        if isinstance(item, dict):
            identity = id(item)
            if identity in seen:
                return []
            seen.add(identity)
            variants = item.get("variants")
            if isinstance(variants, list):
                normalized: list[dict[str, Any]] = []
                for index, variant in enumerate(variants, 1):
                    if not isinstance(variant, dict):
                        continue
                    candidate = dict(variant)
                    candidate["variant_id"] = str(
                        variant.get("variant_id") or f"V{index:02d}"
                    ).upper()
                    candidate["path"] = str(
                        variant.get("path")
                        or variant.get("candidate_path")
                        or variant.get("output_path")
                        or ""
                    ).strip()
                    candidate["direction"] = str(
                        variant.get("direction") or variant.get("description") or ""
                    ).strip()
                    candidate["sha256"] = str(variant.get("sha256") or "").upper()
                    normalized.append(candidate)
                if normalized:
                    return normalized
            for nested in item.values():
                found = visit(nested)
                if found:
                    return found
        elif isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in seen:
                return []
            seen.add(identity)
            for nested in item:
                found = visit(nested)
                if found:
                    return found
        return []

    return visit(value)


def verify_variant_manifest(
    value: Any,
    workspace: Path,
    *,
    previous: dict[str, Any] | None = None,
    retry_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify four project-local PNGs and protect frozen variants by SHA-256."""

    if not isinstance(value, dict):
        raise TypeError("Concept Executor має повернути JSON manifest")
    variants = find_variants(value)
    by_id = {str(item.get("variant_id") or "").upper(): item for item in variants}
    expected = {f"V{index:02d}" for index in range(1, 5)}
    if set(by_id) != expected:
        raise ValueError("Concept round має містити рівно V01, V02, V03 і V04")

    normalized: list[dict[str, Any]] = []
    for variant_id in sorted(expected):
        item = dict(by_id[variant_id])
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            raise ValueError(f"{variant_id} не має path")
        path = workspace_child(workspace, raw_path)
        if not path.is_file() or path.suffix.casefold() != ".png":
            raise ValueError(f"{variant_id} не є наявним PNG у проєкті: {path}")
        actual_hash = sha256(path.read_bytes()).hexdigest().upper()
        reported_hash = str(item.get("sha256") or "").upper()
        if reported_hash and reported_hash != actual_hash:
            raise ValueError(
                f"SHA-256 {variant_id} не відповідає файлу: {reported_hash} != {actual_hash}"
            )
        item["variant_id"] = variant_id
        item["path"] = str(path)
        item["sha256"] = actual_hash
        normalized.append(item)

    context = retry_context or {}
    frozen = context.get("frozen_variants")
    frozen_items = frozen if isinstance(frozen, list) else []
    for prior in frozen_items:
        if not isinstance(prior, dict):
            continue
        variant_id = str(prior.get("variant_id") or "").upper()
        current = next(
            (item for item in normalized if item["variant_id"] == variant_id), None
        )
        if current is None:
            raise ValueError(f"Заморожений {variant_id} зник із нового manifest")
        prior_hash = str(prior.get("sha256") or "").upper()
        if prior_hash and current["sha256"] != prior_hash:
            raise ValueError(f"Executor змінив заморожений {variant_id}")
        prior_path = str(prior.get("path") or "").strip()
        if prior_path and workspace_child(workspace, prior_path) != Path(
            current["path"]
        ).resolve():
            raise ValueError(f"Executor підмінив шлях замороженого {variant_id}")

    enriched = dict(value)
    enriched["variants"] = normalized
    round_id = str(value.get("round_id") or "").strip()
    if not round_id:
        raise ValueError("Concept manifest не має round_id")
    if previous and payload_sha256(previous.get("variants") or []) != payload_sha256(
        normalized
    ):
        previous_round = str(previous.get("round_id") or "")
        if previous_round and previous_round == round_id:
            raise ValueError("Новий concept round не може перезаписувати старий round_id")
    enriched["round_id"] = round_id
    enriched["manifest_sha256"] = payload_sha256(normalized)
    return enriched


def normalized_qa_issues(review: dict[str, Any]) -> list[dict[str, Any]]:
    raw = review.get("issues")
    issues: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            issue = normalize_issue(item)
            if not str(issue.get("description") or "").strip():
                continue
            issues.append(issue)
    if not issues and not bool(review.get("verdict", False)):
        fixes = review.get("must_fix")
        source = fixes if isinstance(fixes, list) else []
        if not source and str(review.get("reason") or "").strip():
            source = [str(review["reason"])]
        for value in source:
            description = str(value).strip()
            if description:
                issues.append(
                    normalize_issue(
                        {
                            "category": "missing_requirement",
                            "severity": "blocking",
                            "description": description,
                            "must_fix": description,
                            "target_files": [],
                        }
                    )
                )
    return issues


def blocking_defect_ids(review: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for issue in normalized_qa_issues(review):
        category = str(issue.get("category") or "")
        severity = str(issue.get("severity") or "blocking")
        if severity == "blocking" or category in QA_BLOCKING_CATEGORIES:
            defect_id = str(issue.get("defect_id") or "")
            if defect_id and defect_id not in ids:
                ids.append(defect_id)
    return ids


def has_non_overridable_issues(review: dict[str, Any]) -> bool:
    return any(
        str(issue.get("category") or "") in QA_BLOCKING_CATEGORIES
        or str(issue.get("severity") or "") == "blocking"
        for issue in normalized_qa_issues(review)
    )


def workspace_child(workspace: Path, relative: str | Path) -> Path:
    root = workspace.resolve()
    candidate = Path(relative)
    if candidate.is_absolute():
        candidate = candidate.resolve()
    else:
        candidate = (root / candidate).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Шлях виходить за межі проєкту: {candidate}") from exc
    return candidate


def validate_declared_output_paths(value: Any, workspace: Path) -> list[str]:
    """Reject declared artifacts outside the project and require them to exist."""

    found: list[str] = []
    scalar_keys = {
        "candidate_path",
        "output_path",
        "manifest_path",
        "composite_preview",
        "report_path",
    }
    list_keys = {"exports", "evidence_files"}

    def add(raw: Any) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        path = workspace_child(workspace, text)
        if not path.is_file():
            raise ValueError(f"Задекларований output не існує: {path}")
        normalized = str(path)
        if normalized not in found:
            found.append(normalized)

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if key in scalar_keys:
                    add(nested)
                elif key in list_keys and isinstance(nested, list):
                    for path_value in nested:
                        add(path_value)
                elif key == "variants" and isinstance(nested, list):
                    for variant in nested:
                        if isinstance(variant, dict):
                            add(variant.get("path"))
                elif isinstance(nested, (dict, list)):
                    visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return found


def append_ui_learning(
    workspace: Path,
    event: dict[str, Any],
    *,
    log_path: str = "learnings/ui_learnings.jsonl",
    profile_path: str = "learnings/ui_project_profile.md",
) -> tuple[Path, Path]:
    """Append one review event and refresh a readable project-local profile."""

    log = workspace_child(workspace, log_path)
    profile = workspace_child(workspace, profile_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    profile.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("timestamp", datetime.now(UTC).isoformat())
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical_json(payload) + "\n")

    events: list[dict[str, Any]] = []
    with log.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                events.append(item)
    recent = events[-50:]
    requirements: list[str] = []
    defects: dict[str, str] = {}
    accepted: list[str] = []
    for item in recent:
        note = str(item.get("user_note") or "").strip()
        if note and note not in requirements:
            requirements.append(note)
        review = item.get("review")
        if isinstance(review, dict):
            for issue in normalized_qa_issues(review):
                defects[str(issue["defect_id"])] = str(issue.get("must_fix") or "")
        if bool(item.get("accepted")):
            artifact = str(item.get("candidate_path") or "").strip()
            if artifact and artifact not in accepted:
                accepted.append(artifact)
    lines = [
        "# UI Project Profile",
        "",
        "Цей файл оновлюється FlowAI з QA та підтверджених рішень користувача.",
        "",
        "## Пріоритет",
        "",
        "Поточні правки користувача → task/spec → цей profile → modern-ui → QA.",
        "",
        "## Активні рішення користувача",
        "",
    ]
    lines.extend(f"- {item}" for item in requirements[-20:])
    if not requirements:
        lines.append("- Поки що немає.")
    lines.extend(["", "## Відомі дефекти та правила", ""])
    lines.extend(f"- `{key}`: {value}" for key, value in sorted(defects.items()))
    if not defects:
        lines.append("- Поки що немає.")
    lines.extend(["", "## Прийняті артефакти", ""])
    lines.extend(f"- {item}" for item in accepted[-20:])
    if not accepted:
        lines.append("- Поки що немає.")
    profile.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return log, profile


class PhotoshopAutomation:
    """Small Windows COM/JSX bridge used for PSD preflight and validation."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.runtime = workspace_child(
            self.workspace, ".flowai/runtime/photoshop"
        )

    @staticmethod
    def executable() -> Path | None:
        candidates = [
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Adobe/Adobe Photoshop 2022/Photoshop.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Adobe/Adobe Photoshop 2023/Photoshop.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Adobe/Adobe Photoshop 2024/Photoshop.exe",
        ]
        return next((path for path in candidates if path.is_file()), None)

    def preflight(self) -> Path:
        if os.name != "nt":
            raise PhotoshopAutomationError("Photoshop automation підтримується лише у Windows")
        executable = self.executable()
        if executable is None:
            raise PhotoshopAutomationError("Adobe Photoshop 2022 або новіший не знайдено")
        self.runtime.mkdir(parents=True, exist_ok=True)
        return executable

    def run_jsx(self, jsx: str, *, name: str = "flowai-photoshop.jsx") -> Path:
        self.preflight()
        jsx_path = workspace_child(self.workspace, self.runtime / name)
        jsx_path.write_text(jsx, encoding="utf-8")
        escaped = str(jsx_path).replace("'", "''")
        command = (
            "$ErrorActionPreference='Stop';"
            "$app=New-Object -ComObject Photoshop.Application;"
            "$app.DisplayDialogs=3;"
            f"$app.DoJavaScriptFile('{escaped}')"
        )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                command,
            ],
            cwd=str(self.workspace),
            capture_output=True,
            text=True,
            creationflags=creation_flags,
            check=False,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "невідома помилка").strip()
            raise PhotoshopAutomationError(f"Photoshop JSX не виконано: {detail}")
        return jsx_path

    def validate_psd(self, psd_path: Path) -> dict[str, Any]:
        psd = workspace_child(self.workspace, psd_path)
        if not psd.is_file() or psd.suffix.lower() != ".psd":
            raise PhotoshopAutomationError(f"PSD не знайдено: {psd}")
        token = sha256(str(psd).encode("utf-8")).hexdigest()[:12]
        report = workspace_child(
            self.workspace, self.runtime / f"validation-{token}.json"
        )
        # Звіт має бути доказом саме цього прогону. Імʼя залежить лише від
        # шляху PSD, тож файл із попередньої перевірки лежав би на місці й
        # пройшов би як свіжий, навіть якби Photoshop нічого не записав.
        report.unlink(missing_ok=True)
        psd_js = str(psd).replace("\\", "/").replace('"', '\\"')
        report_js = str(report).replace("\\", "/").replace('"', '\\"')
        jsx = f'''#target photoshop
app.displayDialogs = DialogModes.NO;
var source = new File("{psd_js}");
var output = new File("{report_js}");
var doc = app.open(source);
var groups = [];
for (var i = 0; i < doc.layerSets.length; i++) groups.push(doc.layerSets[i].name);
var payload = {{
  opened: true,
  width: doc.width.as("px"),
  height: doc.height.as("px"),
  color_mode: String(doc.mode),
  top_level_groups: groups,
  layer_count: doc.layers.length,
  layer_comp_count: doc.layerComps.length
}};
output.encoding = "UTF8";
output.open("w");
output.write(JSON.stringify(payload));
output.close();
doc.close(SaveOptions.DONOTSAVECHANGES);
'''
        self.run_jsx(jsx, name=f"validate-{token}.jsx")
        if not report.is_file():
            raise PhotoshopAutomationError("Photoshop не створив validation report")
        try:
            result = json.loads(report.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PhotoshopAutomationError("Некоректний Photoshop validation report") from exc
        if not isinstance(result, dict) or not result.get("opened"):
            raise PhotoshopAutomationError("Photoshop не зміг повторно відкрити PSD")
        result["psd_path"] = str(psd)
        result["report_path"] = str(report)
        return result
