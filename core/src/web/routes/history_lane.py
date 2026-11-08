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
import html
import re
import threading
import time
import uuid
from typing import Any, Awaitable, Callable

from core.src.pipeline.error_codes import CODES, PipelineError
from core.src.query.analyzer import MockQueryAnalyzer
from core.src.query.release_diff import (
    ReleaseDiffConfig,
    display_release,
    build_batches,
    resolve_release_pair,
    synthesize_release_diff,
)
from core.src.query.schema import Intent, QueryIntent
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


def history_diff_applies(question: str, *, config: "ReleaseDiffConfig | None" = None,
                         intent: "QueryIntent | None" = None, strict: bool = False,
                         ) -> "QueryIntent | None":
    """The intent when this lane should answer: a ``change`` intent, the
    lane enabled, and a History store configured. None otherwise, and the
    SIRA lane answers as before.

    ``intent`` is what nora-web already computed (strand sira-intent,
    D-DRAFT-3); without it the rules classify here. On the rules arm
    ``Intent.CHANGE`` is exactly ``is_release_change_question``.

    ``strict`` (``NORA_SIRA_INTENT_PROMPTS`` on, D-DRAFT-5): a change
    question with the lane enabled but no History store, or one that won't
    open, raises ``PipelineError("RHD-E003")`` instead of returning None.
    A disabled lane still returns None (an operator's choice, not a failure)."""
    cfg = config or ReleaseDiffConfig.from_env()
    if not cfg.enabled:
        return None
    intent = intent if intent is not None else classify(question)
    if intent.intent is not Intent.CHANGE:
        return None
    try:
        store = open_history_store()
    except Exception:                                   # noqa: BLE001 — treated as off
        store = None
    if store is None and strict:
        raise PipelineError("RHD-E003", {})
    return intent if store is not None else None


# ── answer assembly ───────────────────────────────────────────────────

_ID_CAP = 10


def _cap_ids(ids: list) -> str:
    shown = ", ".join(ids[:_ID_CAP])
    return shown + (f" and {len(ids) - _ID_CAP} more" if len(ids) > _ID_CAP else "")


def _change_score(p: dict) -> int:
    c = p.get("counts", {})
    return c.get("changed", 0) + c.get("added", 0) + c.get("removed", 0)


def _summary_lines(result: dict) -> list[str]:
    """The compared releases and the overall picture, first thing on the
    page: requirements per release, then changed / added / removed /
    renumbered / moved counts and the plans."""
    disp = result.get("display") or {}
    mno = disp.get("mno") or result.get("mno", "")
    a, b = disp.get("from") or result.get("from", ""), disp.get("to") or result.get("to", "")
    c = result.get("counts", {})
    plans = result.get("plans", [])
    # headline numbers are store-derived and the same on every run; the
    # model's own "unchanged in meaning" verdicts vary between runs, so
    # they are stated beside the stable count, never in its place
    same = sum(p.get("counts", {}).get("same", 0) for p in plans)
    sent = sum(p.get("counts", {}).get("changed", 0) for p in plans)
    dropped = sum(p.get("counts", {}).get("dropped", 0) for p in plans) - same
    lines = [f"**{mno}: {a} → {b}**", ""] if (mno or a or b) else []
    bits = []
    if c.get("requirements_from") is not None and c.get("requirements_to") is not None:
        bits.append(f"{c['requirements_from']} requirement(s) in {a}, {c['requirements_to']} in {b}")
    bits.append(f"{sent} changed in text"
                + (f" (the model judged {same} of them unchanged in meaning on this run)" if same else "")
                + (f"; another {dropped} differ in bookkeeping only" if dropped else ""))
    bits.append(f"{c.get('added', 0)} added, {c.get('removed', 0)} removed, "
                f"{c.get('renumbered', 0)} renumbered, {c.get('moved', 0)} moved")
    bits.append(f"{len(plans)} plan(s) with changes"
                + (f", {c['absent_plans']} plan(s) present in one release only" if c.get("absent_plans") else ""))
    lines.append("; ".join(bits) + ".")
    lines.append("")
    return lines


