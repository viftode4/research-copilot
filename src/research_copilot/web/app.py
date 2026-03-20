"""FastAPI web server — the main product interface.

Provides a chat API with SSE streaming, backed by the Anthropic API
with tool use wired to our research tools (literature, KB, Slurm).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import anthropic
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from research_copilot.config import load_config
from research_copilot.domain.automl import DOMAIN_SYSTEM_PROMPT
from research_copilot.mcp_servers.registry import execute_tool, get_tool_schemas

app = FastAPI(title="Research Copilot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory conversation store (per session)
_sessions: dict[str, list[dict[str, Any]]] = {}

config = load_config()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    api_key: str | None = None  # BYOK: user provides their own key
    model: str | None = None  # Override model per request


class SessionInfo(BaseModel):
    session_id: str
    message_count: int


# ---------------------------------------------------------------------------
# Anthropic API tool use loop
# ---------------------------------------------------------------------------


def _get_client(api_key: str | None = None) -> anthropic.Anthropic:
    """Create Anthropic client. Uses provided key (BYOK) or falls back to env var."""
    if api_key:
        return anthropic.Anthropic(api_key=api_key)
    return anthropic.Anthropic()


async def _run_tool_loop(
    client: anthropic.Anthropic,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model: str | None = None,
) -> Any:
    """Run the agentic tool use loop. Yields SSE events as the model responds."""
    max_iterations = 15
    use_model = model or config.model

    for _ in range(max_iterations):
        # Stream the response
        with client.messages.stream(
            model=use_model,
            max_tokens=16000,
            system=DOMAIN_SYSTEM_PROMPT,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
        ) as stream:
            # Yield text deltas as they arrive
            for event in stream:
                if event.type == "content_block_start":
                    if hasattr(event, "content_block"):
                        if event.content_block.type == "text":
                            yield {"type": "text_start"}
                        elif event.content_block.type == "tool_use":
                            yield {
                                "type": "tool_start",
                                "tool": event.content_block.name,
                            }
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta"):
                        if event.delta.type == "text_delta":
                            yield {"type": "text_delta", "text": event.delta.text}

            response = stream.get_final_message()

        # Append assistant response to history
        messages.append({"role": "assistant", "content": response.content})

        # Check if we're done
        if response.stop_reason == "end_turn":
            yield {
                "type": "done",
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            }
            return

        if response.stop_reason == "pause_turn":
            # Server-side tool limit hit, continue
            continue

        # Extract tool use blocks and execute them
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            yield {"type": "done"}
            return

        tool_results = []
        for tool_block in tool_use_blocks:
            yield {
                "type": "tool_executing",
                "tool": tool_block.name,
                "input": _safe_serialize(tool_block.input),
            }
            result_text = await execute_tool(tool_block.name, tool_block.input)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": result_text,
                }
            )
            yield {
                "type": "tool_result",
                "tool": tool_block.name,
                "result_preview": result_text[:200],
            }

        messages.append({"role": "user", "content": tool_results})

    yield {"type": "done", "warning": "Max iterations reached"}


def _safe_serialize(obj: Any) -> Any:
    """Safely serialize tool input for SSE."""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the chat UI."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Research Copilot</h1><p>Static files not found. Run from project root.</p>")


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Chat endpoint with SSE streaming."""
    session_id = request.session_id or str(uuid.uuid4())

    # Get or create conversation history
    if session_id not in _sessions:
        _sessions[session_id] = []
    messages = _sessions[session_id]

    # Add user message
    messages.append({"role": "user", "content": request.message})

    tools = get_tool_schemas()
    client = _get_client(api_key=request.api_key)

    async def event_stream():
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"

        try:
            async for event in _run_tool_loop(client, messages, tools, model=request.model):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except anthropic.AuthenticationError:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Invalid API key. Check your key in Settings.'})}\n\n"
        except anthropic.RateLimitError:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Rate limited. Wait a moment and try again.'})}\n\n"
        except anthropic.APIError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'API error: {e.message}'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/sessions")
async def list_sessions():
    """List active sessions."""
    return [
        SessionInfo(session_id=sid, message_count=len(msgs))
        for sid, msgs in _sessions.items()
    ]


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get conversation history for a session."""
    messages = _sessions.get(session_id, [])
    # Return a simplified view (strip tool internals)
    simplified = []
    for msg in messages:
        if msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, str):
                simplified.append({"role": "user", "content": content})
            # Skip tool_result messages in the view
        elif msg["role"] == "assistant":
            texts = []
            content = msg["content"]
            if isinstance(content, list):
                for block in content:
                    if hasattr(block, "text"):
                        texts.append(block.text)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block["text"])
            if texts:
                simplified.append({"role": "assistant", "content": "\n".join(texts)})
    return simplified


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    _sessions.pop(session_id, None)
    return {"status": "ok"}


@app.get("/api/tools")
async def list_tools():
    """List all available tools."""
    return get_tool_schemas()


@app.get("/api/health")
async def health():
    """Health check."""
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {
        "status": "ok",
        "model": config.model,
        "api_key_set": has_key,
        "tools_count": len(get_tool_schemas()),
        "sessions_count": len(_sessions),
    }
