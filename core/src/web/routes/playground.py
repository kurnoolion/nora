"""Test page — multi-section playground for free-form requirement
queries with thumbs-up/down feedback capture.

Currently only the `requirement_bot` section is functional; other
section IDs are placeholders rendered as "Coming soon" in the
template. Each Q&A pair is logged to `<env_dir>/state/
nora_test_feedback.db` via `FeedbackStore`; the user's later
thumbs-up/down + free-form feedback updates the same row.

The Requirement Bot reuses the production query path
(`_run_query_sync` in routes/query.py) so the test page exercises
the same retrieval/synthesis stack as `/query`. The test page
adds: per-Q&A persistence + a feedback widget. It does NOT add
a job queue — calls block synchronously until the answer arrives,
which is the expected UX for a "ask + read + vote" interaction.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# Section registry. Add a new section by appending to this list and
# wiring its handler in `_run_section()`. The template renders one
# tab per entry; only sections marked `enabled=True` get an active
# tab body — others render a "Coming soon" placeholder.
_SECTIONS: list[dict[str, Any]] = [
    {
        "id": "requirement_bot",
        "label": "Requirement Bot",
        "enabled": True,
        "blurb": (
            "Ask any free-form question about the indexed VZW OA "
            "requirements corpus. The answer is synthesized from "
            "retrieved chunks; rate it below to log feedback for "
            "offline review."
        ),
    },
    {
        "id": "compliance_check",
        "label": "Compliance Check",
        "enabled": False,
        "blurb": "Single-requirement compliance against device specs.",
    },
    {
        "id": "cross_mno_compare",
        "label": "Cross-MNO Compare",
        "enabled": False,
        "blurb": "Compare requirement coverage across operators.",
    },
    {
        "id": "standards_lookup",
        "label": "Standards Lookup",
        "enabled": False,
        "blurb": "Look up 3GPP TS section text by spec + section.",
    },
]


# -- Pages ------------------------------------------------------------------


@router.get("/test", response_class=HTMLResponse)
async def playground_page(request: Request, section: str = "requirement_bot"):
    from core.src.web.app import _template_response

    # Default to first enabled section if user passed an unknown id
    valid_ids = {s["id"] for s in _SECTIONS}
    active_section = section if section in valid_ids else "requirement_bot"

    return _template_response(request, "test/index.html", {
        "sections": _SECTIONS,
        "active_section": active_section,
    })


# -- API: ask + feedback ----------------------------------------------------


@router.post("/api/test/ask", response_class=HTMLResponse)
async def playground_ask(request: Request):
    """Submit a question, run the query pipeline, log the Q&A row,
    return rendered answer + citations + feedback widget seeded with
    the row id."""
    from core.src.web.app import _template_response

    form = await request.form()
    question = (form.get("question") or "").strip()
    section = (form.get("section") or "requirement_bot").strip()

    if not question:
        return _template_response(request, "test/_answer.html", {
            "error": "Question is required.",
        })

    # Hand off to the section's runner. Currently only requirement_bot
    # is wired; other sections are tab-disabled in the template, but
    # an explicit POST against them returns a friendly stub.
    if section != "requirement_bot":
        return _template_response(request, "test/_answer.html", {
            "error": f"Section '{section}' is not yet implemented.",
        })

    # Run the query (blocks the request — sync-style UX). Reuses the
    # existing /query path's pipeline construction.
    start = time.time()
    try:
        result = await asyncio.to_thread(_run_query_for_test, question, request.app)
    except Exception as e:
        logger.exception("Test query failed")
        return _template_response(request, "test/_answer.html", {
            "error": f"Query failed: {e}",
        })
    elapsed_ms = int((time.time() - start) * 1000)

    if "error" in result:
        return _template_response(request, "test/_answer.html", {
            "error": result["error"],
        })

    # Persist the Q&A row. The feedback widget renders below with the
    # returned row id; the user's vote later updates this row in
    # place.
    feedback_store = request.app.state.feedback_store
    row_id = await feedback_store.record_qa(
        section=section,
        question=question,
        answer=result.get("answer", ""),
        citations=result.get("citations", []),
        query_elapsed_ms=elapsed_ms,
        llm_model=result.get("llm_model"),
        metadata={"candidate_count": result.get("candidate_count")},
    )

    return _template_response(request, "test/_answer.html", {
        "row_id": row_id,
        "question": question,
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
        "llm_citations": result.get("llm_citations", []),
        "rag_chunks": result.get("rag_chunks", []),
        "rag_chunk_count": result.get("rag_chunk_count", 0),
        "elapsed_ms": elapsed_ms,
        "candidate_count": result.get("candidate_count"),
        "section": section,
    })


@router.post("/api/test/feedback", response_class=HTMLResponse)
async def playground_feedback(
    request: Request,
    row_id: int = Form(...),
    vote: str = Form(""),
    free_form_feedback: str = Form(""),
):
    """Update an existing Q&A row with the user's vote / comment.
    Returns a small confirmation HTML fragment for HTMX swap."""
    from core.src.web.app import _template_response

    vote_clean = vote.strip().lower() or None
    if vote_clean not in ("up", "down", None):
        return _template_response(request, "test/_feedback_ack.html", {
            "error": f"Invalid vote: {vote!r}",
        })

    feedback_store = request.app.state.feedback_store
    try:
        ok = await feedback_store.record_feedback(
            row_id=row_id,
            vote=vote_clean,
            free_form_feedback=(free_form_feedback or "").strip() or None,
        )
    except Exception as e:
        logger.exception("Feedback persist failed")
        return _template_response(request, "test/_feedback_ack.html", {
            "error": f"Could not save feedback: {e}",
        })

    if not ok:
        return _template_response(request, "test/_feedback_ack.html", {
            "error": f"No feedback row with id={row_id}",
        })

    return _template_response(request, "test/_feedback_ack.html", {
        "row_id": row_id,
        "vote": vote_clean,
    })


# -- Helpers ----------------------------------------------------------------


def _run_query_for_test(question: str, app=None) -> dict:
    """Adapt the existing /query pipeline runner into a dict shape
    the test page templates can consume directly. Re-imports the
    helper from the query module so we don't fork pipeline
    construction logic. `app` is passed through so the cached
    pipeline on `app.state` is reused across requests.

    Surfaces three citation views to the template:
      - `citations`: legacy combined list (LLM-cited + fallback)
      - `llm_citations`: subset cited explicitly in the answer text
      - `rag_chunks`: every chunk RAG returned (with text for the
        click-to-expand fragment view)
    """
    from core.src.web.routes.query import _run_query_sync

    raw = _run_query_sync(question, app=app)
    if "error" in raw:
        return {"error": raw["error"]}

    # _run_query_sync may attach _llm_metrics; pop it (we don't
    # display it on the test page).
    raw.pop("_llm_metrics", None)

    return {
        "answer": raw.get("answer", ""),
        "citations": raw.get("citations", []) or [],
        "llm_citations": raw.get("llm_citations", []) or [],
        "rag_chunks": raw.get("rag_chunks", []) or [],
        "rag_chunk_count": raw.get("rag_chunk_count", 0),
        "candidate_count": raw.get("candidate_count"),
        "llm_model": raw.get("llm_model"),
    }
