"""Literature Search MCP server — arXiv + Semantic Scholar integration.

Architecture: handler functions are plain async functions (testable directly).
The create_literature_server() function wraps them as MCP tools.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx

_ARXIV_BASE = "http://export.arxiv.org/api/query"
_S2_BASE = "https://api.semanticscholar.org/graph/v1"

# Reusable async client (connection pooling)
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


# ---------------------------------------------------------------------------
# arXiv helpers
# ---------------------------------------------------------------------------


def _parse_arxiv_entry(entry: ET.Element, ns: dict[str, str]) -> dict[str, Any]:
    """Parse a single arXiv Atom entry into a dict."""
    title = (entry.findtext("a:title", "", ns) or "").strip().replace("\n", " ")
    summary = (entry.findtext("a:summary", "", ns) or "").strip().replace("\n", " ")
    published = entry.findtext("a:published", "", ns) or ""
    arxiv_id_raw = entry.findtext("a:id", "", ns) or ""
    arxiv_id = arxiv_id_raw.split("/abs/")[-1] if "/abs/" in arxiv_id_raw else arxiv_id_raw

    authors = []
    for author_el in entry.findall("a:author", ns):
        name = author_el.findtext("a:name", "", ns)
        if name:
            authors.append(name)

    pdf_url = ""
    for link in entry.findall("a:link", ns):
        if link.get("title") == "pdf":
            pdf_url = link.get("href", "")

    categories = [
        cat.get("term", "")
        for cat in entry.findall("arxiv:primary_category", ns)
    ]

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": authors,
        "abstract": summary[:500] + ("..." if len(summary) > 500 else ""),
        "full_abstract": summary,
        "published": published[:10],
        "pdf_url": pdf_url,
        "categories": categories,
        "source": "arxiv",
    }


async def _search_arxiv(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Search arXiv via the Atom API."""
    client = _get_client()
    params = {
        "search_query": f"all:{quote(query)}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    resp = await client.get(_ARXIV_BASE, params=params)
    resp.raise_for_status()

    ns = {
        "a": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(resp.text)
    entries = root.findall("a:entry", ns)
    return [_parse_arxiv_entry(e, ns) for e in entries]


# ---------------------------------------------------------------------------
# Semantic Scholar helpers
# ---------------------------------------------------------------------------


async def _search_semantic_scholar(
    query: str, max_results: int = 10, api_key: str = ""
) -> list[dict[str, Any]]:
    """Search Semantic Scholar via their Graph API."""
    client = _get_client()
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    params = {
        "query": query,
        "limit": max_results,
        "fields": "title,authors,abstract,year,citationCount,venue,externalIds,url",
    }
    resp = await client.get(f"{_S2_BASE}/paper/search", params=params, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for paper in data.get("data", []):
        authors = [a.get("name", "") for a in paper.get("authors", [])]
        ext_ids = paper.get("externalIds", {}) or {}
        abstract = paper.get("abstract") or ""
        results.append({
            "semantic_scholar_id": paper.get("paperId", ""),
            "title": paper.get("title", ""),
            "authors": authors,
            "abstract": abstract[:500] + ("..." if len(abstract) > 500 else ""),
            "full_abstract": abstract,
            "year": paper.get("year"),
            "citation_count": paper.get("citationCount", 0),
            "venue": paper.get("venue", ""),
            "arxiv_id": ext_ids.get("ArXiv", ""),
            "doi": ext_ids.get("DOI", ""),
            "url": paper.get("url", ""),
            "source": "semantic_scholar",
        })
    return results


async def _get_paper_details(paper_id: str, api_key: str = "") -> dict[str, Any]:
    """Get detailed info about a specific paper from Semantic Scholar."""
    client = _get_client()
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    fields = (
        "title,authors,abstract,year,citationCount,venue,externalIds,"
        "url,references,citations,tldr"
    )
    resp = await client.get(
        f"{_S2_BASE}/paper/{paper_id}",
        params={"fields": fields},
        headers=headers,
    )
    resp.raise_for_status()
    paper = resp.json()

    authors = [a.get("name", "") for a in paper.get("authors", [])]
    ext_ids = paper.get("externalIds", {}) or {}
    tldr = paper.get("tldr", {}) or {}

    citations = [
        {"title": c.get("title", ""), "paperId": c.get("paperId", "")}
        for c in (paper.get("citations", []) or [])[:10]
    ]
    references = [
        {"title": r.get("title", ""), "paperId": r.get("paperId", "")}
        for r in (paper.get("references", []) or [])[:10]
    ]

    return {
        "semantic_scholar_id": paper.get("paperId", ""),
        "title": paper.get("title", ""),
        "authors": authors,
        "abstract": paper.get("abstract", ""),
        "year": paper.get("year"),
        "citation_count": paper.get("citationCount", 0),
        "venue": paper.get("venue", ""),
        "arxiv_id": ext_ids.get("ArXiv", ""),
        "doi": ext_ids.get("DOI", ""),
        "url": paper.get("url", ""),
        "tldr": tldr.get("text", ""),
        "top_citations": citations,
        "top_references": references,
    }


# ---------------------------------------------------------------------------
# Handler functions (plain async — directly testable)
# ---------------------------------------------------------------------------


async def handle_search_papers(args: dict[str, Any]) -> dict[str, Any]:
    query_text = args["query"]
    max_results = args.get("max_results", 10)
    sources = args.get("sources", "both")

    all_results: list[dict[str, Any]] = []

    if sources in ("arxiv", "both"):
        try:
            arxiv_results = await _search_arxiv(query_text, max_results)
            all_results.extend(arxiv_results)
        except Exception as e:
            all_results.append({"error": f"arXiv search failed: {e}", "source": "arxiv"})

    if sources in ("semantic_scholar", "both"):
        try:
            s2_results = await _search_semantic_scholar(query_text, max_results)
            all_results.extend(s2_results)
        except Exception as e:
            all_results.append(
                {"error": f"Semantic Scholar search failed: {e}", "source": "semantic_scholar"}
            )

    # Deduplicate by arxiv_id where possible
    seen_arxiv: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in all_results:
        aid = r.get("arxiv_id", "")
        if aid and aid in seen_arxiv:
            continue
        if aid:
            seen_arxiv.add(aid)
        deduped.append(r)

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"total": len(deduped), "papers": deduped}, indent=2, default=str
                ),
            }
        ]
    }


