"""Slurm MCP server — submit and monitor jobs on HPC clusters.

Supports two modes:
- SSH mode: connects to a real Slurm cluster via SSH
- Mock mode: simulates Slurm for demos and testing

Architecture: handler functions are plain async functions (testable directly).
The create_slurm_server() function wraps them as MCP tools.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Mock Slurm state (for demo / testing)
# ---------------------------------------------------------------------------


@dataclass
class MockJob:
    job_id: str
    name: str
    status: str  # PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
    script: str
    partition: str
    gpus: int
    time_limit: str
    submitted_at: str
    started_at: str | None = None
    completed_at: str | None = None
    output: str = ""
    error: str = ""


_mock_jobs: dict[str, MockJob] = {}
_mock_mode = True  # Default to mock for safety


# ---------------------------------------------------------------------------
# Handler functions (plain async — directly testable)
# ---------------------------------------------------------------------------


async def handle_submit_job(args: dict[str, Any]) -> dict[str, Any]:
    job_name = args["job_name"]
    script = args["script"]
    partition = args.get("partition", "gpu")
    gpus = args.get("gpus", 1)
    time_limit = args.get("time_limit", "04:00:00")

    if _mock_mode:
        job_id = str(10000 + len(_mock_jobs))
        now = datetime.now(timezone.utc).isoformat()
        _mock_jobs[job_id] = MockJob(
            job_id=job_id,
            name=job_name,
            status="PENDING",
            script=script,
            partition=partition,
            gpus=gpus,
            time_limit=time_limit,
            submitted_at=now,
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "job_id": job_id,
                            "status": "PENDING",
                            "message": f"Job '{job_name}' submitted (mock mode)",
                            "partition": partition,
                            "gpus": gpus,
                            "time_limit": time_limit,
                        }
                    ),
                }
            ]
        }

    return {
        "content": [
            {"type": "text", "text": "Real Slurm submission not yet configured. Use mock mode."}
        ],
        "isError": True,
    }


async def handle_check_job_status(args: dict[str, Any]) -> dict[str, Any]:
    job_id = args["job_id"]

    if _mock_mode:
        job = _mock_jobs.get(job_id)
        if not job:
            return {
                "content": [{"type": "text", "text": f"Job {job_id} not found"}],
                "isError": True,
            }

        # Simulate progress: PENDING -> RUNNING -> COMPLETED
        if job.status == "PENDING":
            job.status = "RUNNING"
            job.started_at = datetime.now(timezone.utc).isoformat()
        elif job.status == "RUNNING":
            job.status = "COMPLETED"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            job.output = (
                "Training complete.\n"
                "Final metrics:\n"
                "  val_loss: 0.342\n"
                "  val_accuracy: 0.891\n"
                "  test_nll: 3.456\n"
                "W&B run: https://wandb.ai/example/run123"
            )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "job_id": job.job_id,
                            "name": job.name,
                            "status": job.status,
                            "partition": job.partition,
                            "gpus": job.gpus,
                            "submitted_at": job.submitted_at,
                            "started_at": job.started_at,
                            "completed_at": job.completed_at,
                            "output_tail": job.output[-1000:] if job.output else "",
                        }
                    ),
                }
            ]
        }

    return {
        "content": [
            {"type": "text", "text": "Real Slurm status check not yet configured."}
        ],
        "isError": True,
    }


async def handle_list_jobs(args: dict[str, Any]) -> dict[str, Any]:
    status_filter = args.get("status_filter", "")
    limit = args.get("limit", 20)

    if _mock_mode:
        jobs = list(_mock_jobs.values())
        if status_filter:
            jobs = [j for j in jobs if j.status == status_filter.upper()]
        jobs = jobs[-limit:]

        job_list = [
            {
                "job_id": j.job_id,
                "name": j.name,
                "status": j.status,
                "submitted_at": j.submitted_at,
            }
            for j in jobs
        ]
        return {
            "content": [
                {"type": "text", "text": json.dumps({"total": len(job_list), "jobs": job_list})}
            ]
        }

    return {
        "content": [{"type": "text", "text": "Real Slurm job listing not yet configured."}],
        "isError": True,
    }


async def handle_cancel_job(args: dict[str, Any]) -> dict[str, Any]:
    job_id = args["job_id"]

    if _mock_mode:
        job = _mock_jobs.get(job_id)
        if not job:
            return {
                "content": [{"type": "text", "text": f"Job {job_id} not found"}],
                "isError": True,
            }
        if job.status in ("COMPLETED", "FAILED", "CANCELLED"):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Job {job_id} already in terminal state: {job.status}",
                    }
                ]
            }
        job.status = "CANCELLED"
        return {
            "content": [
                {"type": "text", "text": json.dumps({"job_id": job_id, "status": "CANCELLED"})}
            ]
        }

    return {
        "content": [{"type": "text", "text": "Real Slurm cancellation not yet configured."}],
        "isError": True,
    }


async def handle_get_job_logs(args: dict[str, Any]) -> dict[str, Any]:
    job_id = args["job_id"]
    lines = args.get("lines", 100)

    if _mock_mode:
        job = _mock_jobs.get(job_id)
        if not job:
            return {
                "content": [{"type": "text", "text": f"Job {job_id} not found"}],
                "isError": True,
            }
        output_lines = job.output.split("\n")[-lines:] if job.output else ["(no output yet)"]
        error_lines = job.error.split("\n")[-lines:] if job.error else []
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "job_id": job_id,
                            "stdout": "\n".join(output_lines),
                            "stderr": "\n".join(error_lines),
                        }
                    ),
                }
            ]
        }

    return {
        "content": [{"type": "text", "text": "Real Slurm log retrieval not yet configured."}],
        "isError": True,
    }


