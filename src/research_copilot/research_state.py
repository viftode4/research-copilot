"""File-backed research state helpers for the CLI-first single-user MVP."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
GLOBAL_REGISTRY_VERSION = "1.0"
JSON_SCHEMA_VERSION = "1.0"
NEW_STATE_DIRNAME = ".research-copilot"
LEGACY_STATE_DIRNAME = ".omx"
LEGACY_RESEARCH_SUBDIR = "research"
CANONICAL_DIRECTORIES = (
    "onboarding",
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


@dataclass(frozen=True)
class ResearchStatePaths:
    """Typed path bundle for the canonical research state layout."""

    root: Path
    onboarding: Path
    goals: Path
    experiments: Path
    runs: Path
    reviews: Path
    notes: Path
    papers: Path
    context: Path
    profiles: Path
    insights: Path


@dataclass(frozen=True)
class WorkspaceResolution:
    """Resolved workspace + state-root information."""

    workspace_dir: Path
    canonical_root: Path
    legacy_root: Path
    active_root: Path | None
    mode: str


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def get_workspace_dir() -> Path:
    """Resolve the current workspace directory target."""
    explicit_root = os.getenv("RC_WORKING_DIR", "").strip()
    target = explicit_root or "."
    return Path(target).expanduser().resolve()


def _canonical_root_for_workspace(workspace_dir: Path) -> Path:
    return workspace_dir / NEW_STATE_DIRNAME


def _legacy_root_for_workspace(workspace_dir: Path) -> Path:
    return workspace_dir / LEGACY_STATE_DIRNAME / LEGACY_RESEARCH_SUBDIR


def resolve_workspace(start: Path | None = None) -> WorkspaceResolution:
    """Resolve workspace discovery for canonical and legacy state roots."""
    current = (start or get_workspace_dir()).resolve()
    search_chain: list[Path] = [current]
    git_root: Path | None = None
    home_dir = Path.home().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            git_root = candidate
            break
    if git_root is not None:
        walker = current
        while True:
            if walker not in search_chain:
                search_chain.append(walker)
            if walker == git_root:
                break
            walker = walker.parent
    else:
        walker = current.parent
        while True:
            if current != home_dir and walker == home_dir:
                break
            if walker not in search_chain:
                search_chain.append(walker)
            if walker == walker.parent:
                break
            walker = walker.parent

    for candidate in search_chain:
        canonical_root = _canonical_root_for_workspace(candidate)
        legacy_root = _legacy_root_for_workspace(candidate)
        if canonical_root.exists():
            return WorkspaceResolution(
                workspace_dir=candidate,
                canonical_root=canonical_root,
                legacy_root=legacy_root,
                active_root=canonical_root,
                mode="canonical",
            )
        if legacy_root.exists():
            return WorkspaceResolution(
                workspace_dir=candidate,
                canonical_root=canonical_root,
                legacy_root=legacy_root,
                active_root=legacy_root,
                mode="legacy",
            )

    workspace_dir = current
    return WorkspaceResolution(
        workspace_dir=workspace_dir,
        canonical_root=_canonical_root_for_workspace(workspace_dir),
        legacy_root=_legacy_root_for_workspace(workspace_dir),
        active_root=None,
        mode="uninitialized",
    )


def get_research_root() -> Path:
    """Return the active or canonical research state root for the current workspace."""
    resolved = resolve_workspace()
    return resolved.active_root or resolved.canonical_root


def get_workspace_mode() -> str:
    """Return the current workspace mode: canonical, legacy, or uninitialized."""
    return resolve_workspace().mode


def ensure_research_root() -> Path:
    """Ensure the research root and canonical directories exist."""
    resolved = resolve_workspace()
    root = resolved.canonical_root
    for directory in CANONICAL_DIRECTORIES:
        (root / directory).mkdir(parents=True, exist_ok=True)
    _atomic_write_json(
        workspace_metadata_path(),
        {
            "schema_version": SCHEMA_VERSION,
            "workspace_dir": str(resolved.workspace_dir),
            "workspace_root": str(resolved.workspace_dir),
            "research_root": str(root),
            "initialized_at": utc_now_iso(),
        },
    )
    return root


def get_research_state_paths() -> ResearchStatePaths:
    """Return typed canonical paths for the file-backed state layout."""
    root = ensure_research_root()
    return ResearchStatePaths(
        root=root,
        onboarding=root / "onboarding",
        goals=root / "goals",
        experiments=root / "experiments",
        runs=root / "runs",
        reviews=root / "reviews",
        notes=root / "notes",
        papers=root / "papers",
        context=root / "context",
        profiles=root / "profiles",
        insights=root / "insights",
    )


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


def _family_dir(family: str, *, create: bool = False) -> Path:
    if family not in FILE_BACKED_FAMILIES:
        raise ValueError(f"Unsupported research state family: {family}")
    root = ensure_research_root() if create else get_research_root()
    return root / family


def _artifact_path(family: str, record: dict[str, Any]) -> Path:
    if family == "context":
        key = str(record.get("key", "")).strip()
        if not key:
            raise ValueError("Context records require a key")
        return _family_dir(family, create=True) / f"{_slugify(key)}.json"
    record_id = str(record.get("id", "")).strip()
    if not record_id:
        raise ValueError(f"{family} records require an id")
    return _family_dir(family, create=True) / f"{record_id}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temp_path.replace(path)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def _artifact_dir(name: str) -> Path:
    paths = get_research_state_paths()
    try:
        return getattr(paths, name)
    except AttributeError as exc:
        raise ValueError(f"Unknown research artifact directory '{name}'") from exc


def save_named_artifact(directory: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a named JSON artifact under a canonical research-state directory."""
    path = _artifact_dir(directory) / f"{_slugify(name)}.json"
    _atomic_write_json(path, payload)
    return payload


