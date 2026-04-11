"""Tests for the literature search MCP server."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from research_copilot.mcp_servers.literature import (
    _parse_arxiv_entry,
    handle_find_related_papers,
    handle_get_paper_details,
    handle_search_papers,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ARXIV_RESPONSE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2301.12345v1</id>
    <title>Learning Curve Extrapolation with PFNs</title>
    <summary>We propose a method for extrapolating learning curves using
    prior-fitted networks. Our approach achieves state-of-the-art results
    on the LCDB benchmark.</summary>
    <published>2023-01-15T00:00:00Z</published>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/2301.12345v1"/>
    <arxiv:primary_category term="cs.LG"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2302.67890v2</id>
    <title>Meta-Learning for AutoML</title>
    <summary>A comprehensive survey of meta-learning approaches for
    automated machine learning.</summary>
    <published>2023-02-20T00:00:00Z</published>
    <author><name>Carol Davis</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/2302.67890v2"/>
    <arxiv:primary_category term="cs.AI"/>
  </entry>
</feed>
"""

S2_SEARCH_RESPONSE = {
    "data": [
        {
            "paperId": "abc123",
            "title": "Deep Learning Curves",
            "authors": [{"name": "Eve Wilson"}],
            "abstract": "We study deep learning curves for neural networks.",
            "year": 2023,
            "citationCount": 42,
            "venue": "NeurIPS",
            "externalIds": {"ArXiv": "2301.12345", "DOI": "10.1234/test"},
            "url": "https://semanticscholar.org/paper/abc123",
        },
        {
            "paperId": "def456",
            "title": "Scaling Laws Revisited",
            "authors": [{"name": "Frank Lee"}, {"name": "Grace Kim"}],
            "abstract": "We revisit scaling laws for language models.",
            "year": 2024,
            "citationCount": 15,
            "venue": "ICML",
            "externalIds": {"DOI": "10.5678/scaling"},
            "url": "https://semanticscholar.org/paper/def456",
        },
    ]
}

S2_PAPER_DETAIL = {
    "paperId": "abc123",
    "title": "Deep Learning Curves",
    "authors": [{"name": "Eve Wilson"}],
    "abstract": "We study deep learning curves for neural networks.",
    "year": 2023,
    "citationCount": 42,
    "venue": "NeurIPS",
    "externalIds": {"ArXiv": "2301.12345"},
    "url": "https://semanticscholar.org/paper/abc123",
    "tldr": {"text": "A study of deep learning curves."},
    "citations": [{"title": "Citing Paper 1", "paperId": "cit1"}],
    "references": [{"title": "Reference 1", "paperId": "ref1"}],
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestArxivParsing:
    def test_parse_arxiv_entries(self):
        import xml.etree.ElementTree as ET

        ns = {
            "a": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }
        root = ET.fromstring(ARXIV_RESPONSE_XML)
        entries = root.findall("a:entry", ns)
        assert len(entries) == 2

        paper = _parse_arxiv_entry(entries[0], ns)
        assert paper["arxiv_id"] == "2301.12345v1"
        assert paper["title"] == "Learning Curve Extrapolation with PFNs"
        assert len(paper["authors"]) == 2
        assert paper["authors"][0] == "Alice Smith"
        assert paper["source"] == "arxiv"
        assert "2023-01-15" in paper["published"]


class TestSearchPapers:
    @pytest.mark.asyncio
    async def test_search_papers_both_sources(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ARXIV_RESPONSE_XML
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = S2_SEARCH_RESPONSE

        with patch(
            "research_copilot.mcp_servers.literature._get_client"
        ) as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await handle_search_papers(
                {"query": "learning curves", "max_results": 5, "sources": "both"}
            )

        assert "content" in result
        content = json.loads(result["content"][0]["text"])
        assert content["total"] > 0
        assert "papers" in content

    @pytest.mark.asyncio
    async def test_search_papers_arxiv_only(self):
        mock_response = MagicMock()
        mock_response.text = ARXIV_RESPONSE_XML
        mock_response.raise_for_status = MagicMock()

        with patch(
            "research_copilot.mcp_servers.literature._get_client"
        ) as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await handle_search_papers(
                {"query": "PFN", "max_results": 5, "sources": "arxiv"}
            )

        content = json.loads(result["content"][0]["text"])
        assert content["total"] == 2
        for paper in content["papers"]:
            assert paper["source"] == "arxiv"


class TestPaperDetails:
    @pytest.mark.asyncio
    async def test_get_paper_details(self):
        # Use MagicMock for response since httpx .json() is sync
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = S2_PAPER_DETAIL
        mock_response.raise_for_status = MagicMock()

        with patch(
            "research_copilot.mcp_servers.literature._get_client"
        ) as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await handle_get_paper_details({"paper_id": "abc123"})

        content = json.loads(result["content"][0]["text"])
        assert content["title"] == "Deep Learning Curves"
        assert content["tldr"] == "A study of deep learning curves."
        assert len(content["top_citations"]) == 1

    @pytest.mark.asyncio
    async def test_arxiv_id_prefix(self):
        """arXiv IDs should be auto-prefixed with ARXIV:."""
        mock_response = MagicMock()
        mock_response.json.return_value = S2_PAPER_DETAIL
        mock_response.raise_for_status = MagicMock()

        with patch(
            "research_copilot.mcp_servers.literature._get_client"
        ) as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            await handle_get_paper_details({"paper_id": "2301.12345"})

            # Verify the API was called with ARXIV: prefix
            call_url = client.get.call_args[0][0]
            assert "ARXIV:2301.12345" in call_url


class TestRelatedPapers:
    @pytest.mark.asyncio
    async def test_find_related_papers(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "recommendedPapers": [
                {
                    "paperId": "rel1",
                    "title": "Follow-up Study",
                    "authors": [{"name": "Ada Lovelace"}],
                    "abstract": "A strong follow-up.",
                    "year": 2025,
                    "citationCount": 7,
                    "venue": "ICLR",
                    "externalIds": {"ArXiv": "2501.00001"},
                    "url": "https://semanticscholar.org/paper/rel1",
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("research_copilot.mcp_servers.literature._get_client") as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            result = await handle_find_related_papers({"paper_id": "2301.12345", "max_results": 3})

        content = json.loads(result["content"][0]["text"])
        assert content["total"] == 1
        assert content["papers"][0]["title"] == "Follow-up Study"
        call_url = client.get.call_args[0][0]
        assert "ARXIV:2301.12345" in call_url
