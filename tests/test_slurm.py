"""Tests for the Slurm MCP server (mock mode)."""

from __future__ import annotations

import json

import pytest

from research_copilot.mcp_servers.slurm import (
    _mock_jobs,
    handle_cancel_job,
    handle_check_job_status,
    handle_get_job_logs,
    handle_list_jobs,
    handle_submit_job,
)


@pytest.fixture(autouse=True)
def clean_mock_state():
    """Reset mock Slurm state before each test."""
    _mock_jobs.clear()
    yield


class TestSubmitJob:
    @pytest.mark.asyncio
    async def test_submit_job(self):
        result = await handle_submit_job(
            {
                "job_name": "pfn_train",
                "script": "#!/bin/bash\npython train.py",
                "partition": "gpu",
                "gpus": 2,
                "time_limit": "08:00:00",
            }
        )
        content = json.loads(result["content"][0]["text"])
        assert content["status"] == "PENDING"
        assert content["job_id"]
        assert content["gpus"] == 2

    @pytest.mark.asyncio
    async def test_submit_multiple_jobs(self):
        await handle_submit_job({"job_name": "job1", "script": "#!/bin/bash\necho 1"})
        await handle_submit_job({"job_name": "job2", "script": "#!/bin/bash\necho 2"})

        result = await handle_list_jobs({})
        content = json.loads(result["content"][0]["text"])
        assert content["total"] == 2


class TestJobLifecycle:
    @pytest.mark.asyncio
    async def test_job_status_progression(self):
        """Jobs should progress: PENDING -> RUNNING -> COMPLETED."""
        result = await handle_submit_job(
            {"job_name": "test_job", "script": "#!/bin/bash\npython train.py"}
        )
        job_id = json.loads(result["content"][0]["text"])["job_id"]

        # First check: PENDING -> RUNNING
        status1 = await handle_check_job_status({"job_id": job_id})
        content1 = json.loads(status1["content"][0]["text"])
        assert content1["status"] == "RUNNING"
        assert content1["started_at"] is not None

        # Second check: RUNNING -> COMPLETED
        status2 = await handle_check_job_status({"job_id": job_id})
        content2 = json.loads(status2["content"][0]["text"])
        assert content2["status"] == "COMPLETED"
        assert content2["completed_at"] is not None
        assert "val_loss" in content2["output_tail"]

    @pytest.mark.asyncio
    async def test_cancel_pending_job(self):
        result = await handle_submit_job(
            {"job_name": "cancel_me", "script": "#!/bin/bash\nsleep 100"}
        )
        job_id = json.loads(result["content"][0]["text"])["job_id"]

        cancel_result = await handle_cancel_job({"job_id": job_id})
        content = json.loads(cancel_result["content"][0]["text"])
        assert content["status"] == "CANCELLED"

    @pytest.mark.asyncio
    async def test_cancel_completed_job(self):
        """Cancelling a completed job should not change its status."""
        result = await handle_submit_job(
            {"job_name": "done_job", "script": "#!/bin/bash\necho done"}
        )
        job_id = json.loads(result["content"][0]["text"])["job_id"]

        # Progress to COMPLETED
        await handle_check_job_status({"job_id": job_id})  # PENDING -> RUNNING
        await handle_check_job_status({"job_id": job_id})  # RUNNING -> COMPLETED

        cancel_result = await handle_cancel_job({"job_id": job_id})
        content_text = cancel_result["content"][0]["text"]
        assert "already in terminal state" in content_text

    @pytest.mark.asyncio
    async def test_nonexistent_job(self):
        result = await handle_check_job_status({"job_id": "99999"})
        assert result.get("isError") is True


class TestJobLogs:
    @pytest.mark.asyncio
    async def test_get_logs_after_completion(self):
        result = await handle_submit_job(
            {"job_name": "log_test", "script": "#!/bin/bash\npython train.py"}
        )
        job_id = json.loads(result["content"][0]["text"])["job_id"]

        # Progress to COMPLETED
        await handle_check_job_status({"job_id": job_id})
        await handle_check_job_status({"job_id": job_id})

        logs = await handle_get_job_logs({"job_id": job_id, "lines": 50})
        content = json.loads(logs["content"][0]["text"])
        assert "Training complete" in content["stdout"]

    @pytest.mark.asyncio
    async def test_get_logs_no_output_yet(self):
        result = await handle_submit_job(
            {"job_name": "early_logs", "script": "#!/bin/bash\necho hi"}
        )
        job_id = json.loads(result["content"][0]["text"])["job_id"]

        logs = await handle_get_job_logs({"job_id": job_id})
        content = json.loads(logs["content"][0]["text"])
        assert "no output yet" in content["stdout"]


class TestListJobs:
    @pytest.mark.asyncio
    async def test_list_with_status_filter(self):
        await handle_submit_job({"job_name": "j1", "script": "#!/bin/bash\necho 1"})
        await handle_submit_job({"job_name": "j2", "script": "#!/bin/bash\necho 2"})

        # Progress j1 to RUNNING
        j1_result = await handle_list_jobs({})
        j1_id = json.loads(j1_result["content"][0]["text"])["jobs"][0]["job_id"]
        await handle_check_job_status({"job_id": j1_id})

        # Filter for PENDING only
        pending = await handle_list_jobs({"status_filter": "PENDING"})
        content = json.loads(pending["content"][0]["text"])
        assert content["total"] == 1

    @pytest.mark.asyncio
    async def test_list_with_limit(self):
        for i in range(5):
            await handle_submit_job({"job_name": f"j{i}", "script": "#!/bin/bash\necho"})

        result = await handle_list_jobs({"limit": 3})
        content = json.loads(result["content"][0]["text"])
        assert content["total"] == 3
