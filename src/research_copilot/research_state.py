"""File-backed research state helpers for the CLI-first single-user MVP."""

from __future__ import annotations

import json
import os
import re
import subprocess
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    "runtime",
)
FILE_BACKED_FAMILIES = frozenset({"experiments", "insights", "papers", "context"})
AUTONOMOUS_RUNTIME_SCHEMA_VERSION = "1.0"
AUTONOMOUS_RUNTIME_FILENAME = "autonomous.json"
AUTONOMOUS_RUNTIME_ACTIVE_STATUSES = frozenset({"running", "stopping"})
AUTONOMOUS_RUNTIME_RESUMABLE_STATUSES = frozenset({"stopped", "stale"})
AUTONOMOUS_RUNTIME_TERMINAL_STATUSES = frozenset({"completed", "failed"})
RUNTIME_DIRNAME = "runtime"
RUNTIME_HISTORY_DIRNAME = "history"
RUNTIME_EVENTS_DIRNAME = "events"
ACTIVE_SESSION_SCHEMA_VERSION = "1.0"
ACTIVE_SESSION_FILENAME = "active-session.json"
CODEX_RUNTIME_SCHEMA_VERSION = "1.0"
CODEX_RUNTIME_DIRNAME = "codex"
CODEX_RUNTIME_ACTIVE_FILENAME = "active.json"
CODEX_RUNTIME_HISTORY_DIRNAME = "history"
CODEX_RUNTIME_EVENTS_DIRNAME = "events"
CODEX_RUNTIME_NUDGES_DIRNAME = "nudges"
CODEX_RUNTIME_SUMMARIES_DIRNAME = "summaries"
CODEX_RUNTIME_TRANSPORT_DIRNAME = "transport"
CODEX_RUNTIME_TERMINAL_STATUSES = frozenset({"completed", "failed", "stopped", "archived"})


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
    runtime: Path


@dataclass(frozen=True)
class WorkspaceResolution:
    """Resolved workspace + state-root information."""

    workspace_dir: Path
    canonical_root: Path
    legacy_root: Path
    active_root: Path | None
    mode: str


@dataclass(frozen=True)
class AutonomousRuntimePaths:
    """Typed path bundle for the persisted autonomous runtime layout."""

    root: Path
    active: Path
    history: Path
    events: Path


@dataclass(frozen=True)
class ActiveSessionPaths:
    """Typed path bundle for the persisted active-session resolver artifact."""

    root: Path
    active: Path


@dataclass(frozen=True)
class CodexRuntimePaths:
    """Typed path bundle for the Codex-managed runtime artifact family."""

    root: Path
    active: Path
    history: Path
    events: Path
    nudges: Path
    summaries: Path
    transport: Path


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
        runtime=root / "runtime",
    )


def _parse_iso_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso_with_offset(timestamp: str, seconds: int) -> str:
    parsed = _parse_iso_timestamp(timestamp)
    if parsed is None:
        parsed = datetime.now(timezone.utc)
    return (parsed + timedelta(seconds=seconds)).isoformat()


def _runtime_root(*, create: bool = False) -> Path:
    root = ensure_research_root() if create else get_research_root()
    runtime_root = root / RUNTIME_DIRNAME
    if create:
        runtime_root.mkdir(parents=True, exist_ok=True)
    return runtime_root


def get_active_session_paths(*, create: bool = False) -> ActiveSessionPaths:
    """Return typed paths for the persisted active-session resolver artifact."""

    root = _runtime_root(create=create)
    return ActiveSessionPaths(
        root=root,
        active=root / ACTIVE_SESSION_FILENAME,
    )


def active_session_path() -> Path:
    """Return the persisted active-session resolver artifact path."""

    return get_active_session_paths(create=False).active


