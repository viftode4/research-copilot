"""Shared services for Codex-managed runtime session state and steering."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from research_copilot.research_state import (
    archive_codex_active_session,
    append_codex_runtime_event,
    codex_runtime_nudges_queue_path,
    list_codex_runtime_events,
    load_codex_active_session,
    load_codex_runtime_history,
    load_codex_runtime_transport,
    save_codex_active_session,
    save_codex_runtime_transport,
    save_codex_turn_summary,
    utc_now_iso,
)

CODEX_LAGGING_THRESHOLD_SECONDS = 60
CODEX_STALE_THRESHOLD_SECONDS = 180
CODEX_TERMINAL_STATUSES = frozenset({"completed", "failed", "stopped", "archived"})
CODEX_STEERING_KINDS = frozenset(
    {"nudge", "pause", "resume", "stop_after_turn", "request_summary"}
)


def _string(value: Any) -> str:
    if value in ("", None):
        return ""
    return str(value).strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in ("", None):
        return []
    return [str(value).strip()]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _optional_int(value: Any) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _relative_age_label(value: str, *, now: datetime | None = None) -> str:
    parsed = _parse_iso_timestamp(value)
    if parsed is None:
        return "unknown"
    reference = now or datetime.now(timezone.utc)
    delta_seconds = max(0, int((reference - parsed).total_seconds()))
    if delta_seconds < 60:
        return f"{delta_seconds}s ago"
    if delta_seconds < 3600:
        return f"{delta_seconds // 60}m ago"
    if delta_seconds < 86400:
        return f"{delta_seconds // 3600}h ago"
    return f"{delta_seconds // 86400}d ago"


def _newer_timestamp(current: str, candidate: str) -> str:
    current_dt = _parse_iso_timestamp(current)
    candidate_dt = _parse_iso_timestamp(candidate)
    if candidate_dt is None:
        return current
    if current_dt is None or candidate_dt >= current_dt:
        return candidate
    return current


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f"{path.name}.{uuid4().hex}.tmp"
    try:
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _nudge_queue_paths(session_id: str) -> list[Path]:
    queue_root = codex_runtime_nudges_queue_path(session_id, create=False)
    if not queue_root.exists():
        return []
    return sorted(queue_root.glob("*.json"))


def _load_pending_nudges(session_id: str) -> list[dict[str, Any]]:
    nudges: list[dict[str, Any]] = []
    for path in _nudge_queue_paths(session_id):
        payload = _read_json_dict(path)
        if payload:
            nudges.append(payload)
    return nudges


def _nudge_count(session_id: str) -> int:
    return len(_nudge_queue_paths(session_id))


def _run_tmux_command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _tmux_pane_exists(pane_id: str) -> bool:
    if not pane_id:
        return False
    try:
        _run_tmux_command("display-message", "-p", "-t", pane_id, "#{pane_id}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


def _nudge_message_line(nudge: dict[str, Any]) -> str:
    kind = _string(nudge.get("kind")) or "nudge"
    message = _string(nudge.get("message"))
    prefix = "Research Copilot steering"
    templates = {
        "nudge": f"{prefix}: {message or 'Adjust the next bounded turn according to the latest operator request.'}",
        "request_summary": f"{prefix}: provide a concise summary on your next bounded turn. {message}".strip(),
        "pause": f"{prefix}: pause after the current bounded turn and wait for further instruction. {message}".strip(),
        "resume": f"{prefix}: resume the previous task now. {message}".strip(),
        "stop_after_turn": f"{prefix}: stop after the current bounded turn and summarize what changed. {message}".strip(),
    }
    return templates.get(kind, f"{prefix}: {message}".strip())


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _transport_payload(
    *,
    session_id: str,
    transport: str,
    pane_id: str,
    window_name: str,
    session_name: str,
    workspace: str,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "type": _string(transport) or "tmux-pane",
        "pane_id": _string(pane_id),
        "window_name": _string(window_name),
        "session_name": _string(session_name),
        "workspace": _string(workspace),
        "updated_at": utc_now_iso(),
    }


def _merge_transport(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    session_id = _string(payload.get("session_id"))
    stored_transport = load_codex_runtime_transport(session_id) if session_id else {}
    transport = _dict(payload.get("transport"))
    merged_transport = {
        **stored_transport,
        **transport,
    }
    if not merged_transport:
        transport_type = _string(payload.get("transport"))
        if transport_type:
            merged_transport = {"type": transport_type}
    merged = dict(payload)
    if merged_transport:
        merged["transport"] = merged_transport
    return merged


def _load_session_payload(session_id: str = "") -> dict[str, Any]:
    active = load_codex_active_session()
    if active and (not session_id or _string(active.get("session_id")) == _string(session_id)):
        return _merge_transport(active)
    if session_id:
        history = load_codex_runtime_history(session_id)
        if history:
            return _merge_transport(history)
    return {}


def _freshness_for_payload(payload: dict[str, Any]) -> tuple[str, str]:
    status = _string(payload.get("status")).lower()
    if status in CODEX_TERMINAL_STATUSES:
        timestamp = (
            _string(payload.get("completed_at"))
            or _string(payload.get("updated_at"))
            or _string(payload.get("last_heartbeat_at"))
        )
        if timestamp:
            return "terminal", f"last update {_relative_age_label(timestamp)}"
        return "terminal", "terminal"

    heartbeat_at = _string(payload.get("last_heartbeat_at")) or _string(payload.get("updated_at"))
    if not heartbeat_at:
        return "unknown", "no heartbeat"

    heartbeat = _parse_iso_timestamp(heartbeat_at)
    if heartbeat is None:
        return "unknown", "no heartbeat"

    age_seconds = max(0, int((datetime.now(timezone.utc) - heartbeat).total_seconds()))
    if age_seconds >= CODEX_STALE_THRESHOLD_SECONDS:
        return "stale", f"stale • heartbeat {_relative_age_label(heartbeat_at)}"
    if age_seconds >= CODEX_LAGGING_THRESHOLD_SECONDS:
        return "lagging", f"lagging • heartbeat {_relative_age_label(heartbeat_at)}"
    return "fresh", f"heartbeat {_relative_age_label(heartbeat_at)}"


def _status_response(
    payload: dict[str, Any],
    *,
    include_nudges: bool = False,
    accepted: bool | None = None,
    duplicate: bool | None = None,
    conflict: bool | None = None,
    applied_to_active: bool | None = None,
) -> dict[str, Any]:
    if not payload:
        return {
            "available": False,
            "active": False,
            "session_id": "",
            "status": "unavailable",
            "summary": "No Codex runtime session is registered.",
            "pending_nudge_count": 0,
            "freshness_state": "unknown",
            "freshness_label": "no heartbeat",
        }

    merged = _merge_transport(payload)
    session_id = _string(merged.get("session_id"))
    pending_nudges = _load_pending_nudges(session_id) if session_id and include_nudges else []
    pending_nudge_count = len(pending_nudges) if include_nudges else _nudge_count(session_id)
    freshness_state, freshness_label = _freshness_for_payload(merged)

    response = dict(merged)
    response["available"] = True
    response["active"] = bool(session_id) and _string(response.get("status")).lower() not in CODEX_TERMINAL_STATUSES
    response["pending_nudge_count"] = pending_nudge_count
    response["freshness_state"] = freshness_state
    response["freshness_label"] = freshness_label
    response["is_stale"] = freshness_state == "stale"
    response["is_lagging"] = freshness_state == "lagging"
    if include_nudges:
        response["pending_nudges"] = pending_nudges
    if accepted is not None:
        response["accepted"] = accepted
    if duplicate is not None:
        response["duplicate"] = duplicate
    if conflict is not None:
        response["conflict"] = conflict
    if applied_to_active is not None:
        response["applied_to_active"] = applied_to_active
    return response


def attach_codex_session(
    *,
    session_id: str,
    goal: str = "",
    constraints: list[str] | None = None,
    allowed_actions: list[str] | None = None,
    status: str = "running",
    current_turn: int | None = None,
    turn_state: str = "",
    operator_mode: str = "steerable",
    transport: str = "tmux-pane",
    pane_id: str = "",
    window_name: str = "",
    session_name: str = "",
    workspace: str = "",
    profile_name: str = "",
    autonomy_level: str = "",
    actor_type: str = "codex",
) -> dict[str, Any]:
    """Register or reattach a Codex-managed session as the active runtime."""

    resolved_session_id = _string(session_id)
    if not resolved_session_id:
        raise ValueError("session_id is required to attach a Codex session.")

    timestamp = utc_now_iso()
    existing_active = load_codex_active_session()
    if existing_active and _string(existing_active.get("session_id")) != resolved_session_id:
        archived = dict(existing_active)
        archived["status"] = _string(archived.get("status")) or "archived"
        if _string(archived.get("status")).lower() not in CODEX_TERMINAL_STATUSES:
            archived["status"] = "archived"
        archived["completed_at"] = _string(archived.get("completed_at")) or timestamp
        archive_codex_active_session(archived)

    current = _load_session_payload(resolved_session_id)
    transport_payload = _transport_payload(
        session_id=resolved_session_id,
        transport=transport,
        pane_id=pane_id,
        window_name=window_name,
        session_name=session_name,
        workspace=workspace,
    )
    payload = {
        **current,
        "schema_version": _string(current.get("schema_version")) or "1.0",
        "brain_type": "codex",
        "session_id": resolved_session_id,
        "status": _string(status) or _string(current.get("status")) or "running",
        "goal": _string(goal) or _string(current.get("goal")),
        "constraints": constraints if constraints is not None else _string_list(current.get("constraints")),
        "allowed_actions": (
            allowed_actions if allowed_actions is not None else _string_list(current.get("allowed_actions"))
        ),
        "current_turn": current_turn if current_turn is not None else (_optional_int(current.get("current_turn")) or 0),
        "current_turn_state": _string(turn_state)
        or _string(current.get("current_turn_state"))
        or _string(current.get("current_phase"))
        or "thinking",
        "current_phase": _string(turn_state)
        or _string(current.get("current_turn_state"))
        or _string(current.get("current_phase"))
        or "thinking",
        "last_summary": _string(current.get("last_summary")) or _string(current.get("summary")),
        "summary": _string(current.get("last_summary")) or _string(current.get("summary")),
        "last_action": current.get("last_action") or "",
        "last_action_status": _string(current.get("last_action_status")),
        "last_experiment_id": _string(current.get("last_experiment_id")),
        "last_review_id": _string(current.get("last_review_id")),
        "last_context_update": _string(current.get("last_context_update")),
        "profile_name": _string(profile_name) or _string(current.get("profile_name")),
        "autonomy_level": _string(autonomy_level) or _string(current.get("autonomy_level")),
        "started_at": _string(current.get("started_at")) or timestamp,
        "updated_at": timestamp,
        "last_heartbeat_at": _newer_timestamp(_string(current.get("last_heartbeat_at")), timestamp),
        "completed_at": _string(current.get("completed_at")),
        "stop_requested_at": _string(current.get("stop_requested_at")),
        "stop_reason": _string(current.get("stop_reason")),
        "consecutive_failures": _optional_int(current.get("consecutive_failures")) or 0,
        "operator_mode": _string(operator_mode) or _string(current.get("operator_mode")) or "steerable",
        "pending_nudge_count": _nudge_count(resolved_session_id),
        "transport": transport_payload,
        "pane_id": _string(pane_id) or _string(current.get("pane_id")),
        "window_name": _string(window_name) or _string(current.get("window_name")),
        "session_name": _string(session_name) or _string(current.get("session_name")),
        "workspace": _string(workspace) or _string(current.get("workspace")),
        "provenance": {
            "actor": _string(actor_type) or "codex",
            "attached_at": timestamp,
        },
    }
    save_codex_runtime_transport(resolved_session_id, transport_payload)
    save_codex_active_session(payload)
    append_codex_runtime_event(
        resolved_session_id,
        {
            "event_type": "codex.session.attached",
            "status": payload["status"],
            "operator_mode": payload["operator_mode"],
            "transport": transport_payload,
        },
    )
    return _status_response(payload, include_nudges=True)


def codex_runtime_status(*, session_id: str = "", include_nudges: bool = False) -> dict[str, Any]:
    """Inspect the active or archived Codex runtime session without mutating it."""

    payload = _load_session_payload(_string(session_id))
    return _status_response(payload, include_nudges=include_nudges)


def ingest_codex_turn_report(
    *,
    session_id: str,
    turn_number: int,
    summary: str,
    action: str = "",
    artifacts: list[str] | None = None,
    status: str = "",
    turn_state: str = "",
    experiment_id: str = "",
    review_id: str = "",
    context_update: str = "",
    reported_at: str = "",
    heartbeat_at: str = "",
    pane_id: str = "",
    window_name: str = "",
    session_name: str = "",
    workspace: str = "",
) -> dict[str, Any]:
    """Persist a bounded Codex turn report and update the active runtime contract."""

    resolved_session_id = _string(session_id)
    if not resolved_session_id:
        raise ValueError("session_id is required for Codex turn-report ingestion.")
    if turn_number < 0:
        raise ValueError("turn_number must be zero or greater.")
    if not _string(summary):
        raise ValueError("summary is required for Codex turn-report ingestion.")

    timestamp = _string(reported_at) or utc_now_iso()
    heartbeat_timestamp = _string(heartbeat_at) or timestamp
    normalized_action = _string(action)
    normalized_artifacts = [item for item in _string_list(artifacts) if item]

    current = _load_session_payload(resolved_session_id)
    if not current:
        current = attach_codex_session(
            session_id=resolved_session_id,
            status=_string(status) or "running",
            turn_state=_string(turn_state) or "thinking",
            pane_id=pane_id,
            window_name=window_name,
            session_name=session_name,
            workspace=workspace,
        )

    current_turn = _optional_int(current.get("current_turn")) or 0
    report_payload = {
        "turn_number": int(turn_number),
        "summary": _string(summary),
        "action": normalized_action,
        "artifacts": normalized_artifacts,
        "status": _string(status),
        "turn_state": _string(turn_state),
        "experiment_id": _string(experiment_id),
        "review_id": _string(review_id),
        "context_update": _string(context_update),
    }
    payload_hash = _payload_hash(report_payload)
    accepted_event = next(
        (
            event
            for event in list_codex_runtime_events(resolved_session_id)
            if _string(event.get("event_type")) == "codex.turn_report.accepted"
            and _optional_int(event.get("turn_number")) == int(turn_number)
        ),
        None,
    )
    if accepted_event and _string(accepted_event.get("payload_hash")) != payload_hash:
        append_codex_runtime_event(
            resolved_session_id,
            {
                "event_type": "codex.turn_report.conflict",
                "turn_number": int(turn_number),
                "payload_hash": payload_hash,
                "accepted_payload_hash": _string(accepted_event.get("payload_hash")),
                "reported_at": timestamp,
            },
        )
        return _status_response(
            current,
            include_nudges=True,
            accepted=False,
            duplicate=False,
            conflict=True,
            applied_to_active=False,
        )

    summary_path = save_codex_turn_summary(resolved_session_id, turn_number, _string(summary))
    apply_to_active = int(turn_number) >= current_turn
    duplicate = accepted_event is not None
    if apply_to_active:
        current["current_turn"] = int(turn_number)
        current["current_turn_state"] = _string(turn_state) or _string(current.get("current_turn_state")) or "thinking"
        current["current_phase"] = current["current_turn_state"]
        current["summary"] = _string(summary)
        current["last_summary"] = _string(summary)
        current["last_action"] = {
            "label": normalized_action,
            "artifacts": normalized_artifacts,
        } if normalized_action or normalized_artifacts else current.get("last_action") or ""
        current["last_action_status"] = _string(status) or _string(current.get("last_action_status"))
        current["last_experiment_id"] = _string(experiment_id) or _string(current.get("last_experiment_id"))
        current["last_review_id"] = _string(review_id) or _string(current.get("last_review_id"))
        current["last_context_update"] = _string(context_update) or _string(current.get("last_context_update"))
        current["status"] = _string(status) or _string(current.get("status")) or "running"
    current["updated_at"] = _newer_timestamp(_string(current.get("updated_at")), timestamp)
    current["last_heartbeat_at"] = _newer_timestamp(
        _string(current.get("last_heartbeat_at")),
        heartbeat_timestamp,
    )
    if pane_id or window_name or session_name or workspace:
        transport_payload = _transport_payload(
            session_id=resolved_session_id,
            transport=_string((_dict(current.get("transport")).get("type"))) or "tmux-pane",
            pane_id=pane_id or _string(current.get("pane_id")),
            window_name=window_name or _string(current.get("window_name")),
            session_name=session_name or _string(current.get("session_name")),
            workspace=workspace or _string(current.get("workspace")),
        )
        current["transport"] = transport_payload
        current["pane_id"] = transport_payload["pane_id"]
        current["window_name"] = transport_payload["window_name"]
        current["session_name"] = transport_payload["session_name"]
        current["workspace"] = transport_payload["workspace"]
        save_codex_runtime_transport(resolved_session_id, transport_payload)
    current["pending_nudge_count"] = _nudge_count(resolved_session_id)
    save_codex_active_session(current)

    append_codex_runtime_event(
        resolved_session_id,
        {
            "event_type": "codex.turn_report.accepted" if not duplicate else "codex.turn_report.duplicate",
            "turn_number": int(turn_number),
            "payload_hash": payload_hash,
            "reported_at": timestamp,
            "applied_to_active": apply_to_active,
            "summary_path": str(summary_path),
            "action": normalized_action,
            "artifacts": normalized_artifacts,
        },
    )
    return _status_response(
        current,
        include_nudges=True,
        accepted=True,
        duplicate=duplicate,
        conflict=False,
        applied_to_active=apply_to_active,
    )


def enqueue_codex_nudge(
    *,
    session_id: str,
    kind: str,
    message: str = "",
    payload: dict[str, Any] | None = None,
    actor_type: str = "human",
) -> dict[str, Any]:
    """Persist one operator steering nudge for a Codex-managed session."""

    resolved_session_id = _string(session_id)
    resolved_kind = _string(kind)
    if not resolved_session_id:
        raise ValueError("session_id is required to enqueue a Codex nudge.")
    if resolved_kind not in CODEX_STEERING_KINDS:
        raise ValueError(
            f"Unsupported Codex steering kind '{resolved_kind}'. "
            f"Expected one of: {', '.join(sorted(CODEX_STEERING_KINDS))}."
        )

    current = _load_session_payload(resolved_session_id)
    if not current:
        current = attach_codex_session(session_id=resolved_session_id)

    timestamp = utc_now_iso()
    nudge_id = f"{timestamp.replace(':', '').replace('-', '').replace('+00:00', 'Z')}-{uuid4().hex[:8]}"
    queue_payload = {
        "nudge_id": nudge_id,
        "session_id": resolved_session_id,
        "kind": resolved_kind,
        "message": _string(message),
        "payload": dict(payload or {}),
        "created_at": timestamp,
        "actor_type": _string(actor_type) or "human",
    }
    queue_path = codex_runtime_nudges_queue_path(resolved_session_id, create=True) / f"{nudge_id}.json"
    _write_json(queue_path, queue_payload)

    current = _load_session_payload(resolved_session_id)
    current["pending_nudge_count"] = _nudge_count(resolved_session_id)
    if resolved_kind == "pause":
        current["operator_mode"] = "paused"
        current["status"] = "paused"
    elif resolved_kind == "resume":
        current["operator_mode"] = "steerable"
        if _string(current.get("status")).lower() == "paused":
            current["status"] = "running"
    elif resolved_kind == "stop_after_turn":
        current["stop_requested_at"] = timestamp
        current["stop_reason"] = _string(message) or "Stop after current Codex turn."
    current["updated_at"] = timestamp
    save_codex_active_session(current)
    append_codex_runtime_event(
        resolved_session_id,
        {
            "event_type": "codex.nudge.enqueued",
            "nudge_id": nudge_id,
            "kind": resolved_kind,
            "message": _string(message),
        },
    )
    response = _status_response(current, include_nudges=True)
    response["nudge"] = queue_payload
    return response


def drain_codex_nudges(*, session_id: str, limit: int | None = None) -> dict[str, Any]:
    """Drain pending steering nudges for a Codex-managed session."""

    resolved_session_id = _string(session_id)
    if not resolved_session_id:
        raise ValueError("session_id is required to drain Codex nudges.")

    drained: list[dict[str, Any]] = []
    for path in _nudge_queue_paths(resolved_session_id):
        if limit is not None and len(drained) >= limit:
            break
        payload = _read_json_dict(path)
        if payload:
            drained.append(payload)
        path.unlink(missing_ok=True)

    current = _load_session_payload(resolved_session_id)
    if not current:
        current = attach_codex_session(session_id=resolved_session_id)
    current["pending_nudge_count"] = _nudge_count(resolved_session_id)
    if drained:
        current["last_applied_nudge"] = drained[-1]
    current["updated_at"] = utc_now_iso()
    save_codex_active_session(current)
    if drained:
        append_codex_runtime_event(
            resolved_session_id,
            {
                "event_type": "codex.nudges.drained",
                "drained_count": len(drained),
                "nudge_ids": [item["nudge_id"] for item in drained if _string(item.get("nudge_id"))],
            },
        )

    response = _status_response(current, include_nudges=True)
    response["drained"] = drained
    return response


def apply_codex_nudges(
    *,
    session_id: str,
    limit: int | None = None,
) -> dict[str, Any]:
    """Apply queued nudges into the registered tmux pane, then drain them."""

    resolved_session_id = _string(session_id)
    if not resolved_session_id:
        raise ValueError("session_id is required to apply Codex nudges.")

    current = _load_session_payload(resolved_session_id)
    if not current:
        raise ValueError(f"No Codex runtime session '{resolved_session_id}' was found.")

    transport = _dict(current.get("transport"))
    transport_type = _string(transport.get("type")) or _string(current.get("transport"))
    pane_id = _string(current.get("pane_id")) or _string(transport.get("pane_id"))
    if transport_type != "tmux-pane":
        raise ValueError("Queued nudges can only be applied to tmux-pane Codex sessions.")
    if not _tmux_pane_exists(pane_id):
        raise ValueError(f"tmux pane '{pane_id}' is not available.")

    pending = _load_pending_nudges(resolved_session_id)
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        response = _status_response(current, include_nudges=True)
        response["applied"] = []
        return response

    for nudge in pending:
        message = _nudge_message_line(nudge)
        _run_tmux_command("send-keys", "-t", pane_id, message, "Enter")

    drained = drain_codex_nudges(session_id=resolved_session_id, limit=len(pending))
    current = _load_session_payload(resolved_session_id)
    current["last_steering_applied_at"] = utc_now_iso()
    current["updated_at"] = _string(current.get("last_steering_applied_at"))
    save_codex_active_session(current)
    append_codex_runtime_event(
        resolved_session_id,
        {
            "event_type": "codex.nudges.applied_to_pane",
            "pane_id": pane_id,
            "applied_count": len(pending),
            "nudge_ids": [_string(item.get("nudge_id")) for item in pending if _string(item.get("nudge_id"))],
        },
    )
    response = _status_response(current, include_nudges=True)
    response["applied"] = pending
    response["drained"] = drained.get("drained", [])
    return response
