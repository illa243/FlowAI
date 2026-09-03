from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

BLOCKING_CATEGORIES = frozenset(
    {"visual_mismatch", "technical_blocker", "missing_requirement"}
)
BLOCKING_SEVERITIES = frozenset({"blocking", "blocker", "critical", "error"})
PASS_STATUSES = frozenset({"pass", "passed", "ok", "success", "true"})
FAIL_STATUSES = frozenset({"fail", "failed", "error", "false", "blocking"})


def task_review_contract_rules(pass_threshold: int) -> str:
    """Keep reviewer instructions aligned with the enforced verdict rules."""
    return (
        "score — integer 0..100; verdict — boolean. verdict=true можливий лише "
        f"за score >= {pass_threshold}, порожнього must_fix, без failed checks "
        "і blocking issues. Issue є блокувальним, якщо його severity входить у "
        f"[{', '.join(sorted(BLOCKING_SEVERITIES))}] АБО category входить у "
        f"[{', '.join(sorted(BLOCKING_CATEGORIES))}]. Ці категорії блокують "
        "прийняття навіть із severity=warning або info. Якщо вимогу не виконано, "
        "поверни verdict=false і конкретну правку; не знижуй severity й не "
        "перейменовуй category лише для отримання true. Необов'язкові поради, "
        "які не є порушенням вимог, можна описати в reason. verdict=false "
        "має містити issue або system_error."
    )


class QAContractError(ValueError):
    """A reviewer response is internally inconsistent or incomplete."""

    def __init__(self, errors: Iterable[str], payload: Any = None) -> None:
        self.errors = [str(item) for item in errors if str(item)]
        self.payload = payload
        super().__init__("; ".join(self.errors) or "Некоректний QA contract")


class OperationIntentError(ValueError):
    """An iterative tool operation does not match the active retry contract."""

    def __init__(self, errors: Iterable[str], payload: Any = None) -> None:
        self.errors = [str(item) for item in errors if str(item)]
        self.payload = payload
        super().__init__("; ".join(self.errors) or "Некоректний operation intent")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest().upper()


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _slug(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").casefold().strip()
    text = re.sub(r"[^a-z0-9а-яіїєґ_.-]+", "-", text, flags=re.IGNORECASE)
    return text.strip("-")[:80] or fallback


_RULE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("patch_seam", ("seam", "шов", "стик", "boundary", "межа патч")),
    ("ghost_fragment", ("ghost", "привид", "залишок", "fragment", "артефакт об'єкт")),
    ("paving_continuity", ("paving", "бруків", "ритм", "scale", "масштаб покрит")),
    ("curb_continuity", ("curb", "бордюр", "край дороги")),
    ("shadow", ("shadow", "тін", "alpha тін")),
    ("registration", ("registration", "реєстрац", "координат", "overlay")),
    ("padding", ("padding", "відступ")),
    ("missing_file", ("missing file", "файл відсут", "не знайден")),
    ("broken_link", ("broken link", "битий link", "відсутнє посилан")),
    ("missing_state", ("missing state", "стан відсут", "немає стан")),
    ("editable_text", ("editable text", "редагован", "текстовий шар")),
)


def infer_rule_id(issue: dict[str, Any]) -> str:
    explicit = str(issue.get("rule_id") or issue.get("check_id") or "").strip()
    if explicit:
        return _slug(explicit)
    haystack = " ".join(
        str(issue.get(key) or "")
        for key in ("description", "must_fix", "reason", "category")
    ).casefold()
    for rule, keywords in _RULE_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return rule
    return _slug(issue.get("category"), "requirement")


def infer_legacy_category(text: str) -> str:
    folded = text.casefold()
    if "qa_final_manifest" in folded or "validator_" in folded:
        return "artifact_integrity"
    engine_markers = (
        "progress.json",
        "root progress",
        "checkpoint",
        "receipt",
        "квитанц",
        "status-пол",
        "статус проходження",
        "next_step_allowed",
    )
    return (
        "engine_state"
        if any(marker in folded for marker in engine_markers)
        else "missing_requirement"
    )