def get_autonomous_runtime_paths(*, create: bool = False) -> AutonomousRuntimePaths:
    """Return typed paths for the autonomous runtime artifact family."""
    root = _runtime_root(create=create)
    history = root / RUNTIME_HISTORY_DIRNAME
    events = root / RUNTIME_EVENTS_DIRNAME
    if create:
        history.mkdir(parents=True, exist_ok=True)
        events.mkdir(parents=True, exist_ok=True)
    return AutonomousRuntimePaths(
        root=root,
        active=root / AUTONOMOUS_RUNTIME_FILENAME,
        history=history,
        events=events,
    )


def _codex_runtime_root(*, create: bool = False) -> Path:
    root = _runtime_root(create=create) / CODEX_RUNTIME_DIRNAME
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def get_codex_runtime_paths(*, create: bool = False) -> CodexRuntimePaths:
    """Return typed paths for the Codex-managed runtime artifact family."""

    root = _codex_runtime_root(create=create)
    history = root / CODEX_RUNTIME_HISTORY_DIRNAME
    events = root / CODEX_RUNTIME_EVENTS_DIRNAME
    nudges = root / CODEX_RUNTIME_NUDGES_DIRNAME
    summaries = root / CODEX_RUNTIME_SUMMARIES_DIRNAME
    transport = root / CODEX_RUNTIME_TRANSPORT_DIRNAME
    if create:
        history.mkdir(parents=True, exist_ok=True)
        events.mkdir(parents=True, exist_ok=True)
        nudges.mkdir(parents=True, exist_ok=True)
        summaries.mkdir(parents=True, exist_ok=True)
        transport.mkdir(parents=True, exist_ok=True)
    return CodexRuntimePaths(
        root=root,
        active=root / CODEX_RUNTIME_ACTIVE_FILENAME,
        history=history,
        events=events,
        nudges=nudges,
        summaries=summaries,
        transport=transport,
    )


def autonomous_runtime_path() -> Path:
    """Return the active autonomous runtime contract path."""
    return get_autonomous_runtime_paths(create=False).active


def autonomous_runtime_history_path(run_id: str) -> Path:
    """Return the history snapshot path for one runtime."""
    return get_autonomous_runtime_paths(create=True).history / f"{_slugify(run_id)}.json"


def autonomous_runtime_events_path(run_id: str, *, create: bool = False) -> Path:
    """Return the event directory for one runtime."""
    paths = get_autonomous_runtime_paths(create=create)
    event_root = paths.events / _slugify(run_id)
    if create:
        event_root.mkdir(parents=True, exist_ok=True)
    return event_root


def load_autonomous_runtime() -> dict[str, Any]:
    """Load the active autonomous runtime payload if it exists."""
    path = autonomous_runtime_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_autonomous_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist the active autonomous runtime contract atomically."""
    normalized = dict(payload)
    normalized["schema_version"] = str(
        normalized.get("schema_version") or AUTONOMOUS_RUNTIME_SCHEMA_VERSION
    )
    _atomic_write_json(get_autonomous_runtime_paths(create=True).active, normalized)
    refresh_active_session_resolution()
    return normalized


def clear_autonomous_runtime() -> None:
    """Remove the active autonomous runtime file if it exists."""
    autonomous_runtime_path().unlink(missing_ok=True)
    refresh_active_session_resolution()


def load_autonomous_runtime_history(run_id: str) -> dict[str, Any]:
    """Load one archived autonomous runtime summary."""
    path = autonomous_runtime_history_path(run_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_autonomous_runtime_history(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one archived autonomous runtime summary."""
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("Autonomous runtime history requires a run_id")
    normalized = dict(payload)
    normalized["schema_version"] = str(
        normalized.get("schema_version") or AUTONOMOUS_RUNTIME_SCHEMA_VERSION
    )
    _atomic_write_json(autonomous_runtime_history_path(run_id), normalized)
    return normalized


