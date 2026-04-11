"""Tests for the knowledge base MCP server."""

from __future__ import annotations

import json

import pytest

from research_copilot.mcp_servers.knowledge_base import (
    _store,
    handle_get_experiment,
    handle_get_knowledge_summary,
    handle_get_research_context,
    handle_query_experiments,
    handle_query_insights,
    handle_set_research_context,
    handle_store_experiment,
    handle_store_insight,
    handle_store_paper,
    handle_update_experiment,
)


@pytest.fixture(autouse=True)
def clean_store():
    """Reset the in-memory store before each test."""
    _store["experiments"].clear()
    _store["insights"].clear()
    _store["papers"].clear()
    _store["context"].clear()
    yield
    _store["experiments"].clear()
    _store["insights"].clear()
    _store["papers"].clear()
    _store["context"].clear()


class TestExperiments:
    @pytest.mark.asyncio
    async def test_store_and_get_experiment(self):
        result = await handle_store_experiment(
            {
                "name": "PFN with mu-weighting",
                "hypothesis": "Mu-weighting improves PFN training",
                "description": "Test mu-weighting approach",
                "config": '{"lr": 0.001, "epochs": 100}',
                "status": "planned",
                "dataset": "LCDB",
                "model_type": "PFN",
                "tags": '["pfn", "mu-weighting"]',
                "created_by": "vlad",
            }
        )
        content = json.loads(result["content"][0]["text"])
        exp_id = content["id"]
        assert exp_id

        get_result = await handle_get_experiment({"experiment_id": exp_id})
        exp = json.loads(get_result["content"][0]["text"])
        assert exp["name"] == "PFN with mu-weighting"
        assert exp["config"]["lr"] == 0.001
        assert exp["tags"] == ["pfn", "mu-weighting"]

    @pytest.mark.asyncio
    async def test_update_experiment(self):
        result = await handle_store_experiment({"name": "Test exp", "status": "planned"})
        exp_id = json.loads(result["content"][0]["text"])["id"]

        await handle_update_experiment(
            {
                "experiment_id": exp_id,
                "status": "completed",
                "results": '{"val_loss": 0.342, "test_nll": 3.456}',
                "wandb_run_id": "run_abc123",
            }
        )

        get_result = await handle_get_experiment({"experiment_id": exp_id})
        exp = json.loads(get_result["content"][0]["text"])
        assert exp["status"] == "completed"
        assert exp["results"]["val_loss"] == 0.342
        assert exp["wandb_run_id"] == "run_abc123"

    @pytest.mark.asyncio
    async def test_query_experiments_by_status(self):
        await handle_store_experiment({"name": "Exp 1", "status": "completed", "dataset": "LCDB"})
        await handle_store_experiment({"name": "Exp 2", "status": "running", "dataset": "LCDB"})
        await handle_store_experiment({"name": "Exp 3", "status": "completed", "dataset": "OpenML"})

        result = await handle_query_experiments({"status": "completed"})
        content = json.loads(result["content"][0]["text"])
        assert content["total"] == 2

    @pytest.mark.asyncio
    async def test_query_experiments_by_dataset(self):
        await handle_store_experiment({"name": "Exp 1", "dataset": "LCDB"})
        await handle_store_experiment({"name": "Exp 2", "dataset": "OpenML"})

        result = await handle_query_experiments({"dataset": "lcdb"})
        content = json.loads(result["content"][0]["text"])
        assert content["total"] == 1
        assert content["experiments"][0]["name"] == "Exp 1"

    @pytest.mark.asyncio
    async def test_query_experiments_by_text(self):
        await handle_store_experiment(
            {"name": "PFN training", "hypothesis": "PFN improves extrapolation"}
        )
        await handle_store_experiment(
            {"name": "LightGBM baseline", "hypothesis": "Traditional ML baseline"}
        )

        result = await handle_query_experiments({"search_text": "pfn"})
        content = json.loads(result["content"][0]["text"])
        assert content["total"] == 1


class TestInsights:
    @pytest.mark.asyncio
    async def test_store_and_query_insight(self):
        await handle_store_insight(
            {
                "title": "Mu-weighting helps convergence",
                "content": "Adding mu-weighting to PFN loss reduced training time by 30%",
                "category": "finding",
                "confidence": 0.85,
                "tags": '["pfn", "training"]',
            }
        )

        result = await handle_query_insights({"category": "finding"})
        content = json.loads(result["content"][0]["text"])
        assert content["total"] == 1
        assert content["insights"][0]["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_query_insights_by_text(self):
        await handle_store_insight(
            {"title": "Overfitting on small datasets", "content": "PFN overfits", "category": "failure"}
        )
        await handle_store_insight(
            {"title": "Ensemble improves NLL", "content": "Combining models helps", "category": "finding"}
        )

        result = await handle_query_insights({"search_text": "overfit"})
        content = json.loads(result["content"][0]["text"])
        assert content["total"] == 1


class TestResearchContext:
    @pytest.mark.asyncio
    async def test_set_and_get_context(self):
        await handle_set_research_context(
            {
                "key": "current_goal",
                "value": "Improve LCDB v2 extrapolation accuracy to < 3.0 NLL",
                "context_type": "goal",
            }
        )

        result = await handle_get_research_context({"key": "current_goal"})
        ctx = json.loads(result["content"][0]["text"])
        assert ctx["value"] == "Improve LCDB v2 extrapolation accuracy to < 3.0 NLL"
        assert ctx["context_type"] == "goal"

    @pytest.mark.asyncio
    async def test_update_existing_context(self):
        await handle_set_research_context(
            {"key": "plan", "value": "Step 1: baseline", "context_type": "plan"}
        )
        await handle_set_research_context(
            {"key": "plan", "value": "Step 1: done. Step 2: ablation", "context_type": "plan"}
        )

        result = await handle_get_research_context({"key": "plan"})
        ctx = json.loads(result["content"][0]["text"])
        assert "Step 2" in ctx["value"]

    @pytest.mark.asyncio
    async def test_get_all_context(self):
        await handle_set_research_context({"key": "goal", "value": "v1", "context_type": "goal"})
        await handle_set_research_context({"key": "note", "value": "v2", "context_type": "note"})

        result = await handle_get_research_context({})
        content = json.loads(result["content"][0]["text"])
        assert content["total"] == 2


class TestPapers:
    @pytest.mark.asyncio
    async def test_store_paper(self):
        result = await handle_store_paper(
            {
                "title": "Learning Curve Extrapolation with PFNs",
                "authors": '["Alice Smith", "Bob Jones"]',
                "arxiv_id": "2301.12345",
                "year": 2023,
                "tags": '["pfn", "learning-curves"]',
                "relevance_notes": "Core paper for our approach",
            }
        )
        content = json.loads(result["content"][0]["text"])
        assert content["id"]
        assert len(_store["papers"]) == 1
        assert _store["papers"][0]["authors"] == ["Alice Smith", "Bob Jones"]


class TestKnowledgeSummary:
    @pytest.mark.asyncio
    async def test_summary(self):
        await handle_store_experiment({"name": "Exp 1", "status": "completed"})
        await handle_store_experiment({"name": "Exp 2", "status": "running"})
        await handle_store_insight({"title": "Insight 1", "content": "...", "category": "finding"})
        await handle_store_paper({"title": "Paper 1", "authors": "Author A"})

        result = await handle_get_knowledge_summary({})
        summary = json.loads(result["content"][0]["text"])
        assert summary["experiments"]["total"] == 2
        assert summary["experiments"]["by_status"]["completed"] == 1
        assert summary["insights"]["total"] == 1
        assert summary["papers"]["total"] == 1
