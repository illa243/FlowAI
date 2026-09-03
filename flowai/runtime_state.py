from __future__ import annotations

import json
import os
import struct
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

_ATOMIC_WRITE_LOCK = threading.RLock()

# Оточення агента, а не матеріал проєкту: віртуальне середовище, кеші пакетів
# і кеші інструментів. Поки вони рахувалися артефактами, кожна read-only нода
# хешувала сотні мегабайтів чужих файлів, а звичайний `uv pip` виглядав як
# порушення пісочниці й зупиняв Flow.
IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        ".uv",
        ".uv-cache",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        "__pycache__",
        "node_modules",
    }
)


def atomic_write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with _ATOMIC_WRITE_LOCK:
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
            for attempt in range(6):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt >= 5:
                        raise
                    time.sleep(0.01 * (2**attempt))
        finally:
            with suppress(OSError):
                temporary.unlink()
    return path


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


@dataclass(frozen=True, slots=True)
class FileStamp:
    size: int
    mtime_ns: int
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"size": self.size, "mtime_ns": self.mtime_ns, "sha256": self.sha256}


def snapshot_workspace(
    workspace: Path,
    *,
    hash_all: bool = False,
    ignore_runtime: bool = False,
) -> dict[str, FileStamp]:
    root = workspace.resolve()
    snapshot: dict[str, FileStamp] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if any(
            part.casefold() in IGNORED_DIRECTORIES
            for part in path.relative_to(root).parts[:-1]
        ):
            continue
        lowered = relative.casefold()
        runtime_name = path.name.casefold()
        if ignore_runtime and (
            lowered.startswith(".flowai/runtime/")
            or runtime_name in {"flowai-checkpoint.json", "flowai-run.json"}
            or (
                runtime_name.startswith(
                    (".flowai-checkpoint.json.", ".flowai-run.json.")
                )
                and runtime_name.endswith(".tmp")
            )
        ):
            continue
        try:
            stat = path.stat()
            digest = file_sha256(path) if hash_all else ""
        except OSError:
            continue
        snapshot[relative] = FileStamp(stat.st_size, stat.st_mtime_ns, digest)
    return snapshot