def stable_defect_id(issue: dict[str, Any]) -> str:
    """Build identity from the rule and target, never from mutable prose alone."""

    explicit = str(issue.get("defect_id") or "").strip()
    if explicit and not explicit.upper().startswith("AUTO-"):
        return explicit
    category = _slug(issue.get("category"), "missing-requirement")
    rule = infer_rule_id(issue)
    raw_targets = issue.get("target_files")
    targets = raw_targets if isinstance(raw_targets, list) else []
    logical_targets = sorted(
        {
            _slug(Path(str(path)).name, "file")
            for path in targets
            if str(path).strip()
        }
    )
    raw_regions = issue.get("target_regions")
    regions = raw_regions if isinstance(raw_regions, list) else []
    region_tokens: list[str] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        if region.get("id"):
            region_tokens.append(_slug(region["id"]))
            continue
        coordinates = [region.get(key) for key in ("x", "y", "w", "h")]
        if any(value is not None for value in coordinates):
            region_tokens.append("-".join(str(value or 0) for value in coordinates))
    identity = {
        "category": category,
        "rule": rule,
        "targets": logical_targets or ["task"],
        "regions": sorted(region_tokens),
    }
    digest = payload_hash(identity)[:12]
    return f"AUTO-{category}.{rule}.{digest}"


def normalize_issue(item: dict[str, Any]) -> dict[str, Any]:
    issue = dict(item)
    description = str(
        issue.get("description") or issue.get("must_fix") or issue.get("reason") or ""
    ).strip()
    category = str(issue.get("category") or "missing_requirement").strip().casefold()
    severity = str(
        issue.get("severity")
        or ("warning" if category == "visual_preference" else "blocking")
    ).strip().casefold()
    raw_targets = issue.get("target_files")
    target_files = raw_targets if isinstance(raw_targets, list) else []
    raw_regions = issue.get("target_regions")
    target_regions = raw_regions if isinstance(raw_regions, list) else []
    issue.update(
        {
            "category": category,
            "severity": severity,
            "description": description,
            "must_fix": str(issue.get("must_fix") or description).strip(),
            "target_files": [
                str(path).strip() for path in target_files if str(path).strip()
            ],
            "target_regions": [
                dict(region) for region in target_regions if isinstance(region, dict)
            ],
            "rule_id": infer_rule_id(issue),
        }
    )
    issue["defect_id"] = stable_defect_id(issue)
    return issue


def issue_is_blocking(issue: dict[str, Any]) -> bool:
    return (
        str(issue.get("severity") or "").casefold() in BLOCKING_SEVERITIES
        or str(issue.get("category") or "").casefold() in BLOCKING_CATEGORIES
    )


def normalize_checks(raw: Any) -> list[dict[str, Any]]:
    source = raw if isinstance(raw, list) else []
    checks: list[dict[str, Any]] = []
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            continue
        check = dict(item)
        status = str(check.get("status") or "").strip().casefold()
        if not status and "passed" in check:
            status = "pass" if bool(check.get("passed")) else "fail"
        if status in PASS_STATUSES:
            status = "pass"
        elif status in FAIL_STATUSES:
            status = "fail"
        else:
            status = "unknown"
        raw_files = check.get("target_files") or check.get("files")
        files = raw_files if isinstance(raw_files, list) else []
        check.update(
            {
                "check_id": str(
                    check.get("check_id") or check.get("rule_id") or f"check-{index + 1}"
                ).strip(),
                "status": status,
                "target_files": [
                    str(path).strip() for path in files if str(path).strip()
                ],
            }
        )
        checks.append(check)
    return checks