_COLUMNS = ("changed", "added", "removed", "renumbered", "moved", "bookkeeping")
_COLUMN_HEADS = ("Changed", "Added", "Removed", "Renumbered", "Moved", "Bookkeeping only")


def _attr(value) -> str:
    return html.escape(str(value), quote=True)


def _note_head(note: str) -> str:
    """A triage note without its id list: "3 requirement(s) differ only in
    the revision record: [R2], [R3]." -> "3 requirement(s) differ only in
    the revision record"."""
    return re.split(r":\s*\[", note, 1)[0].rstrip(". ")


def _cell_tips(p: dict, a: str, b: str) -> dict:
    """Hover text per counts column, from the store's numbers and the
    triage notes; the same wording the summary line uses."""
    c = p.get("counts", {})
    groups = p.get("note_groups") or {"structural": [], "bookkeeping": p.get("notes", [])}
    ids = {k: [i for s in p.get("sections", []) if s["kind"] == k for i in s.get("ids", [])]
           for k in ("added", "removed")}
    tips = {"changed": f"{c.get('changed', 0)} requirement(s) changed in text"
                       + (f"; the model judged {c['same']} of them unchanged in meaning on this run."
                          if c.get("same") else ".")}
    tips["added"] = (f"Added in {b}: {_cap_ids(ids['added'])}." if ids["added"]
                     else f"{c.get('added', 0)} requirement(s) added in {b}.")
    tips["removed"] = (f"Removed after {a}: {_cap_ids(ids['removed'])}." if ids["removed"]
                       else f"{c.get('removed', 0)} requirement(s) removed after {a}.")
    renum = [n for n in groups.get("structural", []) if "renumbered (" in n]
    tips["renumbered"] = renum[0] if renum else f"{c.get('renumbered', 0)} requirement(s) renumbered; content unchanged."
    tips["moved"] = f"{c.get('moved', 0)} requirement(s) moved to another section; content unchanged."
    heads = [_note_head(n).replace("requirement(s) ", "", 1) for n in groups.get("bookkeeping", [])]
    n_book = c.get("bookkeeping", c.get("dropped", 0))
    tips["bookkeeping"] = (f"{n_book} with no change in meaning: " + "; ".join(heads) + "." if heads
                           else f"{n_book} requirement(s) with no change in meaning.")
    return tips


def _section_block(kind: str, lead: str, body: list[str]) -> list[str]:
    """One plan section: a bold title, then its entries in a body block the
    stylesheet draws as a thread under the title."""
    return ([f'<div class="rc-sec" data-kind="{kind}" markdown="1">', "", f"**{lead}**", "",
             '<div class="rc-body" markdown="1">', ""] + body + ["", "</div>", "", "</div>", ""])


