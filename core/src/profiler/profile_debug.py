"""Profile debug — inspect / bootstrap / validate document profiles.

Three modes:

  python -m core.src.profiler.profile_debug --env-dir <ENV_DIR>
      Analyzes <ENV_DIR>/out/extract/*_ir.json and out/profile/profile.json,
      prints a compact, no-proprietary-content summary (NFR-8 / D-012) safe
      to paste in chat for remote diagnosis.

  python -m core.src.profiler.profile_debug --emit-prompt
      Prints a prompt template the user can paste into their proprietary LLM
      chat interface, along with 1-2 representative documents, to bootstrap a
      DocumentProfile JSON. The LLM-emitted JSON goes to
      <ENV_DIR>/corrections/profile.json, where the pipeline picks it up via
      the corrections-override workflow (D-011 / FR-15) on the next run.

  python -m core.src.profiler.profile_debug --validate <profile.json> [--fix] [--out <path>]
      Validates an LLM-emitted profile.json before the pipeline loads it:
      - JSON parses cleanly
      - Every regex string compiles (re.compile)
      - No regex exceeds a sane length bound (LLM repetition failure mode)
      - No runaway repetition pattern (e.g. "\\*\\*\\*..." 13kb of escapes)
      - DocumentProfile.load_json accepts the result
      With --fix, sanitizes problems in place (oversized / runaway /
      uncompilable regexes are blanked or dropped from list-valued fields)
      and writes back. With --out, writes the sanitized JSON elsewhere.

Use case: LLM-bootstrap workflow. The LLM occasionally emits oversized
or runaway-repetition regex strings (a known repetition failure mode);
validating before the pipeline loads it surfaces these cleanly without
exploding at runtime.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from core.src.models.document import DocumentIR


# Validation thresholds — tuned for typical LLM-emitted profiles.
_MAX_REGEX_LEN = 500           # any regex > this is suspect (LLM repetition)
_REPETITION_THRESHOLD = 12     # 2-4 char chunk repeated >=N times = runaway
_RUNAWAY_CHUNK_SIZES = (2, 3, 4)


def _is_runaway(s: str, threshold: int = _REPETITION_THRESHOLD) -> bool:
    """Detect runaway repetition (a small chunk repeating >= threshold times).

    Catches LLM repetition failures like '\\*\\*\\*\\*\\*...' — the model gets
    stuck in a token loop and emits the same 2-4 character pattern thousands
    of times consecutively.
    """
    if len(s) < _RUNAWAY_CHUNK_SIZES[0] * threshold:
        return False
    for chunk_size in _RUNAWAY_CHUNK_SIZES:
        chunk_total = chunk_size * threshold
        for i in range(len(s) - chunk_total + 1):
            chunk = s[i:i + chunk_size]
            if not chunk:
                continue
            if s[i:i + chunk_total] == chunk * threshold:
                return True
    return False


def _check_regex(pattern: str) -> tuple[str, str]:
    """Return (status, note) for a regex string.

    status in {'OK', 'BAD'}; 'OK' for empty strings (treated as 'no pattern').
    """
    if not pattern:
        return "OK", "empty"
    if len(pattern) > _MAX_REGEX_LEN:
        return "BAD", f"oversized {len(pattern)} chars (max {_MAX_REGEX_LEN})"
    if _is_runaway(pattern):
        return "BAD", "runaway repetition"
    try:
        re.compile(pattern)
    except re.error as e:
        return "BAD", f"compile error: {e}"
    return "OK", f"{len(pattern)} chars"


def _walk_regex_fields(data: dict, fix: bool) -> list[tuple[str, str, str, str]]:
    """Walk every regex-valued field in the profile. Return issue list.

    Each issue: (label, status, note, action).
    If fix=True, mutate `data` in place: blank bad scalar fields, drop bad
    list entries.
    """
    issues: list[tuple[str, str, str, str]] = []

    def check_str(parent: dict, key: str, label: str) -> None:
        val = parent.get(key, "")
        if not isinstance(val, str):
            issues.append((label, "BAD", f"not a string ({type(val).__name__})",
                           "blanked" if fix else "would blank"))
            if fix:
                parent[key] = ""
            return
        status, note = _check_regex(val)
        action = ""
        if status == "BAD":
            if fix:
                parent[key] = ""
                action = "blanked"
            else:
                action = "would blank"
        issues.append((label, status, note, action))

    def check_list(parent: dict, key: str, label: str) -> None:
        items = parent.get(key, [])
        if not isinstance(items, list):
            issues.append((label, "BAD", f"not a list ({type(items).__name__})",
                           "replaced with []" if fix else "would replace with []"))
            if fix:
                parent[key] = []
            return
        kept: list[str] = []
        for i, val in enumerate(items):
            entry_label = f"{label}[{i}]"
            if not isinstance(val, str):
                issues.append((entry_label, "BAD", f"not a string ({type(val).__name__})",
                               "dropped" if fix else "would drop"))
                continue
            status, note = _check_regex(val)
            action = ""
            if status == "BAD":
                action = "dropped" if fix else "would drop"
            else:
                kept.append(val)
            issues.append((entry_label, status, note, action))
        if fix:
            parent[key] = kept

    hd = data.get("heading_detection") if isinstance(data.get("heading_detection"), dict) else {}
    check_str(hd, "numbering_pattern", "heading_detection.numbering_pattern")

    rid = data.get("requirement_id") if isinstance(data.get("requirement_id"), dict) else {}
    check_str(rid, "pattern", "requirement_id.pattern")

    pm = data.get("plan_metadata") if isinstance(data.get("plan_metadata"), dict) else {}
    for fname in ("plan_name", "plan_id", "version", "release_date"):
        sub = pm.get(fname)
        if isinstance(sub, dict):
            check_str(sub, "pattern", f"plan_metadata.{fname}.pattern")

    zones = data.get("document_zones")
    if isinstance(zones, list):
        for i, z in enumerate(zones):
            if isinstance(z, dict):
                check_str(z, "section_pattern", f"document_zones[{i}].section_pattern")

    hf = data.get("header_footer") if isinstance(data.get("header_footer"), dict) else {}
    check_list(hf, "header_patterns", "header_footer.header_patterns")
    check_list(hf, "footer_patterns", "header_footer.footer_patterns")
    check_str(hf, "page_number_pattern", "header_footer.page_number_pattern")

    crp = data.get("cross_reference_patterns") if isinstance(data.get("cross_reference_patterns"), dict) else {}
    check_list(crp, "standards_citations", "cross_reference_patterns.standards_citations")
    check_str(crp, "internal_section_refs", "cross_reference_patterns.internal_section_refs")
    check_str(crp, "requirement_id_refs", "cross_reference_patterns.requirement_id_refs")

    return issues


def _validate_profile(path: Path, fix: bool, out_path: Path | None) -> int:
    """Validate (and optionally sanitize) an LLM-emitted profile.json.

    Returns the process exit code: 0 if all OK after any applied fixes, 1 otherwise.
    """
    if not path.exists():
        print(f"VLD ERR file not found: {path}")
        return 1
    try:
        text = path.read_text()
    except Exception as e:
        print(f"VLD ERR read failed: {type(e).__name__}: {e}")
        return 1
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"VLD ERR parse error at line {e.lineno} col {e.colno}: {e.msg}")
        return 1
    if not isinstance(data, dict):
        print(f"VLD ERR top-level JSON is not an object (got {type(data).__name__})")
        return 1

    print(f"VLD {path.name} ({path})")
    print(f"VLD bytes={len(text)} top_level_keys={len(data)}")
    print()

    issues = _walk_regex_fields(data, fix)
    bad_count = sum(1 for _, s, _, _ in issues if s == "BAD")
    ok_count = sum(1 for _, s, _, _ in issues if s == "OK")

    for label, status, note, action in issues:
        line = f"{status} {label}: {note}"
        if action:
            line += f" -> {action}"
        print(line)

    print()
    print(f"summary: {ok_count} OK, {bad_count} BAD")

    if fix:
        # --fix always writes (otherwise an --out target stays at its prior
        # state — possibly empty — when there's nothing to sanitize, which
        # surprises callers who expected --fix to emit a guaranteed file).
        target = out_path or path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        target.write_text(payload)
        if bad_count > 0:
            print(f"fix: sanitized; wrote {len(payload)} bytes to {target}")
        else:
            print(f"fix: no issues; wrote {len(payload)} bytes to {target}")
    elif bad_count > 0:
        print("fix: rerun with --fix to apply (blank bad regex fields, drop bad list entries)")

    # Schema-level load check on whichever file is final.
    final = (out_path if (fix and out_path) else path)
    try:
        from core.src.profiler.profile_schema import DocumentProfile
        DocumentProfile.load_json(final)
        print("schema: DocumentProfile.load_json OK")
    except Exception as e:
        print(f"schema: load FAIL — {type(e).__name__}: {e}")
        return 1

    return 0 if bad_count == 0 else 1


def _format_ir_lines(label: str, ir: DocumentIR) -> list[str]:
    """One or two lines describing an IR — no proprietary content."""
    type_counts = Counter(b.type.value for b in ir.content_blocks)
    # h:N,p:N,t:N,i:N,e:N (heading / paragraph / table / image / embedded)
    type_str = ",".join(f"{t[0]}:{c}" for t, c in sorted(type_counts.items()))

    style_counts = Counter(b.style for b in ir.content_blocks if b.style)

    sizes = sorted({round(b.font_info.size, 1) for b in ir.content_blocks if b.font_info})
    bold_count = sum(1 for b in ir.content_blocks if b.font_info and b.font_info.bold)

    lines = [f"{label} fmt={ir.source_format} blk={ir.block_count} type={type_str}"]

    if style_counts:
        # Top 6 styles by count, truncated
        top = style_counts.most_common(6)
        style_summary = ",".join(f"{_safe_style(s)}:{c}" for s, c in top)
        lines.append(f"{label} styles={len(style_counts)} ({style_summary})")
    else:
        lines.append(f"{label} styles=0")

    if sizes:
        sizes_str = ",".join(str(s) for s in sizes[:8])
        lines.append(f"{label} fonts={len(sizes)} sizes={sizes_str} bold={bold_count}")
    else:
        lines.append(f"{label} fonts=0")

    return lines


def _safe_style(style: str) -> str:
    """Strip whitespace and truncate long style names for compact rendering."""
    s = style.replace(" ", "").replace(",", "_")
    return s[:24]


def _format_profile_lines(profile_path: Path) -> list[str]:
    """Compact summary of the emitted profile (no proprietary content)."""
    if not profile_path.exists():
        return [f"PROFILE absent ({profile_path.name} not at out/profile/)"]

    try:
        with open(profile_path) as f:
            p = json.load(f)
    except Exception as e:
        return [f"PROFILE load_error={type(e).__name__}"]

    hd = p.get("heading_detection") or {}
    method = hd.get("method", "?")
    levels = len(hd.get("heading_levels", []) or [])

    req = p.get("requirement_id") or {}
    req_pattern_count = len(req.get("patterns", []) or [])

    zones_block = p.get("document_zones") or {}
    if isinstance(zones_block, dict):
        zones = len(zones_block.get("zones", []) or [])
    elif isinstance(zones_block, list):
        zones = len(zones_block)
    else:
        zones = 0

    hf_block = p.get("header_footer") or {}
    hf_pat = (
        len(hf_block.get("patterns", []) or [])
        if isinstance(hf_block, dict) else 0
    )

    xref = p.get("cross_references") or {}
    xref_count = sum(
        len(xref.get(k, []) or [])
        for k in (
            "internal_patterns",
            "standards_patterns",
            "cross_plan_patterns",
            "patterns",
        )
        if isinstance(xref.get(k, []), list)
    )

    body = p.get("body_text") or {}
    body_size = body.get("dominant_size", body.get("size", "?"))

    return [
        f"PROFILE method={method} levels={levels} rpat={req_pattern_count} zones={zones}",
        f"PROFILE hf_patterns={hf_pat} xref_patterns={xref_count} body_size={body_size}",
    ]


_LLM_PROMPT_TEMPLATE = """\
You are producing a single JSON "DocumentProfile" that drives a profile-driven, LLM-free structural parser. The parser uses your output to identify headings, requirement IDs, document zones, and cross-references in the documents below.