async def handle_get_paper_details(args: dict[str, Any]) -> dict[str, Any]:
    paper_id = args["paper_id"]

    # If it looks like an arXiv ID, prefix it
    if "." in paper_id and "/" not in paper_id and not paper_id.startswith("ARXIV:"):
        paper_id = f"ARXIV:{paper_id}"

    try:
        details = await _get_paper_details(paper_id)
        return {
            "content": [{"type": "text", "text": json.dumps(details, indent=2, default=str)}]
        }
    except httpx.HTTPStatusError as e:
        return {
            "content": [{"type": "text", "text": f"Paper not found or API error: {e}"}],
            "isError": True,
        }


async def handle_find_related_papers(args: dict[str, Any]) -> dict[str, Any]:
    paper_id = args["paper_id"]
    max_results = args.get("max_results", 10)

    if "." in paper_id and "/" not in paper_id and not paper_id.startswith("ARXIV:"):
        paper_id = f"ARXIV:{paper_id}"

    try:
        client = _get_client()
        fields = "title,authors,abstract,year,citationCount,venue,externalIds,url"
        resp = await client.get(
            f"{_S2_BASE}/recommendations/v1/papers/forpaper/{paper_id}",
            params={"fields": fields, "limit": max_results},
        )
        resp.raise_for_status()
        data = resp.json()

        related = []
        for paper in data.get("recommendedPapers", []):
            authors = [a.get("name", "") for a in paper.get("authors", [])]
            ext_ids = paper.get("externalIds", {}) or {}
            abstract = paper.get("abstract") or ""
            related.append({
                "semantic_scholar_id": paper.get("paperId", ""),
                "title": paper.get("title", ""),
                "authors": authors,
                "abstract": abstract[:500] + ("..." if len(abstract) > 500 else ""),
                "year": paper.get("year"),
                "citation_count": paper.get("citationCount", 0),
                "venue": paper.get("venue", ""),
                "arxiv_id": ext_ids.get("ArXiv", ""),
                "doi": ext_ids.get("DOI", ""),
                "url": paper.get("url", ""),
            })

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"total": len(related), "papers": related}, indent=2, default=str
                    ),
                }
            ]
        }
    except httpx.HTTPStatusError as e:
        return {
            "content": [{"type": "text", "text": f"Recommendations failed: {e}"}],
            "isError": True,
        }


