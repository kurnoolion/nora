"""The release-change lane of the Ask page (strand rel-history).

When a question is a release-diff question and the stack serves the
History store, the merged tab answers it from the History store instead
of retrieval: the changed requirements' previous and current texts go to
the language model in batches, in parallel, and the syntheses stream to
the page per plan as they arrive (`query.release_diff`). This module
holds the lane runner, the applicability check and the cancel registry;
the route in `playground.py` wires them to the SSE stream.

Cancel: every run gets a `run_id`; `POST /api/test/ask-cancel` sets its
event; the runner stops starting batches and delivers what finished.
"""

from __future__ import annotations

import os
import asyncio
import logging
import re
import threading
import time
import uuid
from typing import Any, Awaitable, Callable

from core.src.pipeline.error_codes import CODES, PipelineError
from core.src.query.analyzer import MockQueryAnalyzer
from core.src.query.release_diff import (
    ReleaseDiffConfig,
    build_batches,
    is_release_change_question,
    resolve_release_pair,
    synthesize_release_diff,
)
from core.src.query.schema import QueryIntent
from core.src.stores import open_history_store

logger = logging.getLogger(__name__)

LANE = "history"

_CANCELS: dict[str, threading.Event] = {}
_CANCELS_LOCK = threading.Lock()
_MAX_CANCELS = 256


def new_run_id() -> str:
    """Register a cancel event for a new run and return its id."""
    run_id = uuid.uuid4().hex[:16]
    with _CANCELS_LOCK:
        if len(_CANCELS) >= _MAX_CANCELS:          # forgotten runs: drop the oldest
            for k in list(_CANCELS)[: len(_CANCELS) - _MAX_CANCELS + 1]:
                _CANCELS.pop(k, None)
        _CANCELS[run_id] = threading.Event()
    return run_id


def cancel_event(run_id: str) -> "threading.Event | None":
    with _CANCELS_LOCK:
        return _CANCELS.get(run_id)


def cancel_run(run_id: str) -> bool:
    """Set the run's cancel event; False when the id is unknown."""
    ev = cancel_event(run_id)
    if ev is None:
        return False
    ev.set()
    return True


def forget_run(run_id: str) -> None:
    with _CANCELS_LOCK:
        _CANCELS.pop(run_id, None)


def classify(question: str) -> QueryIntent:
    """The rules analyzer: cheap, deterministic, the same one the store
    uses for scope (D-DRAFT-9 lineage)."""
    return MockQueryAnalyzer().analyze(question)


def history_diff_applies(question: str, *, config: "ReleaseDiffConfig | None" = None
                         ) -> "QueryIntent | None":
    """The intent when this lane should answer: a release-diff question,
    the lane enabled, and a History store configured. None otherwise, and
    the SIRA lane answers as before."""
    cfg = config or ReleaseDiffConfig.from_env()
    if not cfg.enabled:
        return None
    intent = classify(question)
    if not is_release_change_question(question, intent):
        return None
    try:
        store = open_history_store()
    except Exception:                                   # noqa: BLE001 — treated as off
        store = None
    return intent if store is not None else None


# ── answer assembly ───────────────────────────────────────────────────

_ID_CAP = 10


def _cap_ids(ids: list) -> str:
    shown = ", ".join(ids[:_ID_CAP])
    return shown + (f" and {len(ids) - _ID_CAP} more" if len(ids) > _ID_CAP else "")


def _change_score(p: dict) -> int:
    c = p.get("counts", {})
    return c.get("changed", 0) + c.get("added", 0) + c.get("removed", 0)