Do NOT assume conventions from any document family you may know — derive every regex and structural rule strictly from what you observe in the actual documents pasted at the bottom.

Emit ONE JSON object — no prose, no markdown fences, no commentary. The JSON must match the schema exactly.

SCHEMA (placeholder values; replace based on the actual documents):

{
  "profile_name": "<short-identifier>",
  "profile_version": 1,
  "created_from": ["<filename1>.docx", "<filename2>.docx"],
  "last_updated": "YYYY-MM-DD",
  "heading_detection": {
    "method": "docx_styles" | "font_size_clustering",
    "levels": [
      {
        "level": 1,
        "font_size_min": <float, points>,
        "font_size_max": <float, points>,
        "bold": true | false | null,
        "all_caps": true | false | null,
        "sample_texts": [],
        "count": 0
      }
    ],
    "numbering_pattern": "<regex matching the section-number prefix on heading lines, e.g. ^(\\\\d+\\\\.)+\\\\d*\\\\s>",
    "max_observed_depth": <int>
  },
  "requirement_id": {
    "pattern": "<regex matching individual requirement labels, or empty string if the docs don't use per-requirement IDs>",
    "components": {"prefix": "<observed>", "separator": "<observed>", "plan_id_position": <int>, "number_position": <int>},
    "sample_ids": [],
    "total_found": 0
  },
  "plan_metadata": {
    "plan_name":    {"location": "first_page", "pattern": "<regex with one capture group, or empty>", "sample_value": ""},
    "plan_id":      {"location": "first_page", "pattern": "<regex>", "sample_value": ""},
    "version":      {"location": "first_page", "pattern": "<regex>", "sample_value": ""},
    "release_date": {"location": "first_page", "pattern": "<regex>", "sample_value": ""}
  },
  "document_zones": [
    {"section_pattern": "<regex>", "zone_type": "<one of: introduction | hardware_specs | software_specs | scenarios | provisioning | performance | test_coverage | references | content>", "description": "<heading text>", "heading_text": "<heading text>"}
  ],
  "header_footer": {
    "header_patterns": [],
    "footer_patterns": [],
    "page_number_pattern": "<regex matching repeating page-number lines, or empty>"
  },
  "cross_reference_patterns": {
    "standards_citations": [
      "<one regex per citation flavor observed: 3GPP TS, IETF RFC, GSMA, IEEE, Wi-Fi Alliance, etc.>"
    ],
    "internal_section_refs": "<regex for 'see section X.Y' style intra-document refs, or empty>",
    "requirement_id_refs": "<same regex as requirement_id.pattern, or empty if no requirement IDs>"
  },
  "body_text": {
    "font_size_min": <float>,
    "font_size_max": <float>,
    "font_families": []
  }
}