def load_named_artifact(directory: str, name: str) -> dict[str, Any]:
    """Load a named JSON artifact from a canonical research-state directory."""
    path = _artifact_dir(directory) / f"{_slugify(name)}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_run_artifact(
    run_id: str,
    payload: dict[str, Any],
    *,
    stdout: str = "",
    stderr: str = "",
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a run artifact bundle under the canonical workspace state root."""
    run_dir = _artifact_dir("runs") / _slugify(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(run_dir / "status.json", payload)
    _atomic_write_text(run_dir / "stdout.log", stdout)
    _atomic_write_text(run_dir / "stderr.log", stderr)
    _atomic_write_json(run_dir / "metrics.json", metrics or {})
    return payload


def load_run_artifact(run_id: str) -> dict[str, Any]:
    """Load a persisted run artifact bundle if one exists."""
    run_dir = _artifact_dir("runs") / _slugify(run_id)
    status_path = run_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    stdout = (run_dir / "stdout.log").read_text(encoding="utf-8") if (run_dir / "stdout.log").exists() else ""
    stderr = (run_dir / "stderr.log").read_text(encoding="utf-8") if (run_dir / "stderr.log").exists() else ""
    metrics_path = run_dir / "metrics.json"
    metrics: dict[str, Any] = {}
    if metrics_path.exists():
        try:
            loaded_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded_metrics = {}
        if isinstance(loaded_metrics, dict):
            metrics = loaded_metrics
    if isinstance(status, dict):
        status["stdout"] = stdout
        status["stderr"] = stderr
        status["metrics"] = metrics
        return status
    return {}


def save_review_artifact(review_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a structured review artifact under the canonical workspace state root."""
    return save_named_artifact("reviews", review_id, payload)


def load_records(family: str) -> list[dict[str, Any]]:
    """Load all persisted records for a family."""
    directory = _family_dir(family, create=False)
    if not directory.exists():
        return []
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
    directory = _family_dir(family, create=False)
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        path.unlink(missing_ok=True)


def workspace_metadata_path() -> Path:
    """Return the workspace metadata path used to detect initialization."""
    return resolve_workspace().canonical_root / "workspace.json"


def is_workspace_initialized() -> bool:
    """Return whether the current workspace has been explicitly initialized."""
    return resolve_workspace().mode == "canonical"


def initialize_workspace() -> dict[str, Any]:
    """Initialize the current workspace and return a summary payload."""
    already_initialized = is_workspace_initialized()
    workspace_dir = _normalize_workspace_path(os.getenv("RC_WORKING_DIR", "."))
    root = ensure_research_root()
    registry = remember_workspace(workspace_dir)
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_dir": str(workspace_dir),
        "research_root": str(root),
        "initialized": True,
        "already_initialized": already_initialized,
        "directories": [str(root / directory) for directory in CANONICAL_DIRECTORIES],
        "recent_workspaces": registry["workspaces"],
    }


def get_recent_workspaces_registry_path() -> Path:
    """Return the convenience-only recent workspaces registry path."""
    explicit_root = os.getenv("RC_GLOBAL_HOME", "").strip()
    if explicit_root:
        base = Path(explicit_root).expanduser().resolve()
    else:
        base = Path.home() / ".research-copilot"
    base.mkdir(parents=True, exist_ok=True)
    return base / "recent-workspaces.json"


