"""Release-change answers from the History store (strand rel-history).

Turns a `HistoryStore.changed_pairs` payload into language-model batches,
runs them in parallel, and reassembles the syntheses per plan:

    payload = history.changed_pairs(mno, rel_a, rel_b)
    result  = synthesize_release_diff(payload, provider, config=cfg,
                                      cancel=event, on_partial=cb)

Three batch kinds, each with its own prompt shape:

- ``pair``    — changed requirements, old and new text side by side; the
                model states what changed per requirement, citing the id;
- ``removed`` — a plan's removed requirements as a group: the ids, then
                one short summary of what the plan lost;
- ``added``   — the same shape for additions.

Pure renumberings, moves and rename pairs are not sent to the model: the
reassembly prints them as one line per plan from the store's own data.
Plans absent from one release get one line, no requirements.

Batches are packed per plan and kind under a byte budget
(`llm.batching`), run `parallel` at a time (`llm.parallel`), delivered as
they complete through `on_partial`, and assembled in plan order. The
compact block (`compact_block`) carries counts only.

Error codes: RHD-E001 fewer than two served releases; RHD-E002 release
not served; RHD-E003 History not configured; RHD-E004 a batch failed
after the fallback (recorded, the run continues); RHD-I001 cancelled.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from core.src.llm import FINAL_ANSWER_MARKER, REASONING_SENTINEL_ENABLED

log = logging.getLogger(__name__)
from core.src.llm.batching import Batch, pack, utf8_size
from core.src.llm.parallel import BatchOutcome, RunResult, run
from core.src.pipeline.error_codes import PipelineError
from core.src.query.schema import QueryIntent, QueryType

MAX_BATCH_BYTES_ENV = "NORA_HISTORY_DIFF_MAX_BATCH_BYTES"
PARALLEL_ENV = "NORA_HISTORY_DIFF_PARALLEL"
ENABLED_ENV = "NORA_HISTORY_DIFF_ENABLED"

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_NAMES = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun", 7: "Jul",
                8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
_REL_RE = re.compile(r"\b([a-z]{3,9})\.?\s*,?\s*(20\d{2})\b|\b(20\d{2})\s*[-/ ]?\s*([a-z]{3,9})\b")
_LATEST = ("latest", "current", "new", "newest", "n")
_PREVIOUS = ("previous", "prior", "last", "old", "n-1")


# ── configuration ─────────────────────────────────────────────────────

@dataclass
class ReleaseDiffConfig:
    max_batch_bytes: int = 32768
    parallel: int = 4
    enabled: bool = True
    max_tokens: int = 2048
    temperature: float = 0.0

    @classmethod
    def from_env(cls, env: "dict | None" = None) -> "ReleaseDiffConfig":
        e = os.environ if env is None else env
        enabled_raw = str(e.get(ENABLED_ENV, "")).strip().lower()
        enabled = (enabled_raw in ("1", "true", "yes", "on")) if enabled_raw \
            else bool(str(e.get("NORA_HISTORY_URL", "")).strip())
        return cls(
            max_batch_bytes=max(1024, int(e.get(MAX_BATCH_BYTES_ENV, "32768") or 32768)),
            parallel=max(1, int(e.get(PARALLEL_ENV, "4") or 4)),
            enabled=enabled)


# ── which questions this answers ──────────────────────────────────────

_CHANGE_WORDS = re.compile(r"\b(chang\w*|differ\w*|diff|delta|updated|new in|added|removed|dropped)\b")
_RELEASE_WORDS = re.compile(r"\b(release\w*|latest|previous|prior|newest|current version|version\w*)\b")


def is_release_change_question(question: str, intent: QueryIntent) -> bool:
    """A release-diff intent, or a one-MNO comparison phrased as a change
    between releases ("… compared to the previous one"), which the rules
    analyzer files under cross-MNO comparison because of "compare".
    Two or more MNOs is a comparison, never a release diff."""
    if intent.query_type == QueryType.RELEASE_DIFF:
        return True
    if len(intent.mnos) >= 2:
        return False
    q = (question or "").lower()
    return bool(_CHANGE_WORDS.search(q) and (_RELEASE_WORDS.search(q) or len(intent.releases) >= 1))


# ── release resolution ────────────────────────────────────────────────

def normalize_release_ref(raw: str) -> str:
    """A human release phrase to ``MMMYYYY``, ``latest``, ``previous`` or
    ``""``. Month-year in either order, full or abbreviated month."""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if s in _LATEST:
        return "latest"
    if s in _PREVIOUS:
        return "previous"
    m = _REL_RE.search(s)
    if m:
        mon, year = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        mi = _MONTHS.get(mon[:3])
        if mi:
            return f"{_MONTH_NAMES[mi]}{year}"
    compact = re.fullmatch(r"([a-z]{3})(20\d{2})", s)
    if compact and compact.group(1) in _MONTHS:
        return f"{_MONTH_NAMES[_MONTHS[compact.group(1)]]}{compact.group(2)}"
    return ""


def resolve_release_pair(wanted: Iterable[str], releases: list[str]) -> tuple[str, str]:
    """(older, newer) from the question's release phrases and the MNO's
    served releases (oldest first). None named → the last two; one named
    → it and the one before; two → ordered. RHD-E001 with fewer than two
    served releases, RHD-E002 for a release not served."""
    if len(releases) < 2:
        raise PipelineError("RHD-E001", {"count": str(len(releases))})
    refs: list[str] = []
    for w in wanted:
        n = normalize_release_ref(w)
        if n and n not in refs:
            refs.append(n)
    idx: list[int] = []
    for r in refs:
        if r == "latest":
            i = len(releases) - 1
        elif r == "previous":
            i = len(releases) - 2
        elif r in releases:
            i = releases.index(r)
        else:
            raise PipelineError("RHD-E002", {"release": r})
        if i not in idx:
            idx.append(i)
    if not idx:
        idx = [len(releases) - 2, len(releases) - 1]
    elif len(idx) == 1:
        i = idx[0]
        idx = [i - 1, i] if i > 0 else [i, i + 1]
    idx = sorted(idx[:2])
    return releases[idx[0]], releases[idx[1]]


# ── batches and prompts ───────────────────────────────────────────────

def _req_text(r: dict) -> str:
    bits = [f"[{r.get('req_id', '')}]"]
    if r.get("section"):
        bits.append(f"section {r['section']}")
    if r.get("title"):
        bits.append(f"title: {r['title']}")
    if r.get("priority"):
        bits.append(f"priority: {r['priority']}")
    app = r.get("applicability") or []
    if app:
        bits.append("applicability: " + ", ".join(str(a) for a in app))
    head = " · ".join(bits)
    return f"{head}\n{r.get('text', '')}".strip()


def _pair_text(c: dict) -> str:
    old = {**c.get("old", {}), "req_id": c["req_id"], "section": c.get("section", "")}
    new = {**c.get("new", {}), "req_id": c["req_id"], "section": c.get("section", "")}
    return (f"=== {c['req_id']} ===\n--- previous release ---\n{_req_text(old)}\n"
            f"--- current release ---\n{_req_text(new)}\n")


def _item_size(item: dict) -> int:
    return utf8_size(item["text"])


def build_batches(payload: dict, max_bytes: int = 32768) -> list[Batch]:
    """Batches per plan and kind, in plan order: pairs, then removed, then
    added. Each item is ``{plan, kind, text, ids}``."""
    items: list[dict] = []
    for plan_id in sorted(payload.get("plans", {})):
        p = payload["plans"][plan_id]
        for c in p.get("changed", []):
            items.append({"plan": plan_id, "kind": "pair", "text": _pair_text(c),
                          "ids": [c["req_id"]]})
        for r in p.get("removed", []):
            items.append({"plan": plan_id, "kind": "removed", "text": _req_text(r) + "\n",
                          "ids": [r["req_id"]]})
        for r in p.get("added", []):
            items.append({"plan": plan_id, "kind": "added", "text": _req_text(r) + "\n",
                          "ids": [r["req_id"]]})
    return pack(items, _item_size, max_bytes=max_bytes, overhead=512,
                group_key=lambda it: f"{it['plan']}\x00{it['kind']}")


_SYSTEM = ("You compare two releases of a telecom device requirements document. "
           "Answer in plain prose, cite every requirement id exactly as given in square "
           "brackets, never invent ids, never restate unchanged text.")
# Thinking models that leak untagged reasoning get the marker instruction the
# other answer paths use (NORA_LLM_REASONING_SENTINEL); the provider strips
# everything before the marker. Same two names, so they switch together.
_SYSTEM_SENTINEL = (
    "\n\nOUTPUT FORMAT: You may reason first if needed, but you MUST then print a "
    f"line containing exactly {FINAL_ANSWER_MARKER} and put ONLY your final answer "
    f"(with the requirement ids in square brackets) after it. Anything before "
    f"{FINAL_ANSWER_MARKER} is discarded and never shown to the user.")


def system_prompt(sentinel: "bool | None" = None) -> str:
    on = REASONING_SENTINEL_ENABLED if sentinel is None else sentinel
    return _SYSTEM + (_SYSTEM_SENTINEL if on else "")

_PROMPTS = {
    "pair": ("For each requirement below, the previous and the current release text are "
             "shown side by side. For each one, state in one or two sentences what changed: "
             "a changed value, condition, scope, wording with a different meaning, or a "
             "priority or applicability change (name it as such). Skip pure rewording with "
             "the same meaning in one short clause. Cite the id.\n\n"),
    "removed": ("The requirements below exist in the previous release and are absent from "
                "the current one. First list the removed ids, then give one short summary of "
                "what this plan lost as a whole (one sentence for a single requirement).\n\n"),
    "added": ("The requirements below are new in the current release. First list the added "
              "ids, then give one short summary of what this plan gained as a whole (one "
              "sentence for a single requirement).\n\n"),
}


def prompt_for(batch: Batch, payload: "dict | None" = None) -> tuple[str, str]:
    """(system, prompt) for one batch; every item in a batch shares the
    plan and the kind."""
    plan, kind = batch.group.split("\x00", 1)
    name = ""
    if payload:
        name = payload.get("plans", {}).get(plan, {}).get("plan_name", "")
    head = f"Plan: {plan}" + (f" ({name})" if name else "")
    head += f"\nReleases: {payload.get('from', '')} → {payload.get('to', '')}\n\n" if payload else "\n\n"
    body = "\n".join(it["text"] for it in batch.items)
    return system_prompt(), head + _PROMPTS[kind] + body


# ── working-text fallback ─────────────────────────────────────────────

_WORKING_RE = re.compile(
    r"^\s*(okay|ok|alright|let me|let's|first,? i|i need to|i'll|i will|the user (wants|asks|is asking)|"
    r"looking at|we need to|so,? the|now,? let)", re.I)
# Phrases of the model talking to itself. Two distinct ones close together
# mark working text wherever it sits: a reply that begins with the TAIL of a
# reasoning block (the marker landed inside the reasoning, so the split kept
# its end), or one that drafts the answer, reasons about it, then answers
# again. Such a block opens mid-sentence, so the anchored openers above never
# match it, and it may follow a draft that already holds the ids.
_PHRASE_RE = re.compile(
    r"\b(the final answer|i should|i need to|i'll|i will|i must|let me|let's|"
    r"the user (wants|asks|asked|is asking|provided|gave|said)|we need to|"
    r"make sure (i|to|that)|wait,|hmm|okay,|avoid restating|the specified format|"
    r"now,? let|putting (it|this) (all )?together|"
    r"the (answer|output|response|summary) (should|will|must|needs|can be)|in plain prose|"
    r"followed by the (final )?answer|(as per|per) the user|the user's instruction|"
    r"structure the (response|answer|output)|list the (added|removed|changed) (ids?|requirements?)|"
    r"referencing the ids?|mentioning the (feature|ids?)|since there('s| is) only|"
    r"as (given|instructed|required))(?!\w)", re.I)
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s)|\n")
_CLUSTER_GAP = 400     # characters between phrases of one working block


def _clusters(text: str) -> list[list["re.Match[str]"]]:
    """Phrase matches grouped by proximity: a new group starts after a gap
    of more than `_CLUSTER_GAP` characters."""
    groups: list[list[re.Match[str]]] = []
    for m in _PHRASE_RE.finditer(text or ""):
        if groups and m.start() - groups[-1][-1].end() <= _CLUSTER_GAP:
            groups[-1].append(m)
        else:
            groups.append([m])
    return groups


def _working_cluster(text: str) -> "list[re.Match[str]] | None":
    """The first group with two distinct phrases; with an anchored opener,
    a group that starts in the opening sentence counts too."""
    groups = _clusters(text)
    for g in groups:
        if len({m.group(1).lower() for m in g}) >= 2:
            return g
    if _WORKING_RE.match(text or "") and groups and groups[0][0].start() < 120:
        return groups[0]
    return None


def working_marks(text: str) -> list[str]:
    """What the detector matched, as the list's own words (an `opener:`
    from the anchored openers, then the cluster's distinct phrases): for
    the log line, so a firing on a genuine answer can be told from a real
    one without quoting the reply."""
    key = lambda w: re.sub(r"\s+", " ", w.lower()).rstrip(",")  # noqa: E731  "okay," -> okay
    marks: list[str] = []
    m = _WORKING_RE.match(text or "")
    if m:
        marks.append("opener:" + key(m.group(1)))
    cluster = _working_cluster(text)
    if cluster:
        marks.extend(sorted({key(c.group(1)) for c in cluster}))
    return marks


def looks_like_working_text(text: str) -> bool:
    """A reply that carries the model's own working: a known opener at the
    very start, or two distinct working phrases close together anywhere
    (the tail of a reasoning block, or a draft-then-answer reply)."""
    return bool(_WORKING_RE.match(text or "")) or _working_cluster(text) is not None


def _ids_in(text: str, ids: "Iterable[str]") -> set[str]:
    return {i for i in ids if i and i in text}


def _first_id(text: str, ids: "Iterable[str]") -> int:
    """Position of the earliest requirement id, bracket included; -1 when
    none is present."""
    present = _ids_in(text, ids)
    if not present:
        return -1
    pos = min(text.index(i) for i in present)
    return pos - 1 if pos and text[pos - 1] == "[" else pos


def _drop_repeated_draft(tail: str, ids: "Iterable[str]") -> str:
    """An answer that resumed on the working line may be a draft that the
    next paragraph repeats: drop leading paragraphs whose ids the rest
    carries."""
    paras = [p for p in tail.split("\n\n") if p.strip()]
    while len(paras) > 1 and _ids_in(paras[0], ids) <= _ids_in("\n\n".join(paras[1:]), ids):
        paras.pop(0)
    return "\n\n".join(paras)


def _inline_cut(text: str, lo: int, id_pos: int) -> int:
    """Where the answer resumes on the working line: after the last colon
    between the phrase and the id ("Putting it all together: The removed
    ids are [..]"), else at the id itself."""
    colon = text.rfind(":", lo, id_pos)
    return colon + 1 if colon >= 0 else id_pos


def _strip_once(text: str, ids: list[str]) -> str:
    first_id = _first_id(text, ids)
    before = list(_PHRASE_RE.finditer(text, 0, first_id)) if first_id > 0 else []
    if before:
        # reasoning precedes the answer, however long and spread out: cut
        # after the sentence holding the last working phrase before the
        # first id, stopping on that line when the id shares the sentence
        end = _SENTENCE_END_RE.search(text, before[-1].end())
        if end is not None and end.start() < first_id:
            return text[end.end():].strip()
        tail = text[_inline_cut(text, before[-1].end(), first_id):].strip()
        return _drop_repeated_draft(tail, ids)
    cluster = _working_cluster(text)
    if cluster is None:
        # an anchored opener with no phrase after it: back up to the
        # paragraph that holds the first id, or keep the last paragraph
        if first_id < 0:
            paras = [p for p in text.split("\n\n") if p.strip()]
            return paras[-1].strip() if len(paras) > 1 else text
        start = text.rfind("\n\n", 0, first_id)
        return text[start + 2:].strip() if start >= 0 else text
    # the block sits after a draft that holds the ids (or between answer
    # parts): drop the cluster's sentences
    first, last = cluster[0], cluster[-1]
    span_start = 0
    for m in _SENTENCE_END_RE.finditer(text, 0, first.start()):
        span_start = m.end()
    end = _SENTENCE_END_RE.search(text, last.end())
    span_end = end.end() if end is not None else len(text)
    inline_id = _first_id(text[last.end():span_end], ids)
    cut_inline = inline_id >= 0
    if cut_inline:
        span_end = _inline_cut(text, last.end(), last.end() + inline_id)
    head, tail = text[:span_start].strip(), text[span_end:].strip()
    head_ids, tail_ids = _ids_in(head, ids), _ids_in(tail, ids)
    if tail_ids:
        if cut_inline:
            tail = _drop_repeated_draft(tail, ids)
        if head_ids and not head_ids <= tail_ids:
            return head + "\n\n" + tail
        return tail
    if head_ids:
        return head
    if first_id < 0:
        paras = [p for p in (head + "\n\n" + tail).split("\n\n") if p.strip()]
        return paras[-1].strip() if paras else text
    return tail or head or text


def strip_working_text(text: str, ids: "Iterable[str]" = ()) -> str:
    """Drop the working text wherever it sits. Reasoning before the first
    requirement id, however long, is cut after the sentence holding its
    last working phrase (stopping at an id on that line, so an answer
    resuming there survives; a draft the next paragraph repeats goes
    too). A block after a draft that holds the ids is cut by its phrase
    cluster, the draft with it when the answer repeats its ids; a block
    between answer parts with different ids leaves both parts. With no
    id in the reply keep the last paragraph only. Up to three passes, so
    a reply with both shapes comes out clean. Unchanged when it does not
    look like working text."""
    if not looks_like_working_text(text):
        return text
    ids = [i for i in ids if i]
    out = text
    for _ in range(3):
        nxt = _strip_once(out, ids)
        if nxt == out or not looks_like_working_text(nxt):
            return nxt
        out = nxt
    return out


# ── reassembly ────────────────────────────────────────────────────────

def _plan_notes(p: dict) -> list[str]:
    notes: list[str] = []
    renum = p.get("renumbered", [])
    if renum:
        by_detail: dict[str, int] = {}
        for r in renum:
            by_detail[r.get("detail", "")] = by_detail.get(r.get("detail", ""), 0) + 1
        parts = ", ".join(f"{n} {d or 'other'}" for d, n in sorted(by_detail.items()))
        notes.append(f"{len(renum)} requirement(s) renumbered ({parts}); content unchanged.")
    renamed = [r for r in p.get("renamed", []) if r.get("kind") == "section"]
    if renamed:
        notes.append("Section(s) renumbered: " + "; ".join(
            f"{r['old_id']} → {r['new_id']}" for r in renamed) + ".")
    rreq = [r for r in p.get("renamed", []) if r.get("kind") == "requirement"]
    if rreq:
        notes.append("Requirement id(s) changed with identical content: " + "; ".join(
            f"{r['old_id']} → {r['new_id']}" for r in rreq) + ".")
    moved = p.get("moved", [])
    if moved:
        notes.append("Moved: " + "; ".join(
            f"{m['req_id']} from {m.get('from', '?')} to {m.get('section', '?')}" for m in moved) + ".")
    return notes


def reassemble(payload: dict, batches: list[Batch], outcomes: dict[int, BatchOutcome],
               cancelled: bool = False) -> dict:
    """Per-plan sections in plan order: pair batches in batch order, then
    removed, then added, then the store-derived notes; absent plans as
    one line each. A batch not in `outcomes` (cancelled) is marked."""
    by_group: dict[str, list[Batch]] = {}
    for b in batches:
        by_group.setdefault(b.group, []).append(b)
    plans_out: list[dict] = []
    for plan_id in sorted(payload.get("plans", {})):
        p = payload["plans"][plan_id]
        sections: list[dict] = []
        for kind in ("pair", "removed", "added"):
            for b in by_group.get(f"{plan_id}\x00{kind}", []):
                o = outcomes.get(b.index)
                if o is None:
                    sections.append({"kind": kind, "batch": b.index, "status": "cancelled",
                                     "text": "", "ids": [i for it in b.items for i in it["ids"]]})
                elif o.error:
                    sections.append({"kind": kind, "batch": b.index, "status": "error",
                                     "text": "", "error": "RHD-E004",
                                     "ids": [i for it in b.items for i in it["ids"]]})
                else:
                    sections.append({"kind": kind, "batch": b.index, "status": "ok",
                                     "text": o.text, "ids": [i for it in b.items for i in it["ids"]]})
        plans_out.append({"plan_id": plan_id, "plan_name": p.get("plan_name", ""),
                          "sections": sections, "notes": _plan_notes(p),
                          "counts": {k: len(p.get(k, [])) for k in
                                     ("changed", "added", "removed", "renamed", "renumbered", "moved")}})
    absent = [{"plan_id": k, "absent_in": v.get("absent_in", "")}
              for k, v in sorted(payload.get("absent_plans", {}).items())]
    return {"mno": payload.get("mno", ""), "from": payload.get("from", ""),
            "to": payload.get("to", ""), "plans": plans_out, "absent_plans": absent,
            "counts": dict(payload.get("counts", {})), "cancelled": cancelled}


# ── orchestration ─────────────────────────────────────────────────────

def synthesize_release_diff(payload: dict, provider: Any, *,
                            config: "ReleaseDiffConfig | None" = None,
                            cancel: "threading.Event | None" = None,
                            on_partial: "Callable[[dict], None] | None" = None) -> dict:
    """Pack, run, reassemble. `on_partial` receives, per completed batch,
    ``{plan, kind, batch, done, total, status, text}`` so a route can
    stream it. The result carries the reassembled plans, the run numbers
    and the compact block."""
    cfg = config or ReleaseDiffConfig()
    batches = build_batches(payload, cfg.max_batch_bytes)
    total = len(batches)

    working = {"retried": 0, "stripped": 0}
    wlock = threading.Lock()

    def call(batch: Batch) -> str:
        system, prompt = prompt_for(batch, payload)
        text = provider.complete(prompt, system=system, temperature=cfg.temperature,
                                 max_tokens=cfg.max_tokens)
        marks = working_marks(text)
        if marks:
            # the marker was left out: one retry with a blunter instruction,
            # then a heuristic cut, so the page never shows the working
            retry = provider.complete(
                prompt + "\n\nOutput only the final answer, nothing before it.",
                system=system, temperature=cfg.temperature, max_tokens=cfg.max_tokens)
            stripped = looks_like_working_text(retry)
            text = strip_working_text(retry, [i for it in batch.items for i in it["ids"]]) \
                if stripped else retry
            with wlock:
                working["retried"] += 1
                working["stripped"] += int(stripped)
            # one line per firing, no prompt or reply text: which batch,
            # what matched (the list's own words, `|`-joined: a key may
            # hold a comma), what was done, how much the cut removed
            log.info("RHD working text: batch=%d group=%s marks=%s retried=1 stripped=%d cut_chars=%d",
                     batch.index, batch.group.replace("\x00", "/"), "|".join(marks),
                     int(stripped), (len(retry) - len(text)) if stripped else 0)
        return text

    done_count = [0]

    def on_result(o: BatchOutcome) -> None:
        done_count[0] += 1
        if on_partial is not None:
            plan, kind = o.group.split("\x00", 1)
            on_partial({"plan": plan, "kind": kind, "batch": o.index, "done": done_count[0],
                        "total": total, "status": "error" if o.error else "ok",
                        "text": "" if o.error else o.text, "error": o.error[:120]})

    rr: RunResult = run(batches, call, parallel=cfg.parallel, cancel=cancel,
                        on_result=on_result,
                        stats_of=lambda: getattr(provider, "last_call_stats", {}) or {})
    out = reassemble(payload, batches, rr.outcomes, cancelled=rr.cancelled)
    out["run"] = {"batches": total, "done": rr.done, "failed": rr.failed,
                  "skipped": rr.skipped, "parallel": cfg.parallel,
                  "max_batch_bytes": cfg.max_batch_bytes, "elapsed_ms": rr.elapsed_ms,
                  "oversized": sum(1 for b in batches if b.oversized),
                  "batch_ms": rr.latency_ms(),
                  "retried": working["retried"], "stripped": working["stripped"],
                  "errors": sorted({o.error.split(":")[0] for o in rr.outcomes.values() if o.error})}
    out["compact"] = compact_block(out)
    return out


def compact_block(result: dict) -> str:
    """Counts only — safe to paste."""
    c = result.get("counts", {})
    r = result.get("run", {})
    return ("RHD mno={mno} from={a} to={b} plans={plans} absent={absent} pairs={pairs} "
            "added={added} removed={removed} renumbered={renum} batches={batches} "
            "parallel={par} bytes_max={bm} done={done} failed={failed} cancelled={canc} "
            "elapsed_ms={ms} batch_ms_p50={p50} batch_ms_p95={p95} batch_ms_max={mx} "
            "retried={ret} stripped={strp}{errs}").format(
        mno=result.get("mno", ""), a=result.get("from", ""), b=result.get("to", ""),
        plans=c.get("plans", 0), absent=c.get("absent_plans", 0), pairs=c.get("changed", 0),
        added=c.get("added", 0), removed=c.get("removed", 0), renum=c.get("renumbered", 0),
        batches=r.get("batches", 0), par=r.get("parallel", 0), bm=r.get("max_batch_bytes", 0),
        done=r.get("done", 0), failed=r.get("failed", 0),
        canc=1 if result.get("cancelled") else 0, ms=r.get("elapsed_ms", 0),
        p50=r.get("batch_ms", {}).get("p50", 0), p95=r.get("batch_ms", {}).get("p95", 0),
        mx=r.get("batch_ms", {}).get("max", 0),
        ret=r.get("retried", 0), strp=r.get("stripped", 0),
        errs=(" errors=" + ",".join(r["errors"])) if r.get("errors") else "")
