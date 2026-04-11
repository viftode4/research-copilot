"""Ultrawork profile registry for agent-facing research ops workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class UltraworkLane:
    name: str
    focus: str


@dataclass(frozen=True)
class UltraworkProfile:
    name: str
    summary: str
    when_to_use: str
    lanes: tuple[UltraworkLane, ...]
    expected_output: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["lanes"] = [asdict(lane) for lane in self.lanes]
        data["expected_output"] = list(self.expected_output)
        return data


_PROFILES: tuple[UltraworkProfile, ...] = (
    UltraworkProfile(
        name="ops-triage",
        summary="Fast diagnosis of the current research ops state.",
        when_to_use="Use when a researcher needs a quick current-state summary and next action.",
        lanes=(
            UltraworkLane("lane-1", "jobs/logs collection"),
            UltraworkLane("lane-2", "experiment/context summary"),
            UltraworkLane("lane-3", "issues/anomalies summary"),
        ),
        expected_output=("current-state summary", "top blockers", "suggested next action"),
    ),
    UltraworkProfile(
        name="experiment-launch",
        summary="Parallel launch prep for a new experiment run.",
        when_to_use="Use when registering a new run with metadata, config validation, and safety checks.",
        lanes=(
            UltraworkLane("lane-1", "experiment metadata + config validation"),
            UltraworkLane("lane-2", "submission script/job command"),
            UltraworkLane("lane-3", "regression/safety checks"),
        ),
        expected_output=("registered experiment", "submission artifact", "verification notes"),
    ),
    UltraworkProfile(
        name="run-review",
        summary="Structured interpretation for a completed run.",
        when_to_use="Use when a finished run needs metrics review, comparison, and saved conclusions.",
        lanes=(
            UltraworkLane("lane-1", "metrics/log analysis"),
            UltraworkLane("lane-2", "experiment comparison/context lookup"),
            UltraworkLane("lane-3", "insight extraction / note draft"),
        ),
        expected_output=("result summary", "keep/drop recommendation", "saved insight/context"),
    ),
    UltraworkProfile(
        name="literature-context",
        summary="Literature and context enrichment for a workflow.",
        when_to_use="Use when an experiment or review needs related papers and prior context.",
        lanes=(
            UltraworkLane("lane-1", "literature search"),
            UltraworkLane("lane-2", "relevance triage"),
            UltraworkLane("lane-3", "paper/context persistence"),
        ),
        expected_output=("short reading list", "saved papers", "context updates"),
    ),
    UltraworkProfile(
        name="incident-recovery",
        summary="Failure triage and safe recovery guidance.",
        when_to_use="Use when jobs fail or a workflow is broken and needs recovery options.",
        lanes=(
            UltraworkLane("lane-1", "logs/error extraction"),
            UltraworkLane("lane-2", "recent code/config deltas"),
            UltraworkLane("lane-3", "rollback/fix recommendation"),
        ),
        expected_output=("root-cause summary", "safe recovery options", "next command to run"),
    ),
)

_PROFILE_INDEX = {profile.name: profile for profile in _PROFILES}


def list_ultrawork_profiles() -> tuple[UltraworkProfile, ...]:
    """Return all supported ultrawork profiles in stable CLI order."""

    return _PROFILES


def get_ultrawork_profile(name: str) -> UltraworkProfile | None:
    """Return a named ultrawork profile if it exists."""

    return _PROFILE_INDEX.get(name)


def build_ultrawork_run_plan(profile_name: str, *, goal: str | None = None) -> dict[str, Any]:
    """Build an agent-safe execution contract for a named ultrawork profile."""

    profile = get_ultrawork_profile(profile_name)
    if profile is None:
        available = ", ".join(registered.name for registered in _PROFILES)
        raise KeyError(f"Unknown ultrawork profile '{profile_name}'. Available: {available}")

    return {
        "profile": profile.as_dict(),
        "goal": goal or "",
        "status": "ready",
        "lane_count": len(profile.lanes),
        "notes": [
            "Use the TUI as the human dashboard and CLI output as the agent surface.",
            "This command is read-only and emits the approved execution contract.",
        ],
    }
