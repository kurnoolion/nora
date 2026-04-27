"""Profile debug — inspect extracted IRs / emitted profile, or emit an LLM prompt.

Two modes:

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

Use case: when run_profile produces an empty profile (lvl=0 zones=0), the
analyzer surfaces structural metadata (style names, font sizes, block-type
counts) to diagnose whether the DOCX uses named Heading styles, whether the
font sizes cluster cleanly, or whether the corpus needs a hand-curated /
LLM-bootstrapped profile.

The --emit-prompt flow is the path forward when the heuristic profiler can't
identify document structure on a new MNO / doc-family that wasn't in the
profiler's training corpus. It treats LLM-derived profiles as user
corrections, preserving D-003 (parser stays heuristic, profile is
deterministic) while letting profile *generation* use an LLM.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from core.src.models.document import DocumentIR


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
            "or emit an LLM prompt template for bootstrapping a DocumentProfile "
            "via a proprietary chat interface."
        ),
    )
    parser.add_argument(
        "--env-dir",
        type=Path,
        help="Path to env_dir (required for analysis mode; not used with --emit-prompt).",
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
            "Paste the output into your proprietary LLM chat with 1-2 representative "
            "documents; save the LLM's JSON to <env_dir>/corrections/profile.json."
        ),
    )
    args = parser.parse_args()

    if args.emit_prompt:
        sys.stdout.write(_LLM_PROMPT_TEMPLATE)
        return

    if args.env_dir is None:
        parser.error("--env-dir is required (or use --emit-prompt to print the LLM prompt template)")

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
