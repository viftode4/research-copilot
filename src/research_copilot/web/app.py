"""Legacy web compatibility shim for the terminal-first Research Copilot.

The shipped product surface is moving to a full-screen terminal UI.  This
module keeps the historic ``research_copilot.web.app:app`` import path valid
for transitional tooling while making it clear that the web interface is no
longer the intended workflow.
"""

from __future__ import annotations

from fastapi import FastAPI, status
from fastapi.responses import HTMLResponse, JSONResponse

DEPRECATION_MESSAGE = (
    "Research Copilot has moved to a terminal-first workflow UI. "
    "Launch the `research-copilot` CLI instead of relying on the removed web app."
)

app = FastAPI(title="Research Copilot (legacy web shim)", version="0.1.0")


@app.get("/", response_class=HTMLResponse, status_code=status.HTTP_410_GONE)
async def index() -> HTMLResponse:
    """Explain that the legacy web UI has been removed."""
    return HTMLResponse(
        "<h1>Research Copilot</h1>"
        "<p>The legacy web UI has been removed.</p>"
        "<p>Launch the terminal workflow with <code>research-copilot</code>.</p>",
        status_code=status.HTTP_410_GONE,
    )


@app.get("/api/health")
async def health() -> JSONResponse:
    """Return a deprecation signal for legacy automation."""
    return JSONResponse(
        {
            "status": "deprecated",
            "message": DEPRECATION_MESSAGE,
            "primary_interface": "terminal",
        }
    )
