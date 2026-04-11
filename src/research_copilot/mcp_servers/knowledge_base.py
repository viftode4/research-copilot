"""Knowledge Base MCP server — persistent experiment and insight storage.

Wraps PostgreSQL via asyncpg for storing experiments, insights, papers,
and research context across sessions.

Architecture: handler functions are plain async functions (testable directly).
The create_knowledge_base_server() function wraps them as MCP tools.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# In-memory fallback store (when PostgreSQL is not available)
# ---------------------------------------------------------------------------

_store: dict[str, list[dict[str, Any]]] = {
    "experiments": [],
    "insights": [],
    "papers": [],
    "context": [],
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mutation_metadata(args: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "actor_type": args.get("actor_type", ""),
        "workflow_name": args.get("workflow_name", ""),
        "updated_at": _now_iso(),
    }
    if args.get("linked_experiment_id"):
        metadata["linked_experiment_id"] = args["linked_experiment_id"]
    if args.get("linked_job_id"):
        metadata["linked_job_id"] = args["linked_job_id"]
    return metadata


# ---------------------------------------------------------------------------
# Handler functions (plain async — directly testable)
# ---------------------------------------------------------------------------


async def handle_store_experiment(args: dict[str, Any]) -> dict[str, Any]:
    exp_id = str(uuid4())
    config = {}
    if args.get("config"):
        try:
            config = json.loads(args["config"])
        except json.JSONDecodeError:
            config = {"raw": args["config"]}

    results = {}
    if args.get("results"):
        try:
            results = json.loads(args["results"])
        except json.JSONDecodeError:
            results = {"raw": args["results"]}

    tags = []
    if args.get("tags"):
        try:
            tags = json.loads(args["tags"])
        except json.JSONDecodeError:
            tags = [t.strip() for t in args["tags"].split(",")]

    experiment = {
        "id": exp_id,
        "name": args["name"],
        "hypothesis": args.get("hypothesis", ""),
        "description": args.get("description", ""),
        "status": args.get("status", "planned"),
        "config": config,
        "results": results,
        "dataset": args.get("dataset", ""),
        "model_type": args.get("model_type", ""),
        "tags": tags,
        "wandb_run_id": args.get("wandb_run_id", ""),
        "created_by": args.get("created_by", ""),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "actor_type": args.get("actor_type", ""),
        "workflow_name": args.get("workflow_name", ""),
    }
    _store["experiments"].append(experiment)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"id": exp_id, "message": f"Experiment '{args['name']}' stored"}),
            }
        ]
    }


async def handle_update_experiment(args: dict[str, Any]) -> dict[str, Any]:
    exp_id = args["experiment_id"]
    for exp in _store["experiments"]:
        if exp["id"] == exp_id:
            if args.get("status"):
                exp["status"] = args["status"]
            if args.get("results"):
                try:
                    exp["results"] = json.loads(args["results"])
                except json.JSONDecodeError:
                    exp["results"] = {"raw": args["results"]}
            if args.get("wandb_run_id"):
                exp["wandb_run_id"] = args["wandb_run_id"]
            if args.get("wandb_run_url"):
                exp["wandb_run_url"] = args["wandb_run_url"]
            if args.get("slurm_job_id"):
                exp["slurm_job_id"] = args["slurm_job_id"]
            exp["updated_at"] = _now_iso()
            if args.get("actor_type"):
                exp["actor_type"] = args["actor_type"]
            if args.get("workflow_name"):
                exp["workflow_name"] = args["workflow_name"]
            return {
                "content": [
                    {"type": "text", "text": json.dumps({"id": exp_id, "message": "Updated"})}
                ]
            }

    return {
        "content": [{"type": "text", "text": f"Experiment {exp_id} not found"}],
        "isError": True,
    }


async def handle_query_experiments(args: dict[str, Any]) -> dict[str, Any]:
    results = list(_store["experiments"])

    if args.get("status"):
        results = [e for e in results if e["status"] == args["status"]]
    if args.get("dataset"):
        results = [e for e in results if args["dataset"].lower() in e.get("dataset", "").lower()]
    if args.get("model_type"):
        results = [
            e for e in results if args["model_type"].lower() in e.get("model_type", "").lower()
        ]
    if args.get("tag"):
        results = [e for e in results if args["tag"] in e.get("tags", [])]
    if args.get("search_text"):
        q = args["search_text"].lower()
        results = [
            e
            for e in results
            if q in e.get("name", "").lower()
            or q in e.get("hypothesis", "").lower()
            or q in e.get("description", "").lower()
        ]

    limit = args.get("limit", 20)
    results = results[-limit:]

    summaries = [
        {
            "id": e["id"],
            "name": e["name"],
            "status": e["status"],
            "hypothesis": e.get("hypothesis", "")[:200],
            "dataset": e.get("dataset", ""),
            "model_type": e.get("model_type", ""),
            "tags": e.get("tags", []),
            "created_at": e.get("created_at", ""),
            "has_results": bool(e.get("results")),
        }
        for e in results
    ]
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"total": len(summaries), "experiments": summaries}, indent=2),
            }
        ]
    }


async def handle_get_experiment(args: dict[str, Any]) -> dict[str, Any]:
    exp_id = args["experiment_id"]
    for exp in _store["experiments"]:
        if exp["id"] == exp_id:
            return {"content": [{"type": "text", "text": json.dumps(exp, indent=2)}]}

    return {
        "content": [{"type": "text", "text": f"Experiment {exp_id} not found"}],
        "isError": True,
    }


async def handle_store_insight(args: dict[str, Any]) -> dict[str, Any]:
    insight_id = str(uuid4())
    tags = []
    if args.get("tags"):
        try:
            tags = json.loads(args["tags"])
        except json.JSONDecodeError:
            tags = [t.strip() for t in args["tags"].split(",")]

    insight = {
        "id": insight_id,
        "title": args["title"],
        "content": args["content"],
        "category": args.get("category", "observation"),
        "experiment_id": args.get("experiment_id"),
        "confidence": args.get("confidence"),
        "tags": tags,
        "created_by": args.get("created_by", ""),
        "created_at": _now_iso(),
        "actor_type": args.get("actor_type", ""),
        "workflow_name": args.get("workflow_name", ""),
        "linked_experiment_id": args.get("linked_experiment_id", args.get("experiment_id")),
        "linked_job_id": args.get("linked_job_id", ""),
    }
    _store["insights"].append(insight)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"id": insight_id, "message": f"Insight stored: {args['title']}"}
                ),
            }
        ]
    }


async def handle_query_insights(args: dict[str, Any]) -> dict[str, Any]:
    results = list(_store["insights"])

    if args.get("category"):
        results = [i for i in results if i["category"] == args["category"]]
    if args.get("tag"):
        results = [i for i in results if args["tag"] in i.get("tags", [])]
    if args.get("search_text"):
        q = args["search_text"].lower()
        results = [
            i
            for i in results
            if q in i.get("title", "").lower() or q in i.get("content", "").lower()
        ]

    limit = args.get("limit", 20)
    results = results[-limit:]

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"total": len(results), "insights": results}, indent=2),
            }
        ]
    }


async def handle_set_research_context(args: dict[str, Any]) -> dict[str, Any]:
    key = args["key"]
    value = args["value"]
    context_type = args.get("context_type", "note")

    for ctx in _store["context"]:
        if ctx["key"] == key:
            ctx["value"] = value
            ctx["context_type"] = context_type
            ctx["updated_at"] = _now_iso()
            ctx.update(_mutation_metadata(args))
            return {
                "content": [
                    {"type": "text", "text": json.dumps({"key": key, "message": "Updated"})}
                ]
            }

    ctx_entry = {
        "id": str(uuid4()),
        "key": key,
        "value": value,
        "context_type": context_type,
        "updated_at": _now_iso(),
        **_mutation_metadata(args),
    }
    _store["context"].append(ctx_entry)
    return {
        "content": [
            {"type": "text", "text": json.dumps({"key": key, "message": "Created"})}
        ]
    }


async def handle_get_research_context(args: dict[str, Any]) -> dict[str, Any]:
    key = args.get("key", "")

    if key:
        for ctx in _store["context"]:
            if ctx["key"] == key:
                return {"content": [{"type": "text", "text": json.dumps(ctx, indent=2)}]}
        return {
            "content": [{"type": "text", "text": f"Context key '{key}' not found"}],
            "isError": True,
        }

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"total": len(_store["context"]), "context": _store["context"]}, indent=2
                ),
            }
        ]
    }


async def handle_store_paper(args: dict[str, Any]) -> dict[str, Any]:
    paper_id = str(uuid4())
    authors = []
    if args.get("authors"):
        try:
            authors = json.loads(args["authors"])
        except json.JSONDecodeError:
            authors = [a.strip() for a in args["authors"].split(",")]

    tags = []
    if args.get("tags"):
        try:
            tags = json.loads(args["tags"])
        except json.JSONDecodeError:
            tags = [t.strip() for t in args["tags"].split(",")]

    paper = {
        "id": paper_id,
        "title": args["title"],
        "authors": authors,
        "abstract": args.get("abstract", ""),
        "arxiv_id": args.get("arxiv_id", ""),
        "year": args.get("year"),
        "url": args.get("url", ""),
        "relevance_notes": args.get("relevance_notes", ""),
        "tags": tags,
        "added_at": _now_iso(),
        "actor_type": args.get("actor_type", ""),
        "workflow_name": args.get("workflow_name", ""),
    }
    _store["papers"].append(paper)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"id": paper_id, "message": f"Paper stored: {args['title']}"}),
            }
        ]
    }


async def handle_get_knowledge_summary(args: dict[str, Any]) -> dict[str, Any]:
    exp_by_status: dict[str, int] = {}
    for exp in _store["experiments"]:
        status = exp.get("status", "unknown")
        exp_by_status[status] = exp_by_status.get(status, 0) + 1

    insight_by_cat: dict[str, int] = {}
    for ins in _store["insights"]:
        cat = ins.get("category", "unknown")
        insight_by_cat[cat] = insight_by_cat.get(cat, 0) + 1

    summary = {
        "experiments": {
            "total": len(_store["experiments"]),
            "by_status": exp_by_status,
        },
        "insights": {
            "total": len(_store["insights"]),
            "by_category": insight_by_cat,
        },
        "papers": {"total": len(_store["papers"])},
        "context_entries": len(_store["context"]),
    }
    return {"content": [{"type": "text", "text": json.dumps(summary, indent=2)}]}