def archive_autonomous_runtime(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy the active autonomous runtime payload into history."""
    runtime = dict(payload or load_autonomous_runtime())
    if not runtime:
        return {}
    runtime["archived_at"] = str(runtime.get("archived_at") or utc_now_iso())
    archived = save_autonomous_runtime_history(runtime)
    refresh_active_session_resolution()
    return archived


def list_autonomous_runtime_events(run_id: str) -> list[dict[str, Any]]:
    """Load all persisted event entries for one autonomous runtime."""
    event_dir = autonomous_runtime_events_path(run_id, create=False)
    if not event_dir.exists():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(event_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def append_autonomous_runtime_event(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Append one ordered event entry for the autonomous runtime log."""
    event_dir = autonomous_runtime_events_path(run_id, create=True)
    existing_sequences = [
        int(path.stem)
        for path in event_dir.glob("*.json")
        if path.stem.isdigit()
    ]
    sequence = (max(existing_sequences) if existing_sequences else 0) + 1
    event = dict(payload)
    event.setdefault("schema_version", AUTONOMOUS_RUNTIME_SCHEMA_VERSION)
    event.setdefault("run_id", run_id)
    event.setdefault("sequence", sequence)
    event.setdefault("recorded_at", utc_now_iso())
    _atomic_write_json(event_dir / f"{sequence:06d}.json", event)
    return event


def codex_runtime_history_path(session_id: str) -> Path:
    """Return the archived Codex session path for one session."""

    return get_codex_runtime_paths(create=True).history / f"{_slugify(session_id)}.json"


def codex_runtime_events_path(session_id: str, *, create: bool = False) -> Path:
    """Return the event directory for one Codex session."""

    paths = get_codex_runtime_paths(create=create)
    event_root = paths.events / _slugify(session_id)
    if create:
        event_root.mkdir(parents=True, exist_ok=True)
    return event_root


def codex_runtime_nudges_queue_path(session_id: str, *, create: bool = False) -> Path:
    """Return the operator nudge queue directory for one Codex session."""

    paths = get_codex_runtime_paths(create=create)
    queue_root = paths.nudges / _slugify(session_id) / "queue"
    if create:
        queue_root.mkdir(parents=True, exist_ok=True)
    return queue_root


def codex_runtime_transport_path(session_id: str) -> Path:
    """Return the persisted transport metadata path for one Codex session."""

    return get_codex_runtime_paths(create=True).transport / f"{_slugify(session_id)}.json"


def codex_runtime_summary_path(session_id: str, turn_id: str | int, *, create: bool = False) -> Path:
    """Return the persisted visible turn-summary markdown path."""

    paths = get_codex_runtime_paths(create=create)
    summary_dir = paths.summaries / _slugify(session_id)
    if create:
        summary_dir.mkdir(parents=True, exist_ok=True)
    turn_token = str(turn_id).strip()
    filename = f"{int(turn_token):06d}.md" if turn_token.isdigit() else f"{_slugify(turn_token)}.md"
    return summary_dir / filename


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in ("", None):
        return []
    return [str(value).strip()]


def _optional_int(value: Any) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _action_label(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("summary", "label", "name", "action", "type", "command"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
        return ""
    return str(value or "").strip()


def codex_session_is_active(payload: dict[str, Any]) -> bool:
    """Return whether a Codex-managed session should win active-session resolution."""

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return False
    status = str(payload.get("status") or "").strip().lower()
    return status not in CODEX_RUNTIME_TERMINAL_STATUSES


def load_codex_active_session() -> dict[str, Any]:
    """Load the active Codex session payload if it exists."""

    return _read_json_dict(get_codex_runtime_paths(create=False).active)


def save_codex_active_session(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist the active Codex session contract atomically."""

    normalized = dict(payload)
    normalized["schema_version"] = str(normalized.get("schema_version") or CODEX_RUNTIME_SCHEMA_VERSION)
    normalized["brain_type"] = str(normalized.get("brain_type") or "codex")
    _atomic_write_json(get_codex_runtime_paths(create=True).active, normalized)
    refresh_active_session_resolution()
    return normalized


def clear_codex_active_session() -> None:
    """Remove the active Codex session file if it exists."""

    get_codex_runtime_paths(create=False).active.unlink(missing_ok=True)
    refresh_active_session_resolution()


def load_codex_runtime_history(session_id: str) -> dict[str, Any]:
    """Load one archived Codex session summary."""

    return _read_json_dict(codex_runtime_history_path(session_id))


def save_codex_runtime_history(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one archived Codex session summary."""

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("Codex runtime history requires a session_id")
    normalized = dict(payload)
    normalized["schema_version"] = str(normalized.get("schema_version") or CODEX_RUNTIME_SCHEMA_VERSION)
    normalized["brain_type"] = str(normalized.get("brain_type") or "codex")
    _atomic_write_json(codex_runtime_history_path(session_id), normalized)
    return normalized


def archive_codex_active_session(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy the active Codex session payload into history."""

    session = dict(payload or load_codex_active_session())
    if not session:
        return {}
    session["archived_at"] = str(session.get("archived_at") or utc_now_iso())
    archived = save_codex_runtime_history(session)
    refresh_active_session_resolution()
    return archived


def list_codex_runtime_events(session_id: str) -> list[dict[str, Any]]:
    """Load all persisted event entries for one Codex-managed session."""

    event_dir = codex_runtime_events_path(session_id, create=False)
    if not event_dir.exists():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(event_dir.glob("*.json")):
        payload = _read_json_dict(path)
        if payload:
            events.append(payload)
    return events


def append_codex_runtime_event(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Append one ordered event entry for the Codex session log."""

    event_dir = codex_runtime_events_path(session_id, create=True)
    existing_sequences = [
        int(path.stem)
        for path in event_dir.glob("*.json")
        if path.stem.isdigit()
    ]
    sequence = (max(existing_sequences) if existing_sequences else 0) + 1
    event = dict(payload)
    event.setdefault("schema_version", CODEX_RUNTIME_SCHEMA_VERSION)
    event.setdefault("session_id", session_id)
    event.setdefault("sequence", sequence)
    event.setdefault("recorded_at", utc_now_iso())
    _atomic_write_json(event_dir / f"{sequence:06d}.json", event)
    return event


def save_codex_runtime_transport(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist transport metadata for a Codex-managed session."""

    normalized = dict(payload)
    normalized["schema_version"] = str(normalized.get("schema_version") or CODEX_RUNTIME_SCHEMA_VERSION)
    normalized["session_id"] = str(normalized.get("session_id") or session_id)
    _atomic_write_json(codex_runtime_transport_path(session_id), normalized)
    return normalized


def load_codex_runtime_transport(session_id: str) -> dict[str, Any]:
    """Load persisted transport metadata for a Codex-managed session."""

    return _read_json_dict(codex_runtime_transport_path(session_id))


def save_codex_turn_summary(session_id: str, turn_id: str | int, summary: str) -> Path:
    """Persist one visible Codex turn summary markdown artifact."""

    path = codex_runtime_summary_path(session_id, turn_id, create=True)
    _atomic_write_text(path, summary)
    return path


def load_codex_turn_summary(session_id: str, turn_id: str | int) -> str:
    """Load one visible Codex turn summary markdown artifact."""

    path = codex_runtime_summary_path(session_id, turn_id, create=False)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _normalize_codex_active_session(payload: dict[str, Any]) -> dict[str, Any]:
    transport_payload = payload.get("transport")
    transport = dict(transport_payload) if isinstance(transport_payload, dict) else {}
    last_action_payload = payload.get("last_action")
    return {
        "schema_version": ACTIVE_SESSION_SCHEMA_VERSION,
        "source": "codex",
        "brain_type": "codex",
        "session_id": str(payload.get("session_id") or ""),
        "run_id": str(payload.get("session_id") or ""),
        "status": str(payload.get("status") or "running"),
        "goal": str(payload.get("goal") or ""),
        "constraints": _string_list(payload.get("constraints")),
        "allowed_actions": _string_list(payload.get("allowed_actions")),
        "current_turn": _optional_int(payload.get("current_turn")),
        "current_phase": str(
            payload.get("current_turn_state") or payload.get("current_phase") or payload.get("status") or ""
        ),
        "iteration": _optional_int(payload.get("current_turn")) or 0,
        "max_iterations": None,
        "summary": str(payload.get("last_summary") or payload.get("summary") or ""),
        "last_summary": str(payload.get("last_summary") or payload.get("summary") or ""),
        "last_action": _action_label(last_action_payload),
        "last_action_status": str(payload.get("last_action_status") or ""),
        "last_experiment_id": str(payload.get("last_experiment_id") or ""),
        "last_review_id": str(payload.get("last_review_id") or ""),
        "last_context_update": str(payload.get("last_context_update") or ""),
        "profile_name": str(payload.get("profile_name") or ""),
        "autonomy_level": str(payload.get("autonomy_level") or ""),
        "started_at": str(payload.get("started_at") or ""),
        "updated_at": str(payload.get("updated_at") or ""),
        "last_heartbeat_at": str(payload.get("last_heartbeat_at") or ""),
        "lease_expires_at": "",
        "completed_at": str(payload.get("completed_at") or ""),
        "stop_requested_at": str(payload.get("stop_requested_at") or ""),
        "stop_reason": str(payload.get("stop_reason") or ""),
        "consecutive_failures": _optional_int(payload.get("consecutive_failures")) or 0,
        "operator_mode": str(payload.get("operator_mode") or ""),
        "pending_nudge_count": _optional_int(payload.get("pending_nudge_count")) or 0,
        "transport": str(
            payload.get("transport")
            if isinstance(payload.get("transport"), str)
            else transport.get("type") or payload.get("transport_type") or ""
        ),
        "pane_id": str(payload.get("pane_id") or transport.get("pane_id") or ""),
        "window_name": str(payload.get("window_name") or transport.get("window_name") or ""),
        "session_name": str(payload.get("session_name") or transport.get("session_name") or ""),
        "workspace": str(payload.get("workspace") or transport.get("workspace") or ""),
        "resolved_at": utc_now_iso(),
        "active": True,
    }


def _normalize_autonomous_active_session(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": ACTIVE_SESSION_SCHEMA_VERSION,
        "source": "autonomous",
        "brain_type": str(payload.get("brain_type") or "autonomous"),
        "session_id": "",
        "run_id": str(payload.get("run_id") or ""),
        "status": str(payload.get("status") or "unknown"),
        "goal": str(payload.get("goal") or ""),
        "constraints": _string_list(payload.get("constraints")),
        "allowed_actions": _string_list(payload.get("allowed_actions")),
        "current_turn": None,
        "current_phase": str(payload.get("current_phase") or ""),
        "iteration": _optional_int(payload.get("iteration")) or 0,
        "max_iterations": _optional_int(payload.get("max_iterations")),
        "summary": str(payload.get("summary") or ""),
        "last_summary": str(payload.get("summary") or ""),
        "last_action": _action_label(payload.get("last_action")),
        "last_action_status": str(payload.get("last_action_status") or ""),
        "last_experiment_id": str(payload.get("last_experiment_id") or ""),
        "last_review_id": str(payload.get("last_review_id") or ""),
        "last_context_update": "",
        "profile_name": str(payload.get("profile_name") or ""),
        "autonomy_level": str(payload.get("autonomy_level") or ""),
        "started_at": str(payload.get("started_at") or ""),
        "updated_at": str(payload.get("updated_at") or ""),
        "last_heartbeat_at": str(payload.get("last_heartbeat_at") or ""),
        "lease_expires_at": str(payload.get("lease_expires_at") or ""),
        "completed_at": str(payload.get("completed_at") or ""),
        "stop_requested_at": str(payload.get("stop_requested_at") or ""),
        "stop_reason": str(payload.get("stop_reason") or ""),
        "consecutive_failures": _optional_int(payload.get("consecutive_failures")) or 0,
        "operator_mode": "hands_off",
        "pending_nudge_count": 0,
        "transport": "managed-process" if payload.get("owner_pid") else "",
        "pane_id": "",
        "window_name": "",
        "session_name": "",
        "workspace": "",
        "resolved_at": utc_now_iso(),
        "active": True,
    }


def _inactive_active_session() -> dict[str, Any]:
    return {
        "schema_version": ACTIVE_SESSION_SCHEMA_VERSION,
        "source": "none",
        "brain_type": "",
        "session_id": "",
        "run_id": "",
        "status": "idle",
        "goal": "",
        "constraints": [],
        "allowed_actions": [],
        "current_turn": None,
        "current_phase": "",
        "iteration": 0,
        "max_iterations": None,
        "summary": "",
        "last_summary": "",
        "last_action": "",
        "last_action_status": "",
        "last_experiment_id": "",
        "last_review_id": "",
        "last_context_update": "",
        "profile_name": "",
        "autonomy_level": "",
        "started_at": "",
        "updated_at": "",
        "last_heartbeat_at": "",
        "lease_expires_at": "",
        "completed_at": "",
        "stop_requested_at": "",
        "stop_reason": "",
        "consecutive_failures": 0,
        "operator_mode": "",
        "pending_nudge_count": 0,
        "transport": "",
        "pane_id": "",
        "window_name": "",
        "session_name": "",
        "workspace": "",
        "resolved_at": utc_now_iso(),
        "active": False,
    }


def resolve_active_session(*, persist: bool = False) -> dict[str, Any]:
    """Resolve the authoritative live runtime across Codex and legacy runtime surfaces."""

    codex_session = load_codex_active_session()
    if codex_session_is_active(codex_session):
        resolved = _normalize_codex_active_session(codex_session)
    else:
        autonomous_runtime = load_autonomous_runtime()
        resolved = (
            _normalize_autonomous_active_session(autonomous_runtime)
            if autonomous_runtime_is_active(autonomous_runtime)
            else _inactive_active_session()
        )
    if persist:
        _atomic_write_json(get_active_session_paths(create=True).active, resolved)
    return resolved


def refresh_active_session_resolution() -> dict[str, Any]:
    """Persist and return the current authoritative active-session resolution."""

    return resolve_active_session(persist=True)


def load_active_session_resolution() -> dict[str, Any]:
    """Load the persisted active-session resolver output if it exists."""

    return _read_json_dict(active_session_path())


def mint_owner_token() -> str:
    """Return a fresh autonomous runtime owner token."""
    return uuid4().hex


def autonomous_runtime_is_active(payload: dict[str, Any]) -> bool:
    """Return whether a runtime is in one of the active lifecycle states."""
    return str(payload.get("status") or "").lower() in AUTONOMOUS_RUNTIME_ACTIVE_STATUSES


def autonomous_runtime_is_resumable(payload: dict[str, Any]) -> bool:
    """Return whether a runtime is resumable without new business inputs."""
    return str(payload.get("status") or "").lower() in AUTONOMOUS_RUNTIME_RESUMABLE_STATUSES


def autonomous_runtime_is_terminal(payload: dict[str, Any]) -> bool:
    """Return whether a runtime is in a terminal lifecycle state."""
    return str(payload.get("status") or "").lower() in AUTONOMOUS_RUNTIME_TERMINAL_STATUSES


def autonomous_runtime_lease_expired(
    payload: dict[str, Any], *, now: str | None = None
) -> bool:
    """Return whether the persisted lease has expired."""
    lease_expires_at = str(payload.get("lease_expires_at") or "")
    if not lease_expires_at:
        return False
    lease_deadline = _parse_iso_timestamp(lease_expires_at)
    current = _parse_iso_timestamp(now or utc_now_iso())
    if lease_deadline is None or current is None:
        return False
    return current >= lease_deadline


def process_is_running(pid: int | str | None) -> bool:
    """Return whether a process id still appears to be alive."""
    if pid in ("", None):
        return False
    try:
        normalized_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if normalized_pid <= 0:
        return False
    if os.name == "nt":
        popen_kwargs: dict[str, Any] = {
            "args": [
                "tasklist",
                "/FI",
                f"PID eq {normalized_pid}",
                "/FO",
                "CSV",
                "/NH",
            ],
            "capture_output": True,
            "text": True,
            "check": False,
        }
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if creationflags:
            popen_kwargs["creationflags"] = creationflags
        result = subprocess.run(**popen_kwargs)
        stdout = result.stdout.strip()
        if not stdout or "No tasks are running" in stdout:
            return False
        return f'"{normalized_pid}"' in stdout or f",{normalized_pid}," in stdout
    try:
        os.kill(normalized_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def autonomous_runtime_is_stale(payload: dict[str, Any], *, now: str | None = None) -> bool:
    """Return whether the runtime lease/owner fields indicate staleness."""
    status = str(payload.get("status") or "").lower()
    if status == "stale":
        return True
    if status not in AUTONOMOUS_RUNTIME_ACTIVE_STATUSES:
        return False
    if autonomous_runtime_lease_expired(payload, now=now):
        return True
    owner_pid = payload.get("owner_pid")
    return owner_pid not in ("", None) and not process_is_running(owner_pid)


def claim_autonomous_runtime_lease(
    payload: dict[str, Any],
    *,
    owner_token: str,
    owner_instance_id: str = "",
    owner_pid: int | None = None,
    lease_duration_seconds: int = 30,
    timestamp: str | None = None,
    allow_takeover: bool = False,
) -> dict[str, Any]:
    """Claim or refresh the autonomous runtime lease for one owner."""
    normalized = dict(payload)
    if not owner_token:
        raise ValueError("owner_token is required to claim the autonomous runtime lease")
    existing_owner = str(normalized.get("owner_token") or "")
    existing_instance = str(normalized.get("owner_instance_id") or "")
    provided_instance = str(owner_instance_id or "")
    if existing_owner and existing_owner != owner_token:
        if not allow_takeover and not autonomous_runtime_is_stale(normalized, now=timestamp):
            raise RuntimeError("Autonomous runtime already has an active owner.")
    if existing_owner == owner_token and existing_instance and provided_instance:
        if existing_instance != provided_instance and not autonomous_runtime_is_stale(normalized, now=timestamp):
            raise RuntimeError("Autonomous runtime lease is already bound to a different worker instance.")
        existing_pid = normalized.get("owner_pid")
        if (
            existing_instance == provided_instance
            and existing_pid not in ("", None)
            and owner_pid is not None
            and int(existing_pid) != int(owner_pid)
            and process_is_running(existing_pid)
        ):
            raise RuntimeError("Autonomous runtime worker instance is already active.")
    claimed_at = timestamp or utc_now_iso()
    normalized["owner_token"] = owner_token
    if provided_instance:
        normalized["owner_instance_id"] = provided_instance
    if owner_pid is not None:
        normalized["owner_pid"] = int(owner_pid)
    normalized["last_heartbeat_at"] = claimed_at
    normalized["lease_expires_at"] = _iso_with_offset(claimed_at, lease_duration_seconds)
    normalized["updated_at"] = claimed_at
    if autonomous_runtime_is_resumable(normalized):
        normalized["status"] = "running"
    return normalized


def mark_autonomous_runtime_stale(
    payload: dict[str, Any],
    *,
    reason: str = "Autonomous runtime lease expired.",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Return a runtime payload marked stale with a persisted reason."""
    normalized = dict(payload)
    marked_at = timestamp or utc_now_iso()
    normalized["status"] = "stale"
    normalized["updated_at"] = marked_at
    normalized["summary"] = reason or str(normalized.get("summary") or "")
    normalized["stop_reason"] = reason or str(normalized.get("stop_reason") or "")
    return normalized


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
    temp_path = path.parent / f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        _replace_with_retry(temp_path, path)
    finally:
        with suppress(FileNotFoundError):
            temp_path.unlink()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        temp_path.write_text(content, encoding="utf-8")
        _replace_with_retry(temp_path, path)
    finally:
        with suppress(FileNotFoundError):
            temp_path.unlink()


def _replace_with_retry(source: Path, destination: Path, *, attempts: int = 8) -> None:
    for attempt in range(attempts):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


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