def diff_workspace(
    workspace: Path,
    before: dict[str, FileStamp],
    *,
    hash_changed: bool = True,
    ignore_runtime: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    root = workspace.resolve()
    after = snapshot_workspace(root, hash_all=False, ignore_runtime=ignore_runtime)
    generated: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    for relative, stamp in after.items():
        prior = before.get(relative)
        if prior is None:
            kind = generated
        elif prior.size != stamp.size or prior.mtime_ns != stamp.mtime_ns:
            kind = modified
        elif prior.sha256:
            path = root / Path(relative)
            try:
                changed_hash = file_sha256(path)
            except OSError:
                continue
            if changed_hash != prior.sha256:
                kind = modified
            else:
                continue
        else:
            continue
        path = root / Path(relative)
        try:
            digest = file_sha256(path) if hash_changed else ""
        except OSError:
            # An atomic writer may have renamed its temporary file after the
            # snapshot. It is runtime noise, not a user artifact mutation.
            continue
        kind.append(
            {
                "path": str(path),
                "relative_path": relative,
                "size_bytes": stamp.size,
                "mtime_ns": stamp.mtime_ns,
                "sha256": digest,
            }
        )
    for relative, stamp in before.items():
        if relative not in after:
            deleted.append(
                {
                    "path": str(root / Path(relative)),
                    "relative_path": relative,
                    "size_bytes": stamp.size,
                    "mtime_ns": stamp.mtime_ns,
                    "sha256": stamp.sha256,
                }
            )
    return {"generated": generated, "modified": modified, "deleted": deleted}


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as stream:
            header = stream.read(24)
        if header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR":
            return struct.unpack(">II", header[16:24])
    except OSError:
        return None
    return None


def file_evidence(path: Path) -> dict[str, Any]:
    stat = path.stat()
    evidence: dict[str, Any] = {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": file_sha256(path),
        "suffix": path.suffix.casefold(),
    }
    dimensions = _png_dimensions(path)
    if dimensions is not None:
        evidence["width"], evidence["height"] = dimensions
    return evidence


def qa_packet(
    workspace: Path,
    files: list[str],
    *,
    task_id: str = "",
    attempt_id: str = "",
    validator_version: str = "1",
) -> dict[str, Any]:
    root = workspace.resolve()
    evidence: list[dict[str, Any]] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw in files:
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = root / path
        try:
            path = path.resolve()
            path.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            evidence.append(file_evidence(path))
        else:
            missing.append(str(path))
    payload: dict[str, Any] = {
        "version": 1,
        "validator_version": validator_version,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "files": evidence,
        "missing_files": missing,
    }
    payload["packet_hash"] = sha256(
        json.dumps(
            _packet_identity(payload), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest().upper()
    return payload


# Час зміни — це метадані, а не вміст. Поки `mtime_ns` входив у матеріал
# хеша, байт-ідентичний результат щоразу давав новий `packet_hash`, і
# детермінований QA-кеш не міг влучити жодного разу.
_VOLATILE_EVIDENCE_FIELDS = frozenset({"mtime_ns"})


def _packet_identity(payload: dict[str, Any]) -> dict[str, Any]:
    """Матеріал хеша: лише те, що справді описує перевірювані файли."""
    # Номер спроби теж не описує файли: якщо Executor нічого не змінив,
    # QA не має перевіряти той самий результат заново.
    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"files", "attempt_id"}
    }
    identity["files"] = [
        {
            key: value
            for key, value in evidence.items()
            if key not in _VOLATILE_EVIDENCE_FIELDS
        }
        for evidence in payload["files"]
    ]
    return identity


def write_audit_report(
    workspace: Path,
    *,
    node_id: str,
    mutation: dict[str, Any],
) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    target = (
        workspace.resolve()
        / ".flowai"
        / "runtime"
        / "mutation-audits"
        / f"{stamp}-{node_id}.json"
    )
    return atomic_write_json(target, mutation)


class JsonArtifactCache:
    def __init__(self, workspace: Path, namespace: str) -> None:
        self.directory = (
            workspace.resolve() / ".flowai" / "runtime" / namespace
        )

    def path(self, key: str) -> Path:
        safe = "".join(character for character in key if character.isalnum() or character in "-_")
        return self.directory / f"{safe[:160]}.json"

    def load(self, key: str) -> dict[str, Any] | None:
        try:
            value = json.loads(self.path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def save(self, key: str, value: dict[str, Any]) -> Path:
        path = self.path(key)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
        # Content-addressed packets must keep their mtime as well as their
        # content. Rewriting an identical packet would invalidate the caller's
        # attachment fingerprint and turn every QA cache lookup into a miss.
        if current == value:
            return path
        return atomic_write_json(path, value)


def write_versioned_attempt_manifest(
    workspace: Path,
    *,
    task_id: str,
    attempt_number: int,
    manifest: dict[str, Any],
) -> tuple[Path, Path]:
    root = workspace.resolve()
    task_slug = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in str(task_id)
    ).strip("-") or "flow"
    attempt_id = f"attempt-{max(1, int(attempt_number)):03d}"
    directory = root / ".flowai" / "runtime" / "attempts" / task_slug / attempt_id
    enriched = dict(manifest)
    enriched.update(
        {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
    )
    manifest_path = atomic_write_json(directory / "manifest.json", enriched)
    current_path = atomic_write_json(
        directory.parent / "current.json",
        {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "manifest_path": str(manifest_path),
            "manifest_sha256": file_sha256(manifest_path),
        },
    )
    return manifest_path, current_path


def index_legacy_attempt_files(
    workspace: Path,
    *,
    task_id: str,
    artifact_directory: Path,
    current_names: list[str] | None = None,
) -> tuple[Path, Path]:
    """Index legacy artifacts in place without moving, deleting or guessing age."""

    root = workspace.resolve()
    directory = artifact_directory.resolve()
    directory.relative_to(root)
    selected = {str(name).casefold() for name in current_names or []}
    records: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file():
            continue
        name_folded = path.name.casefold()
        if name_folded in selected:
            role = "current"
        elif "accepted" in name_folded or "snapshot" in name_folded:
            role = "protected_snapshot"
        elif "candidate" in name_folded or "retry" in name_folded:
            role = "legacy_candidate"
        else:
            role = "supporting"
        stat = path.stat()
        records.append(
            {
                "path": str(path),
                "name": path.name,
                "role": role,
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": file_sha256(path),
            }
        )
    payload = {
        "task_id": task_id,
        "indexed_at": datetime.now(UTC).isoformat(),
        "artifact_directory": str(directory),
        "migration_policy": "indexed_in_place; no files moved or deleted",
        "files": records,
    }
    task_slug = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in str(task_id)
    ).strip("-") or "flow"
    runtime_directory = root / ".flowai" / "runtime" / "attempts" / task_slug
    index_path = atomic_write_json(runtime_directory / "legacy-index.json", payload)
    local_index = atomic_write_json(
        directory / "attempts" / "legacy-index.json", payload
    )
    current = [record for record in records if record["role"] == "current"]
    current_path = atomic_write_json(
        runtime_directory / "current.json",
        {
            "task_id": task_id,
            "attempt_id": "legacy-indexed",
            "legacy_index_path": str(index_path),
            "artifact_index_path": str(local_index),
            "current_files": current,
        },
    )
    return index_path, current_path
