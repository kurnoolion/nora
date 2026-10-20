"""parse_debug — diagnostic CLI for parser-side detection paths.

First subcommand:

  python -m core.src.parser.parse_debug revhist --env-dir <ENV_DIR> --doc <DOC_ID>

      Walks every TABLE block in <ENV_DIR>/out/extract/<DOC_ID>_ir.json and
      prints, for each, the per-signal breakdown that the active profile's
      RevhistDetection scorer would compute:

        - position fraction (block_index / total_blocks)
        - joined headers
        - merged-cell anchor text(s)
        - first 3 body rows
        - position / vocab / cell sub-scores
        - combined score and whether it clears the threshold

      Use this to diagnose "why doesn't doc X get its revhist detected" —
      surfaces the weakest signal so you can either lower the threshold,
      add a vocab token, or relax the position cutoff.

The CLI emits no proprietary content beyond what's already in the IR;
the user is expected to redact when sharing chunks of this output.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# revhist subcommand
# ---------------------------------------------------------------------------

def _load_profile(env_dir: Path):
    """Load the profile through the same substitution chain the parse
    stage uses (D-062), so placeholder regex fields like
    ``requirement_id.pattern = "<MNO0>_REQ_<PLAN>_\\d+"`` are resolved
    to their real values before parse_debug builds compiled regexes.

    Without this, parse_debug would build ``_req_id_anchored_re`` from
    the literal-placeholder pattern, ``_heading_title_text``'s last-run
    stripping would never match the real-value runs in the IR, and the
    label-path candidate output would show spurious 'miss' verdicts
    that don't reflect what the real parser sees.
    """
    from core.src.profiler.profile_substitute import load_substituted_profile

    candidates = [
        env_dir / "corrections" / "profile.json",
        env_dir / "out" / "profile" / "profile.json",
    ]
    for p in candidates:
        if p.exists():
            return load_substituted_profile(p, env_dir=env_dir), p
    raise FileNotFoundError(
        f"No profile at {candidates[0]} or {candidates[1]}. "
        "Run the profile stage first."
    )


def _load_ir(env_dir: Path, doc_id: str):
    from core.src.models.document import DocumentIR

    p = env_dir / "out" / "extract" / f"{doc_id}_ir.json"
    if not p.exists():
        raise FileNotFoundError(
            f"No IR at {p}. Run the extract stage first."
        )
    return DocumentIR.load_json(p)


def _format_row(cells: list[str], max_cells: int = 6, cell_max: int = 40) -> str:
    cells = list(cells)[:max_cells]
    return " | ".join((c or "")[:cell_max] for c in cells)


def cmd_revhist(args: argparse.Namespace) -> int:
    from core.src.models.document import BlockType
    from core.src.parser.structural_parser import GenericStructuralParser

    env_dir: Path = args.env_dir
    profile, profile_path = _load_profile(env_dir)
    rd = profile.revhist_detection
    parser = GenericStructuralParser(profile)
    ir = _load_ir(env_dir, args.doc)

    total = len(ir.content_blocks)
    print(f"# parse_debug revhist — doc={args.doc!r}")
    print(f"profile: {profile_path}")
    print(f"  revision_history_label_pattern = "
          f"{profile.revision_history_label_pattern!r}")
    print(f"  requirement_id.anchor = "
          f"{profile.requirement_id.anchor!r}")
    print(f"  revhist_detection.enabled = {rd.enabled}")
    print(f"  threshold = {rd.threshold:.2f}  "
          f"weights: position={rd.position_weight} vocab={rd.vocab_weight} "
          f"cell={rd.cell_weight}")
    print(f"  max_position_fraction = {rd.max_position_fraction}")
    print(f"  vocab_tokens ({len(rd.vocab_tokens)}): "
          f"{', '.join(rd.vocab_tokens[:10])}"
          + ("…" if len(rd.vocab_tokens) > 10 else ""))
    print()
    print(f"total content blocks: {total}")
    print()

    # --- Label-path candidates ---
    # Walk every HEADING/PARAGRAPH block whose text looks like it might
    # be a revhist label. Show raw text, the normalized title that the
    # body pass actually tests, and whether the regex matches.
    print("[label-path candidates — text contains 'history' or 'log']")
    label_hits = 0
    for b in ir.content_blocks:
        if b.type not in (BlockType.PARAGRAPH, BlockType.HEADING):
            continue
        t = (b.text or "").lower()
        if not any(k in t for k in ("history", "change log", "revision log",
                                    "version log", "document log")):
            continue
        normalized = parser._heading_title_text(b)
        matches = bool(
            parser._revhist_re
            and parser._revhist_re.match(normalized)
        )
        flag = "✓ MATCH" if matches else "  miss "
        if matches:
            label_hits += 1
        print(f"{flag}  idx={b.position.index:>4d}  "
              f"type={b.type.value:9s}")
        print(f"        raw:        {(b.text or '')[:120]!r}")
        print(f"        normalized: {normalized[:120]!r}")
        if b.runs:
            run_summary = " | ".join(
                f"{r.text[:30]!r}" for r in b.runs[:4]
            )
            print(f"        runs ({len(b.runs)}): {run_summary}")
        print()
    if label_hits == 0:
        print("  (no label-path matches — fall through to table scoring)")
        print()
    else:
        print(f"  total label-path matches: {label_hits}")
        print()

    n_tables = 0
    n_pass = 0
    for b in ir.content_blocks:
        if b.type != BlockType.TABLE or not (b.headers or b.merged_cells):
            continue
        n_tables += 1
        idx = b.position.index
        frac = idx / max(total - 1, 1)
        joined_headers = " | ".join(h.strip() for h in (b.headers or []))
        merged_texts = [
            mc.text for mc in (b.merged_cells or []) if mc.text
        ]
        rows = b.rows or []

        # Always compute the score breakdown, even when revhist_detection
        # is disabled — the user wants to know whether enabling would
        # have caught this table.
        if not parser._revhist_score_enabled:
            # Temporarily enable to compute breakdown.
            parser._revhist_score_enabled = True
            score, breakdown = parser._score_revhist_table(b, total)
            parser._revhist_score_enabled = False
        else:
            score, breakdown = parser._score_revhist_table(b, total)

        clears = score >= rd.threshold
        marker = "✓ PASS" if clears else "  ----"
        if clears:
            n_pass += 1
        print(f"{marker}  idx={idx:>4d}  frac={frac:.2f}  "
              f"score={score:.2f} (pos={breakdown.get('position', 0):.2f} "
              f"vocab={breakdown.get('vocab', 0):.2f} "
              f"cell={breakdown.get('cell', 0):.2f})")
        print(f"        headers: {joined_headers[:120] or '(empty)'}")
        if merged_texts:
            print(f"        merged:  {' || '.join(merged_texts)[:120]}")
        for r_idx, row in enumerate(rows[:3]):
            print(f"        row[{r_idx}]: {_format_row(row)}")
        print()

    print(
        f"summary: {n_tables} TABLE blocks scanned · "
        f"{n_pass} would clear threshold "
        f"({'enabled' if rd.enabled else 'DISABLED — counts hypothetical'})"
    )
    return 0


# ---------------------------------------------------------------------------
# glossary subcommand
# ---------------------------------------------------------------------------

# The density gate (keywords + stopwords + token split + helper) lives
# in ``structural_parser`` so the parser and this diagnostic share one
# implementation. The legacy gate threshold lives there too. Keep the
# parse_debug-local helpers (col-0 shape, prose heuristic, position
# scoring) here — they're preview-only scoring signals that haven't
# landed in the parser yet.
from core.src.parser.structural_parser import (
    _GLOSSARY_LABEL_KEYWORDS as _GLOSSARY_KEYWORDS,
    _GLOSSARY_LABEL_STOPWORDS as _GLOSSARY_STOPWORDS,
    _GLOSSARY_LABEL_MIN_DENSITY,
    _glossary_label_density as _glossary_density,
)

# Col-0 shape: short uppercase / mixed-case acronym tokens. Bounded
# length so prose doesn't slip in.
_GLOSSARY_COL0_ACRONYM_RE = re.compile(r"^[A-Z][A-Za-z0-9/_-]{1,15}$")

# Col-1 shape: prose. Heuristic — any cell with > 8 chars AND >= 1
# whitespace (rules out single-word values).
def _looks_like_prose(text: str) -> bool:
    s = (text or "").strip()
    return len(s) > 8 and any(c.isspace() for c in s)


def _glossary_position_score(frac: float) -> float:
    """Glossaries can sit anywhere — typically Acronyms/Definitions
    section mid-doc or at end. Heuristic: middle-and-back of doc
    favoured slightly. Front-matter (first 15%) gets 0; everywhere
    else gets 1.0. Subject to revision when the real corpus reveals
    a clearer pattern."""
    return 0.0 if frac <= 0.15 else 1.0


def cmd_glossary(args: argparse.Namespace) -> int:
    from core.src.models.document import BlockType

    env_dir: Path = args.env_dir
    profile, profile_path = _load_profile(env_dir)
    ir = _load_ir(env_dir, args.doc)

    total = len(ir.content_blocks)
    print(f"# parse_debug glossary — doc={args.doc!r}")
    print(f"profile: {profile_path}")
    print(f"  heading_detection.definitions_section_pattern = "
          f"{profile.heading_detection.definitions_section_pattern!r}")
    print(f"  heading_detection.definitions_table_header_pattern = "
          f"{profile.heading_detection.definitions_table_header_pattern!r}")
    print(f"  definitions_entry_pattern = "
          f"{profile.definitions_entry_pattern!r}")
    print()
    print(f"# proposed GlossaryDetection scoring defaults (preview — "
          f"not yet in profile schema)")
    print(f"  keywords ({len(_GLOSSARY_KEYWORDS)}): "
          f"{', '.join(sorted(_GLOSSARY_KEYWORDS))}")
    print(f"  stopwords ({len(_GLOSSARY_STOPWORDS)}): "
          f"{', '.join(sorted(_GLOSSARY_STOPWORDS))}")
    print(f"  density rule: keyword_hits / meaningful_tokens >= "
          f"{_GLOSSARY_LABEL_MIN_DENSITY:.2f}  "
          f"(also enforced in parser._extract_definitions)")
    print(f"  surface: heading/paragraph title OR table joined-headers + merged-cells")
    print(f"  req_id strip: trailing req_id token removed before tokenizing")
    print(f"  col-0 acronym shape: {_GLOSSARY_COL0_ACRONYM_RE.pattern!r}")
    print(f"  col-1 prose heuristic: len>8 AND has whitespace")
    print(f"  position rule: 1.0 if frac > 0.15 else 0.0  "
          f"(glossaries typically mid-doc or end)")
    print()
    print(f"total content blocks: {total}")
    print()

    # --- Label-path candidates --- glossary keywords in heading/paragraph
    # Build a parser instance for ``_heading_title_text`` access.
    from core.src.parser.structural_parser import GenericStructuralParser
    parser = GenericStructuralParser(profile)
    # Use the non-anchored req_id regex so embedded ids in joined-header
    # text get stripped (the anchored variant only matches a full string
    # that *is* a req_id). The label-path input from
    # ``_heading_title_text`` is already req_id-stripped, so this only
    # matters for the table-surface scoring below.
    req_id_re = parser._req_id_re

    print("[label-path candidates — heading/paragraph density check]")
    print(f"  rule: density(hits / meaningful tokens) >= "
          f"{_GLOSSARY_LABEL_MIN_DENSITY:.2f} after stripping req_id + stopwords")
    print()
    label_hits = 0
    section_re_legacy = (
        re.compile(profile.heading_detection.definitions_section_pattern)
        if profile.heading_detection.definitions_section_pattern else None
    )
    for b in ir.content_blocks:
        if b.type not in (BlockType.PARAGRAPH, BlockType.HEADING):
            continue
        t = (b.text or "").lower()
        if not any(k in t for k in _GLOSSARY_KEYWORDS):
            continue
        normalized = parser._heading_title_text(b)

        hits, total_meaningful, density, hit_tokens = _glossary_density(
            normalized, req_id_re,
        )
        density_match = density >= _GLOSSARY_LABEL_MIN_DENSITY

        # Also show the legacy regex verdict for comparison.
        legacy_match = bool(section_re_legacy and section_re_legacy.search(normalized))

        flag = "✓ DENSITY" if density_match else "  miss   "
        if density_match:
            label_hits += 1
        print(f"{flag}  idx={b.position.index:>4d}  type={b.type.value:9s}  "
              f"density={hits}/{total_meaningful}={density:.0%}  "
              f"(legacy regex: {'MATCH' if legacy_match else 'miss'})")
        print(f"        raw:        {(b.text or '')[:120]!r}")
        print(f"        normalized: {normalized[:120]!r}")
        if hit_tokens:
            print(f"        hit tokens: {hit_tokens}")
        if b.runs:
            run_summary = " | ".join(
                f"{r.text[:30]!r}" for r in b.runs[:4]
            )
            print(f"        runs ({len(b.runs)}): {run_summary}")
        print()
    if label_hits == 0:
        print("  (no label-path matches at density >= threshold)")
        print()
    else:
        print(f"  total label-path matches (density): {label_hits}")
        print()

    # --- Table-header regex check (D-069 path) ---
    print("[table-header regex path — joined headers vs definitions_table_header_pattern]")
    table_header_re = (
        re.compile(profile.heading_detection.definitions_table_header_pattern)
        if profile.heading_detection.definitions_table_header_pattern else None
    )
    if table_header_re is None:
        print("  (definitions_table_header_pattern is empty — path disabled)")
        print()
    else:
        any_hit = False
        for b in ir.content_blocks:
            if b.type != BlockType.TABLE or not b.headers:
                continue
            joined = " | ".join(h.strip() for h in b.headers)
            if table_header_re.search(joined):
                any_hit = True
                idx = b.position.index
                frac = idx / max(total - 1, 1)
                print(f"  ✓ MATCH  idx={idx}  frac={frac:.2f}")
                print(f"           headers: {joined[:120]}")
        if not any_hit:
            print("  (no joined-header match)")
        print()

    # --- Per-TABLE proposed-score breakdown ---
    # Vocab signal applies to headers + merged-cell text (label surface),
    # NOT body cells — the density rule is about the label, not the
    # table's content rows. The cell-fingerprint signal scans rows
    # separately for col-0 acronym shape + col-1 prose shape.
    print("[per-TABLE proposed-score preview — "
          "position + vocab-density + cell-fingerprint]")
    n_tables = 0
    n_pass = 0
    threshold = 0.55  # mirror RevhistDetection default
    for b in ir.content_blocks:
        if b.type != BlockType.TABLE or not (b.headers or b.merged_cells):
            continue
        n_tables += 1
        idx = b.position.index
        frac = idx / max(total - 1, 1)
        joined_headers = " | ".join(h.strip() for h in (b.headers or []))
        merged_texts = [mc.text for mc in (b.merged_cells or []) if mc.text]
        rows = b.rows or []

        # Position
        pos_score = _glossary_position_score(frac)

        # Vocab density — applied to headers + merged-cell text.
        label_surface = joined_headers + " " + " ".join(merged_texts)
        hits, total_meaningful, density, hit_tokens = _glossary_density(
            label_surface, req_id_re,
        )
        vocab_score = 1.0 if density >= _GLOSSARY_LABEL_MIN_DENSITY else 0.0

        # Cell fingerprint — col 0 acronym-shape, col 1 prose-shape.
        # Each column contributes 0.5 if its shape gate fires for
        # ≥50% of body cells (so 1.0 cap when both fire).
        cell_score = 0.0
        if rows and any(row for row in rows):
            for col_idx, gate, weight in [
                (0, _GLOSSARY_COL0_ACRONYM_RE.match, 0.5),
                (1, lambda v: _looks_like_prose(v), 0.5),
            ]:
                col_vals = [
                    str(row[col_idx]).strip()
                    for row in rows if len(row) > col_idx and row[col_idx]
                ]
                if not col_vals:
                    continue
                match_frac = sum(1 for v in col_vals if gate(v)) / len(col_vals)
                if match_frac >= 0.5:
                    cell_score += weight

        combined = (
            pos_score * 0.20
            + vocab_score * 0.50
            + cell_score * 0.30
        )
        clears = combined >= threshold
        marker = "✓ PASS" if clears else "  ----"
        if clears:
            n_pass += 1
        density_repr = (
            f"{hits}/{total_meaningful}={density:.0%}"
            if total_meaningful else "(empty surface)"
        )
        print(f"{marker}  idx={idx:>4d}  frac={frac:.2f}  "
              f"score={combined:.2f} "
              f"(pos={pos_score:.2f} vocab={vocab_score:.2f} cell={cell_score:.2f})")
        print(f"        headers: {joined_headers[:120] or '(empty)'}")
        if merged_texts:
            print(f"        merged:  {' || '.join(merged_texts)[:120]}")
        print(f"        density: {density_repr}"
              + (f"  hits={hit_tokens}" if hit_tokens else ""))
        for r_idx, row in enumerate(rows[:3]):
            row_repr = " | ".join(
                (str(c).strip() or '')[:40] for c in list(row)[:6]
            )
            print(f"        row[{r_idx}]: {row_repr}")
        print()

    print(
        f"summary: {n_tables} TABLE blocks scanned · "
        f"{n_pass} would clear threshold {threshold:.2f}  "
        f"(preview — GlossaryDetection not yet in profile schema)"
    )
    return 0


# ---------------------------------------------------------------------------
# references subcommand
# ---------------------------------------------------------------------------

def _trunc(s: str, n: int = 100) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def cmd_references(args: argparse.Namespace) -> int:
    """Diagnostic preview for the four reference-detection paths:

      1. Reference-list / bibliography section extraction
         (profile.reference_list_section_pattern + entry_pattern → tree.reference_list_map)
      2. Intra-doc requirement_id citations
         (profile.requirement_id.pattern → tree.requirements[*].cross_references.internal)
      3. Cross-doc requirement_id citations
         (same regex → cross_references.external_plans)
      4. Standards spec citations
         (hardcoded 3GPP TS / Release regexes → cross_references.standards)

    Also surfaces compiled-but-unused profile fields
    (cross_reference_patterns.standards_citations + internal_section_refs)
    so the user can see what they'd catch if wired into the parser.

    Runs the full parser to get accurate evidence — same code path the
    production pipeline takes.
    """
    from core.src.parser.structural_parser import GenericStructuralParser

    env_dir: Path = args.env_dir
    profile, profile_path = _load_profile(env_dir)
    ir = _load_ir(env_dir, args.doc)
    parser = GenericStructuralParser(profile)

    crp = profile.cross_reference_patterns
    total = len(ir.content_blocks)

    print(f"# parse_debug references — doc={args.doc!r}")
    print(f"profile: {profile_path}")
    print(f"  reference_list_section_pattern = "
          f"{profile.reference_list_section_pattern!r}")
    print(f"  reference_list_entry_pattern   = "
          f"{profile.reference_list_entry_pattern!r}")
    print(f"  requirement_id.pattern         = "
          f"{profile.requirement_id.pattern!r}")
    print(f"  requirement_id.components      = "
          f"separator={profile.requirement_id.components.get('separator','_')!r} "
          f"plan_id_position={profile.requirement_id.components.get('plan_id_position')}")
    print(f"  cross_reference_patterns:")
    print(f"    standards_citations ({len(crp.standards_citations)}): "
          f"{crp.standards_citations!r}")
    print(f"    internal_section_refs       = {crp.internal_section_refs!r}")
    print(f"    requirement_id_refs         = {crp.requirement_id_refs!r}")
    print()
    print(f"total content blocks: {total}")
    print()

    # Run the full parser. Cheap relative to the embedding stage and
    # matches what the production pipeline sees.
    tree = parser.parse(ir)

    # ------------------------------------------------------------------
    # [1] Reference list / bibliography section
    # ------------------------------------------------------------------
    print("[1. Reference list / bibliography section]")
    if tree.reference_list_section_number:
        print(f"  ✓ MATCH  section={tree.reference_list_section_number!r}  "
              f"entries={len(tree.reference_list_map)}")
        if tree.reference_list_map:
            # Sample up to 20 entries (sorted by entry number).
            sample = sorted(tree.reference_list_map.items())[:20]
            for num, entry in sample:
                spec = entry.get("spec", "")
                title = entry.get("title", "")
                print(f"    [{num}] spec={spec!r}"
                      + (f"  title={_trunc(title, 80)!r}" if title else ""))
            if len(tree.reference_list_map) > 20:
                print(f"    … {len(tree.reference_list_map) - 20} more entries")
        else:
            print("    (section matched but reference_list_entry_pattern produced "
                  "no entries — check the entry regex against the section body)")
    elif parser._reference_list_section_re is None:
        print("  (disabled — reference_list_section_pattern is empty)")
    else:
        print(f"  miss   no section matched "
              f"{profile.reference_list_section_pattern!r}")
        # List the first few section titles to help diagnose pattern misses.
        sample_titles = [
            f"{s.section_number} {s.title}".strip()
            for s in (tree.requirements or [])[:10]
            if s.title
        ]
        if sample_titles:
            print(f"    sample section titles in tree:")
            for t in sample_titles:
                print(f"      - {_trunc(t, 100)}")
    print()

    # ------------------------------------------------------------------
    # [2] Intra-doc requirement_id refs
    # [3] Cross-doc requirement_id refs
    # ------------------------------------------------------------------
    # Aggregate per-section CrossReferences into corpus-wide tallies.
    from collections import Counter, defaultdict

    intra_total = 0
    cross_plan_counts: Counter = Counter()         # plan_name -> count of mentioning sections
    standards_keys: Counter = Counter()            # (spec, section, release) -> count
    per_section_intra: list[tuple[str, str, int]] = []  # (section_number, title, intra_count)

    for r in tree.requirements:
        xr = r.cross_references
        intra_total += len(xr.internal)
        if xr.internal:
            per_section_intra.append((
                r.section_number or "—",
                _trunc(r.title or "", 60),
                len(xr.internal),
            ))
        for plan in xr.external_plans:
            cross_plan_counts[plan] += 1
        for s in xr.standards:
            standards_keys[(s.spec, s.section or "", s.release or "")] += 1

    print("[2. Intra-doc (same-plan) requirement_id citations]")
    distinct_intra = set()
    for r in tree.requirements:
        for rid in r.cross_references.internal:
            distinct_intra.add(rid)
    print(f"  intra_doc refs (cumulative): {intra_total}")
    print(f"  distinct intra req_ids cited: {len(distinct_intra)}")
    if per_section_intra:
        # Top 10 sections by intra-doc citation count.
        top = sorted(per_section_intra, key=lambda x: -x[2])[:10]
        print(f"  top sections by intra-doc citation count:")
        for sec_num, title, n in top:
            print(f"    §{sec_num:<10}  intra={n:>3}  title={title!r}")
    else:
        print(f"  (no intra-doc citations found — check requirement_id.pattern "
              f"against the section body text)")
    print()

    print("[3. Cross-doc (external-plan) requirement_id citations]")
    if cross_plan_counts:
        print(f"  distinct external plans cited: {len(cross_plan_counts)}")
        for plan, n in cross_plan_counts.most_common(20):
            print(f"    plan={plan!r:<30}  cited in {n} section(s)")
    else:
        print(f"  (no external-plan citations — either single-plan corpus, "
              f"or req_id.components.plan_id_position is misconfigured)")
    print()

    # ------------------------------------------------------------------
    # [4] Standards spec citations
    # ------------------------------------------------------------------
    print("[4. Standards spec citations]")
    print(f"  hardcoded regexes used by the parser today:")
    print(f"    3GPP TS detail:    {parser._std_detail_re.pattern!r}")
    # Hoisted: a backslash inside an f-string expression needs Python 3.12.
    spec_only_re = r"3GPP\s+TS\s+(\d[\d.]*\d)"
    print(f"    3GPP TS spec-only: {spec_only_re!r}  (inline)")
    print(f"    Release:           {parser._std_release_re.pattern!r}")
    print()
    if standards_keys:
        print(f"  distinct standards detected: {len(standards_keys)}")
        for (spec, section, release), n in standards_keys.most_common(30):
            sect = f"§{section}" if section else "(no section)"
            rel = f"  {release}" if release else ""
            print(f"    {spec:<18}  {sect:<15}{rel:<14}  refs={n}")
        # Releases summary
        if tree.referenced_standards_releases:
            print(f"  referenced_standards_releases: "
                  f"{dict(tree.referenced_standards_releases)}")
    else:
        print(f"  (no standards citations detected — corpus may not cite 3GPP "
              f"specs, or the hardcoded '3GPP TS ...' regex misses the citation "
              f"style this corpus uses)")
    print()

    # ------------------------------------------------------------------
    # [5] Compiled-but-unused profile fields
    #
    # The parser's __init__ compiles `crp.standards_citations` (into
    # _std_res) and `crp.internal_section_refs` (into _section_ref_re),
    # but `_extract_cross_refs` only uses the hardcoded 3GPP regexes and
    # the req_id regex. These two profile-driven patterns currently
    # aren't consulted. Surface their hit counts so the user can see
    # what they'd catch if wired in.
    # ------------------------------------------------------------------
    print("[5. Profile-listed-but-UNUSED cross-reference patterns]")
    print(f"  (compiled at parser.__init__ but `_extract_cross_refs` doesn't "
          f"call them — wiring them is a separate task)")
    print()

    # Aggregate section text for hit-counting. Only scan sections that
    # actually have body text (skip the empty container sections).
    all_text = "\n\n".join(
        r.text for r in tree.requirements if r.text
    )
    text_len = len(all_text)
    print(f"  scanned text: {text_len:,} chars across {sum(1 for r in tree.requirements if r.text)} "
          f"non-empty sections")
    print()

    # 5a. standards_citations — profile-configurable spec citation regexes.
    print(f"  5a. cross_reference_patterns.standards_citations "
          f"({len(parser._std_res)} pattern(s)):")
    if not parser._std_res:
        print(f"      (empty — no profile-driven spec patterns configured)")
    else:
        for i, rx in enumerate(parser._std_res):
            matches = rx.findall(all_text)
            n = len(matches)
            sample = matches[0] if matches else None
            sample_str = (
                f"  sample={_trunc(str(sample), 60)!r}"
                if sample is not None else ""
            )
            print(f"    [{i}] {rx.pattern!r}  matches={n}{sample_str}")
    print()

    # 5b. internal_section_refs — profile-configurable intra-doc section refs.
    print(f"  5b. cross_reference_patterns.internal_section_refs:")
    if parser._section_ref_re is None:
        print(f"      (empty — no profile-driven section-ref pattern configured)")
    else:
        matches = parser._section_ref_re.findall(all_text)
        n = len(matches)
        print(f"      pattern={parser._section_ref_re.pattern!r}  matches={n}")
        if matches:
            uniq = list(dict.fromkeys(
                m if isinstance(m, str) else " | ".join(str(x) for x in m)
                for m in matches
            ))[:10]
            print(f"      sample (up to 10 distinct):")
            for m in uniq:
                print(f"        - {_trunc(str(m), 100)}")
    print()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(
        f"summary: ref_list={len(tree.reference_list_map)} entries · "
        f"intra_doc={intra_total} refs ({len(distinct_intra)} distinct) · "
        f"cross_plan={sum(cross_plan_counts.values())} refs "
        f"({len(cross_plan_counts)} distinct plans) · "
        f"standards={sum(standards_keys.values())} refs "
        f"({len(standards_keys)} distinct)"
    )
    return 0


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        prog="parse_debug",
        description="Diagnostic CLI for parser-side detection paths.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    rh = sub.add_parser(
        "revhist",
        help="Show per-table RevhistDetection signal breakdown for one doc.",
    )
    rh.add_argument("--env-dir", required=True, type=Path)
    rh.add_argument(
        "--doc", required=True,
        help="Doc id (the stem of <env_dir>/out/extract/<doc>_ir.json)",
    )

    gl = sub.add_parser(
        "glossary",
        help="Show glossary detection diagnostics (label / table-header / "
             "preview scoring) for one doc. Preview uses proposed defaults "
             "since GlossaryDetection isn't in the profile schema yet.",
    )
    gl.add_argument("--env-dir", required=True, type=Path)
    gl.add_argument(
        "--doc", required=True,
        help="Doc id (the stem of <env_dir>/out/extract/<doc>_ir.json)",
    )

    rf = sub.add_parser(
        "references",
        help="Show reference / citation detection diagnostics: bibliography "
             "section + entries; intra-doc, cross-doc, and standards "
             "citation tallies; plus hit counts for profile-listed-but-"
             "currently-unused cross-reference patterns.",
    )
    rf.add_argument("--env-dir", required=True, type=Path)
    rf.add_argument(
        "--doc", required=True,
        help="Doc id (the stem of <env_dir>/out/extract/<doc>_ir.json)",
    )

    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        if args.cmd == "revhist":
            rc = cmd_revhist(args)
        elif args.cmd == "glossary":
            rc = cmd_glossary(args)
        elif args.cmd == "references":
            rc = cmd_references(args)
        else:
            p.error(f"unknown subcommand: {args.cmd}")
            rc = 2
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        rc = 2

    sys.exit(rc)


if __name__ == "__main__":
    main()