GUIDANCE:
- For DOCX with explicit "Heading 1" / "Heading 2" paragraph styles → method = "docx_styles". For visual-only headings (bold + larger font without semantic styles) → method = "font_size_clustering".
- Requirement IDs may follow any scheme ("REQ-NNN", "VOWIFI_R042", "[V-001]", or none at all). If the documents enumerate requirements without a stable label format, leave requirement_id.pattern as empty string and the parser will fall back to section-number anchoring.
- document_zones: only top-level structural sections. Skip nested subsections. zone_type must be one of the listed values; pick "content" if none of the others fits.
- plan_metadata fields that don't appear in the documents → empty pattern string, empty sample_value.
- standards_citations: list one regex per citation flavor (3GPP TS XXX.YYY, RFC NNNN, etc.). Don't conflate flavors into one regex.
- All sample_texts arrays can be empty; they're informational.

Now, here are the representative documents:

[PASTE 1-2 .docx FILES BELOW — text content of the documents]
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect extracted IRs and the emitted profile in compact format, "
            "emit an LLM prompt template for bootstrapping a DocumentProfile, "
            "or validate (and optionally sanitize) an LLM-emitted profile.json."
        ),
    )
    parser.add_argument(
        "--env-dir",
        type=Path,
        help="Path to env_dir (analysis mode; not used with --emit-prompt or --validate).",
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=10,
        help="Maximum number of IR files to summarize (default: 10).",
    )
    parser.add_argument(
        "--emit-prompt",
        action="store_true",
        help=(
            "Print the LLM prompt template (no env_dir needed) and exit. "
            "Paste into your proprietary LLM chat with 1-2 representative documents; "
            "save the LLM's JSON to <env_dir>/corrections/profile.json."
        ),
    )
    parser.add_argument(
        "--validate",
        type=Path,
        metavar="PROFILE_JSON",
        help=(
            "Path to an LLM-emitted profile.json to validate. Checks JSON parse, "
            "regex compile, length bounds, runaway repetition, and schema load."
        ),
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help=(
            "With --validate: sanitize problems in place — blank bad regex "
            "fields, drop bad list entries — and write back."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        help=(
            "With --validate --fix: write sanitized JSON to this path instead "
            "of overwriting the input."
        ),
    )
    args = parser.parse_args()

    if args.emit_prompt:
        sys.stdout.write(_LLM_PROMPT_TEMPLATE)
        return

    if args.validate:
        rc = _validate_profile(
            Path(args.validate).expanduser().resolve(),
            fix=args.fix,
            out_path=Path(args.out).expanduser().resolve() if args.out else None,
        )
        sys.exit(rc)

    if args.env_dir is None:
        parser.error(
            "specify one of --env-dir, --emit-prompt, or --validate <profile.json>"
        )

    env_dir = Path(args.env_dir).expanduser().resolve()
    extract_dir = env_dir / "out" / "extract"
    profile_dir = env_dir / "out" / "profile"
    corrections_dir = env_dir / "corrections"

    print(f"PRD {env_dir.name} {datetime.now().strftime('%Y-%m-%dT%H:%M')}")
    print(f"ENV {env_dir}")

    if not extract_dir.exists():
        print(f"ERR no extract dir at out/extract/")
        sys.exit(1)

    ir_files = sorted(extract_dir.glob("*_ir.json"))
    if not ir_files:
        print("ERR no *_ir.json files in out/extract/")
        sys.exit(1)

    print()
    for i, f in enumerate(ir_files[: args.max_docs], start=1):
        try:
            ir = DocumentIR.load_json(f)
        except Exception as e:
            print(f"DOC{i} load_error={type(e).__name__}")
            continue
        for line in _format_ir_lines(f"DOC{i}", ir):
            print(line)

    if len(ir_files) > args.max_docs:
        print(f"... {len(ir_files) - args.max_docs} more IR files (use --max-docs)")

    print()
    for line in _format_profile_lines(profile_dir / "profile.json"):
        print(line)

    print()
    profile_corr = corrections_dir / "profile.json"
    taxonomy_corr = corrections_dir / "taxonomy.json"
    print(
        f"CORR profile={'present' if profile_corr.exists() else 'absent'} "
        f"taxonomy={'present' if taxonomy_corr.exists() else 'absent'}"
    )

    print()
    print("ERR none")


if __name__ == "__main__":
    main()
