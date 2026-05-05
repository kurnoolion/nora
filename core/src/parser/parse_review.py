"""Parse-log review format and compact report generator.

Workflow:
  1. generate template:  parse_review_cli --create <doc_id>_parse_log.json
  2. reviewer edits:     <doc_id>_parse_review.json  (corrections section only)
  3. generate report:    parse_review_cli --report <doc_id>_parse_review.json
  4. paste report into chat

The review JSON has two top-level sections:
  parser_snapshot  — pre-populated by --create; read-only reference for reviewers
  corrections      — reviewer fills in only the errors found (empty = no error)

Correction entry shapes
  false_positive_drops  list of {"pages":"8-8","reason":"text_strikethrough","note":"..."}
  missed_drops          list of {"pages":"35-36","expected_reason":"struck","note":"..."}
  toc_error             null | {"correct_page_start":N,"correct_page_end":N,"note":"..."}
  revhist_error         null | {"correct_page_start":N,"correct_page_end":N,"note":"..."}
  glossary_error        null | {"correct_page_start":N,"correct_page_end":N,
                                 "correct_section_number":"...","note":"..."}
  acronym_wrong_expansion  list of {"acronym":"SDM","correct":"...","note":"..."}
  acronym_missed           list of {"acronym":"MNO","expansion":"..."}
  acronym_extra            list of {"acronym":"ABCDE","note":"..."}

Compact report codes (one per error line)
  FP  false positive drop  — parser dropped, should not have
  MD  missed drop          — parser kept, should have dropped
  TOC toc boundary wrong
  RH  revhist boundary wrong
  GL  glossary boundary / section wrong
  AX  acronym wrong expansion
  AM  acronym missed
  AE  acronym extra (false positive extraction)
"""
from __future__ import annotations

import json
import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# Max note chars included in compact report (prevents proprietary content leak).
_NOTE_MAX = 50


def _trunc(text: str, max_len: int = _NOTE_MAX) -> str:
    if not text:
        return ""
    t = text.strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[:max_len - 1].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------------------
# Template generation
# ---------------------------------------------------------------------------

