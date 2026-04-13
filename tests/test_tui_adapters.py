"""Tests for terminal workflow snapshot adapters."""

from __future__ import annotations

import json

import pytest

from research_copilot.mcp_servers.knowledge_base import (
    _store,
    handle_set_research_context,
    handle_store_experiment,
    handle_store_insight,
    handle_store_paper,
    handle_update_experiment,
)
from research_copilot.mcp_servers.slurm import (
    _mock_jobs,
    handle_check_job_status,
    handle_submit_job,
)
from research_copilot.services.codex_runtime import attach_codex_session
from research_copilot.services.research_ops import ResearchOpsService
from research_copilot.services.workflow_snapshot import (
    build_canonical_snapshot,
    build_workflow_snapshot,
    summarize_job,
)
from research_copilot.tui.adapters import (
    build_dashboard_snapshot,
    fetch_full_entity_log,
    fetch_full_run_log,
    load_full_job_logs,
)


@pytest.fixture(autouse=True)
def clean_state():
    _mock_jobs.clear()
    _store["experiments"].clear()
    _store["insights"].clear()
    _store["papers"].clear()
    _store["context"].clear()
    yield
    _mock_jobs.clear()
    _store["experiments"].clear()
    _store["insights"].clear()
    _store["papers"].clear()
    _store["context"].clear()


