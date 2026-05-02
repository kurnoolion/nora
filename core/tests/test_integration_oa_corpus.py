"""Integration test: extract → profile → parse against the 5 Verizon OA PDFs.

Pins the parser/profiler contract by asserting known-good req_id → section
mappings hand-verified against the source documents. Caught the entire
class of bugs landed during 2026-05-01 corpus-correctness work
(numbering pattern, TOC threshold, req_id lateral cascade, deferred
table extraction, struck-id leakage). Acts as a regression net — any
future change that perturbs these assignments is surfaced immediately.

Skipped when the OA PDFs are not present in the working directory
(matches the existing test_pipeline.py convention).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.src.extraction.registry import extract_document
from core.src.parser.structural_parser import (
    GenericStructuralParser,
    RequirementTree,
)
from core.src.profiler.profiler import DocumentProfiler

PDF_DIR = Path(".")
PDF_NAMES = [
    "LTEAT.pdf",
    "LTEB13NAC.pdf",
    "LTEDATARETRY.pdf",
    "LTEOTADM.pdf",
    "LTESMS.pdf",
]
PDF_PATHS = [PDF_DIR / name for name in PDF_NAMES]

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in PDF_PATHS),
    reason="OA PDF test data not available",
)


# ---------------------------------------------------------------------------
# Fixtures — extract + profile + parse run ONCE per test session (module
# scope), then every test reads from the cached trees dict.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def oa_corpus():
    """Extract all 5 OA PDFs, profile from them, parse each, return a
    dict {plan_id_or_stem: RequirementTree}."""
    irs = [
        extract_document(p, mno="VZW", release="Feb2026", doc_type="requirement")
        for p in PDF_PATHS
    ]
    profile = DocumentProfiler().create_profile(irs, profile_name="VZW_OA_integration")
    parser = GenericStructuralParser(profile)
    trees: dict[str, RequirementTree] = {}
    for path, ir in zip(PDF_PATHS, irs):
        tree = parser.parse(ir)
        trees[path.stem] = tree
    return trees


@pytest.fixture(scope="module")
def all_reqs_by_id(oa_corpus):
    """Index every Requirement across all parsed trees by req_id. A
    req_id may appear in multiple trees (rare) — the value is a list."""
    by_id: dict[str, list] = {}
    for tree in oa_corpus.values():
        for r in tree.requirements:
            if r.req_id:
                by_id.setdefault(r.req_id, []).append(r)
    return by_id


# ---------------------------------------------------------------------------
# Ground-truth req_id → section_number pairs
# Hand-verified against the source PDFs by the project lead.
# ---------------------------------------------------------------------------

GROUND_TRUTH_PAIRS = [
    # (req_id, expected_section_number)
    # — top-of-doc and depth-2 sections across all 5 docs —
    ("VZ_REQ_LTEAT_45", "1"),
    ("VZ_REQ_LTEAT_33075", "1.1"),
    ("VZ_REQ_LTEB13NAC_1869", "1.1"),
    ("VZ_REQ_LTEDATARETRY_68", "1"),
    ("VZ_REQ_LTEDATARETRY_2365", "1.1"),
    ("VZ_REQ_LTEOTADM_2395", "1.2"),
    ("VZ_REQ_LTESMS_30206", "1.1"),
    # — exercise the no-space-before-title fix at depth 6 (1.4.3.1.1.*) —
    ("VZ_REQ_LTEDATARETRY_23804", "1.4.3.1.1"),
    ("VZ_REQ_LTEDATARETRY_7747", "1.4.3.1.1.1"),
    ("VZ_REQ_LTEDATARETRY_7754", "1.4.3.1.1.9"),
    # — exercise the no-lateral-on-extra-req-id fix —
    ("VZ_REQ_LTEDATARETRY_2377", "1.3.4"),
    ("VZ_REQ_LTEDATARETRY_7742", "1.3.3.4.1.6"),
    # — deep nesting, varied docs —
    ("VZ_REQ_LTEB13NAC_6443", "1.3.2.10.5.24"),
    ("VZ_REQ_LTEOTADM_31777", "1.5.1.4.3.2"),
    # — exercise the deferred-table-extraction fix (req in cross-ref table
    #   on page 3 + paragraph anchor on page 34; paragraph wins) —
    ("VZ_REQ_LTEOTADM_7672", "1.5.1.3.8"),
]

# req_ids that the source has marked struck-through; they must NOT
# appear in the parsed tree. Paragraph small-font block IS struck,
# tracked into struck_req_ids, then table-anchored extraction skips them.
STRUCK_REQ_IDS = [
    "VZ_REQ_LTEB13NAC_1871",
    "VZ_REQ_LTEDATARETRY_2366",
]


# ---------------------------------------------------------------------------
# Ground-truth assertions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("req_id, expected_section", GROUND_TRUTH_PAIRS)
def test_req_id_at_expected_section(all_reqs_by_id, req_id, expected_section):
    """Every ground-truth req_id must resolve to exactly one Requirement
    at exactly the expected section_number."""
    nodes = all_reqs_by_id.get(req_id, [])
    assert nodes, f"{req_id} missing from parsed tree"
    assert len(nodes) == 1, (
        f"{req_id} appeared {len(nodes)}x in tree (expected 1) — duplicate "
        f"anchoring? sections={[n.section_number for n in nodes]}"
    )
    actual = nodes[0].section_number
    assert actual == expected_section, (
        f"{req_id} at section {actual!r}, expected {expected_section!r}"
    )


@pytest.mark.parametrize("req_id", STRUCK_REQ_IDS)
def test_struck_req_id_absent(all_reqs_by_id, req_id):
    """Struck-through req_ids must not appear in the parsed tree at all
    (neither paragraph- nor table-anchored)."""
    nodes = all_reqs_by_id.get(req_id, [])
    assert not nodes, (
        f"struck req_id {req_id} leaked into tree: "
        f"{[(n.section_number, n.parent_section) for n in nodes]}"
    )


# ---------------------------------------------------------------------------
# Macro-stat assertions — guard against catastrophic regressions even
# when individual req_ids drift (e.g. a future MNO release). Bounded
# ranges chosen 10-20% above/below the empirical post-fix values to
# allow normal corpus evolution without test churn.
# ---------------------------------------------------------------------------


def test_total_requirements_in_expected_range(oa_corpus):
    total = sum(len(t.requirements) for t in oa_corpus.values())
    # Empirical post-fix: 1051 across all 5 docs (2026-05-01).
    # Tight floor — any drop below ~900 is a regression worth investigating.
    # Loose ceiling — extraction recovering more sub-sections is fine.
    assert 900 <= total <= 1300, (
        f"total req count {total} outside expected range [900, 1300] — "
        f"a parser change may have lost or duplicated reqs"
    )


def test_per_doc_min_requirements(oa_corpus):
    """Each doc must produce at least a baseline number of reqs.
    Catches a regression where one doc parses to near-zero (extractor
    failure, profile misclassification, etc.)."""
    minimums = {
        "LTEAT": 10,
        "LTEB13NAC": 400,
        "LTEDATARETRY": 100,
        "LTEOTADM": 60,
        "LTESMS": 100,
    }
    for stem, mn in minimums.items():
        tree = oa_corpus[stem]
        assert len(tree.requirements) >= mn, (
            f"{stem}: {len(tree.requirements)} reqs < min {mn}"
        )


def test_depth_distribution_reasonable(oa_corpus):
    """Most reqs should be at depths 2–6. Excessive depth (>=10) suggests
    runaway numbering (false positives matching a long dotted run)."""
    runaway_threshold = 10  # depth >= 10 is suspicious for OA corpus
    runaway_count = 0
    total = 0
    for tree in oa_corpus.values():
        for r in tree.requirements:
            sn = r.section_number
            if not sn:
                continue
            total += 1
            depth = sn.count(".") + 1
            if depth >= runaway_threshold:
                runaway_count += 1
    # Allow a tiny number of legitimate deep paths but not >2%.
    if total:
        assert runaway_count / total < 0.02, (
            f"{runaway_count}/{total} reqs at depth >= {runaway_threshold} "
            f"({runaway_count/total:.1%}) — runaway numbering suspected"
        )


def test_strikeout_drop_active(oa_corpus):
    """All five docs go through strikeout drop; cumulative counter must
    be non-zero — the OA corpus is known to contain struck content."""
    total_struck = sum(t.parse_stats.struck_blocks_dropped for t in oa_corpus.values())
    assert total_struck > 0, (
        "no struck blocks dropped across the corpus — strikeout detection "
        "may be silently inactive"
    )


def test_toc_drop_active(oa_corpus):
    """OA PDFs all start with a TOC; cumulative TOC drop must be
    substantial (not just edge-case block-level matches)."""
    total_toc = sum(t.parse_stats.toc_blocks_dropped for t in oa_corpus.values())
    assert total_toc >= 200, (
        f"only {total_toc} TOC blocks dropped — page-level TOC detection "
        f"may be misconfigured"
    )


def test_section_numbers_unique_per_tree(oa_corpus):
    """Within a single document, section_numbers must be unique. Phantom
    duplicates (TOC residuals, etc.) are how off-by-one cascades start."""
    for stem, tree in oa_corpus.items():
        seen: set[str] = set()
        dups: list[str] = []
        for r in tree.requirements:
            if not r.section_number:
                continue
            if r.section_number in seen:
                dups.append(r.section_number)
            seen.add(r.section_number)
        assert not dups, f"{stem}: duplicate section_numbers {dups[:5]}"


def test_no_orphan_paragraph_reqs_have_long_titles(oa_corpus):
    """Sanity check: a paragraph-anchored req with a >300-char title
    indicates the heading classifier is picking up body text. Empirical
    OA titles top out around 200 chars."""
    cap = 300
    offenders: list[tuple[str, int]] = []
    for tree in oa_corpus.values():
        for r in tree.requirements:
            if r.section_number and len(r.title) > cap:
                offenders.append((r.req_id, len(r.title)))
    assert not offenders, (
        f"requirements with titles > {cap} chars: {offenders[:5]}"
    )
