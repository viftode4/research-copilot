"""Ultrawork profile registry for agent-facing research ops workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from research_copilot.research_state import load_onboarding_contract


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
        name="goal-chaser",
        summary="Drive a bounded loop toward a named research goal.",
        when_to_use="Use when a solo researcher has a concrete target and wants disciplined iteration.",
        lanes=(
            UltraworkLane("lane-1", "goal + current-state summary"),
            UltraworkLane("lane-2", "experiment/run execution path"),
            UltraworkLane("lane-3", "review + next-step loop"),
        ),
        expected_output=("goal progress summary", "current blockers", "next bounded action"),
    ),
    UltraworkProfile(
        name="baseline-improver",
        summary="Try to beat an existing baseline in a controlled way.",
        when_to_use="Use when there is a baseline and the next work is focused on measurable improvement.",
        lanes=(
            UltraworkLane("lane-1", "baseline/context summary"),
            UltraworkLane("lane-2", "improvement experiment path"),
            UltraworkLane("lane-3", "comparison + recommendation"),
        ),
        expected_output=("comparison summary", "improvement delta", "keep/drop recommendation"),
    ),
    UltraworkProfile(
        name="overfit-hunter",
        summary="Diagnose and reduce overfitting in a bounded loop.",
        when_to_use="Use when generalization problems are suspected and the next work should focus on overfitting signals.",
        lanes=(
            UltraworkLane("lane-1", "train/val/test gap inspection"),
            UltraworkLane("lane-2", "regularization/data hypothesis path"),
            UltraworkLane("lane-3", "overfitting diagnosis + next test"),
        ),
        expected_output=("overfitting diagnosis", "candidate fixes", "next bounded experiment"),
    ),
    UltraworkProfile(
        name="result-reasoner",
        summary="Interpret existing runs and turn them into explicit next steps.",
        when_to_use="Use when runs already exist and the main need is synthesis rather than launch.",
        lanes=(
            UltraworkLane("lane-1", "metrics/log analysis"),
            UltraworkLane("lane-2", "context + baseline comparison"),
            UltraworkLane("lane-3", "decision + review artifact"),
        ),
        expected_output=("result summary", "decision rationale", "next-step artifact"),
    ),
    UltraworkProfile(
        name="explore-improver",
        summary="Bounded exploratory improvement when the target is not yet rigid.",
        when_to_use="Use when you want to improve something for learning value, not only for a strict metric target.",
        lanes=(
            UltraworkLane("lane-1", "current-state exploration"),
            UltraworkLane("lane-2", "candidate improvement path"),
            UltraworkLane("lane-3", "result reasoning + next exploration"),
        ),
        expected_output=("what changed", "what helped", "next experiment to try"),
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

    onboarding = load_onboarding_contract()
    resolved_name = profile_name
    if profile_name == "active" and onboarding:
        resolved_name = str(onboarding.get("active_profile") or "")

    profile = get_ultrawork_profile(resolved_name)
    if profile is None:
        available = ", ".join(registered.name for registered in _PROFILES)
        raise KeyError(f"Unknown ultrawork profile '{profile_name}'. Available: {available}")

    return {
        "profile": profile.as_dict(),
        "goal": goal or str(onboarding.get("goal", "") if onboarding else ""),
        "status": "ready",
        "lane_count": len(profile.lanes),
        "onboarding": onboarding,
        "notes": [
            "Use the TUI as the human dashboard and CLI output as the agent surface.",
            "Use --execute to run the bounded profile loop instead of only printing the contract.",
        ],
    }


async def execute_ultrawork_profile(
    profile_name: str,
    *,
    goal: str | None = None,
    command: str = "",
    experiment_id: str = "",
    name: str = "",
    hypothesis: str = "",
    dataset: str = "",
    model_type: str = "",
) -> dict[str, Any]:
    """Execute a bounded single-user autonomous profile over the local workflow surface."""
    contract = build_ultrawork_run_plan(profile_name, goal=goal)
    profile = contract["profile"]
    resolved_name = str(profile["name"])
    steps: list[dict[str, Any]] = []

    # Local execution profiles
    run_payload: dict[str, Any] | None = None
    if resolved_name in {"goal-chaser", "baseline-improver", "overfit-hunter", "explore-improver"}:
        if not command:
            raise ValueError(f"Profile '{resolved_name}' requires --command for local execution.")
        from research_copilot.services.workflows import next_step, overfitting_check, run_experiment

        run_payload = await run_experiment(
            command=command,
            experiment_id=experiment_id,
            name=name,
            hypothesis=hypothesis,
            dataset=dataset,
            model_type=model_type,
            actor_type="system",
            created_by="research-copilot",
        )
        experiment_id = run_payload["experiment"]["id"]
        steps.append(
            {
                "step": "run-experiment",
                "status": run_payload["run"]["status"],
                "experiment_id": experiment_id,
                "run_id": run_payload["run"]["run_id"],
            }
        )

        if resolved_name == "overfit-hunter":
            overfit_payload = await overfitting_check(experiment_id=experiment_id, actor_type="system")
            steps.append(
                {
                    "step": "overfitting-check",
                    "status": "completed",
                    "diagnostics": overfit_payload["review"]["diagnostics"],
                }
            )

        next_payload = await next_step(experiment_id=experiment_id, actor_type="system")
        steps.append(
            {
                "step": "next-step",
                "status": "completed",
                "suggestions": next_payload["review"]["suggestions"],
            }
        )

        return {
            "status": "completed",
            "profile": profile,
            "goal": contract["goal"],
            "onboarding": contract["onboarding"],
            "steps": steps,
            "experiment_id": experiment_id,
            "run": run_payload["run"] if run_payload else None,
        }

    if resolved_name == "result-reasoner":
        if not experiment_id:
            raise ValueError("Profile 'result-reasoner' requires --experiment-id.")
        from research_copilot.services.workflows import next_step

        next_payload = await next_step(experiment_id=experiment_id, actor_type="system")
        steps.append(
            {
                "step": "next-step",
                "status": "completed",
                "suggestions": next_payload["review"]["suggestions"],
            }
        )
        return {
            "status": "completed",
            "profile": profile,
            "goal": contract["goal"],
            "onboarding": contract["onboarding"],
            "steps": steps,
            "experiment_id": experiment_id,
        }

    raise ValueError(f"Profile '{resolved_name}' does not have an execution path yet.")
