"""Resolve-review: template generation and compact RES-CHK report.

Workflow:
  1. generate template:  resolve_review_cli create <doc_id>_xrefs.json
  2. reviewer edits:     <doc_id>_resolve_review.json  (corrections section only)
  3. generate report:    resolve_review_cli report <doc_id>_resolve_review.json
  4. paste into chat

Correction entry shapes
  internal_false_broken   list of {"target_req_id":"VZ_REQ_X","note":"..."}
  internal_wrong_target   list of {"source_req_id":"VZ_REQ_X","wrong_target":"VZ_REQ_Y","correct_target":"VZ_REQ_Z","note":"..."}
  cross_plan_wrong_id     list of {"source_req_id":"VZ_REQ_X","wrong_plan_id":"MMOTADM","correct_plan_id":"MNOTADM","note":"..."}
  standards_wrong_spec    list of {"source_req_id":"VZ_REQ_X","wrong_spec":"3GPP TS 36.101","correct_spec":"...","correct_release":"Rel-16","note":"..."}

Compact report codes
  FB  false broken internal ref   — resolver said broken, ref actually exists
  WT  wrong internal target       — resolver resolved to wrong req
  WP  wrong cross-plan ID         — plan ID in citation is a typo
  WS  wrong standards spec/release
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_NOTE_MAX = 50


def _trunc(text: str, max_len: int = _NOTE_MAX) -> str:
    if not text:
        return ""
    t = text.strip().replace("\n", " ")
    return t if len(t) <= max_len else t[: max_len - 1].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------------------
# Template generation
# ---------------------------------------------------------------------------

def generate_template(xrefs_path: Path) -> dict[str, Any]:
    """Build a pre-populated review template from a *_xrefs.json file."""
    with open(xrefs_path, encoding="utf-8") as f:
        xrefs = json.load(f)

    doc_id = xrefs.get("plan_id", Path(xrefs_path).stem.replace("_xrefs", ""))
    summary = xrefs.get("summary", {})

    def _stat(total_key: str, resolved_key: str, other_key: str) -> str:
        t = summary.get(total_key, 0)
        r = summary.get(resolved_key, 0)
        o = summary.get(other_key, 0)
        label = "broken" if "broken" in other_key else "unresolved"
        return f"{t} total ({r} resolved, {o} {label})"

    broken_internal = [
        f"{r['source_req_id']} → {r['target_req_id']}  (§{r['source_section']})"
        for r in xrefs.get("internal_refs", [])
        if r.get("status") == "broken"
    ]
    unresolved_xplan = list({
        r["target_plan_id"]
        for r in xrefs.get("cross_plan_refs", [])
        if r.get("status") == "unresolved"
    })
    unresolved_std = list({
        r["spec"]
        for r in xrefs.get("standards_refs", [])
        if r.get("status") in ("unresolved", "broken")
    })

    return {
        "_instructions": (
            "Fill in reviewer/review_date/overall_verdict, then add entries to "
            "'corrections' for every error found. resolver_snapshot is read-only reference. "
            "IMPORTANT: notes must not contain proprietary document content."
        ),
        "doc_id": doc_id,
        "reviewer": "",
        "review_date": "",
        "overall_verdict": "",
        "resolver_snapshot": {
            "internal_refs": _stat("total_internal", "resolved_internal", "broken_internal"),
            "cross_plan_refs": _stat("total_cross_plan", "resolved_cross_plan", "unresolved_cross_plan"),
            "standards_refs": _stat("total_standards", "resolved_standards", "unresolved_standards"),
            "broken_internal_refs": broken_internal,
            "unresolved_cross_plan_ids": unresolved_xplan,
            "unresolved_standards_specs": unresolved_std,
        },
        "corrections": {
            "_help_internal_false_broken": (
                "Internal refs the resolver marked 'broken' that actually exist. "
                "[{\"target_req_id\": \"VZ_REQ_X\", \"note\": \"...\"}]"
            ),
            "internal_false_broken": [],
            "_help_internal_wrong_target": (
                "Internal refs resolved to the WRONG requirement. "
                "[{\"source_req_id\": \"VZ_REQ_X\", \"wrong_target\": \"VZ_REQ_Y\", "
                "\"correct_target\": \"VZ_REQ_Z\", \"note\": \"...\"}]"
            ),
            "internal_wrong_target": [],
            "_help_cross_plan_wrong_id": (
                "Cross-plan refs where the plan ID in the citation is wrong. "
                "[{\"source_req_id\": \"VZ_REQ_X\", \"wrong_plan_id\": \"MMOTADM\", "
                "\"correct_plan_id\": \"MNOTADM\", \"note\": \"...\"}]"
            ),
            "cross_plan_wrong_id": [],
            "_help_standards_wrong_spec": (
                "Standards refs with the wrong spec number or release. "
                "[{\"source_req_id\": \"VZ_REQ_X\", \"wrong_spec\": \"3GPP TS 36.101\", "
                "\"correct_spec\": \"3GPP TS 36.100\", \"correct_release\": \"Rel-16\", "
                "\"note\": \"...\"}]"
            ),
            "standards_wrong_spec": [],
        },
        "notes": "",
    }


# ---------------------------------------------------------------------------
# Compact report
# ---------------------------------------------------------------------------

def generate_compact_report(
    review_path: Path,
    xrefs_path: Path | None = None,
) -> str:
    """Read a completed review JSON and return a compact RES-CHK report."""
    with open(review_path, encoding="utf-8") as f:
        review = json.load(f)

    doc_id      = review.get("doc_id", "?")
    reviewer    = review.get("reviewer") or "?"
    review_date = review.get("review_date") or "?"
    verdict     = review.get("overall_verdict") or "?"
    notes       = _trunc(review.get("notes") or "")

    # Auto-detect sibling xrefs file
    if xrefs_path is None:
        candidate = review_path.parent / f"{doc_id}_xrefs.json"
        if candidate.exists():
            xrefs_path = candidate

    xrefs: dict[str, Any] = {}
    if xrefs_path and xrefs_path.exists():
        with open(xrefs_path, encoding="utf-8") as f:
            xrefs = json.load(f)

    summary = xrefs.get("summary", {})
    ti = summary.get("total_internal", 0)
    ri = summary.get("resolved_internal", 0)
    bi = summary.get("broken_internal", 0)
    tx = summary.get("total_cross_plan", 0)
    rx = summary.get("resolved_cross_plan", 0)
    ux = summary.get("unresolved_cross_plan", 0)
    ts = summary.get("total_standards", 0)
    rs = summary.get("resolved_standards", 0)
    us = summary.get("unresolved_standards", 0)

    header      = f"RES-CHK {doc_id}  {review_date}  reviewer={reviewer}  verdict={verdict}"
    resolver_line = (
        f"resolver  int={ti}({ri}r/{bi}b)"
        f"  xplan={tx}({rx}r/{ux}u)"
        f"  std={ts}({rs}r/{us}u)"
    )

    corrections = review.get("corrections", {})
    error_lines: list[str] = []

    for fb in corrections.get("internal_false_broken") or []:
        target = fb.get("target_req_id", "?")
        note   = _trunc(fb.get("note") or "")
        note_p = f"  [{len(note)}ch]" if note else ""
        error_lines.append(f"  FB   {target}{note_p}")

    for wt in corrections.get("internal_wrong_target") or []:
        src    = wt.get("source_req_id", "?")
        wrong  = wt.get("wrong_target", "?")
        correct = wt.get("correct_target", "?")
        error_lines.append(f"  WT   {src}  {wrong}→{correct}")

    for wp in corrections.get("cross_plan_wrong_id") or []:
        src    = wp.get("source_req_id", "?")
        wrong  = wp.get("wrong_plan_id", "?")
        correct = wp.get("correct_plan_id", "?")
        error_lines.append(f"  WP   {src}  {wrong}→{correct}")

    for ws in corrections.get("standards_wrong_spec") or []:
        src     = ws.get("source_req_id", "?")
        wrong   = ws.get("wrong_spec", "?")
        correct = ws.get("correct_spec", wrong)
        rel     = ws.get("correct_release", "")
        rel_p   = f"  rel={rel}" if rel else ""
        error_lines.append(f"  WS   {src}  {wrong}→{correct}{rel_p}")

    lines = [header, resolver_line]
    if error_lines:
        lines.append(f"errors {len(error_lines)}:")
        lines.extend(error_lines)
    else:
        lines.append("errors 0  (all ok)")

    if notes:
        lines.append(f"notes [{notes}]")

    return "\n".join(lines)
