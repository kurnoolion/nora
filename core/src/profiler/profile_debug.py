"""Profile debug — inspect extracted IRs and the emitted profile in compact format.

Diagnostic tool for the case when `run_profile` produces an empty or low-quality
profile (zero heading levels, zero zones, etc.) and you need to figure out why
without pasting proprietary document content into chat. Emits a compact report
matching NFR-8 / D-012 conventions: only field names, counts, style names,
font sizes, and method labels — no document text.

Usage:
    python -m core.src.profiler.profile_debug --env-dir /path/to/env_dir

Output is line-oriented and < 30 lines for a typical 5-doc corpus, safe to paste
in chat-mediated debugging sessions.
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect extracted IRs and the emitted profile in compact format. "
            "No proprietary document content — safe to paste in chat."
        ),
    )
    parser.add_argument(
        "--env-dir",
        required=True,
        type=Path,
        help="Path to env_dir (the same one used for the pipeline run).",
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=10,
        help="Maximum number of IR files to summarize (default: 10).",
    )
    args = parser.parse_args()

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
