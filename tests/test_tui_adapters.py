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
from research_copilot.services.workflow_snapshot import (
    build_workflow_snapshot,
    summarize_job,
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
            }
        )
        await handle_store_paper(
            {"title": "PFNs for Learning Curves", "authors": '["Alice", "Bob"]', "year": 2024}
        )
        await handle_set_research_context(
            {"key": "focus", "value": "Monitor active LCDB jobs", "context_type": "goal"}
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
