"""Tests for the shared research ops read service."""

from __future__ import annotations

import json

import pytest

from research_copilot.mcp_servers.knowledge_base import (
    _store,
    handle_store_experiment,
    handle_update_experiment,
)
from research_copilot.mcp_servers.slurm import _mock_jobs, handle_submit_job
from research_copilot.services.research_ops import ResearchOpsService


@pytest.fixture(autouse=True)
def clean_state():
    _mock_jobs.clear()
    for key in _store:
        _store[key].clear()
    yield


@pytest.mark.asyncio
async def test_service_snapshot_links_jobs_and_experiments():
    submit = await handle_submit_job(
        {"job_name": "train-lcdb", "script": "#!/bin/bash\npython train.py"}
    )
    job_id = json.loads(submit["content"][0]["text"])["job_id"]

    first = await handle_store_experiment({"name": "older run", "status": "planned"})
    first_id = json.loads(first["content"][0]["text"])["id"]
    second = await handle_store_experiment(
        {
            "name": "linked run",
            "status": "running",
            "dataset": "LCDB",
            "model_type": "PFN",
            "results": '{"val_loss": 0.42}',
        }
    )
    second_id = json.loads(second["content"][0]["text"])["id"]

    await handle_update_experiment(
        {"experiment_id": second_id, "slurm_job_id": job_id, "status": "running"}
    )

    snapshot = ResearchOpsService().snapshot(job_limit=5, experiment_limit=5)

    assert snapshot.active_jobs == 1
    assert snapshot.jobs[0].job_id == job_id
    assert snapshot.experiments[0].experiment_id == second_id
    assert snapshot.experiments[0].linked_job_id == job_id
    assert snapshot.experiments[0].linked_job_status == "PENDING"
    assert snapshot.experiments[0].results == {"val_loss": 0.42}
    assert snapshot.experiments[1].experiment_id == first_id
    assert snapshot.experiment_status_counts == {"running": 1, "planned": 1}


def test_service_normalizes_non_mapping_results():
    service = ResearchOpsService(
        store={
            "experiments": [
                {
                    "id": "exp-1",
                    "name": "string-results",
                    "status": "completed",
                    "results": "val_loss=0.42",
                }
            ],
            "insights": [],
            "papers": [],
            "context": [],
        },
        jobs={},
    )

    snapshot = service.snapshot(experiment_limit=1)

    assert snapshot.experiments[0].results == {"raw": "val_loss=0.42"}
