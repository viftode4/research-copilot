"""Tool registry — maps tool names to handler functions and schemas.

This replaces the Agent SDK MCP server wrappers with a simple dict
that the Anthropic API tool use loop can consume directly.
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from research_copilot.mcp_servers.knowledge_base import (
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
from research_copilot.mcp_servers.literature import (
    handle_find_related_papers,
    handle_get_paper_details,
    handle_search_papers,
)
from research_copilot.mcp_servers.slurm import (
    handle_cancel_job,
    handle_check_job_status,
    handle_get_job_logs,
    handle_list_jobs,
    handle_submit_job,
)

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _prop(description: str, type_: str, enum: list[str] | None = None) -> dict[str, Any]:
    """Helper to build a JSON Schema property."""
    p: dict[str, Any] = {"type": type_, "description": description}
    if enum:
        p["enum"] = enum
    return p


# Each entry: (handler, description, input_schema)
TOOL_DEFINITIONS: dict[str, tuple[ToolHandler, str, dict[str, Any]]] = {
    # --- Literature ---
    "search_papers": (
        handle_search_papers,
        "Search for academic papers across arXiv and Semantic Scholar. Returns titles, authors, abstracts.",
        {
            "type": "object",
            "properties": {
                "query": _prop("Search query for finding papers", "string"),
                "max_results": _prop("Maximum number of results per source (default 10)", "integer"),
                "sources": _prop("Which sources to search", "string", ["arxiv", "semantic_scholar", "both"]),
            },
            "required": ["query"],
        },
    ),
    "get_paper_details": (
        handle_get_paper_details,
        "Get detailed info about a paper (abstract, citations, references, TL;DR). Accepts Semantic Scholar ID or arXiv ID.",
        {
            "type": "object",
            "properties": {
                "paper_id": _prop("Semantic Scholar paper ID or arXiv ID (e.g. '2301.12345')", "string"),
            },
            "required": ["paper_id"],
        },
    ),
    "find_related_papers": (
        handle_find_related_papers,
        "Find papers related to a given paper via Semantic Scholar recommendations.",
        {
            "type": "object",
            "properties": {
                "paper_id": _prop("Paper ID to find recommendations for", "string"),
                "max_results": _prop("Maximum recommendations (default 10)", "integer"),
            },
            "required": ["paper_id"],
        },
    ),
    # --- Knowledge Base: Experiments ---
    "store_experiment": (
        handle_store_experiment,
        "Store a new experiment in the knowledge base with hypothesis, config, and metadata.",
        {
            "type": "object",
            "properties": {
                "name": _prop("Experiment name", "string"),
                "hypothesis": _prop("What you expect to happen and why", "string"),
                "description": _prop("Detailed description of the experiment", "string"),
                "config": _prop("JSON string of hyperparameters and config", "string"),
                "status": _prop("Experiment status", "string", ["planned", "running", "completed", "failed", "cancelled"]),
                "dataset": _prop("Dataset name (e.g. 'LCDB', 'OpenML')", "string"),
                "model_type": _prop("Model type (e.g. 'PFN', 'LightGBM')", "string"),
                "tags": _prop("JSON array of tags, or comma-separated", "string"),
                "wandb_run_id": _prop("W&B run ID if applicable", "string"),
                "results": _prop("JSON string of results/metrics", "string"),
                "created_by": _prop("Who created this experiment", "string"),
            },
            "required": ["name"],
        },
    ),
    "update_experiment": (
        handle_update_experiment,
        "Update an existing experiment's status, results, or links.",
        {
            "type": "object",
            "properties": {
                "experiment_id": _prop("UUID of the experiment to update", "string"),
                "status": _prop("New status", "string", ["planned", "running", "completed", "failed", "cancelled"]),
                "results": _prop("JSON string of results/metrics", "string"),
                "wandb_run_id": _prop("W&B run ID", "string"),
                "wandb_run_url": _prop("W&B run URL", "string"),
                "slurm_job_id": _prop("Slurm job ID", "string"),
            },
            "required": ["experiment_id"],
        },
    ),
    "query_experiments": (
        handle_query_experiments,
        "Search experiments in the knowledge base by status, dataset, model type, tag, or free text.",
        {
            "type": "object",
            "properties": {
                "status": _prop("Filter by status", "string"),
                "dataset": _prop("Filter by dataset name (case-insensitive)", "string"),
                "model_type": _prop("Filter by model type", "string"),
                "tag": _prop("Filter by tag", "string"),
                "search_text": _prop("Free text search in name/hypothesis/description", "string"),
                "limit": _prop("Max results (default 20)", "integer"),
            },
        },
    ),
    "get_experiment": (
        handle_get_experiment,
        "Get full details of a specific experiment by its ID.",
        {
            "type": "object",
            "properties": {
                "experiment_id": _prop("UUID of the experiment", "string"),
            },
            "required": ["experiment_id"],
        },
    ),
    # --- Knowledge Base: Insights ---
    "store_insight": (
        handle_store_insight,
        "Store a research insight or lesson learned, optionally linked to an experiment.",
        {
            "type": "object",
            "properties": {
                "title": _prop("Short title for the insight", "string"),
                "content": _prop("Detailed content of the insight", "string"),
                "category": _prop("Category", "string", ["finding", "failure", "hypothesis", "technique", "observation"]),
                "experiment_id": _prop("Link to an experiment UUID", "string"),
                "confidence": _prop("Confidence level 0.0-1.0", "number"),
                "tags": _prop("JSON array of tags, or comma-separated", "string"),
                "created_by": _prop("Who created this insight", "string"),
            },
            "required": ["title", "content"],
        },
    ),
    "query_insights": (
        handle_query_insights,
        "Search insights by category, tag, or free text.",
        {
            "type": "object",
            "properties": {
                "category": _prop("Filter by category", "string"),
                "tag": _prop("Filter by tag", "string"),
                "search_text": _prop("Free text search", "string"),
                "limit": _prop("Max results (default 20)", "integer"),
            },
        },
    ),
    # --- Knowledge Base: Context ---
    "set_research_context": (
        handle_set_research_context,
        "Set or update persistent research context (goals, plans, notes). Persists across sessions.",
        {
            "type": "object",
            "properties": {
                "key": _prop("Context key (e.g. 'current_goal', 'lcdb_v2_plan')", "string"),
                "value": _prop("The context value/content", "string"),
                "context_type": _prop("Type of context", "string", ["goal", "plan", "note", "reference", "constraint"]),
            },
            "required": ["key", "value"],
        },
    ),
    "get_research_context": (
        handle_get_research_context,
        "Get research context. Pass a key for a specific entry, or omit for all entries.",
        {
            "type": "object",
            "properties": {
                "key": _prop("Specific context key to retrieve (omit for all)", "string"),
            },
        },
    ),
    # --- Knowledge Base: Papers ---
    "store_paper": (
        handle_store_paper,
        "Save a paper reference to the knowledge base for future reference.",
        {
            "type": "object",
            "properties": {
                "title": _prop("Paper title", "string"),
                "authors": _prop("JSON array of author names, or comma-separated", "string"),
                "abstract": _prop("Paper abstract", "string"),
                "arxiv_id": _prop("arXiv ID", "string"),
                "year": _prop("Publication year", "integer"),
                "url": _prop("Paper URL", "string"),
                "relevance_notes": _prop("Why this paper is relevant to our research", "string"),
                "tags": _prop("JSON array of tags", "string"),
            },
            "required": ["title"],
        },
    ),
    # --- Knowledge Base: Summary ---
    "get_knowledge_summary": (
        handle_get_knowledge_summary,
        "Get a high-level summary of everything in the knowledge base: experiment counts, insights, papers, context.",
        {
            "type": "object",
            "properties": {},
        },
    ),
    # --- Slurm ---
    "submit_job": (
        handle_submit_job,
        "Submit a job to the Slurm HPC cluster. Returns a job ID for tracking.",
        {
            "type": "object",
            "properties": {
                "job_name": _prop("Name for the job", "string"),
                "script": _prop("Bash script content to run", "string"),
                "partition": _prop("Slurm partition (default 'gpu')", "string"),
                "gpus": _prop("Number of GPUs (default 1)", "integer"),
                "time_limit": _prop("Time limit (default '04:00:00')", "string"),
            },
            "required": ["job_name", "script"],
        },
    ),
    "check_job_status": (
        handle_check_job_status,
        "Check the status of a Slurm job by ID.",
        {
            "type": "object",
            "properties": {
                "job_id": _prop("Slurm job ID", "string"),
            },
            "required": ["job_id"],
        },
    ),
    "list_jobs": (
        handle_list_jobs,
        "List recent Slurm jobs, optionally filtered by status.",
        {
            "type": "object",
            "properties": {
                "status_filter": _prop("Filter by status (PENDING, RUNNING, COMPLETED, FAILED, CANCELLED)", "string"),
                "limit": _prop("Max results (default 20)", "integer"),
            },
        },
    ),
    "cancel_job": (
        handle_cancel_job,
        "Cancel a running or pending Slurm job.",
        {
            "type": "object",
            "properties": {
                "job_id": _prop("Slurm job ID to cancel", "string"),
            },
            "required": ["job_id"],
        },
    ),
    "get_job_logs": (
        handle_get_job_logs,
        "Get stdout/stderr logs from a Slurm job.",
        {
            "type": "object",
            "properties": {
                "job_id": _prop("Slurm job ID", "string"),
                "lines": _prop("Number of lines to return (default 100)", "integer"),
            },
            "required": ["job_id"],
        },
    ),
}


def get_tool_schemas() -> list[dict[str, Any]]:
    """Return Anthropic API tool definitions for all registered tools."""
    return [
        {
            "name": name,
            "description": desc,
            "input_schema": schema,
        }
        for name, (_, desc, schema) in TOOL_DEFINITIONS.items()
    ]


async def execute_tool(name: str, input_args: dict[str, Any]) -> str:
    """Execute a tool by name and return the text result."""
    entry = TOOL_DEFINITIONS.get(name)
    if not entry:
        return f"Unknown tool: {name}"
    handler = entry[0]
    result = await handler(input_args)
    # Extract text from MCP-style response
    content = result.get("content", [])
    texts = [block["text"] for block in content if block.get("type") == "text"]
    return "\n".join(texts) if texts else "No output"
