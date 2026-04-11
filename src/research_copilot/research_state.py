"""File-backed research state helpers for the CLI-first single-user MVP."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
CANONICAL_DIRECTORIES = (
    "goals",
    "experiments",
    "runs",
    "reviews",
    "notes",
    "papers",
    "context",
    "profiles",
    "insights",
)
FILE_BACKED_FAMILIES = frozenset({"experiments", "insights", "papers", "context"})


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def get_research_root() -> Path:
    """Resolve the canonical local research state root."""
    explicit_root = os.getenv("RC_RESEARCH_ROOT", "").strip()
    if explicit_root:
        return Path(explicit_root).expanduser().resolve()
    working_dir = Path(os.getenv("RC_WORKING_DIR", ".")).expanduser()
    return (working_dir / ".omx" / "research").resolve()


def ensure_research_root() -> Path:
    """Ensure the research root and canonical directories exist."""
    root = get_research_root()
    for directory in CANONICAL_DIRECTORIES:
        (root / directory).mkdir(parents=True, exist_ok=True)
    return root


def build_provenance(
    args: dict[str, Any] | None = None,
    *,
    actor: str = "",
    workflow_name: str = "",
    profile_name: str = "",
    related_goal_id: str = "",
    related_experiment_id: str = "",
    related_run_id: str = "",
    related_job_id: str = "",
    content_kind: str = "observed",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a normalized provenance block for persisted artifacts."""
    payload = args or {}
    return {
        "actor": actor or str(payload.get("actor_type") or payload.get("actor") or "system"),
        "timestamp": timestamp or utc_now_iso(),
        "workflow_name": workflow_name or str(payload.get("workflow_name", "")),
        "profile_name": profile_name or str(payload.get("profile_name", "")),
        "related_goal_id": related_goal_id or str(payload.get("linked_goal_id", "")),
        "related_experiment_id": related_experiment_id
        or str(payload.get("linked_experiment_id", payload.get("experiment_id", ""))),
        "related_run_id": related_run_id or str(payload.get("linked_run_id", "")),
        "related_job_id": related_job_id or str(payload.get("linked_job_id", "")),
        "content_kind": str(payload.get("content_kind") or content_kind),
    }


def apply_storage_contract(
    family: str,
    record: dict[str, Any],
    *,
    args: dict[str, Any] | None = None,
    content_kind: str = "observed",
) -> dict[str, Any]:
    """Attach schema/provenance metadata required by the file-backed state contract."""
    normalized = dict(record)
    timestamp = str(
        normalized.get("updated_at")
        or normalized.get("created_at")
        or normalized.get("added_at")
        or utc_now_iso()
    )
    existing_provenance = normalized.get("provenance")
    if not isinstance(existing_provenance, dict):
        existing_provenance = {}
    provenance = {
        **{key: value for key, value in existing_provenance.items() if value not in ("", None)},
        **{
            key: value
            for key, value in build_provenance(
                args, content_kind=content_kind, timestamp=timestamp
            ).items()
            if value not in ("", None)
        },
    }
    if family == "insights":
        provenance["content_kind"] = provenance.get("content_kind") or "inferred"
    normalized["schema_version"] = str(normalized.get("schema_version") or SCHEMA_VERSION)
    normalized["provenance"] = provenance
    return normalized


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return slug or "entry"


def _family_dir(family: str) -> Path:
    if family not in FILE_BACKED_FAMILIES:
        raise ValueError(f"Unsupported research state family: {family}")
    return ensure_research_root() / family


def _artifact_path(family: str, record: dict[str, Any]) -> Path:
    if family == "context":
        key = str(record.get("key", "")).strip()
        if not key:
            raise ValueError("Context records require a key")
        return _family_dir(family) / f"{_slugify(key)}.json"
    record_id = str(record.get("id", "")).strip()
    if not record_id:
        raise ValueError(f"{family} records require an id")
    return _family_dir(family) / f"{record_id}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temp_path.replace(path)


def load_records(family: str) -> list[dict[str, Any]]:
    """Load all persisted records for a family."""
    directory = _family_dir(family)
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(apply_storage_contract(family, payload))
    return records


def save_record(
    family: str,
    record: dict[str, Any],
    *,
    args: dict[str, Any] | None = None,
    content_kind: str = "observed",
) -> dict[str, Any]:
    """Persist one record atomically and return the normalized payload."""
    normalized = apply_storage_contract(family, record, args=args, content_kind=content_kind)
    _atomic_write_json(_artifact_path(family, normalized), normalized)
    return normalized


def clear_records(family: str) -> None:
    """Remove all persisted records for a family."""
    directory = _family_dir(family)
    for path in directory.glob("*.json"):
        path.unlink(missing_ok=True)


class FileBackedCollection(list[dict[str, Any]]):
    """List-like collection that mirrors persisted family records."""

    def __init__(self, family: str, *, default_content_kind: str = "observed") -> None:
        super().__init__()
        self.family = family
        self.default_content_kind = default_content_kind
        self.refresh()

    def refresh(self) -> None:
        records = load_records(self.family)
        super().clear()
        super().extend(records)

    def __iter__(self):  # type: ignore[override]
        self.refresh()
        return super().__iter__()

    def __len__(self) -> int:  # type: ignore[override]
        self.refresh()
        return super().__len__()

    def __getitem__(self, item):  # type: ignore[override]
        self.refresh()
        return super().__getitem__(item)

    def append(self, item: dict[str, Any]) -> None:  # type: ignore[override]
        save_record(self.family, item, content_kind=self.default_content_kind)
        self.refresh()

    def clear(self) -> None:  # type: ignore[override]
        clear_records(self.family)
        super().clear()