def answer_markdown(result: dict) -> str:
    """One markdown document from the reassembled plans, as one
    `.rel-change` block the page script can re-shape (`static/js/
    release_change.js`: table / tree / expanded views and the export
    menu); without the script it reads as a tree. In order: the compared
    releases and a summary line (`.rc-head`), a counts table (`.rc-counts`,
    plans by change count then id, each non-zero count a button with the
    column's summary as its hover text), then each plan collapsed
    (`details.rc-plan`) holding one `.rc-sec` per kind: "Changed" with the
    pairs' syntheses, "Removed" and "Added" with the ids listed by the lane
    before the model's summary, "Structural changes only", and
    "Bookkeeping only" whose ids stay collapsed; then the absent plans.
    Requirement ids stay literal so they become bubbles; id lists in
    status lines are capped."""
    disp = result.get("display") or {}
    a, b = disp.get("from") or result.get("from", ""), disp.get("to") or result.get("to", "")
    mno = disp.get("mno") or result.get("mno", "")
    lines: list[str] = [f'<div class="rel-change" data-mno="{_attr(mno)}" data-from="{_attr(a)}" '
                        f'data-to="{_attr(b)}" markdown="1">', "", '<div class="rc-head" markdown="1">', ""]
    if result.get("cancelled"):
        lines.append("_Cancelled before every plan was synthesized; the plans below are complete._\n")
    lines.extend(_summary_lines(result))
    lines += ["</div>", ""]
    plans = sorted(result.get("plans", []), key=lambda p: (-_change_score(p), p["plan_id"]))
    if plans:
        lines += ['<table class="rc-counts">', "<thead><tr>"
                  + "".join(f"<th>{h}</th>" for h in ("Plan",) + _COLUMN_HEADS) + "</tr></thead>", "<tbody>"]
        for p in plans:
            c, tips = p.get("counts", {}), _cell_tips(p, a, b)
            name = f"{p['plan_id']}" + (f" — {p['plan_name']}" if p.get("plan_name") else "")
            cells = []
            for k in _COLUMNS:
                n = c.get(k, c.get("dropped", 0)) if k == "bookkeeping" else c.get(k, 0)
                tab = "structural" if k in ("renumbered", "moved") else k
                cells.append(f'<td><button type="button" class="rc-count" data-tab="{tab}" '
                             f'title="{_attr(tips[k])}">{n}</button></td>' if n
                             else '<td><span class="rc-zero">0</span></td>')
            lines.append(f'<tr class="rc-row" data-plan="{_attr(p["plan_id"])}">'
                         f'<td class="rc-plan-name">{html.escape(name)}</td>' + "".join(cells) + "</tr>")
        lines += ["</tbody>", "</table>", ""]
    lines += ['<div class="rc-tree" markdown="1">', ""]
    for p in plans:
        title = p["plan_id"] + (f" — {p['plan_name']}" if p.get("plan_name") else "")
        c = p.get("counts", {})
        brief = ", ".join(f"{c.get(k, 0)} {k}" for k in ("changed", "added", "removed") if c.get(k))
        lines.append(f'<details class="rc-plan" data-plan="{_attr(p["plan_id"])}" markdown="1">')
        lines.append(f"<summary>{title}" + (f" · {brief}" if brief else " · bookkeeping only") + "</summary>")
        lines.append("")
        by_kind: dict[str, list[str]] = {"changed": [], "added": [], "removed": []}
        leads: dict[str, str] = {"changed": f"Changed: {c.get('changed', 0)} requirement(s)"}
        for s in p.get("sections", []):
            kind = s["kind"] if s["kind"] in by_kind else "changed"
            body = by_kind[kind]
            if s["status"] == "ok":
                if s.get("lead") and kind not in leads:
                    # the lane's own "Removed: [ids]" / "Added: [ids]" line is the section title
                    leads[kind] = s["lead"]
                elif s.get("lead"):
                    body += [f"**{s['lead']}**", ""]      # a further batch of the same kind
                if s.get("text"):
                    body += [s["text"].strip(), ""]
            elif s["status"] == "cancelled":
                body += [f"_{s['kind']}: not synthesized (cancelled); ids: {_cap_ids(s.get('ids', []))}_", ""]
            elif s["status"] == "error":
                body += [f"_{s['kind']}: synthesis failed ({s.get('error', 'RHD-E004')}); "
                         f"ids: {_cap_ids(s.get('ids', []))}_", ""]
        if c.get("changed") and not by_kind["changed"]:
            # the pairs exist but no synthesis text does: say so in place,
            # so the table's count and the plan's sections agree
            pair_ids = [i for s in p.get("sections", []) if s["kind"] == "pair" for i in s.get("ids", [])]
            missing = [i for i in pair_ids if i not in set(p.get("model_same") or [])]
            by_kind["changed"] = ([f"_changed: no synthesis text came back for {len(missing)} pair(s); "
                                   f"ids: {_cap_ids(missing)}_"] if missing
                                  else [f"_the model judged all {c['changed']} changed pair(s) unchanged "
                                        f"in meaning on this run_"])
        for kind in ("changed", "added", "removed"):
            if by_kind[kind]:
                lines += _section_block(kind, leads.get(kind, kind.capitalize()), by_kind[kind])
        groups = p.get("note_groups") or {"structural": [], "bookkeeping": p.get("notes", [])}
        if groups.get("structural"):
            lines += _section_block("structural", "Structural changes only", [f"- {n}" for n in groups["structural"]])
        if groups.get("bookkeeping"):
            # the title is visible once the plan is open; the id list stays collapsed until asked for
            n_book = c.get("bookkeeping", c.get("dropped", 0)) + c.get("same", 0)   # what the list holds
            body = ['<details class="rc-ids" markdown="1">',
                    f"<summary>{n_book} requirement(s) with no change in meaning (bookkeeping only"
                    + (f", and {c['same']} the model judged unchanged on this run" if c.get("same") else "")
                    + ") · show</summary>", ""] + [f"- {n}" for n in groups["bookkeeping"]] + ["", "</details>"]
            lines += _section_block("bookkeeping", "Bookkeeping only", body)
        lines += ["</details>", ""]
    lines += ["</div>", ""]
    absent = result.get("absent_plans") or []
    if absent:
        lines += _section_block("absent", "Plans present in one release only",
                                [f"- {x['plan_id']}: not part of {x['absent_in']}" for x in absent])
    lines += ["</div>", ""]
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
    mno, releases = "", []
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
        rel_a, rel_b = resolve_release_pair(intent.releases, releases, cfg.names_for(mno))
        await say(f"History: {mno} {rel_a} → {rel_b}; reading changed requirements…")
        payload = await asyncio.to_thread(store.changed_pairs, mno, rel_a, rel_b)
    except PipelineError as exc:
        msg = f"[{exc.code}] {exc.message}"
        if exc.code in ("RHD-E001", "RHD-E002") and releases:
            # say what History has, so the user can rephrase (D-DRAFT-5)
            names = cfg.names_for(mno)
            msg += (f". History has {', '.join(display_release(r, names) for r in releases)}"
                    f" for {mno}; ask about one release, or name a served one.")
        return {"error": msg, "error_code": exc.code}
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
    names = cfg.names_for(mno)
    result["display"] = {"mno": mno, "from": display_release(rel_a, names),
                         "to": display_release(rel_b, names)}
    # which plan each id belongs to: the side-by-side bubble asks History
    # for one requirement's versions by plan
    result["bubble_plans"] = {i: p["plan_id"] for p in result["plans"]
                              for i in [*(x for s in p["sections"] for x in s.get("ids", [])),
                                        *p.get("note_ids", [])]}
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
        # which plan each id belongs to: a stored answer rebuilds its
        # side-by-side bubble endpoints from this map
        "bubble_plans": dict(result.get("bubble_plans", {})),
        "retried": result["run"].get("retried", 0),
        "stripped": result["run"].get("stripped", 0),
        "empty": result["run"].get("empty", 0),
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


