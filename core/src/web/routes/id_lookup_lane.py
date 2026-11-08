"""The requirement-ID lookup lane of the Ask page (strand sira-intent, D-DRAFT-8).

A message made only of requirement IDs (one or several, separated by
spaces, commas, semicolons or new lines, optionally with one release such
as ``Feb2026``) is a lookup, not a question. The lane fetches each ID from
the Trees store (`req_tree.find_req`) and shows its full text: no
classification, no retrieval, no language model.

Release shown: the latest that carries the ID, with the others listed; a
release named in the message wins. Unknown IDs are listed as not found.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.src.extraction.release_key import release_order_key
from core.src.query.analyzer import _REQ_ID_PATTERN  # the rules' ID shape (Verizon-only, design §9)
from core.src.stores.trees import latest_match, release_sort_key

LANE = "id_lookup"
ENV_SWITCH = "NORA_ID_LOOKUP"

_ID_RE = re.compile(_REQ_ID_PATTERN.pattern, re.IGNORECASE)
_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_RELEASE_RE = re.compile(rf"\b({_MONTHS})\s?(\d{{4}})\b", re.IGNORECASE)
_SEPARATORS = re.compile(r"[\s,;]*")


def enabled() -> bool:
    """The ``NORA_ID_LOOKUP`` switch, read per request; default off."""
    return os.getenv(ENV_SWITCH, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class IdMessage:
    ids: tuple[str, ...]     # in message order, duplicates dropped
    release: str             # "" when the message names none; else MMMYYYY


def parse_id_message(question: str) -> "IdMessage | None":
    """The IDs (and optional release) when the message is nothing but
    those; None for anything else, which goes to the normal lanes."""
    ids = [m.group(0) for m in _ID_RE.finditer(question)]
    if not ids:
        return None
    rels = list(_RELEASE_RE.finditer(question))
    if len(rels) > 1:
        return None
    rest = _RELEASE_RE.sub(" ", _ID_RE.sub(" ", question))
    if not _SEPARATORS.fullmatch(rest):
        return None
    release = ""
    if rels:
        release = rels[0].group(1).title() + rels[0].group(2)
    seen: dict[str, None] = {}
    for i in ids:
        seen.setdefault(i.upper(), None)
    return IdMessage(tuple(seen), release)


def lookup(env_dir: Path, msg: IdMessage, find_req=None) -> dict[str, Any]:
    """Resolve each ID. Returns ``{"entries": [...], "not_found": [...]}``;
    an entry is ``{"req": <find_req row>, "also_in": [release, ...]}``, or
    ``{"req_id", "missing_in": release, "also_in": [...]}`` when the named
    release doesn't carry it."""
    if find_req is None:
        from core.src.web import req_tree
        find_req = req_tree.find_req
    entries: list[dict] = []
    not_found: list[str] = []
    for req_id in msg.ids:
        matches = find_req(env_dir, req_id, ignore_case=True)
        if not matches:
            not_found.append(req_id)
            continue
        releases = sorted({m["release"] for m in matches}, key=release_sort_key)
        if msg.release:
            in_rel = [m for m in matches if m["release"] == msg.release]
            if not in_rel:
                entries.append({"req_id": matches[0]["req_id"], "missing_in": msg.release,
                                "also_in": releases})
                continue
            shown = in_rel[0]
        else:
            shown = latest_match(matches)
        entries.append({"req": shown,
                        "also_in": [r for r in releases if r != shown["release"]]})
    return {"entries": entries, "not_found": not_found}


def answer_markdown(found: dict[str, Any]) -> str:
    """The stored answer text (feedback row, share link). Requirement text
    goes in a fenced block so markdown never rewrites corpus text."""
    parts: list[str] = []
    for e in found["entries"]:
        if "missing_in" in e:
            parts.append(f"**{e['req_id']}** is not in {e['missing_in']}; "
                         f"it is in: {', '.join(e['also_in'])}.")
            continue
        r = e["req"]
        meta = " · ".join(x for x in (r.get("plan_name") or r.get("plan"), r.get("mno"),
                                      r.get("release"),
                                      _section(r) and f"Section {_section(r)}") if x)
        head = f"**{r['req_id']}**" + (f" — {r['title']}" if r.get("title") else "")
        block = [head, meta, "", "```text", (r.get("text") or "").rstrip(), "```"]
        if e["also_in"]:
            block.append(f"Also in: {', '.join(e['also_in'])}")
        parts.append("\n".join(block))
    if found["not_found"]:
        parts.append("**Not found:** " + ", ".join(found["not_found"]))
    return "\n\n---\n\n".join(parts)


