"""Tests for the Codex-managed runtime control surface."""

from __future__ import annotations

import json

import pytest

from research_copilot.research_state import (
    list_codex_runtime_events,
    load_codex_active_session,
    load_codex_runtime_transport,
    load_codex_turn_summary,
    resolve_active_session,
    save_codex_active_session,
)
from research_copilot.services.codex_runtime import (
    apply_codex_nudges,
    attach_codex_session,
    codex_runtime_status,
    drain_codex_nudges,
    enqueue_codex_nudge,
    ingest_codex_turn_report,
)


@pytest.fixture(autouse=True)
def clean_research_state_env(monkeypatch) -> None:
    for variable in ("RC_RESEARCH_ROOT", "RC_WORKING_DIR", "RC_GLOBAL_HOME"):
        monkeypatch.delenv(variable, raising=False)


def test_attach_codex_session_persists_active_transport_and_resolver(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    payload = attach_codex_session(
        session_id="codex-1",
        goal="Investigate scheduler drift",
        constraints=["single-user only"],
        allowed_actions=["review results"],
        pane_id="%12",
        window_name="brain",
        session_name="research",
        actor_type="codex",
    )

    active = load_codex_active_session()
    transport = load_codex_runtime_transport("codex-1")
    resolved = resolve_active_session(persist=False)

    assert payload["session_id"] == "codex-1"
    assert active["goal"] == "Investigate scheduler drift"
    assert active["transport"]["pane_id"] == "%12"
    assert transport["pane_id"] == "%12"
    assert resolved["source"] == "codex"
    assert resolved["session_id"] == "codex-1"
    assert resolved["goal"] == "Investigate scheduler drift"


def test_turn_report_ingestion_updates_active_session_and_summary(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    attach_codex_session(session_id="codex-1")

    result = ingest_codex_turn_report(
        session_id="codex-1",
        turn_number=4,
        summary="Reviewed the latest experiment outputs.",
        action="review-results",
        artifacts=["experiment:exp-4", "review:rev-4"],
        experiment_id="exp-4",
        review_id="rev-4",
        context_update="next_step",
    )

    active = load_codex_active_session()

    assert result["accepted"] is True
    assert result["duplicate"] is False
    assert result["conflict"] is False
    assert result["applied_to_active"] is True
    assert active["current_turn"] == 4
    assert active["last_summary"] == "Reviewed the latest experiment outputs."
    assert active["last_experiment_id"] == "exp-4"
    assert active["last_review_id"] == "rev-4"
    assert load_codex_turn_summary("codex-1", 4) == "Reviewed the latest experiment outputs."
    assert any(
        event["event_type"] == "codex.turn_report.accepted"
        for event in list_codex_runtime_events("codex-1")
    )


def test_duplicate_and_conflicting_turn_reports_are_handled_deterministically(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    attach_codex_session(session_id="codex-1")

    first = ingest_codex_turn_report(
        session_id="codex-1",
        turn_number=2,
        summary="Baseline summary.",
        action="review-results",
    )
    duplicate = ingest_codex_turn_report(
        session_id="codex-1",
        turn_number=2,
        summary="Baseline summary.",
        action="review-results",
    )
    conflict = ingest_codex_turn_report(
        session_id="codex-1",
        turn_number=2,
        summary="Conflicting rewrite.",
        action="review-results",
    )

    active = load_codex_active_session()
    events = list_codex_runtime_events("codex-1")

    assert first["accepted"] is True
    assert duplicate["accepted"] is True
    assert duplicate["duplicate"] is True
    assert conflict["accepted"] is False
    assert conflict["conflict"] is True
    assert active["last_summary"] == "Baseline summary."
    assert load_codex_turn_summary("codex-1", 2) == "Baseline summary."
    assert any(event["event_type"] == "codex.turn_report.duplicate" for event in events)
    assert any(event["event_type"] == "codex.turn_report.conflict" for event in events)


def test_out_of_order_turn_report_is_persisted_without_replacing_active_summary(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    attach_codex_session(session_id="codex-1")

    ingest_codex_turn_report(
        session_id="codex-1",
        turn_number=5,
        summary="Current active turn.",
        action="review-results",
    )
    out_of_order = ingest_codex_turn_report(
        session_id="codex-1",
        turn_number=3,
        summary="Older turn delivered late.",
        action="review-results",
    )

    active = load_codex_active_session()

    assert out_of_order["accepted"] is True
    assert out_of_order["applied_to_active"] is False
    assert active["current_turn"] == 5
    assert active["last_summary"] == "Current active turn."
    assert load_codex_turn_summary("codex-1", 3) == "Older turn delivered late."


def test_nudges_persist_and_drain_from_queue(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    attach_codex_session(session_id="codex-1")

    pause = enqueue_codex_nudge(session_id="codex-1", kind="pause", message="Pause after the current turn.")
    request = enqueue_codex_nudge(session_id="codex-1", kind="request_summary", message="Need a concise recap.")
    drained = drain_codex_nudges(session_id="codex-1")
    status = codex_runtime_status(session_id="codex-1", include_nudges=True)

    assert pause["pending_nudge_count"] == 1
    assert request["pending_nudge_count"] == 2
    assert load_codex_active_session()["status"] == "paused"
    assert len(drained["drained"]) == 2
    assert status["pending_nudge_count"] == 0
    assert status["pending_nudges"] == []
    assert json.loads(json.dumps(drained["drained"]))[0]["session_id"] == "codex-1"


def test_apply_codex_nudges_sends_to_tmux_pane_and_drains(monkeypatch, tmp_path) -> None:
    sent: list[tuple[str, ...]] = []

    def fake_run_tmux_command(*args: str):
        sent.append(args)
        return None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("research_copilot.services.codex_runtime._run_tmux_command", fake_run_tmux_command)
    monkeypatch.setattr("research_copilot.services.codex_runtime._tmux_pane_exists", lambda pane_id: pane_id == "%12")
    attach_codex_session(session_id="codex-1", pane_id="%12", window_name="brain", session_name="codex-1")
    enqueue_codex_nudge(session_id="codex-1", kind="request_summary", message="Need a tighter recap.")
    enqueue_codex_nudge(session_id="codex-1", kind="stop_after_turn", message="Stop after this turn.")

    applied = apply_codex_nudges(session_id="codex-1")
    status = codex_runtime_status(session_id="codex-1", include_nudges=True)

    assert len(applied["applied"]) == 2
    assert status["pending_nudge_count"] == 0
    assert any(args[:3] == ("send-keys", "-t", "%12") for args in sent)
    assert any("Need a tighter recap." in " ".join(args) for args in sent)
    assert any("Stop after this turn." in " ".join(args) for args in sent)


def test_codex_runtime_status_reports_lagging_and_stale_from_heartbeat_age(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    attach_codex_session(session_id="codex-1")

    lagging_payload = load_codex_active_session()
    lagging_payload["last_heartbeat_at"] = "2026-04-13T00:00:00+00:00"
    lagging_payload["updated_at"] = "2026-04-13T00:00:00+00:00"
    save_codex_active_session(lagging_payload)
    monkeypatch.setattr("research_copilot.services.codex_runtime.datetime", __import__("datetime").datetime)
    monkeypatch.setattr(
        "research_copilot.services.codex_runtime.datetime",
        type(
            "FixedDateTime",
            (),
            {
                "now": staticmethod(lambda tz=None: __import__("datetime").datetime(2026, 4, 13, 0, 1, 10, tzinfo=tz)),
                "fromisoformat": staticmethod(__import__("datetime").datetime.fromisoformat),
            },
        ),
    )
    lagging = codex_runtime_status(session_id="codex-1")

    stale_payload = load_codex_active_session()
    stale_payload["last_heartbeat_at"] = "2026-04-13T00:00:00+00:00"
    stale_payload["updated_at"] = "2026-04-13T00:00:00+00:00"
    save_codex_active_session(stale_payload)
    monkeypatch.setattr(
        "research_copilot.services.codex_runtime.datetime",
        type(
            "FixedDateTime",
            (),
            {
                "now": staticmethod(lambda tz=None: __import__("datetime").datetime(2026, 4, 13, 0, 3, 10, tzinfo=tz)),
                "fromisoformat": staticmethod(__import__("datetime").datetime.fromisoformat),
            },
        ),
    )
    stale = codex_runtime_status(session_id="codex-1")

    assert lagging["freshness_state"] == "lagging"
    assert stale["freshness_state"] == "stale"