class TestWorkflowSnapshot:
    def test_empty_snapshot(self):
        snapshot = build_workflow_snapshot()

        assert snapshot["jobs"]["total"] == 0
        assert snapshot["experiments"]["by_status"] == {}
        assert snapshot["knowledge"]["papers_total"] == 0
        assert snapshot["selection"]["default_job_id"] is None
        assert snapshot["selection"]["default_experiment_id"] is None

    def test_canonical_snapshot_exposes_contract_fields(self):
        snapshot = build_canonical_snapshot()

        assert snapshot["schema_version"] == "1.0.0"
        assert snapshot["snapshot_owner"] == "research_copilot.services.workflow_snapshot.build_canonical_snapshot"
        assert snapshot["workspace"]["id"].startswith("workspace:")
        assert "entities" in snapshot
        assert "links" in snapshot
        assert "actions" in snapshot
        assert snapshot["state_semantics"]["snapshot_state"] == "complete"
        assert all("source_id" in link and "target_id" in link and "link_type" in link for link in snapshot["links"])
        assert all(
            {
                "action_id",
                "label",
                "tier",
                "safety_level",
                "enabled",
                "preconditions",
                "target_entity_id",
                "scope",
            }.issubset(action)
            for action in snapshot["actions"]
        )

    def test_canonical_snapshot_uses_codex_active_session_when_present(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("research_copilot.services.codex_runtime._tmux_pane_exists", lambda pane_id: pane_id == "%91")
        monkeypatch.setattr(
            "research_copilot.services.codex_runtime._tmux_pane_metadata",
            lambda pane_id: {
                "pane_id": "%91",
                "session_name": "codex-1",
                "window_name": "brain",
                "workspace": str(tmp_path),
            },
        )
        attach_codex_session(
            session_id="codex-1",
            goal="Monitor Codex runtime in the dashboard",
            pane_id="%91",
            window_name="brain",
            session_name="codex-1",
        )

        snapshot = build_canonical_snapshot()

        assert snapshot["runtime"]["source"] == "codex"
        assert snapshot["runtime"]["session_id"] == "codex-1"
        assert snapshot["runtime"]["goal"] == "Monitor Codex runtime in the dashboard"

    def test_dashboard_snapshot_marks_lagging_and_stale_freshness_explicitly(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        attach_codex_session(session_id="codex-1")
        active_path = tmp_path / ".research-copilot" / "runtime" / "codex" / "active.json"
        payload = json.loads(active_path.read_text(encoding="utf-8"))
        payload["last_heartbeat_at"] = "2026-04-13T00:00:00+00:00"
        payload["updated_at"] = "2026-04-13T00:00:00+00:00"
        active_path.write_text(json.dumps(payload), encoding="utf-8")

        class LaggingDateTime:
            @staticmethod
            def now(tz=None):
                from datetime import datetime, timezone

                return datetime(2026, 4, 13, 0, 1, 10, tzinfo=timezone.utc)

            @staticmethod
            def fromisoformat(value):
                from datetime import datetime

                return datetime.fromisoformat(value)

        class StaleDateTime:
            @staticmethod
            def now(tz=None):
                from datetime import datetime, timezone

                return datetime(2026, 4, 13, 0, 3, 10, tzinfo=timezone.utc)

            @staticmethod
            def fromisoformat(value):
                from datetime import datetime

                return datetime.fromisoformat(value)

        monkeypatch.setattr("research_copilot.tui.adapters.datetime", LaggingDateTime)
        lagging_snapshot = build_dashboard_snapshot()
        assert lagging_snapshot.runtime is not None
        assert lagging_snapshot.runtime.freshness_state == "lagging"
        assert "lagging" in lagging_snapshot.runtime.freshness_label

        monkeypatch.setattr("research_copilot.tui.adapters.datetime", StaleDateTime)
        stale_snapshot = build_dashboard_snapshot()
        assert stale_snapshot.runtime is not None
        assert stale_snapshot.runtime.freshness_state == "stale"
        assert "stale" in stale_snapshot.runtime.freshness_label

    @pytest.mark.asyncio
    async def test_snapshot_links_jobs_experiments_and_knowledge(self):
        submit = await handle_submit_job(
            {"job_name": "train-lcdb", "script": "#!/bin/bash\npython train.py"}
        )
        job_id = json.loads(submit["content"][0]["text"])["job_id"]

        experiment = await handle_store_experiment(
            {
                "name": "LCDB run",
                "hypothesis": "Better scheduling improves convergence",
                "status": "running",
                "dataset": "LCDB",
                "model_type": "PFN",
                "results": '{"val_loss": 0.42}',
                "tags": '["pfn", "lcdb"]',
            }
        )
        experiment_id = json.loads(experiment["content"][0]["text"])["id"]
        await handle_update_experiment(
            {"experiment_id": experiment_id, "slurm_job_id": job_id, "status": "running"}
        )
        await handle_store_insight(
            {
                "title": "Scheduler note",
                "content": "Warmup keeps loss stable",
                "category": "finding",
                "linked_experiment_id": experiment_id,
            }
        )
        await handle_store_paper(
            {
                "title": "PFNs for Learning Curves",
                "authors": '["Alice", "Bob"]',
                "year": 2024,
                "linked_experiment_id": experiment_id,
            }
        )
        await handle_set_research_context(
            {
                "key": "focus",
                "value": "Monitor active LCDB jobs",
                "context_type": "goal",
                "linked_experiment_id": experiment_id,
            }
        )

        snapshot = build_workflow_snapshot()

        assert snapshot["jobs"]["active"] == 1
        assert snapshot["jobs"]["items"][0]["job_id"] == job_id
        assert snapshot["experiments"]["total"] == 1
        assert snapshot["experiments"]["items"][0]["linked_job_status"] == "PENDING"
        assert snapshot["experiments"]["items"][0]["result_keys"] == ["val_loss"]
        assert snapshot["knowledge"]["insights_total"] == 1
        assert snapshot["knowledge"]["papers_total"] == 1
        assert snapshot["knowledge"]["context_total"] == 1
        assert snapshot["selection"]["default_job_id"] == job_id
        assert snapshot["selection"]["default_experiment_id"] == experiment_id

        canonical = build_canonical_snapshot()
        experiment_entity = canonical["entities"]["experiment"][0]
        assert experiment_entity["attributes"]["experiment_id"] == experiment_id
        assert canonical["entities"]["workspace"][0]["attributes"]["counts"]["jobs"] == 1
        assert canonical["entities"]["workspace"][0]["attributes"]["counts"]["experiments"] == 1
        assert any(link["link_type"] == "run_to_job" for link in canonical["links"])
        assert any(link["link_type"] == "experiment_to_run" for link in canonical["links"])
        assert any(link["link_type"] == "experiment_to_insight" for link in canonical["links"])
        assert any(link["link_type"] == "experiment_to_context" for link in canonical["links"])

    @pytest.mark.asyncio
    async def test_snapshot_refresh_reflects_job_progression(self):
        submit = await handle_submit_job(
            {"job_name": "progress-job", "script": "#!/bin/bash\npython train.py"}
        )
        job_id = json.loads(submit["content"][0]["text"])["job_id"]

        pending = build_workflow_snapshot()
        assert pending["jobs"]["items"][0]["status"] == "PENDING"

        await handle_check_job_status({"job_id": job_id})
        running = build_workflow_snapshot()
        assert running["jobs"]["items"][0]["status"] == "RUNNING"
        assert running["jobs"]["active"] == 1

        await handle_check_job_status({"job_id": job_id})
        completed = build_workflow_snapshot()
        assert completed["jobs"]["items"][0]["status"] == "COMPLETED"
        assert completed["jobs"]["active"] == 0
        assert "val_accuracy" in completed["jobs"]["items"][0]["stdout_preview"]


class TestJobSummaries:
    def test_summarize_job_truncates_logs(self):
        from research_copilot.mcp_servers.slurm import MockJob

        job = MockJob(
            job_id="12345",
            name="log-heavy",
            status="RUNNING",
            script="echo test",
            partition="gpu",
            gpus=1,
            time_limit="01:00:00",
            submitted_at="2026-04-11T14:00:00+00:00",
            output="\n".join(f"line {idx}" for idx in range(12)),
            error="warning\n" * 3,
        )

        summary = summarize_job(job, max_log_lines=3, max_log_chars=18)

        assert summary["is_active"] is True
        assert summary["stdout_preview"].startswith("…")
        assert "line 11" in summary["stdout_preview"]
        assert summary["stderr_preview"]

    def test_load_full_job_logs_fetches_full_output(self):
        from research_copilot.mcp_servers.slurm import MockJob

        _mock_jobs["job-logs"] = MockJob(
            job_id="job-logs",
            name="log-heavy",
            status="RUNNING",
            script="echo test",
            partition="gpu",
            gpus=1,
            time_limit="01:00:00",
            submitted_at="2026-04-11T14:00:00+00:00",
            output="full stdout body",
            error="full stderr body",
        )

        stdout, stderr = load_full_job_logs("job-logs")

        assert stdout == "full stdout body"
        assert stderr == "full stderr body"

    def test_fetch_full_run_log_uses_entity_id_without_snapshot_log_leakage(self):
        from research_copilot.mcp_servers.slurm import MockJob

        jobs = {
            "12345": MockJob(
                job_id="12345",
                name="log-heavy",
                status="RUNNING",
                script="echo test",
                partition="gpu",
                gpus=1,
                time_limit="01:00:00",
                submitted_at="2026-04-11T14:00:00+00:00",
                output="\n".join(f"epoch={idx}" for idx in range(24)),
                error="stderr line",
            )
        }

        canonical = build_canonical_snapshot(jobs=jobs, max_items=5, max_log_lines=3, max_log_chars=18)
        run_entity = canonical["entities"]["run"][0]
        preview = run_entity["attributes"]["log_summary"]["stdout_preview"]

        assert "epoch=23" in preview
        assert "epoch=0" not in preview

        full_log = fetch_full_run_log("run:12345", service=ResearchOpsService(store={}, jobs=jobs))

        assert full_log.entity_id == "run:12345"
        assert full_log.job_id == "12345"
        assert "epoch=0" in full_log.stdout
        assert full_log.stderr == "stderr line"

    def test_fetch_full_entity_log_rejects_unknown_entity_ids(self):
        with pytest.raises(ValueError, match="Unsupported log entity id"):
            fetch_full_entity_log("paper:pap-1", service=ResearchOpsService(store={}, jobs={}))

    @pytest.mark.asyncio
    async def test_fetch_full_entity_log_resolves_experiment_to_linked_job(self):
        submit = await handle_submit_job(
            {"job_name": "train-linked", "script": "#!/bin/bash\npython train.py"}
        )
        job_id = json.loads(submit["content"][0]["text"])["job_id"]
        experiment = await handle_store_experiment(
            {
                "name": "Linked experiment",
                "status": "running",
                "results": '{"val_loss": 0.42}',
            }
        )
        experiment_id = json.loads(experiment["content"][0]["text"])["id"]
        await handle_update_experiment(
            {"experiment_id": experiment_id, "slurm_job_id": job_id, "status": "running"}
        )

        record = fetch_full_entity_log(f"experiment:{experiment_id}")

        assert record.entity_id == f"experiment:{experiment_id}"
        assert record.job_id == job_id

    @pytest.mark.asyncio
    async def test_fetch_full_entity_log_rejects_experiment_without_linked_run(self):
        experiment = await handle_store_experiment({"name": "Unlinked experiment", "status": "planned"})
        experiment_id = json.loads(experiment["content"][0]["text"])["id"]

        with pytest.raises(ValueError, match="has no linked run logs"):
            fetch_full_entity_log(f"experiment:{experiment_id}")