def generate_template(log_path: Path) -> dict[str, Any]:
    """Build a pre-populated review template from a parse_log JSON file.

    The returned dict should be written to <doc_id>_parse_review.json.
    The reviewer edits only the top-level metadata and the 'corrections' section.
    """
    with open(log_path, encoding="utf-8") as f:
        log = json.load(f)

    doc_id = log.get("doc_id", Path(log_path).stem.replace("_parse_log", ""))
    summary = log.get("summary", {})
    toc = log.get("toc")
    rh = log.get("revision_history")
    gs = log.get("glossary_section")
    acronyms = log.get("acronyms", [])

    def _range_str(obj: dict | None) -> str:
        if not obj:
            return "none"
        ps, pe = obj.get("page_start", "?"), obj.get("page_end", "?")
        bs, be = obj.get("block_start", "?"), obj.get("block_end", "?")
        pages = f"p{ps}" if ps == pe else f"p{ps}-{pe}"
        return f"{pages}  blocks {bs}–{be}"

    # Compact acronym list: show first 10, then ellipsis
    acr_list: list[str] = []
    for a in acronyms[:10]:
        acr_list.append(f"{a['acronym']}: {a['expansion']}  [{a['source']}]")
    if len(acronyms) > 10:
        acr_list.append(f"… +{len(acronyms) - 10} more (see parse_log)")

    # Build per-range struck entries for the reviewer checklist
    dropped = log.get("dropped_blocks", [])
    struck_ranges = [r for r in dropped if r["reason"] in ("text_strikethrough", "cascade")]

    return {
        "_instructions": (
            "Fill in reviewer/review_date/overall_verdict, then add entries to "
            "'corrections' for every error found. Leave parser_snapshot untouched — "
            "it is for reference only. "
            "IMPORTANT: notes must not contain proprietary document content."
        ),
        "doc_id": doc_id,
        "reviewer": "",
        "review_date": "",
        "overall_verdict": "",
        "parser_snapshot": {
            "toc": _range_str(toc) + f"  ({summary.get('toc_blocks_dropped', 0)} blocks)",
            "revision_history": _range_str(rh) + f"  ({summary.get('revhist_blocks_dropped', 0)} blocks)",
            "struck_and_cascade": (
                f"{summary.get('struck_blocks_dropped', 0)} struck + "
                f"{summary.get('cascade_blocks_dropped', 0)} cascade = "
                f"{summary.get('struck_blocks_dropped', 0) + summary.get('cascade_blocks_dropped', 0)} total"
                f"  across {len(struck_ranges)} range(s)"
            ),
            "struck_ranges": [
                {
                    "pages": (
                        f"{r['page_start']}"
                        if r["page_start"] == r["page_end"]
                        else f"{r['page_start']}-{r['page_end']}"
                    ),
                    "blocks": f"{r['block_start']}-{r['block_end']}",
                    "count": r["block_count"],
                    "reason": r["reason"],
                }
                for r in struck_ranges
            ],
            "glossary": (
                f"section {gs['section_number']} \"{gs['section_title']}\""
                f"  p{gs['page_start']}-{gs['page_end']}"
                f"  blocks {gs['block_start']}-{gs['block_end']}"
                f"  {gs['acronym_count']} acronyms"
            ) if gs else "none detected",
            "acronyms": acr_list,
            "total_dropped": summary.get("total_dropped", 0),
        },
        "corrections": {
            "_help_false_positive_drops": (
                "Blocks the parser dropped that should NOT have been dropped. "
                "Use page numbers from the PDF. reason values: "
                "text_strikethrough | cascade | toc | revhist"
            ),
            "false_positive_drops": [],
            "_help_missed_drops": (
                "Blocks the parser KEPT but should have dropped. "
                "expected_reason values: struck | cascade | toc | revhist"
            ),
            "missed_drops": [],
            "_help_toc_error": (
                "null if TOC range is correct. "
                "Otherwise: {\"correct_page_start\": N, \"correct_page_end\": N, \"note\": \"...\"}"
            ),
            "toc_error": None,
            "_help_revhist_error": (
                "null if revision-history range is correct. "
                "Otherwise: {\"correct_page_start\": N, \"correct_page_end\": N, \"note\": \"...\"}"
            ),
            "revhist_error": None,
            "_help_glossary_error": (
                "null if glossary section is correct. "
                "Otherwise fill in any wrong fields: "
                "{\"correct_page_start\": N, \"correct_page_end\": N, "
                "\"correct_section_number\": \"1.5\", \"note\": \"...\"}"
            ),
            "glossary_error": None,
            "_help_acronym_wrong_expansion": (
                "Acronyms the parser extracted with the wrong expansion. "
                "[{\"acronym\": \"SDM\", \"correct\": \"Correct Expansion\", \"note\": \"...\"}]"
            ),
            "acronym_wrong_expansion": [],
            "_help_acronym_missed": (
                "Acronyms present in the glossary that the parser did NOT extract. "
                "[{\"acronym\": \"MNO\", \"expansion\": \"Mobile Network Operator\"}]"
            ),
            "acronym_missed": [],
            "_help_acronym_extra": (
                "Terms the parser extracted as acronyms that are NOT real glossary entries. "
                "[{\"acronym\": \"ABCDE\", \"note\": \"section code, not acronym\"}]"
            ),
            "acronym_extra": [],
        },
        "notes": "",
    }


# ---------------------------------------------------------------------------
# Compact report
# ---------------------------------------------------------------------------