def load_recent_workspaces() -> dict[str, Any]:
    """Load the convenience-only recent workspaces registry."""
    path = get_recent_workspaces_registry_path()
    if not path.exists():
        return {"schema_version": GLOBAL_REGISTRY_VERSION, "last_workspace": "", "workspaces": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": GLOBAL_REGISTRY_VERSION, "last_workspace": "", "workspaces": []}
    workspaces = payload.get("workspaces") if isinstance(payload, dict) else []
    if not isinstance(workspaces, list):
        workspaces = []
    return {
        "schema_version": str(payload.get("schema_version") or GLOBAL_REGISTRY_VERSION)
        if isinstance(payload, dict)
        else GLOBAL_REGISTRY_VERSION,
        "last_workspace": str(payload.get("last_workspace") or "") if isinstance(payload, dict) else "",
        "workspaces": [str(item) for item in workspaces if str(item).strip()],
    }


def remember_workspace(root: Path | str) -> dict[str, Any]:
    """Record a workspace in the convenience-only recent registry."""
    normalized = str(_normalize_workspace_path(root))
    payload = load_recent_workspaces()
    workspaces = [workspace for workspace in payload["workspaces"] if workspace != normalized]
    workspaces.insert(0, normalized)
    updated = {
        "schema_version": GLOBAL_REGISTRY_VERSION,
        "last_workspace": normalized,
        "workspaces": workspaces[:10],
    }
    _atomic_write_json(get_recent_workspaces_registry_path(), updated)
    return updated


def get_last_workspace() -> str:
    """Return the last workspace path from the convenience-only registry."""
    return str(load_recent_workspaces().get("last_workspace") or "")


def _normalize_workspace_path(root: Path | str) -> Path:
    candidate = Path(root).expanduser().resolve()
    if candidate.name == "research" and candidate.parent.name == ".omx":
        return candidate.parent.parent
    if candidate.name == NEW_STATE_DIRNAME:
        return candidate.parent
    return candidate


def is_legacy_workspace() -> bool:
    """Return whether the current workspace is legacy-only."""
    return resolve_workspace().mode == "legacy"


def migrate_workspace() -> dict[str, Any]:
    """Migrate a legacy workspace into the canonical standalone root."""
    resolved = resolve_workspace()
    if resolved.mode == "canonical":
        registry = remember_workspace(resolved.workspace_dir)
        return {
            "schema_version": SCHEMA_VERSION,
            "migrated": False,
            "already_migrated": True,
            "workspace_dir": str(resolved.workspace_dir),
            "research_root": str(resolved.canonical_root),
            "legacy_root": str(resolved.legacy_root),
            "recent_workspaces": registry["workspaces"],
        }
    if resolved.mode != "legacy":
        raise ValueError("No legacy workspace was found to migrate.")

    staging_root = resolved.workspace_dir / f"{NEW_STATE_DIRNAME}.staging"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    shutil.copytree(resolved.legacy_root, staging_root)
    _atomic_write_json(
        staging_root / "workspace.json",
        {
            "schema_version": SCHEMA_VERSION,
            "workspace_dir": str(resolved.workspace_dir),
            "research_root": str(resolved.canonical_root),
            "migrated_from": str(resolved.legacy_root),
            "migrated_at": utc_now_iso(),
        },
    )
    if resolved.canonical_root.exists():
        shutil.rmtree(staging_root)
    else:
        staging_root.replace(resolved.canonical_root)
    registry = remember_workspace(resolved.workspace_dir)
    return {
        "schema_version": SCHEMA_VERSION,
        "migrated": True,
        "already_migrated": False,
        "workspace_dir": str(resolved.workspace_dir),
        "research_root": str(resolved.canonical_root),
        "legacy_root": str(resolved.legacy_root),
        "recent_workspaces": registry["workspaces"],
    }


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


def onboarding_current_path() -> Path:
    """Return the canonical onboarding contract artifact path."""
    return get_research_root() / "onboarding" / "current.json"


def onboarding_interview_path() -> Path:
    """Return the canonical onboarding transcript artifact path."""
    return get_research_root() / "onboarding" / "interview.md"


def save_onboarding_contract(
    *,
    goal: str,
    success_criteria: str,
    active_profile: str,
    autonomy_level: str,
    allowed_actions: list[str],
    constraints: list[str],
    stop_conditions: list[str],
    notes: str,
    actor: str = "human",
    workflow_name: str = "onboard",
) -> dict[str, Any]:
    """Persist the current onboarding contract and its human-readable interview summary."""
    timestamp = utc_now_iso()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "goal": goal,
        "success_criteria": success_criteria,
        "active_profile": active_profile,
        "autonomy_level": autonomy_level,
        "allowed_actions": allowed_actions,
        "constraints": constraints,
        "stop_conditions": stop_conditions,
        "notes": notes,
        "updated_at": timestamp,
        "provenance": build_provenance(
            {
                "actor_type": actor,
                "workflow_name": workflow_name,
                "content_kind": "inferred",
            },
            content_kind="inferred",
            timestamp=timestamp,
        ),
    }
    _atomic_write_json(onboarding_current_path(), payload)
    interview = "\n".join(
        [
            "# Research Onboarding",
            "",
            f"- Goal: {goal}",
            f"- Success criteria: {success_criteria}",
            f"- Active profile: {active_profile}",
            f"- Autonomy level: {autonomy_level}",
            f"- Allowed actions: {', '.join(allowed_actions) if allowed_actions else 'None recorded'}",
            f"- Constraints: {', '.join(constraints) if constraints else 'None recorded'}",
            f"- Stop conditions: {', '.join(stop_conditions) if stop_conditions else 'None recorded'}",
            f"- Notes: {notes or 'None recorded'}",
            f"- Updated at: {timestamp}",
        ]
    )
    _atomic_write_text(onboarding_interview_path(), interview)
    return payload


def load_onboarding_contract() -> dict[str, Any]:
    """Load the current onboarding contract if one exists."""
    path = onboarding_current_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