def _section(r: dict) -> str:
    return r.get("section_number") or r.get("parent_section") or ""


async def run_id_lookup_lane(question: str, msg: IdMessage, *, env_dir: Path,
                             find_req=None) -> dict[str, Any]:
    """The lane output in the merged tab's shape (`result`, `elapsed_ms`,
    `retrieved_ids`, `cited_ids`, `lane_config`, plus `lookup`)."""
    start = time.time()
    try:
        found = await asyncio.to_thread(lookup, env_dir, msg, find_req)
    except Exception as exc:                            # noqa: BLE001 — surfaced to the page
        return {"error": f"Requirement lookup failed: {type(exc).__name__}"}
    elapsed_ms = int((time.time() - start) * 1000)
    shown = [e["req"]["req_id"] for e in found["entries"] if "req" in e]
    return {
        "result": {
            "answer": answer_markdown(found),
            "citations": [], "llm_citations": [],
            "rag_chunks": [], "rag_chunk_count": 0,
            "llm_model": "",
            "timings_ms": {"lookup": elapsed_ms, "total_ms": elapsed_ms},
        },
        "elapsed_ms": elapsed_ms,
        "retrieved_ids": shown,
        "reranked_ids": None,
        "cited_ids": shown,
        "lane_config": {
            "lane": LANE, "stack": os.getenv("NORA_STACK_NAME", ""),
            "intent": "other",
            "ids": list(msg.ids), "release": msg.release,
            "shown": [{"req_id": e["req"]["req_id"], "release": e["req"]["release"]}
                      for e in found["entries"] if "req" in e],
            "missing_in_release": [e["req_id"] for e in found["entries"] if "missing_in" in e],
            "not_found": found["not_found"],
        },
        "lookup": found,
    }


# ── IDs named inside a question (D-DRAFT-8, design §4 item 4) ─────────

PIN_SWITCH = "NORA_PIN_NAMED_IDS"


def pin_enabled() -> bool:
    """The ``NORA_PIN_NAMED_IDS`` switch, read per request; default off."""
    return os.getenv(PIN_SWITCH, "").strip().lower() in {"1", "true", "yes", "on"}


def named_ids(question: str) -> tuple[list[str], str]:
    """Requirement IDs named anywhere in the question (message order, no
    duplicates) and the one release it names ("" for none or several)."""
    seen: dict[str, None] = {}
    for m in _ID_RE.finditer(question):
        seen.setdefault(m.group(0).upper(), None)
    rels = list(_RELEASE_RE.finditer(question))
    release = rels[0].group(1).title() + rels[0].group(2) if len(rels) == 1 else ""
    return list(seen), release


def pinned_candidates(env_dir: Path, question: str, *, text_chars: int = 0,
                      find_req=None) -> tuple[list[str], list[dict]]:
    """The question's named IDs and, for each one found in the Trees store,
    a select-synth candidate (``req_id``, ``mno``, ``release``, ``title``,
    ``text``): the named release when the question names one that carries
    it, else the latest. Text capped at ``text_chars`` when > 0."""
    ids, release = named_ids(question)
    if not ids:
        return [], []
    if find_req is None:
        from core.src.web import req_tree
        find_req = req_tree.find_req
    out: list[dict] = []
    for req_id in ids:
        matches = find_req(env_dir, req_id, ignore_case=True)
        if not matches:
            continue
        in_rel = [m for m in matches if release and m["release"] == release]
        r = in_rel[0] if in_rel else latest_match(matches)
        text = r.get("text") or ""
        out.append({"req_id": r["req_id"], "mno": r.get("mno", ""), "release": r.get("release", ""),
                    "title": r.get("title", ""), "text": text[:text_chars] if text_chars > 0 else text,
                    "pinned_by_id": True})
    return ids, out