def normalize_task_review(
    payload: Any,
    *,
    pass_threshold: int = 80,
    strict: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise QAContractError(["QA має повернути JSON object"], payload)
    review = dict(payload)
    errors: list[str] = []

    raw_verdict = review.get("verdict")
    if not isinstance(raw_verdict, bool):
        errors.append("verdict має бути boolean")
    verdict = raw_verdict if isinstance(raw_verdict, bool) else bool(raw_verdict)

    raw_score = review.get("score")
    score: int | None = None
    if isinstance(raw_score, bool):
        errors.append("score має бути цілим числом 0–100")
    else:
        try:
            numeric = float(raw_score)
            if numeric.is_integer():
                score = int(numeric)
            else:
                errors.append("score має бути цілим числом")
        except (TypeError, ValueError):
            if strict:
                errors.append("score є обов'язковим числом 0–100")
    if score is None and not strict:
        score = pass_threshold if verdict else 0
    if score is not None and not 0 <= score <= 100:
        errors.append("score має бути в межах 0–100")

    raw_issues = review.get("issues")
    issues = [
        normalize_issue(item)
        for item in (raw_issues if isinstance(raw_issues, list) else [])
        if isinstance(item, dict)
    ]
    raw_fixes = review.get("must_fix")
    fixes = raw_fixes if isinstance(raw_fixes, list) else []
    must_fix: list[Any] = []
    for item in fixes:
        if isinstance(item, dict):
            must_fix.append(dict(item))
        elif str(item).strip():
            must_fix.append(str(item).strip())

    # Compatibility: old Flow schemas had only reason/must_fix. Convert them to
    # structured issues before enforcing the new invariant.
    if not verdict and not issues:
        legacy: list[Any] = must_fix or (
            [str(review.get("reason") or "").strip()]
            if str(review.get("reason") or "").strip()
            else []
        )
        issues = []
        for value in legacy:
            if isinstance(value, dict):
                issue = dict(value)
                issue.setdefault("defect_id", issue.get("id"))
                issue.setdefault(
                    "category",
                    "visual_mismatch"
                    if str(issue.get("type") or "").casefold() == "visual"
                    else "missing_requirement",
                )
                issue.setdefault("severity", "blocking")
                issue.setdefault(
                    "description",
                    issue.get("must_fix")
                    or issue.get("allowed_change")
                    or issue.get("acceptance")
                    or issue.get("id")
                    or "Structured must-fix",
                )
                bbox = issue.get("bbox")
                if isinstance(bbox, list) and len(bbox) == 4:
                    issue.setdefault(
                        "target_regions",
                        [
                            {
                                "x": bbox[0],
                                "y": bbox[1],
                                "w": bbox[2],
                                "h": bbox[3],
                            }
                        ],
                    )
                issues.append(normalize_issue(issue))
            else:
                text = str(value).strip()
                if text:
                    issues.append(
                        normalize_issue(
                            {
                                "category": infer_legacy_category(text),
                                "severity": "blocking",
                                "description": text,
                                "must_fix": text,
                            }
                        )
                    )

    checks = normalize_checks(review.get("checks"))
    blocking = [issue for issue in issues if issue_is_blocking(issue)]
    failing_checks = [check for check in checks if check["status"] == "fail"]
    if verdict:
        if score is not None and score < pass_threshold:
            errors.append(
                f"verdict=true вимагає score >= pass_threshold ({pass_threshold})"
            )
        if blocking:
            errors.append("verdict=true не може містити blocking issues")
        if must_fix:
            errors.append("verdict=true вимагає порожній must_fix")
        if failing_checks:
            errors.append("verdict=true не може містити failed checks")
    elif strict and not issues and not review.get("system_error"):
        errors.append("verdict=false вимагає хоча б один issue або system_error")

    if errors:
        raise QAContractError(errors, payload)

    evidence = review.get("evidence_files")
    evidence_files = evidence if isinstance(evidence, list) else []
    review.update(
        {
            "verdict": verdict,
            "score": int(score or 0),
            "pass_threshold": int(pass_threshold),
            "issues": issues,
            "must_fix": must_fix,
            "checks": checks,
            "evidence_files": [
                str(path).strip() for path in evidence_files if str(path).strip()
            ],
        }
    )
    review["qa_contract_hash"] = payload_hash(
        {
            key: review.get(key)
            for key in (
                "verdict",
                "score",
                "pass_threshold",
                "issues",
                "must_fix",
                "checks",
                "evidence_files",
            )
        }
    )
    return review


def _resolve_workspace_file(raw: str, workspace: Path) -> Path | None:
    if not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace / path
    try:
        resolved = path.resolve()
        resolved.relative_to(workspace.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def build_retry_contract(
    review: dict[str, Any],
    *,
    workspace: Path,
    task_id: str,
    source_qa_run_id: str = "",
    previous_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    all_issues = [
        normalize_issue(item)
        for item in review.get("issues", [])
        if isinstance(item, dict) and issue_is_blocking(item)
    ]
    system_issues = [
        issue
        for issue in all_issues
        if issue.get("category") in {"engine_state", "tool_failure"}
    ]
    issues = [issue for issue in all_issues if issue not in system_issues]
    checks = normalize_checks(review.get("checks"))
    failed_checks = [
        str(check["check_id"]) for check in checks if check["status"] == "fail"
    ]
    if not failed_checks:
        failed_checks = list(
            dict.fromkeys(
                f"{issue['category']}.{issue['rule_id']}" for issue in issues
            )
        )
    protected_checks = [
        str(check["check_id"]) for check in checks if check["status"] == "pass"
    ]
    editable_files = list(
        dict.fromkeys(
            str(path)
            for issue in issues
            for path in issue.get("target_files", [])
            if str(path)
        )
    )
    passed_files = list(
        dict.fromkeys(
            str(path)
            for check in checks
            if check["status"] == "pass"
            for path in check.get("target_files", [])
            if str(path)
        )
    )
    immutable_files = [path for path in passed_files if path not in editable_files]
    immutable_hashes: dict[str, str] = {}
    for raw in immutable_files:
        path = _resolve_workspace_file(raw, workspace)
        if path is not None:
            immutable_hashes[str(path)] = file_sha256(path)
    contract: dict[str, Any] = {
        "version": 1,
        "task_id": task_id,
        "source_qa_run_id": source_qa_run_id,
        "failed_checks": failed_checks,
        "protected_passed_checks": protected_checks,
        "issues": issues,
        "system_issues": system_issues,
        "editable_files": editable_files,
        "editable_regions": [
            dict(region)
            for issue in issues
            for region in issue.get("target_regions", [])
            if isinstance(region, dict)
        ],
        "immutable_files": immutable_files,
        "immutable_hashes": immutable_hashes,
        "required_outputs": editable_files,
        "acceptance_checks": failed_checks,
        "previous_contract_hash": str(
            (previous_contract or {}).get("retry_contract_hash") or ""
        ),
    }
    contract["retry_contract_hash"] = payload_hash(contract)
    return contract


def protected_artifact_regressions(
    contract: dict[str, Any], workspace: Path
) -> list[dict[str, str]]:
    regressions: list[dict[str, str]] = []
    expected = contract.get("immutable_hashes")
    if not isinstance(expected, dict):
        return regressions
    for raw, wanted in expected.items():
        path = _resolve_workspace_file(str(raw), workspace)
        actual = file_sha256(path) if path is not None else "MISSING"
        if actual.casefold() != str(wanted).casefold():
            regressions.append(
                {"path": str(raw), "expected_sha256": str(wanted), "actual_sha256": actual}
            )
    return regressions


def _workspace_path(raw: Any, workspace: Path) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(workspace.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved


def validate_operation_intent(
    payload: Any,
    *,
    contract: dict[str, Any],
    workspace: Path,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the declared objective before an expensive local script starts."""

    if not isinstance(payload, dict):
        raise OperationIntentError(["operation_intent має бути JSON object"], payload)
    intent = dict(payload)
    errors: list[str] = []
    failed_checks = {
        str(item).strip() for item in contract.get("failed_checks", []) if str(item).strip()
    }
    protected_checks = {
        str(item).strip()
        for item in contract.get("protected_passed_checks", [])
        if str(item).strip()
    }
    target_check = str(intent.get("target_check") or "").strip()
    if not target_check:
        errors.append("target_check є обов'язковим")
    elif target_check not in failed_checks:
        if target_check in protected_checks:
            errors.append(f"target_check {target_check} уже має PASS і захищений")
        else:
            errors.append(f"target_check {target_check} відсутній у failed_checks")

    expected_contract_hash = str(contract.get("retry_contract_hash") or "")
    declared_contract_hash = str(intent.get("retry_contract_hash") or "")
    if declared_contract_hash != expected_contract_hash:
        errors.append("retry_contract_hash не відповідає активному контракту")

    raw_outputs = intent.get("output_files")
    output_values = raw_outputs if isinstance(raw_outputs, list) else []
    output_paths: list[Path] = []
    if not output_values:
        errors.append("output_files має містити хоча б один шлях")
    for raw in output_values:
        resolved = _workspace_path(raw, workspace)
        if resolved is None:
            errors.append(f"output поза workspace або невалідний: {raw}")
        else:
            output_paths.append(resolved)

    immutable_paths = {
        str(path).casefold()
        for raw in contract.get("immutable_files", [])
        if (path := _workspace_path(raw, workspace)) is not None
    }
    editable_paths = {
        str(path).casefold()
        for raw in contract.get("editable_files", [])
        if (path := _workspace_path(raw, workspace)) is not None
    }
    for output in output_paths:
        if str(output).casefold() in immutable_paths:
            errors.append(f"output захищений immutable contract: {output}")
        if editable_paths and str(output).casefold() not in editable_paths:
            errors.append(f"output відсутній у editable_files: {output}")

    metric = str(intent.get("metric") or "").strip()
    if not metric:
        errors.append("metric є обов'язковим")
    if intent.get("acceptable_threshold") is None:
        errors.append("acceptable_threshold є обов'язковим")

    configured = policy if isinstance(policy, dict) else {}
    maximum = max(1, int(configured.get("max_iterations", 500)))
    try:
        max_operations = int(intent.get("max_operations"))
    except (TypeError, ValueError):
        max_operations = 0
    if max_operations < 1:
        errors.append("max_operations має бути додатним integer")
    elif max_operations > maximum:
        errors.append(
            f"max_operations {max_operations} перевищує policy limit {maximum}"
        )

    for field, fallback in (
        ("no_improvement_patience", configured.get("no_improvement_patience", 50)),
        ("checkpoint_every", configured.get("checkpoint_every", 10)),
    ):
        try:
            value = int(intent.get(field, fallback))
        except (TypeError, ValueError):
            value = 0
        if value < 1:
            errors.append(f"{field} має бути додатним integer")
        intent[field] = value

    if errors:
        raise OperationIntentError(errors, payload)
    intent.update(
        {
            "target_check": target_check,
            "output_files": [str(path) for path in output_paths],
            "metric": metric,
            "max_operations": max_operations,
            "retry_contract_hash": expected_contract_hash,
        }
    )
    intent.pop("operation_intent_hash", None)
    intent["operation_intent_hash"] = payload_hash(intent)
    return intent


_ITERATION_PROGRESS = re.compile(
    r"progress\s+iteration=(?P<iteration>\d+)(?:/(?P<maximum>\d+))?"
    r"(?:.*?best=(?P<best>[^\r\n]+))?",
    flags=re.IGNORECASE,
)


def operation_progress_from_activity(value: Any) -> dict[str, Any]:
    """Extract structured progress printed by a cancelable iterative script."""

    strings: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    for item in strings:
        match = _ITERATION_PROGRESS.search(item)
        if not match:
            continue
        return {
            "iteration": int(match.group("iteration")),
            "max_iterations": int(match.group("maximum") or 0),
            "best_metric": str(match.group("best") or "").strip(),
        }
    return {}


@dataclass(slots=True)
class ConvergencePolicy:
    max_iterations: int = 500
    no_improvement_patience: int = 50
    min_delta: float = 0.0001
    acceptable_threshold: float | None = None
    direction: str = "minimize"
    checkpoint_every: int = 10

    @classmethod
    def from_dict(cls, raw: Any) -> ConvergencePolicy:
        data = raw if isinstance(raw, dict) else {}
        threshold = data.get("acceptable_threshold")
        return cls(
            max_iterations=max(1, int(data.get("max_iterations", 500))),
            no_improvement_patience=max(
                1, int(data.get("no_improvement_patience", 50))
            ),
            min_delta=max(0.0, float(data.get("min_delta", 0.0001))),
            acceptable_threshold=(
                float(threshold) if threshold is not None and threshold != "" else None
            ),
            direction=(
                "maximize" if str(data.get("direction")) == "maximize" else "minimize"
            ),
            checkpoint_every=max(1, int(data.get("checkpoint_every", 10))),
        )


class ConvergenceTracker:
    def __init__(self, policy: ConvergencePolicy) -> None:
        self.policy = policy
        self.iteration = 0
        self.best_metric: float | None = None
        self.best_iteration = 0

    def observe(self, metric: float) -> dict[str, Any]:
        self.iteration += 1
        improved = self.best_metric is None
        if self.best_metric is not None:
            delta = (
                metric - self.best_metric
                if self.policy.direction == "maximize"
                else self.best_metric - metric
            )
            improved = delta >= self.policy.min_delta
        if improved:
            self.best_metric = float(metric)
            self.best_iteration = self.iteration
        target_reached = False
        if self.policy.acceptable_threshold is not None:
            target_reached = (
                metric >= self.policy.acceptable_threshold
                if self.policy.direction == "maximize"
                else metric <= self.policy.acceptable_threshold
            )
        stalled = (
            self.iteration - self.best_iteration
            >= self.policy.no_improvement_patience
        )
        exhausted = self.iteration >= self.policy.max_iterations
        return {
            "iteration": self.iteration,
            "metric": float(metric),
            "best_metric": self.best_metric,
            "best_iteration": self.best_iteration,
            "improved": improved,
            "checkpoint_due": self.iteration % self.policy.checkpoint_every == 0,
            "target_reached": target_reached,
            "stalled": stalled,
            "exhausted": exhausted,
            "should_stop": target_reached or stalled or exhausted,
            "stop_reason": (
                "target_reached"
                if target_reached
                else "no_improvement"
                if stalled
                else "operation_budget_exhausted"
                if exhausted
                else ""
            ),
        }