# ── the side-by-side bubble ───────────────────────────────────────────

def versions_at(store: Any, mno: str, plan: str, req_id: str, rel_a: str, rel_b: str) -> dict:
    """Both releases' versions of one requirement from History: the version
    live at `rel_a` and the one live at `rel_b` (None where the requirement
    is absent), with word-level diffs of title and text between them."""
    from core.src.stores.history import word_diff
    releases = store.releases(mno)
    order = {r: i for i, r in enumerate(releases)}
    versions = store.req_history(mno, plan, req_id, rel_a, rel_b)

    def live_at(rel: str) -> "dict | None":
        i = order.get(rel)
        if i is None:
            return None
        for v in versions:
            lo = order.get(v.get("valid_from", ""), -1)
            hi = order.get(v.get("valid_to") or "", len(releases) - 1) if v.get("valid_to") else len(releases) - 1
            if lo <= i <= hi:
                return v
        return None

    old, new = live_at(rel_a), live_at(rel_b)
    diff = None
    if old and new:
        ot, nt = old.get("title", ""), new.get("title", "")
        ox, nx = old.get("text", ""), new.get("text", "")
        diff = {"title": word_diff(ot, nt) if ot != nt else None,
                "text": word_diff(ox, nx) if ox != nx else None}
    return {"req_id": req_id, "plan": plan, "mno": mno, "from": rel_a, "to": rel_b,
            "old": old, "new": new, "diff": diff}
