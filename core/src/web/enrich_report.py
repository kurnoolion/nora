"""Enrichment-review exports: the label × category pivot report and the
prompt-fix scorecard (strand sira-enrichment-review, D-DRAFT-5).

Pure text builders over flattened word records — deterministic ordering
throughout (FIX-report precedent: successive exports diff cleanly). D-012
posture: keyword tokens, req_ids, category names, counts, reviewer notes —
NEVER requirement body text.

Cell-scoped inputs (plans, current LLM words) are supplied by the caller
per MNO from that MNO's LATEST loaded release — prompt-fix re-runs land in
the latest cells, so the scorecard measures against them; older releases'
drift shows up in the ORIGIN DRIFT section instead.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

SAMPLE_REQ_IDS = 5


def flatten_records(overlays: dict[str, dict]) -> list[dict[str, Any]]:
    """{mno: overlay} -> flat record rows
    {mno, req_id, direction, word, label, category, note, by, origin}."""
    rows: list[dict[str, Any]] = []

    def _row(mno, rid, direction, rec, word):
        rows.append({
            "mno": mno, "req_id": rid, "direction": direction, "word": word,
            "label": rec.get("label") or "",
            "category": (rec.get("reason") or {}).get("category") or "",
            "note": (rec.get("note") if "note" in rec
                     else (rec.get("reason") or {}).get("note")) or "",
            "by": rec.get("by") or "",
            "origin": (rec.get("origin") or {}).get("release") or "",
        })

    for mno in sorted(overlays):
        for rid in sorted(overlays[mno]):
            entry = overlays[mno][rid] or {}
            for direction in ("remove", "add"):
                for rec in entry.get(direction) or []:
                    _row(mno, rid, direction, rec, rec.get("word", ""))
            sup = entry.get("suppress_all")
            if isinstance(sup, dict) and sup.get("value"):
                _row(mno, rid, "suppress_all", sup, "")
    return rows


def _in_scope(r: dict, label: str, mno: str) -> bool:
    return (not label or r["label"] == label) and (not mno or r["mno"] == mno)


def _scope_str(label: str, mno: str) -> str:
    parts = [f"label={label}" if label else "", f"mno={mno}" if mno else ""]
    return " ".join(p for p in parts if p) or "all"


def _word_pivot(rows: list[dict], plans: dict) -> list[str]:
    """Per-word lines: word, #reqs, #plans, categories, sample req_ids."""
    by_word: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_word[r["word"]].append(r)
    out = []
    ranked = sorted(by_word.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for word, recs in ranked:
        reqs = sorted({(r["mno"], r["req_id"]) for r in recs})
        plan_set = sorted({plans.get((m, rid), "") for m, rid in reqs} - {""})
        cats = sorted({r["category"] for r in recs if r["category"]})
        sample = ", ".join(rid for _, rid in reqs[:SAMPLE_REQ_IDS])
        more = f" +{len(reqs) - SAMPLE_REQ_IDS} more" if len(reqs) > SAMPLE_REQ_IDS else ""
        out.append(f"  {word}  ·  reqs={len(reqs)}  plans={len(plan_set)}"
                   f"  [{', '.join(cats) or '-'}]  ({sample}{more})")
    return out


def build_report(overlays: dict[str, dict], *, label: str = "", mno: str = "",
                 disabled: set[str] | None = None,
                 plans: dict | None = None,
                 generated: str = "", service_note: str = "") -> str:
    """The label × category pivot report. `plans` maps (mno, req_id) -> plan
    (latest release's view; missing entries render as unknown)."""
    disabled = disabled or set()
    plans = plans or {}
    rows = [r for r in flatten_records(overlays) if _in_scope(r, label, mno)]
    removes = [r for r in rows if r["direction"] == "remove"]
    adds = [r for r in rows if r["direction"] == "add"]
    sups = [r for r in rows if r["direction"] == "suppress_all"]

    L: list[str] = []
    L.append(f"ENRICH-REVIEW REPORT  ·  generated {generated}"
             f"  ·  scope: {_scope_str(label, mno)}")
    L.append(f"records: {len(removes)} remove / {len(adds)} add / "
             f"{len(sups)} suppress"
             f"  ·  disabled labels: {', '.join(sorted(disabled)) or '(none)'}")
    if service_note:
        L.append(f"note: {service_note}")

    L.append("")
    L.append("LABEL x CATEGORY (record counts)")
    matrix = Counter((r["label"] or "(none)", r["category"] or "(none)")
                     for r in rows)
    for (lab, cat), n in sorted(matrix.items()):
        L.append(f"  {lab}  ·  {cat}: {n}")
    if not matrix:
        L.append("  (no records in scope)")

    L.append("")
    L.append("TOP REMOVED WORDS")
    L.extend(_word_pivot(removes, plans) or ["  (none)"])
    L.append("")
    L.append("TOP ADDED WORDS")
    L.extend(_word_pivot(adds, plans) or ["  (none)"])

    L.append("")
    L.append(f"SUPPRESSIONS ({len(sups)})")
    for cat, n in sorted(Counter(r["category"] or "(none)" for r in sups).items()):
        L.append(f"  {cat}: {n}")
    if not sups:
        L.append("  (none)")

    L.append("")
    L.append("ORIGIN DRIFT (records by origin release)")
    drift = Counter((r["mno"], r["origin"] or "(none)") for r in rows)
    for (m, origin), n in sorted(drift.items()):
        L.append(f"  {m} · {origin}: {n}")
    if not drift:
        L.append("  (none)")

    L.append("")
    L.append("NOTES (verbatim, by category)")
    noted = [r for r in rows if r["note"]]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in noted:
        by_cat[r["category"] or "(none)"].append(r)
    for cat in sorted(by_cat):
        L.append(f"  {cat}:")
        seen = set()
        for r in sorted(by_cat[cat], key=lambda x: (x["note"], x["req_id"])):
            key = (r["note"], r["req_id"])
            if key in seen:
                continue
            seen.add(key)
            L.append(f'   - "{r["note"]}" ({r["req_id"]})')
    if not noted:
        L.append("  (none)")

    return "\n".join(L) + "\n"


def build_scorecard(overlays: dict[str, dict], *, label: str = "",
                    mno: str = "", current_llm: dict | None = None,
                    generated: str = "") -> str:
    """Prompt-fix scorecard: per remove-record, does the CURRENT LLM output
    (latest release per MNO; `current_llm` maps (mno, req_id) -> set(words))
    still produce the word? gone = fixed. This is why stale remove-records
    are retained — they are the measuring stick (D-DRAFT-5)."""
    current_llm = current_llm or {}
    rows = [r for r in flatten_records(overlays)
            if r["direction"] == "remove" and _in_scope(r, label, mno)]

    L = [f"PROMPT-FIX SCORECARD  ·  generated {generated}"
         f"  ·  scope: {_scope_str(label, mno)}"]

    by_label: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_label[r["label"] or "(none)"].append(r)

    total_fixed = total_unfixed = total_absent = 0
    for lab in sorted(by_label):
        fixed = unfixed = absent = 0
        unfixed_words: Counter = Counter()
        unfixed_reqs: dict[str, list[str]] = defaultdict(list)
        for r in by_label[lab]:
            key = (r["mno"], r["req_id"])
            if key not in current_llm:
                absent += 1
            elif r["word"] in current_llm[key]:
                unfixed += 1
                unfixed_words[r["word"]] += 1
                unfixed_reqs[r["word"]].append(r["req_id"])
            else:
                fixed += 1
        scored = fixed + unfixed
        pct = f" ({100 * fixed // scored}%)" if scored else ""
        L.append("")
        L.append(f"label {lab}: fixed {fixed} / {scored} remove-records{pct}"
                 + (f"  ·  {absent} req(s) absent from current corpus" if absent else ""))
        for word, n in sorted(unfixed_words.items(), key=lambda kv: (-kv[1], kv[0])):
            sample = ", ".join(sorted(unfixed_reqs[word])[:SAMPLE_REQ_IDS])
            L.append(f"  unfixed: {word} x{n} ({sample})")
        total_fixed += fixed
        total_unfixed += unfixed
        total_absent += absent

    scored = total_fixed + total_unfixed
    L.append("")
    L.append(f"TOTAL: fixed {total_fixed} / {scored}"
             + (f" ({100 * total_fixed // scored}%)" if scored else "")
             + (f"  ·  absent {total_absent}" if total_absent else ""))
    if not rows:
        L.append("(no remove-records in scope)")
    return "\n".join(L) + "\n"