def generate_compact_report(review_path: Path, log_path: Path | None = None) -> str:
    """Read a completed review JSON and return a compact report string suitable
    for pasting into chat. Loads the parse_log alongside it if available."""
    with open(review_path, encoding="utf-8") as f:
        review = json.load(f)

    doc_id = review.get("doc_id", "?")
    reviewer = review.get("reviewer") or "?"
    review_date = review.get("review_date") or "?"
    verdict = review.get("overall_verdict") or "?"
    notes = _trunc(review.get("notes") or "")

    # Load parse_log if not provided
    if log_path is None:
        candidate = review_path.parent / f"{doc_id}_parse_log.json"
        if candidate.exists():
            log_path = candidate

    log: dict[str, Any] = {}
    if log_path and log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)

    summary = log.get("summary", {})
    toc = log.get("toc")
    rh = log.get("revision_history")
    gs = log.get("glossary_section")

    def _page_span(obj: dict | None) -> str:
        if not obj:
            return "none"
        ps, pe = obj.get("page_start", "?"), obj.get("page_end", "?")
        return f"p{ps}" if ps == pe else f"p{ps}-{pe}"

    struck = summary.get("struck_blocks_dropped", 0)
    cascade = summary.get("cascade_blocks_dropped", 0)
    dropped_blocks = log.get("dropped_blocks", [])
    struck_ranges = len([r for r in dropped_blocks
                         if r["reason"] in ("text_strikethrough", "cascade")])

    toc_str = (
        f"{_page_span(toc)}({summary.get('toc_blocks_dropped', 0)})"
        if toc else "none"
    )
    rh_str = (
        f"{_page_span(rh)}({summary.get('revhist_blocks_dropped', 0)})"
        if rh else "none"
    )
    struck_str = f"{struck + cascade}/{struck_ranges}rng" if struck + cascade else "0"
    gs_str = (
        f"s{gs['section_number']}/{_page_span(gs)}/{gs['acronym_count']}acr"
        if gs else "none"
    )

    header = f"PLG-CHK {doc_id}  {review_date}  reviewer={reviewer}  verdict={verdict}"
    parser_line = (
        f"parser  toc={toc_str}  revhist={rh_str}  "
        f"struck={struck_str}  cascade={cascade}  glossary={gs_str}"
    )

    # Collect errors
    corrections = review.get("corrections", {})
    error_lines: list[str] = []

    for fp in corrections.get("false_positive_drops") or []:
        pages = fp.get("pages", "?")
        reason = fp.get("reason", "?")
        note = _trunc(fp.get("note") or "")
        note_part = f"  [{len(note)}ch]" if note else ""
        error_lines.append(f"  FP   p{pages}   reason={reason}{note_part}")

    for md in corrections.get("missed_drops") or []:
        pages = md.get("pages", "?")
        want = md.get("expected_reason", "?")
        note = _trunc(md.get("note") or "")
        note_part = f"  [{len(note)}ch]" if note else ""
        error_lines.append(f"  MD   p{pages}   want={want}{note_part}")

    if toc_err := corrections.get("toc_error"):
        ps = toc_err.get("correct_page_start", "?")
        pe = toc_err.get("correct_page_end", "?")
        parser_ps = toc.get("page_start", "?") if toc else "?"
        parser_pe = toc.get("page_end", "?") if toc else "?"
        error_lines.append(f"  TOC  boundary  p{parser_ps}-{parser_pe}→p{ps}-{pe}")

    if rh_err := corrections.get("revhist_error"):
        ps = rh_err.get("correct_page_start", "?")
        pe = rh_err.get("correct_page_end", "?")
        parser_ps = rh.get("page_start", "?") if rh else "?"
        parser_pe = rh.get("page_end", "?") if rh else "?"
        error_lines.append(f"  RH   boundary  p{parser_ps}-{parser_pe}→p{ps}-{pe}")

    if gl_err := corrections.get("glossary_error"):
        parts: list[str] = []
        if gl_err.get("correct_page_start") is not None or gl_err.get("correct_page_end") is not None:
            parser_ps = gs.get("page_start", "?") if gs else "?"
            parser_pe = gs.get("page_end", "?") if gs else "?"
            correct_ps = gl_err.get("correct_page_start", parser_ps)
            correct_pe = gl_err.get("correct_page_end", parser_pe)
            parts.append(f"pages p{parser_ps}-{parser_pe}→p{correct_ps}-{correct_pe}")
        if gl_err.get("correct_section_number"):
            parser_sec = gs.get("section_number", "?") if gs else "?"
            parts.append(f"sec {parser_sec}→{gl_err['correct_section_number']}")
        error_lines.append(f"  GL   {'  '.join(parts) if parts else 'wrong'}")

    for ax in corrections.get("acronym_wrong_expansion") or []:
        acronym = ax.get("acronym", "?")
        # Find what parser extracted for this acronym from the log
        parser_val = next(
            (a["expansion"] for a in log.get("acronyms", []) if a["acronym"] == acronym),
            "?"
        )
        correct = ax.get("correct", "?")
        error_lines.append(f"  AX   {acronym}  parser=\"{parser_val}\"  want=\"{correct}\"")

    for am in corrections.get("acronym_missed") or []:
        acronym = am.get("acronym", "?")
        expansion = am.get("expansion", "?")
        error_lines.append(f"  AM   {acronym}=\"{expansion}\"")

    for ae in corrections.get("acronym_extra") or []:
        acronym = ae.get("acronym", "?")
        error_lines.append(f"  AE   {acronym}  (not an acronym)")

    lines = [header, parser_line]
    if error_lines:
        lines.append(f"errors {len(error_lines)}:")
        lines.extend(error_lines)
    else:
        lines.append("errors 0  (all ok)")

    if notes:
        lines.append(f"notes [{notes}]")

    return "\n".join(lines)