def answer_markdown(result: dict) -> str:
    """One markdown document from the reassembled plans: a counts table
    first (plans by real change count, then id), then each plan collapsed
    with its syntheses in order and the store-derived notes, then the
    absent plans. Requirement ids stay literal so they become bubbles;
    id lists in status lines are capped."""
    lines: list[str] = []
    if result.get("cancelled"):
        lines.append("_Cancelled before every plan was synthesized; the plans below are complete._\n")
    plans = sorted(result.get("plans", []), key=lambda p: (-_change_score(p), p["plan_id"]))
    if plans:
        lines.append("| Plan | Changed | Added | Removed | Renumbered | Moved | Bookkeeping only |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for p in plans:
            c = p.get("counts", {})
            name = f"{p['plan_id']}" + (f" — {p['plan_name']}" if p.get("plan_name") else "")
            lines.append(f"| {name} | {c.get('changed', 0)} | {c.get('added', 0)} | {c.get('removed', 0)} "
                         f"| {c.get('renumbered', 0)} | {c.get('moved', 0)} | {c.get('dropped', 0)} |")
        lines.append("")
    for p in plans:
        title = p["plan_id"] + (f" — {p['plan_name']}" if p.get("plan_name") else "")
        c = p.get("counts", {})
        brief = ", ".join(f"{c.get(k, 0)} {k}" for k in ("changed", "added", "removed") if c.get(k))
        lines.append('<details markdown="1">')
        lines.append(f"<summary>{title}" + (f" · {brief}" if brief else " · bookkeeping only") + "</summary>")
        lines.append("")
        for s in p.get("sections", []):
            if s["status"] == "ok" and s.get("text"):
                lines.append(s["text"].strip())
            elif s["status"] == "cancelled":
                lines.append(f"_{s['kind']}: not synthesized (cancelled); "
                             f"ids: {_cap_ids(s.get('ids', []))}_")
            elif s["status"] == "error":
                lines.append(f"_{s['kind']}: synthesis failed ({s.get('error', 'RHD-E004')}); "
                             f"ids: {_cap_ids(s.get('ids', []))}_")
            lines.append("")
        for n in p.get("notes", []):
            lines.append(f"- {n}")
        if p.get("notes"):
            lines.append("")
        lines.append("</details>")
        lines.append("")
    absent = result.get("absent_plans") or []
    if absent:
        lines.append("### Plans not present in one release")
        for a in absent:
            lines.append(f"- {a['plan_id']}: not part of {a['absent_in']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


_ID_RE = re.compile(r"[A-Z][A-Z0-9_.-]*\d[A-Z0-9_.-]*")


def _ids_in(text: str, known: set[str]) -> list[str]:
    return sorted(i for i in known if i in text)


# ── the lane ──────────────────────────────────────────────────────────

async def run_history_lane(
    question: str, intent: QueryIntent, *,
    provider: Any,
    run_id: str,
    emit_progress: "Callable[[str], Awaitable[None]] | None" = None,
    emit_partial: "Callable[[dict], Awaitable[None]] | None" = None,
    config: "ReleaseDiffConfig | None" = None,
    history: Any = None,
) -> dict[str, Any]:
    """Resolve MNO and releases, fetch the pairs, synthesize with
    streaming partials, and return the lane output in the merged tab's
    shape (`result`, `elapsed_ms`, `retrieved_ids`, `cited_ids`,
    `lane_config`, plus `history` with the reassembled result)."""
    start = time.time()
    cfg = config or ReleaseDiffConfig.from_env()
    loop = asyncio.get_running_loop()

    async def say(msg: str) -> None:
        if emit_progress is not None:
            await emit_progress(msg)

    def coded(code: str, **kw: str) -> dict[str, Any]:
        d = CODES[code]
        return {"error": f"[{code}] {d.message.format(**kw)}", "error_code": code}

    store = history if history is not None else open_history_store()
    if store is None:
        return coded("RHD-E003")
    try:
        mnos = await asyncio.to_thread(store.list_mnos)
        wanted = [m for m in intent.mnos if m in mnos]
        if wanted:
            mno = wanted[0]
        elif len(mnos) == 1:
            mno = mnos[0]
        else:
            return {"error": "Name the MNO the release question is about; served: "
                             + ", ".join(mnos), "error_code": ""}
        releases = await asyncio.to_thread(store.releases, mno)
        rel_a, rel_b = resolve_release_pair(intent.releases, releases)
        await say(f"History: {mno} {rel_a} → {rel_b}; reading changed requirements…")
        payload = await asyncio.to_thread(store.changed_pairs, mno, rel_a, rel_b)
    except PipelineError as exc:
        return {"error": f"[{exc.code}] {exc.message}", "error_code": exc.code}
    except Exception as exc:                            # noqa: BLE001 — surfaced to the page
        logger.exception("history lane: store call failed")
        return {"error": f"History store call failed: {type(exc).__name__}"}

    c = payload.get("counts", {})
    n_batches = len(build_batches(payload, cfg.max_batch_bytes))
    await say(f"{c.get('plans', 0)} plan(s) with changes, {c.get('changed', 0)} changed, "
              f"{c.get('added', 0)} added, {c.get('removed', 0)} removed, "
              f"{c.get('absent_plans', 0)} plan(s) absent from one release; "
              f"{n_batches} batches, {cfg.parallel} at a time…")

    cancel = cancel_event(run_id) or threading.Event()

    def on_partial(p: dict) -> None:
        if emit_partial is None:
            return
        fut = asyncio.run_coroutine_threadsafe(emit_partial(p), loop)
        try:
            fut.result(timeout=5)
        except Exception:                               # noqa: BLE001 — a slow client never stalls the run
            pass

    result = await asyncio.to_thread(
        synthesize_release_diff, payload, provider, config=cfg, cancel=cancel,
        on_partial=on_partial)
    forget_run(run_id)
    elapsed_ms = int((time.time() - start) * 1000)
    known = {i for p in result["plans"] for s in p["sections"] for i in s.get("ids", [])}
    known |= {i for p in result["plans"] for i in p.get("note_ids", [])}   # triage notes name ids too
    answer = answer_markdown(result)
    cited = _ids_in(answer, known)
    from core.src.llm.base import answering_model  # local: keeps this module import-light
    lane_config = {
        "lane": LANE, "stack": os.getenv("NORA_STACK_NAME", ""),
        "mno": mno, "from": rel_a, "to": rel_b,
        "parallel": cfg.parallel, "max_batch_bytes": cfg.max_batch_bytes,
        "batches": result["run"]["batches"], "done": result["run"]["done"],
        "failed": result["run"]["failed"], "cancelled": result["cancelled"],
        "llm_model": answering_model(provider) or getattr(provider, "model", "") or "",
        # the rest of the RHD line, so the stored row rebuilds it: the
        # store's counts and the run's timing
        "counts": dict(result.get("counts", {})),
        "elapsed_ms": result["run"]["elapsed_ms"],
        "batch_ms": dict(result["run"].get("batch_ms", {})),
        "triage": dict(result.get("triage", {})),
        "retried": result["run"].get("retried", 0),
        "stripped": result["run"].get("stripped", 0),
        "errors": list(result["run"].get("errors", [])),
    }
    return {
        "result": {
            "answer": answer,
            "citations": [],
            # bubbles anchor on rag_chunks; the "Cited by LLM" list stays
            # empty — hundreds of ids made the page's bulk in the field
            "llm_citations": [],
            "rag_chunks": [{"req_id": i} for i in sorted(known)],
            "rag_chunk_count": len(known),
            "llm_model": lane_config["llm_model"],
            "timings_ms": {"synth": result["run"]["elapsed_ms"], "total_ms": elapsed_ms},
        },
        "elapsed_ms": elapsed_ms,
        "retrieved_ids": sorted(known),
        "reranked_ids": None,
        "cited_ids": cited,
        "lane_config": lane_config,
        "history": result,
    }
