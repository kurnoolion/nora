"""Generic, profile-driven structural parser (TDD 5.3).

Parses normalized IR + document profile into a structured requirement
tree. No per-MNO code — behavior driven entirely by the profile JSON.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from core.src.models.document import BlockType, ContentBlock, DocumentIR
from core.src.profiler.profile_schema import DocumentProfile, HeadingLevel

logger = logging.getLogger(__name__)


# Maximum heading text length. Headings in technical specs are usually short;
# numbered body sentences ("1. The system shall ...") run longer. 200 chars
# accommodates verbose spec titles like
# "1.2.3.4 LTE Idle Mode Procedures and Behavior Under Various Conditions"
# while still rejecting body-paragraph numbered list items.
_HEADING_MAX_LEN = 200


# Section number extractor — independent of whatever capture-group shape the
# profile's `numbering_pattern` happens to use. We use the profile pattern as
# a gate ("is this a heading?") and this regex to pull out the canonical
# `<digits>(\.<digits>)*` portion.
_SECTION_NUM_RE = re.compile(r"^\d+(?:\.\d+)*")


# Req-ID canonicalization. PDF text extraction occasionally drops the
# underscore between a req_id's plan-prefix and trailing digit (e.g.
# `"VZ_REQ_LTEOTADM_65"` → extracted as `"VZ_REQ_LTEOTADM 65"` because
# bold-formatting fused two text runs). Profile patterns now accept
# either separator; this helper normalizes a matched id to the
# canonical underscore form for storage and comparison so the same
# requirement isn't tracked under two different identifiers.
_REQ_ID_WHITESPACE_RE = re.compile(r"\s+")


# DOCX heading-style detector (case-insensitive). Drives the
# ``method="docx_styles"`` classification path: paragraphs whose
# ``style`` matches gain heading status with depth = group(1). Used in
# place of ``numbering_pattern`` for corpora whose Word-styled headings
# carry no inline section number.
_DOCX_HEADING_STYLE_RE = re.compile(r"(?i)^Heading\s+(\d+)$")

# Word's canonical TOC paragraph styles — ``TOC 1``, ``TOC 2``, … — are
# universally TOC entries. Recognised independent of profile config so
# downstream label matchers (revhist label, glossary section title,
# reference list) never accidentally see a TOC entry as a real
# section heading. Compared with ``block.style`` via match().
_WORD_TOC_STYLE_RE = re.compile(r"(?i)^toc\s+\d+$")


# Title-normalization for TOC ↔ body heading pairing. Collapses runs of
# whitespace + lowercases so cosmetic differences ("LTE/IMS", "lte / ims")
# don't break the pair-up.
_TITLE_NORM_RE = re.compile(r"\s+")


def _normalize_title(s: str) -> str:
    return _TITLE_NORM_RE.sub(" ", s.strip()).lower()


def _canonicalize_req_id(rid: str) -> str:
    """Normalize whitespace in a matched req_id to underscores."""
    return _REQ_ID_WHITESPACE_RE.sub("_", rid).strip("_")


def build_context_string(
    parent_section: str,
    section_index: dict[str, tuple[str, str]],
    mode: str,
    max_chars: int = 0,
) -> str:
    """Assemble a requirement's enclosing-section context (D-DRAFT-5).

    Generic for all corpora — consumers (the SIRA adapter, the NORA chunk
    builder) call this at emit time from the section nodes already in the tree,
    so the context is **not** duplicated per requirement in the parsed tree.

    Anchors on ``parent_section`` (the deepest enclosing section), which works
    in both detection models: leading-id requirements' ``parent_section`` is the
    enclosing section; heading-anchored requirements' is the parent section
    (their own section is excluded — it is the requirement, not context). Walks
    that number's prefixes (``5 → 5.1 → 5.1.2 → 5.1.2.3``), looking each up in
    ``section_index`` (``{section_number: (title, body)}``).

    ``mode`` (formats settled in strand multi-mno-nora, D-DRAFT-5):
      - ``"none"`` → ``""``.
      - ``"path"`` → a single-line breadcrumb wrapped in a ``[Context: …]``
        label: ``"[Context: 5 Bands > 5.1 Frequency > 5.1.2 LTE]"`` (number +
        title per hop, no section bodies).
      - ``"path_and_content"`` → one bracketed header per ancestor followed by
        that section's body, top-down::

            [5 Bands]
            <5 body>
            [5.1 Frequency]
            <5.1 body>
            [5.1.2 LTE]
            <5.1.2 body>

    ``max_chars`` (profile ``build_context_max_chars``, 0 = uncapped) bounds
    **each ancestor's body** in ``path_and_content`` — a single huge ancestor
    section would otherwise dominate the chunk and blow past the embedder's
    input limit (truncating its embedding). Headers/titles are never truncated;
    ``path`` mode (no bodies) is unaffected.
    """
    if mode == "none" or not parent_section:
        return ""
    parts = parent_section.split(".")
    entries: list[tuple[str, str, str]] = []
    for i in range(1, len(parts) + 1):
        num = ".".join(parts[:i])
        node = section_index.get(num)
        if node is None:
            continue
        title, text = node
        entries.append((num, title, text))
    if not entries:
        return ""
    if mode == "path":
        crumbs = " > ".join(f"{num} {title}".strip() for num, title, _ in entries)
        return f"[Context: {crumbs}]"
    # path_and_content
    blocks: list[str] = []
    for num, title, text in entries:
        head = "[" + f"{num} {title}".strip() + "]"
        if text and max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        blocks.append(f"{head}\n{text}".rstrip() if text else head)
    return "\n".join(blocks)


# Glossary-label density gate. The legacy `definitions_section_pattern`
# (default `(?i)acronym|definition|glossary`) is a substring match — it
# fires on any title that mentions one of those words ("Section 2.3
# Acronyms list and notes" → false positive). To suppress these, we
# require that ≥75% of a candidate title's *meaningful* tokens belong
# to a narrow keyword set. Stopwords ("and", "the", etc.) and trailing
# req_ids are stripped before counting so connectives don't dilute the
# signal. Used by ``StructuralParser._extract_definitions`` as a gate
# on regex matches; also imported by ``parse_debug`` for diagnostics.
_GLOSSARY_LABEL_KEYWORDS = frozenset({
    "glossary",
    "definition", "definitions",
    "acronym", "acronyms",
    "abbreviation", "abbreviations",
    "term", "terms",
})
_GLOSSARY_LABEL_STOPWORDS = frozenset({
    "and", "or", "the", "with", "for", "of", "a", "an",
})
_GLOSSARY_LABEL_TOKEN_SPLIT_RE = re.compile(r"[\s/,&|;()]+")
_GLOSSARY_LABEL_MIN_DENSITY = 0.75


def render_table_markdown(headers: list[str], rows: list[list[str]]) -> str:
    """Render a parsed table as GitHub-flavored markdown.

    The canonical table renderer: the parser inlines this into ``Requirement.text``
    at the table's document position (so text is faithful and self-contained), and
    ``ChunkBuilder._table_to_markdown`` delegates here so NORA chunks and the SIRA
    corpus render tables identically. Empty-header artifact tables collapse to a
    compact ``[Table: …]`` line; headerless tables render rows only.
    """
    if not rows:
        return ""
    if headers and all((h or "") == "" for h in headers):
        cells = [c for row in rows for c in row if (c or "").strip()]
        return "[Table: " + " | ".join(cells) + "]" if cells else ""
    if not headers:
        return "\n".join(
            "| " + " | ".join(str(c) for c in row) + " |" for row in rows
        )
    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        padded = list(row) + [""] * (len(headers) - len(row))
        lines.append("| " + " | ".join(str(c) for c in padded[: len(headers)]) + " |")
    return "\n".join(lines)


def _glossary_label_density(
    text: str, req_id_re: re.Pattern | None,
) -> tuple[int, int, float, list[str]]:
    """Return ``(keyword_hits, meaningful_token_count, density, hit_tokens)``
    for *text*.

    Strips any req_id-shaped substring before tokenizing (real corpus
    titles often carry a trailing ``VZ_REQ_..._\\d+`` that would inflate
    the denominator). Splits on whitespace + common separators
    (``/``, ``,``, ``&``, ``|``, ``;``, ``()``). Drops empty tokens and
    stopwords. Density is 0.0 when no meaningful tokens remain.
    """
    if req_id_re is not None:
        text = req_id_re.sub(" ", text or "")
    tokens = _GLOSSARY_LABEL_TOKEN_SPLIT_RE.split(text or "")
    meaningful = [
        t for t in tokens
        if t and t.lower() not in _GLOSSARY_LABEL_STOPWORDS
    ]
    if not meaningful:
        return 0, 0, 0.0, []
    hit_tokens = [t for t in meaningful if t.lower() in _GLOSSARY_LABEL_KEYWORDS]
    return (
        len(hit_tokens), len(meaningful),
        len(hit_tokens) / len(meaningful), hit_tokens,
    )


# ── Output data structures ──────────────────────────────────────────


@dataclass
class TableData:
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    source: str = "inline"  # "inline" or "embedded_xlsx"


@dataclass
class ImageRef:
    path: str = ""
    surrounding_text: str = ""


@dataclass
class StandardsRef:
    spec: str = ""
    section: str = ""
    release: str = ""


@dataclass
class CrossReferences:
    internal: list[str] = field(default_factory=list)  # req IDs within same plan
    external_plans: list[str] = field(default_factory=list)  # other plan names
    standards: list[StandardsRef] = field(default_factory=list)


@dataclass
class Requirement:
    req_id: str = ""
    plan_id: str = ""
    """D-DRAFT-1: the plan this requirement belongs to, extracted per-req from
    ``req_id`` via the profile's ``requirement_id.components`` config. A single
    source document may carry **multiple** plans (e.g. one PDF whose sections
    each correspond to a plan), so plan is a *within-tree* dimension, not just
    the tree-level scalar. Falls back to the tree's ``plan_id`` when the req has
    no extractable plan (e.g. a structural heading node with no ``req_id``, or a
    profile with no plan component configured). For single-plan documents every
    requirement's ``plan_id`` equals ``tree.plan_id`` — unchanged behavior."""
    section_number: str = ""
    title: str = ""
    parent_req_id: str = ""
    parent_section: str = ""
    hierarchy_path: list[str] = field(default_factory=list)
    zone_type: str = ""
    priority: str = ""  # FR-31: extracted via profile.heading_detection.priority_marker_pattern
    applicability: list[str] = field(default_factory=list)  # FR-32 [D-030]: form-factor labels
    text: str = ""
    context: str = ""
    """D-DRAFT-5: ancestor-section context prepended for synthesis — the
    headings + body text of the enclosing section/subsection chain, so a
    requirement carries its structural context explicitly (the synthesizer sees
    ``5 → 5.1 → 5.1.2`` rather than an unlabeled blob). Populated for
    ``leading_id_body`` corpora, where sections are non-requirement context;
    empty otherwise. A general field — other detection models can populate it
    later. Kept separate from ``text`` so downstream (chunk builder, graph) can
    label requirement body vs. inherited context."""
    tables: list[TableData] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    children: list[str] = field(default_factory=list)  # child req_ids
    cross_references: CrossReferences = field(default_factory=CrossReferences)
    # ContentBlock.position.index of the heading block that opened this
    # Requirement. ``None`` for table-anchored Requirements (no heading
    # block) and for legacy tree.json that pre-dates this field. Web's
    # parse_review route uses it for O(1) reverse-mapping from a
    # Requirement to its source block, avoiding the brittle title-text
    # fuzzy-match path that broke when the parser stripped the title
    # differently than the IR block text carried.
    source_block_idx: int | None = None


@dataclass
class ParseStats:
    """Per-document parser diagnostics. Surfaced in compact RPT.

    Counters are zero by default; populated by the parser passes that
    drop or extract content. Consumers should not rely on field presence
    on serialized older trees — `RequirementTree._from_dict` defaults
    missing values to zero.
    """
    struck_blocks_dropped: int = 0  # FR-33 [D-031]
    cascade_blocks_dropped: int = 0 # FR-33 (struck-heading section cascade)
    toc_blocks_dropped: int = 0     # FR-34
    revhist_blocks_dropped: int = 0 # FR-34 (revision-history table omission)
    defs_extracted: int = 0         # FR-35 [D-032]
    refs_extracted: int = 0         # D-059, D-061 (reference_list_map entries)
    toc_pair_misses: int = 0        # generic-rules pivot — body heading with no TOC entry
    frontmatter_blocks_dropped: int = 0
    """Generic-rules pivot — blocks ≤ ``max(toc_end, revhist_end)`` that are
    NOT themselves TOC or revhist (typically: doc title heading, preface
    paragraphs, page-1 metadata blocks). TOC and revhist drops are still
    counted on their own counters."""


@dataclass
class TocEntry:
    """A single TOC line, parsed from a ``toc N``-styled paragraph.

    Produced by ``_extract_toc_index`` during the pre-pass; consumed by
    ``_classify_heading_docx_styles`` to attach the document's actual
    section number to each body heading.
    """
    depth: int             # 1-based, from the ``toc N`` style suffix
    section_number: str    # e.g. "1.2.3" — the literal in the doc's TOC
    title: str             # body title text (req_id peeled from tail)
    req_id: str            # canonicalized, normalize-applied; "" when absent
    block_index: int       # source block position for diagnostics


@dataclass
class TocPairMiss:
    """A body heading that had no matching TOC entry.

    Surfaced in the parse log so the architect can spot drift between
    TOC and body without scanning the whole tree. Title is kept locally
    only — never pasted into compact reports per the no-proprietary-
    content rule (the *count* is what reaches RPT).
    """
    block_index: int
    page: int
    depth: int
    req_id: str
    title: str


@dataclass
class RequirementTree:
    mno: str = ""
    release: str = ""
    plan_id: str = ""
    plan_name: str = ""
    version: str = ""
    release_date: str = ""
    build_context: str = "none"
    """D-DRAFT-5: mirrors ``profile.build_context`` (`none` | `path` |
    `path_and_content`). Stamped on the tree so consumers (SIRA adapter, NORA
    chunk builder) know how much enclosing-section context to assemble per
    requirement via ``build_context_string`` — the context is built at emit
    time, not stored per-requirement here."""
    build_context_max_chars: int = 0
    """Mirrors ``profile.build_context_max_chars`` (0 = uncapped). Per-ancestor
    body cap for ``path_and_content`` context — bounds chunk growth so one huge
    ancestor section can't blow past the embedder input limit. Passed to
    ``build_context_string`` by consumers."""
    profile_fingerprint: str = ""
    """D-DRAFT-8: hash of the substituted profile this tree was parsed with.
    The pipeline parse stage stamps it so an incremental re-run can skip a
    cell whose tree is up-to-date AND whose profile/mapping is unchanged — a
    changed fingerprint forces a re-parse (mtime alone can't see a mapping
    edit). Empty for trees parsed outside the pipeline."""
    detection_mode: str = "heading"
    """D-DRAFT-1/2: mirrors ``profile.requirement_id.detection_mode``
    (`"heading"` default | `"leading_id_body"`). Stamped on the tree so
    downstream consumers (SIRA adapter, graph builder) can distinguish the
    two document models — a table-anchored requirement (heading model) and a
    leading-id requirement (leading_id_body model) are otherwise
    indistinguishable in the serialized tree (both have an empty
    ``section_number`` and a ``parent_section``). The per-req-plan grouping and
    parent_section-based section derivation apply only in ``"leading_id_body"``;
    ``"heading"`` corpora (e.g. MNO-A) keep their original behavior."""
    referenced_standards_releases: dict[str, str] = field(default_factory=dict)
    requirements: list[Requirement] = field(default_factory=list)
    parse_stats: ParseStats = field(default_factory=ParseStats)
    definitions_map: dict[str, str] = field(default_factory=dict)
    """FR-35 [D-032]: term → expansion pairs extracted from the document's
    definitions / acronyms / glossary section. Per-document scope (not
    aggregated across the corpus) so a term that means different things
    in different MNO documents doesn't collide. Consumed at chunk-build
    time by the vectorstore stage."""
    definitions_section_number: str = ""
    reference_list_map: dict[int, dict[str, Any]] = field(default_factory=dict)
    """D-059, D-061: bibliography entries extracted from the document's
    reference_list section. Map key = entry number (e.g. `5` for `[5]`);
    value = `{"spec": "3GPP TS 24.301", "title"?: "...", "section"?: "..."}`.
    Per-document scope (not aggregated across the corpus). Consumed by the
    resolver when it sees an indirect spec citation (`reference_spec` with
    `style=indirect`) — the bracketed number is looked up in this map to
    get the actual spec name + optional default section."""
    reference_list_section_number: str = ""
    """Section number of the references / bibliography section when one
    was identified (else empty)."""
    embed_glossary: bool = True
    """Mirrors ``profile.embed_glossary`` (default True). When False,
    the parser drops the glossary section + descendants from
    ``requirements``, and the chunk builder skips per-acronym chunks.
    ``definitions_map`` remains populated so acronym-expansion in body
    chunks still applies."""
    """Section number of the definitions / acronyms / glossary section
    when one was identified (else empty). The chunk builder uses it to
    skip inline expansion within the section's own chunks (and its
    descendants), avoiding `ETWS (Earthquake...) — Earthquake...`-style
    double-anchoring."""
    parse_log: Any = field(default=None)
    """ParseLog built during parsing; not serialized to the tree JSON.
    Consumed by the pipeline parse stage to write the separate
    parse_log/<doc_id>_parse_log.json audit file."""

    parse_summary: Any = field(default=None)
    """Per-doc ``DocSummary`` (parse_summary.py) — captures the
    *evidence* the parser found for each profile-driven detection
    (which pattern fired, what text matched, what table headers
    accompanied it). Pipeline parse stage aggregates these into
    ``<env_dir>/reports/parse_summary.json``. Not serialized to the
    tree JSON — debugging side-channel only."""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("parse_log", None)  # not embedded in tree JSON — written separately
        d.pop("parse_summary", None)  # debugging side-channel only
        return d

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> RequirementTree:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        reqs = []
        for r in data.get("requirements", []):
            xr = r.get("cross_references", {})
            standards = [StandardsRef(**s) for s in xr.get("standards", [])]
            reqs.append(Requirement(
                req_id=r.get("req_id", ""),
                plan_id=r.get("plan_id", ""),
                section_number=r.get("section_number", ""),
                title=r.get("title", ""),
                parent_req_id=r.get("parent_req_id", ""),
                parent_section=r.get("parent_section", ""),
                hierarchy_path=r.get("hierarchy_path", []),
                zone_type=r.get("zone_type", ""),
                priority=r.get("priority", ""),
                applicability=r.get("applicability", []),
                text=r.get("text", ""),
                context=r.get("context", ""),
                tables=[TableData(**t) for t in r.get("tables", [])],
                images=[ImageRef(**i) for i in r.get("images", [])],
                children=r.get("children", []),
                cross_references=CrossReferences(
                    internal=xr.get("internal", []),
                    external_plans=xr.get("external_plans", []),
                    standards=standards,
                ),
                source_block_idx=r.get("source_block_idx"),
            ))
        ps = data.get("parse_stats", {}) or {}
        return cls(
            mno=data.get("mno", ""),
            release=data.get("release", ""),
            plan_id=data.get("plan_id", ""),
            plan_name=data.get("plan_name", ""),
            version=data.get("version", ""),
            release_date=data.get("release_date", ""),
            detection_mode=data.get("detection_mode", "heading"),
            build_context=data.get("build_context", "none"),
            build_context_max_chars=int(data.get("build_context_max_chars", 0) or 0),
            profile_fingerprint=data.get("profile_fingerprint", ""),
            referenced_standards_releases=data.get("referenced_standards_releases", {}),
            requirements=reqs,
            parse_stats=ParseStats(
                struck_blocks_dropped=ps.get("struck_blocks_dropped", 0),
                cascade_blocks_dropped=ps.get("cascade_blocks_dropped", 0),
                toc_blocks_dropped=ps.get("toc_blocks_dropped", 0),
                revhist_blocks_dropped=ps.get("revhist_blocks_dropped", 0),
                defs_extracted=ps.get("defs_extracted", 0),
                refs_extracted=ps.get("refs_extracted", 0),
                toc_pair_misses=ps.get("toc_pair_misses", 0),
                frontmatter_blocks_dropped=ps.get("frontmatter_blocks_dropped", 0),
            ),
            definitions_map=dict(data.get("definitions_map", {})),
            definitions_section_number=data.get("definitions_section_number", ""),
            reference_list_map={
                int(k): dict(v) for k, v in (data.get("reference_list_map") or {}).items()
            },
            reference_list_section_number=data.get("reference_list_section_number", ""),
            embed_glossary=data.get("embed_glossary", True),
        )


# ── Parser ──────────────────────────────────────────────────────────


class GenericStructuralParser:
    """Profile-driven structural parser for requirement documents."""

    def __init__(self, profile: DocumentProfile):
        self.profile = profile
        # Pre-compile regexes from profile
        self._num_re = (
            re.compile(profile.heading_detection.numbering_pattern)
            if profile.heading_detection.numbering_pattern
            else None
        )
        self._req_id_re = (
            re.compile(profile.requirement_id.pattern)
            if profile.requirement_id.pattern
            else None
        )
        # Anchored req_id regex — same pattern, full-match only. Used by
        # ``anchor="last_run"`` extraction to test whether a single run's
        # text *is* a req_id (vs. merely contains one).
        self._req_id_anchored_re = (
            re.compile(rf"^\s*(?:{profile.requirement_id.pattern})\s*$")
            if profile.requirement_id.pattern
            else None
        )
        # Leading req_id regex (D-DRAFT-2) — same pattern, anchored at the
        # START of the text but NOT requiring a full match (the requirement
        # statement follows the id). Used by ``detection_mode ==
        # "leading_id_body"`` to test whether a body block *opens* with a
        # req_id (a new requirement) vs merely *mentions* one mid-text (a
        # cross-reference, which must NOT spawn a phantom requirement).
        self._req_id_leading_re = (
            re.compile(rf"^\s*(?:{profile.requirement_id.pattern})")
            if profile.requirement_id.pattern
            else None
        )
        self._leading_id_mode = (
            profile.requirement_id.detection_mode == "leading_id_body"
        )
        # Revision/version-history heading detection (FR-34) — compiled
        # once; None if disabled. Drops the matching paragraph and the
        # immediately-following table block (within a small window).
        self._revhist_re = (
            re.compile(profile.revision_history_label_pattern)
            if profile.revision_history_label_pattern
            else None
        )
        # Table-header fallback for revhist (bare-table-at-the-top docs,
        # no introducing heading). Tested against ` | `.join(table.headers).
        self._revhist_table_header_re = (
            re.compile(profile.revhist_table_header_pattern)
            if profile.revhist_table_header_pattern
            else None
        )
        # Signal-based revhist detection — third path after label and
        # regex-header. Pre-compile cell-content fingerprint regexes
        # once so the body pass doesn't re-compile per block.
        rd = profile.revhist_detection
        self._revhist_score_enabled = bool(rd.enabled)
        self._revhist_score_cfg = rd
        self._revhist_cell_res = [re.compile(p) for p in rd.cell_patterns]
        # Lowercased token set for vocab matching (case-insensitive
        # word-boundary lookup).
        self._revhist_vocab_lower = [t.lower() for t in rd.vocab_tokens]
        # Populated by ``_find_revhist_block_indices`` to surface the
        # active detection path + (for scoring) per-signal breakdown
        # in the parse_summary diagnostics.
        self._frontmatter_revhist_match_info: dict = {}
        # TOC entry detection (FR-34) — compiled once; None if disabled
        self._toc_re = (
            re.compile(profile.toc_detection_pattern)
            if profile.toc_detection_pattern
            else None
        )
        # Style-driven TOC detection (generic-rules pivot). When
        # ``style_pattern`` is set, the pre-pass walks paragraphs whose
        # ``style`` matches and parses each via ``entry_pattern`` to
        # build a ``(section_number, title, req_id)`` index keyed by
        # depth. ``_classify_heading_docx_styles`` consults the index
        # to attach the document's real section_number to each body
        # heading.
        self._toc_style_re = (
            re.compile(profile.toc_detection.style_pattern)
            if profile.toc_detection.style_pattern
            else None
        )
        self._toc_entry_re = (
            re.compile(profile.toc_detection.entry_pattern)
            if profile.toc_detection.entry_pattern
            else None
        )
        # ``body`` field of a TOC entry holds ``"<title> <req_id>"`` or
        # ``"<title><req_id>"``; this peeler tolerates either spacing.
        self._toc_body_peel_re = (
            re.compile(rf"^(?P<title>.*?)\s*(?P<req_id>{profile.requirement_id.pattern})\s*$")
            if profile.requirement_id.pattern
            else None
        )
        # Priority marker detection (FR-31) — compiled once; None if disabled
        self._priority_re = (
            re.compile(profile.heading_detection.priority_marker_pattern)
            if profile.heading_detection.priority_marker_pattern
            else None
        )
        # Applicability detection (FR-32 [D-030]) — compiled once
        ad = profile.applicability_detection
        self._applicability_res = [re.compile(p) for p in ad.requirement_patterns]
        self._applicability_global_re = (
            re.compile(ad.global_section_pattern) if ad.global_section_pattern else None
        )
        self._applicability_split_re = (
            re.compile(ad.label_split_pattern, re.IGNORECASE)
            if ad.label_split_pattern
            else None
        )
        # Definitions / acronyms detection (FR-35 [D-032]) — compiled once
        self._definitions_section_re = (
            re.compile(profile.heading_detection.definitions_section_pattern)
            if profile.heading_detection.definitions_section_pattern
            else None
        )
        # Table-header fallback for the glossary (no introducing heading).
        # Tested against ` | `.join(table.headers); a matching table is
        # treated as the definitions section even when no preceding
        # heading matched ``definitions_section_pattern``.
        self._definitions_table_header_re = (
            re.compile(profile.heading_detection.definitions_table_header_pattern)
            if profile.heading_detection.definitions_table_header_pattern
            else None
        )
        self._definitions_entry_re = (
            re.compile(profile.definitions_entry_pattern, re.MULTILINE)
            if profile.definitions_entry_pattern
            else None
        )
        # Glossary table column-header detection (Phase 5 of the
        # generic-rules pivot). When both patterns are set, the parser
        # uses them to recognize the canonical (term, definition) header
        # row of a glossary table — fold-or-skip decision in
        # `_extract_definitions`. Default patterns cover the OA
        # canonical set ("Acronym", "Term", "Definition", ...).
        self._definitions_table_term_re = (
            re.compile(profile.definitions_table_term_column)
            if profile.definitions_table_term_column
            else None
        )
        self._definitions_table_definition_re = (
            re.compile(profile.definitions_table_definition_column)
            if profile.definitions_table_definition_column
            else None
        )
        # Reference-list / bibliography detection (D-059, D-061) — compiled once
        self._reference_list_section_re = (
            re.compile(profile.reference_list_section_pattern)
            if profile.reference_list_section_pattern
            else None
        )
        self._reference_list_entry_re = (
            re.compile(profile.reference_list_entry_pattern, re.MULTILINE)
            if profile.reference_list_entry_pattern
            else None
        )
        # Cross-reference regexes
        self._std_res = [
            re.compile(p)
            for p in profile.cross_reference_patterns.standards_citations
        ]
        self._section_ref_re = (
            re.compile(profile.cross_reference_patterns.internal_section_refs)
            if profile.cross_reference_patterns.internal_section_refs
            else None
        )
        # 3GPP spec + section extraction (more specific than profile patterns)
        # Use \d[\d.]*\d to avoid capturing trailing dots from punctuation
        self._std_detail_re = re.compile(
            r"3GPP\s+TS\s+(\d[\d.]*\d)\s+(?:[Ss]ection\s+)?(\d[\d.]*\d)"
        )
        self._std_release_re = re.compile(
            r"3GPP\s+(?:TS\s+\d[\d.]*\d\s+)?[Rr]elease\s+(\d+)"
        )
        # Plan ID extraction from requirement IDs (profile-driven)
        components = profile.requirement_id.components
        self._req_id_separator = components.get("separator", "_")
        self._req_id_plan_pos = components.get("plan_id_position")
        # Zone classification
        self._zone_map = {
            z.section_pattern: z.zone_type
            for z in profile.document_zones
        }

    def parse(self, doc: DocumentIR) -> RequirementTree:
        """Parse a document IR into a structured requirement tree."""
        logger.info(f"Parsing {doc.source_file} with profile '{self.profile.profile_name}'")

        # Per-document parse diagnostics — populated by passes that drop or
        # extract content. Surfaced in the compact RPT.
        self._parse_stats = ParseStats()

        # Parse-log collectors — reset per document.
        # Each entry: (block_index, page, reason).
        self._dropped_entries: list[tuple[int, int, str]] = []
        # Each entry: (section_number, depth, block_index, page).
        self._heading_entries: list[tuple[str, int, int, int]] = []
        # TOC pairing — body headings that didn't match any TOC entry.
        self._toc_pair_misses: list[TocPairMiss] = []
        # Doc identity threaded into ``_log_format_error`` so warning
        # lines are self-contained without reference to outer scope.
        self._doc_source_file = doc.source_file

        # 1a. Style-driven TOC pre-pass (generic-rules pivot). Builds a
        #     ``(req_id | (depth, normalized_title)) → TocEntry`` index
        #     consulted during heading classification to assign the
        #     document's actual section_number to each body heading.
        #     Also tracks the set of TOC block indices for drop-time
        #     skip in the body pass.
        self._toc_index, self._toc_block_indices = self._extract_toc_index(doc)

        # 1b. Revhist range pre-pass + front-matter cutoff (Phase 4).
        #     Everything at block-index ≤ cutoff is dropped — TOC,
        #     revhist, AND any preface content (doc title heading,
        #     classification notice, etc.) that sits in the front
        #     matter region. Cutoff = max of last TOC index and last
        #     revhist index.
        #
        #     **Opt-in via ``toc_detection.style_pattern``.** The cutoff
        #     fires only when the profile uses the style-driven TOC
        #     path (the generic-rules pivot for DOCX corpora where
        #     front-matter is at the top). OA-style numbering corpora
        #     locate revhist *inside* chapter 1; applying the cutoff
        #     there would drop chapter-1's heading. For those corpora
        #     ``style_pattern`` is empty and the inline revhist consume
        #     in the body pass remains the only drop mechanism.
        self._frontmatter_revhist_indices = set()
        self._frontmatter_cutoff = -1
        if self._toc_style_re is not None:
            self._frontmatter_revhist_indices = self._find_revhist_block_indices(doc)
            toc_end = max(self._toc_block_indices, default=-1)
            rev_end = max(self._frontmatter_revhist_indices, default=-1)
            self._frontmatter_cutoff = max(toc_end, rev_end)

        # D-DRAFT-4: content-start cutoff. Independent of the toc_style path —
        # drop every block before the first real (font-confirmed) heading whose
        # top-level section number == profile.content_start_section, skipping
        # the front page / TOC / intro chapters in one positive anchor (for
        # corpora whose front matter isn't reliably detectable negatively).
        if self.profile.content_start_section:
            csi = self._find_content_start_index(doc)
            if csi >= 0:
                self._frontmatter_cutoff = max(self._frontmatter_cutoff, csi - 1)

        # 1. Extract plan metadata
        plan_meta = self._extract_plan_metadata(doc)

        # 2. Classify blocks and build section hierarchy.
        #    Two anchor sources for Requirements (see Key Choices in MODULE.md):
        #    paragraph anchors (heading or standalone-ID-in-small-font) and
        #    table-cell anchors (req-IDs found in column-1 of a row, falling
        #    back to all cells). Paragraph anchors win on duplicate req_ids.
        sections = self._build_sections(doc)
        self._parse_stats.toc_pair_misses = len(self._toc_pair_misses)

        # 3. Extract referenced standards releases
        std_releases = self._extract_standards_releases(doc)

        # 4. Extract cross-references for each section
        plan_id = plan_meta.get("plan_id", "")
        for sec in sections:
            sec.cross_references = self._extract_cross_refs(sec.text, plan_id)

        # 5. Build parent-child relationships
        self._link_parents(sections)

        # 6. Table-anchored Requirements have no section_number, so
        #    _link_parents skips them. Inherit hierarchy_path from their
        #    paragraph-anchored parent now that the parents are linked.
        self._propagate_hierarchy_to_table_reqs(sections)

        # 7. Apply form-factor applicability with hierarchical inheritance
        #    (FR-32 [D-030]). Document-order walk; explicit value wins,
        #    else inherit from parent_section, else fall back to root default.
        self._apply_applicability(sections)

        # 8. Extract definitions / acronyms map from the glossary section
        #    (FR-35 [D-032]). The section itself stays in the parsed tree
        #    by default; with ``profile.embed_glossary == False``
        #    (Phase 5 — generic-rules pivot) the section + descendants
        #    are dropped from ``sections`` after the map has been built
        #    so the glossary content doesn't reach RAG / KG. The map
        #    itself is preserved on the tree for acronym-expansion.
        (
            definitions_map,
            definitions_section_number,
            definitions_section_title,
            acronym_entries,
            glossary_table_headers,
        ) = self._extract_definitions(sections)
        self._parse_stats.defs_extracted = len(definitions_map)
        if not self.profile.embed_glossary and definitions_section_number:
            sections = self._drop_glossary_subtree(
                sections, definitions_section_number
            )

        # 8b. Extract reference / bibliography map from the references
        #     section (D-059, D-061). Used by the resolver to resolve
        #     indirect spec citations (`[5]` → spec lookup). The section
        #     itself stays in the parsed tree.
        (
            reference_list_map,
            reference_list_section_number,
        ) = self._extract_reference_list(sections)
        self._parse_stats.refs_extracted = len(reference_list_map)

        # 8b'. Drop profile-excluded sections (D-DRAFT-13) — non-normative
        #      sections matched by `exclude_section_pattern` (REFERENCES,
        #      traceability matrices). Runs AFTER reference extraction so a
        #      REFERENCES section still populates `reference_list_map` before
        #      being dropped from the tree / RAG. No-op when the pattern is empty.
        sections = self._drop_excluded_sections(sections)
        # 8b''. Truncate trailing non-normative appendices glued onto a
        #       requirement's body (D-DRAFT-13) — e.g. a traceability matrix +
        #       test-case tables that begin at `content_end_marker` on the last
        #       real requirement. No-op when the marker is empty.
        self._apply_content_end_marker(sections)

        # 8c. Populate per-requirement plan_id (D-DRAFT-1). The plan is encoded
        #     in the req_id and extracted via the profile's
        #     ``requirement_id.components`` config (separator + plan_id_position).
        #     Storing it per-req lets a multi-plan document (one PDF,
        #     sections-as-plans) be grouped by plan downstream, while the tree
        #     stays one-per-document. Reqs with no extractable plan (structural
        #     heading nodes with no req_id, or a profile without a plan
        #     component) fall back to the tree-level plan. For single-plan
        #     documents this yields plan_id == tree.plan_id for every req.
        tree_plan_id = plan_meta.get("plan_id", "")
        for req in sections:
            rid_plan = (
                self._extract_plan_id_from_req(req.req_id) if req.req_id else None
            )
            req.plan_id = rid_plan or tree_plan_id

        # Per-requirement Context (D-DRAFT-5) is NOT materialized in the tree —
        # consumers (SIRA adapter, NORA chunk builder) assemble it on demand from
        # the section nodes via `build_context_string`, governed by
        # `profile.build_context`. Keeps the tree compact (no per-req
        # duplication); the section content is stored once on the section nodes.

        # 9. Build parse transparency log.
        parse_log = self._build_parse_log(
            doc,
            definitions_section_number,
            definitions_section_title,
            acronym_entries,
        )

        # 10. Build parse summary (debugging side-channel). Captures
        #     the evidence that profile-driven detection found — which
        #     pattern matched, what label text triggered it, what
        #     table headers accompanied. Pipeline parse stage
        #     aggregates these into reports/parse_summary.json.
        parse_summary = self._build_parse_summary(
            doc, plan_meta, definitions_section_title,
            glossary_table_headers, definitions_map,
            sections,
        )

        tree = RequirementTree(
            mno=doc.mno,
            release=doc.release,
            plan_id=plan_meta.get("plan_id", ""),
            plan_name=plan_meta.get("plan_name", ""),
            version=plan_meta.get("version", ""),
            release_date=plan_meta.get("release_date", ""),
            detection_mode=self.profile.requirement_id.detection_mode,
            build_context=self.profile.build_context,
            build_context_max_chars=self.profile.build_context_max_chars,
            referenced_standards_releases=std_releases,
            requirements=sections,
            parse_stats=self._parse_stats,
            definitions_map=definitions_map,
            definitions_section_number=definitions_section_number,
            reference_list_map=reference_list_map,
            reference_list_section_number=reference_list_section_number,
            embed_glossary=self.profile.embed_glossary,
            parse_log=parse_log,
            parse_summary=parse_summary,
        )

        logger.info(
            f"Parsed {doc.source_file}: {len(sections)} requirements, "
            f"plan_id={tree.plan_id}"
        )
        return tree

    # ── Plan metadata ───────────────────────────────────────────────

    def _extract_plan_metadata(self, doc: DocumentIR) -> dict[str, str]:
        """Extract plan-level metadata using profile patterns.

        Matched **per-paragraph** (first hit wins) so no regex can
        accidentally span multiple paragraphs. The previous joined-blob
        approach concatenated every page-1 paragraph with " " (no
        newlines), which broke `(.+?)(?:\\n|Plan\\s+Id|$)`-style
        terminators: when neither alternation matched in the doc, the
        regex fell through to ``$`` (end of joined blob) and gobbled
        the entire TOC + body into ``plan_name``.

        DOCX docs without explicit ``<w:br w:type="page"/>`` markers
        report every paragraph as ``page=1``; that's the corpus we
        encounter most often and it's where the leak surfaced.
        Per-paragraph scoping is robust against that.

        Returns ``{}`` (empty per field, not key-absent) when a pattern
        is unset or matches nothing — downstream code reads via
        ``meta.get(field_name, "")`` so missing == "".
        """
        page1_paragraphs = [
            b.text for b in doc.blocks_by_type(BlockType.PARAGRAPH)
            if b.position.page == 1 and b.text
        ]

        meta: dict[str, str] = {}
        for field_name in ["plan_name", "plan_id", "version", "release_date"]:
            mf = getattr(self.profile.plan_metadata, field_name)
            if not mf.pattern:
                continue
            try:
                rx = re.compile(mf.pattern)
            except re.error as exc:
                logger.warning(
                    "plan_metadata.%s pattern doesn't compile (%s) — skipping",
                    field_name, exc,
                )
                continue
            for p_text in page1_paragraphs:
                m = rx.search(p_text)
                if not m:
                    continue
                # Prefer capture group 1; fall back to full match for
                # legacy patterns without a group.
                value = (m.group(1) if m.groups() else m.group(0)).strip()
                if value:
                    meta[field_name] = value
                    break

        logger.info(f"Plan metadata: {meta}")
        return meta

    # ── Section hierarchy ───────────────────────────────────────────

    def _identify_toc_pages(self, doc: DocumentIR) -> set[int]:
        """Return the set of page numbers classified as TOC pages (FR-34).

        A page is a TOC page when at least `toc_page_threshold` of its
        paragraph blocks match `toc_detection_pattern`. Pages with no
        paragraph blocks are never TOC pages. When `_toc_re` is None
        (TOC detection disabled), returns an empty set.
        """
        if self._toc_re is None:
            return set()
        threshold = self.profile.toc_page_threshold
        if threshold <= 0.0 or threshold > 1.0:
            return set()  # disabled / invalid
        # Bucket paragraph blocks by page; count how many match the pattern.
        per_page_total: dict[int, int] = {}
        per_page_match: dict[int, int] = {}
        for b in doc.content_blocks:
            if b.type != BlockType.PARAGRAPH or not b.text:
                continue
            page = b.position.page
            per_page_total[page] = per_page_total.get(page, 0) + 1
            if self._toc_re.search(b.text.strip()):
                per_page_match[page] = per_page_match.get(page, 0) + 1
        toc_pages: set[int] = set()
        for page, total in per_page_total.items():
            if total >= 2 and per_page_match.get(page, 0) / total >= threshold:
                toc_pages.add(page)
        if toc_pages:
            logger.info(
                f"TOC pages detected: {sorted(toc_pages)} "
                f"(threshold={threshold:.0%})"
            )
        return toc_pages

    def _extract_toc_index(
        self, doc: DocumentIR
    ) -> tuple[dict[str, TocEntry], set[int]]:
        """Style-driven TOC pre-pass.

        Walks paragraph blocks whose ``style`` matches
        ``profile.toc_detection.style_pattern``, parses each via
        ``entry_pattern``, and builds an index used for body-heading
        section-number pairing.

        The returned dict has two key shapes:
          * ``"rid:<req_id>"`` — primary lookup, when the TOC entry's
            body trailing token matches the requirement_id pattern.
          * ``"tt:<depth>:<normalized_title>"`` — fallback for entries
            whose body has no req_id (e.g. front-matter). Same key
            scheme for ``_classify_heading_docx_styles`` lookups.

        Returns ``(index, toc_block_indices)`` — the second element is
        the set of ``position.index`` values to skip during the body
        pass. When ``style_pattern`` is unset, both are empty.
        """
        index: dict[str, TocEntry] = {}
        toc_blocks: set[int] = set()

        if not self._toc_style_re or not self._toc_entry_re:
            return index, toc_blocks

        normalize = self.profile.requirement_id.normalize

        for block in doc.content_blocks:
            if block.type != BlockType.PARAGRAPH:
                continue
            style = block.style or ""
            sm = self._toc_style_re.match(style)
            if not sm:
                continue
            try:
                depth = int(sm.group(1))
            except (IndexError, ValueError):
                continue

            toc_blocks.add(block.position.index)

            em = self._toc_entry_re.match(block.text)
            if not em:
                # Style says TOC but text doesn't parse — drop the block,
                # don't index. Logged at debug for diagnosis.
                logger.debug(
                    "toc.entry_unparseable: depth=%d block=%d style=%r",
                    depth, block.position.index, style,
                )
                continue

            section_number = em.group("num").strip()
            body = em.group("body").strip()

            # Peel req_id from tail of body (whitespace-tolerant).
            title = body
            req_id = ""
            if self._toc_body_peel_re:
                pm = self._toc_body_peel_re.match(body)
                if pm:
                    title = pm.group("title").strip()
                    raw = pm.group("req_id")
                    rid = _canonicalize_req_id(raw)
                    req_id = rid.upper() if normalize == "upper" else rid

            entry = TocEntry(
                depth=depth,
                section_number=section_number,
                title=title,
                req_id=req_id,
                block_index=block.position.index,
            )

            # Index by req_id when present (unique, primary key); also
            # by (depth, normalized title) as fallback. First-occurrence
            # wins on duplicates so document-order is preserved.
            if req_id:
                index.setdefault(f"rid:{req_id}", entry)
            index.setdefault(
                f"tt:{depth}:{_normalize_title(title)}", entry
            )

        if toc_blocks:
            logger.info(
                "toc.indexed: entries=%d blocks=%d",
                len(set(e.block_index for e in index.values())),
                len(toc_blocks),
            )
        return index, toc_blocks

    def _score_revhist_table(
        self,
        block: ContentBlock,
        total_blocks: int,
    ) -> tuple[float, dict[str, float]]:
        """Score a TABLE block against the revhist signal mix.

        Returns ``(combined_score, per_signal_dict)``. Combined score is
        compared against ``RevhistDetection.threshold`` by the caller.
        Per-signal dict is included for parse-time diagnostics — the
        Summary tab can surface "why didn't this score" without
        re-running the scorer.

        Returns ``(0.0, {})`` when scoring is disabled or the block is
        not a TABLE — keeps the call site free of pre-flight checks.
        """
        if not self._revhist_score_enabled or block.type != BlockType.TABLE:
            return 0.0, {}
        cfg = self._revhist_score_cfg

        # 1. Position score — 1.0 if the table is within the leading
        #    `max_position_fraction` of the document by block index.
        position_score = 0.0
        if total_blocks > 0:
            frac = block.position.index / max(total_blocks - 1, 1)
            if frac <= cfg.max_position_fraction:
                position_score = 1.0

        # 2. Vocabulary score — sum unique-token hits from (a) the joined
        #    column headers, (b) every merged-cell anchor text, and
        #    (c) every body-row cell. Body-cell scan handles the case
        #    where the real column-header row is buried in body rows
        #    (reversed table layout) or where a label like "Revision
        #    History" sits in a footer-merged cell that we may not have
        #    populated into ``merged_cells`` (pre-9ee028c IRs).
        joined_headers = " | ".join(h.strip() for h in (block.headers or []))
        merged_text = " | ".join(
            mc.text for mc in (block.merged_cells or []) if mc.text
        )
        body_text = " || ".join(
            " | ".join(str(c).strip() for c in row if c)
            for row in (block.rows or [])
            if row
        )
        haystack = (
            joined_headers + " || " + merged_text + " || " + body_text
        ).lower()
        hit_tokens: set[str] = set()
        for tok in self._revhist_vocab_lower:
            if not tok:
                continue
            # Word-boundary match so 'ver' doesn't match 'version' and
            # vice versa, while still matching 'Ver.' (`\b` handles
            # punctuation boundaries).
            if re.search(r"\b" + re.escape(tok) + r"\b", haystack):
                hit_tokens.add(tok)
        vocab_raw = min(len(hit_tokens), cfg.vocab_score_cap)
        vocab_score = vocab_raw / cfg.vocab_score_cap if cfg.vocab_score_cap else 0.0

        # 3. Cell-content fingerprint — count columns where ≥
        #    cell_min_match_fraction of body rows match any pattern.
        rows = block.rows or []
        n_cols = max((len(r) for r in rows), default=0)
        cell_columns_matched = 0
        if n_cols and rows:
            for col_idx in range(n_cols):
                col_values = [
                    (row[col_idx] if col_idx < len(row) else "")
                    for row in rows
                ]
                col_values = [v for v in col_values if v and v.strip()]
                if not col_values:
                    continue
                for rx in self._revhist_cell_res:
                    matches = sum(1 for v in col_values if rx.match(v))
                    if matches / len(col_values) >= cfg.cell_min_match_fraction:
                        cell_columns_matched += 1
                        break  # one matching pattern per column is enough
        cell_raw = min(cell_columns_matched, cfg.cell_score_cap)
        cell_score = cell_raw / cfg.cell_score_cap if cfg.cell_score_cap else 0.0

        combined = (
            position_score * cfg.position_weight
            + vocab_score * cfg.vocab_weight
            + cell_score * cfg.cell_weight
        )
        return combined, {
            "position": position_score,
            "vocab": vocab_score,
            "cell": cell_score,
            "combined": combined,
            "threshold": cfg.threshold,
        }

    def _find_revhist_block_indices(self, doc: DocumentIR) -> set[int]:
        """Return the block indices that constitute the revision-history
        section (label paragraph + its trailing tables/images, up to but
        not including the next paragraph).

        Used by the front-matter cutoff (Phase 4 of the generic-rules
        pivot): the body pass drops everything at index ≤
        ``max(toc_end, revhist_end)`` so doc-title headings and preface
        paragraphs that sit between front-matter sections also get
        omitted. Returns the set of indices for the *first* revhist
        section found — multiple revhist sections in one document is
        rare; the inline consume in ``_build_sections`` still catches
        any that appear after the cutoff.

        Empty when ``revision_history_label_pattern`` is unset or no
        matching label is found.
        """
        if (
            self._revhist_re is None
            and self._revhist_table_header_re is None
            and not self._revhist_score_enabled
        ):
            return set()

        # Track which detection path fired and (for scoring) the
        # per-signal breakdown. The body-pass uses ``indices`` for the
        # drop semantics; ``_build_parse_summary`` reads
        # ``_frontmatter_revhist_match_info`` to surface diagnostics.
        self._frontmatter_revhist_match_info = {}
        indices: set[int] = set()
        consuming = False
        total_blocks = len(doc.content_blocks)
        for b in doc.content_blocks:
            if not consuming:
                # Detect the revhist label on PARAGRAPH-typed blocks
                # (PDF extractor / numbering corpora) AND HEADING-typed
                # blocks (DOCX extractor's Word ``Heading N`` style).
                # ``_heading_title_text`` strips the trailing req_id
                # run from the title when ``anchor=last_run`` — without
                # this, a heading like "REVISION HISTORY <MNO>_REQ_..."
                # never matches the end-anchored revhist regex on
                # DOCX corpora.
                if (
                    self._revhist_re is not None
                    and b.type in (BlockType.PARAGRAPH, BlockType.HEADING)
                    and b.text
                    and self._revhist_re.match(self._heading_title_text(b))
                ):
                    indices.add(b.position.index)
                    consuming = True
                    if not self._frontmatter_revhist_match_info:
                        self._frontmatter_revhist_match_info = {
                            "pattern_id": "label",
                        }
                # Table-header regex fallback (legacy, narrower).
                elif (
                    self._revhist_table_header_re is not None
                    and b.type == BlockType.TABLE
                    and b.headers
                    and self._revhist_table_header_re.search(
                        " | ".join(h.strip() for h in b.headers)
                    )
                ):
                    indices.add(b.position.index)
                    consuming = True
                    if not self._frontmatter_revhist_match_info:
                        self._frontmatter_revhist_match_info = {
                            "pattern_id": "table_header_regex",
                        }
                # Signal-based scoring fallback.
                elif (
                    self._revhist_score_enabled
                    and b.type == BlockType.TABLE
                ):
                    score, breakdown = self._score_revhist_table(b, total_blocks)
                    if score >= self._revhist_score_cfg.threshold:
                        indices.add(b.position.index)
                        consuming = True
                        if not self._frontmatter_revhist_match_info:
                            self._frontmatter_revhist_match_info = {
                                "pattern_id": "score",
                                "score_breakdown": breakdown,
                            }
                continue
            # Consuming: drop non-paragraph blocks (tables, images)
            # until the next paragraph OR heading, either of which
            # marks the start of post-revhist content. Originally only
            # PARAGRAPH closed the consume (revhist's tail was always a
            # PDF paragraph); DOCX extractors emit Word-styled headings
            # as BlockType.HEADING, so a heading right after the
            # revhist table would otherwise be swept into the revhist
            # range — inflating ``revhist_blocks_dropped`` and
            # extending the front-matter cutoff incorrectly.
            if b.type in (BlockType.PARAGRAPH, BlockType.HEADING):
                break
            indices.add(b.position.index)
        return indices

    def _toc_lookup(
        self, depth: int, req_id: str, title: str
    ) -> TocEntry | None:
        """Resolve a body heading against the TOC index.

        Primary key: ``req_id``. Fallback: ``(depth, normalized_title)``.
        Returns ``None`` when neither matches.
        """
        if not self._toc_index:
            return None
        if req_id:
            hit = self._toc_index.get(f"rid:{req_id}")
            if hit is not None:
                return hit
        return self._toc_index.get(
            f"tt:{depth}:{_normalize_title(title)}"
        )

    def _build_sections(self, doc: DocumentIR) -> list[Requirement]:
        """Build the flat list of sections with hierarchy info from content blocks."""
        sections: list[Requirement] = []
        current_section: Requirement | None = None

        # D-DRAFT-2: in ``leading_id_body`` detection mode, ``current_section``
        # remains the enclosing HEADING (structural context / parenting
        # anchor), while ``current_leading_req`` is the most-recent leading-id
        # body Requirement — the append target for continuation body blocks
        # (PyMuPDF often splits one requirement's text across blocks). Reset to
        # None whenever a fresh heading opens, so a section's pre-requirement
        # preamble lands on the heading, not the prior section's last req.
        current_leading_req: Requirement | None = None

        # Pending req ID — small font blocks that appear before/after a heading
        pending_req_id: str = ""

        # Track which req_ids were anchored by a paragraph (heading-block
        # assignment, pending-id resolution, or inline body-text id) so the
        # table-anchored detection can dedup against them — paragraph wins.
        paragraph_req_ids: set[str] = set()

        # Track req_ids that appeared in a struck-through block. Table
        # cells containing these ids must NOT produce table-anchored reqs
        # — the strikethrough means the source author marked them deleted.
        # PDF strike detection is geometric (lines crossing text); table
        # cells often slip past it because the cell text and the strike
        # line don't intersect cleanly. Recording the id when we see it
        # struck in a paragraph block is a reliable secondary signal.
        struck_req_ids: set[str] = set()

        # Tables are deferred: collected during the main walk, processed
        # for table-anchored req extraction in a SECOND pass after
        # paragraph_req_ids and struck_req_ids are fully populated.
        # Without deferral, a req_id whose paragraph anchor is later in
        # the document gets duplicated as table-anchored when the table
        # appears earlier (e.g. in a change-log table on page 3 vs the
        # real anchor on page 34).
        deferred_tables: list[tuple[ContentBlock, Requirement]] = []

        # Heading-continuation defense state. PyMuPDF text extraction
        # often splits a long heading across multiple blocks ("1.1.7
        # DEVICE TESTING ON ... BAND" / "13 NETWORK"). When the
        # continuation line happens to start with `<digits><space>
        # <uppercase>`, the relaxed numbering gate misclassifies it as
        # a phantom depth-1 chapter. The fingerprint of these false
        # positives is precise: depth-1 section_number AFTER a deeper
        # section has been classified, with the immediately-preceding
        # block also heading-shaped (no body text intervening). When
        # that fingerprint matches, the new "section" is appended to
        # the current section's title as a continuation rather than
        # creating a phantom chapter.
        seen_deep_section = False
        previous_block_was_heading = False

        # Track section_numbers already created. Numbering-driven heading
        # classification is permissive enough that body paragraphs starting
        # with a previously-seen section number can occasionally match the
        # gate. Section numbers are unique per document, so the first
        # heading wins; later matches with the same number are demoted to
        # body text appended to the current section.
        seen_section_numbers: set[str] = set()

        # FR-34: identify TOC pages — pages where >= toc_page_threshold of
        # paragraph blocks match the TOC entry pattern. Computed once up
        # front so the per-block loop below can drop matching blocks AND
        # all blocks on a TOC page (including non-matching ones, e.g. a
        # "Table of Contents" header).
        toc_pages = self._identify_toc_pages(doc)

        # FR-34: revision-history table omission. When a paragraph
        # matches the revision-history heading pattern, this flag is
        # raised and ALL subsequent non-paragraph blocks (tables AND
        # images) are dropped until the next paragraph block. Multi-
        # page revhist tables are common — pdfplumber emits each page's
        # slice as its own table block — so the consumer is intentionally
        # unbounded by block count. The next paragraph is treated as
        # the boundary because the next section's heading is always a
        # paragraph block.
        revhist_active = False

        # FR-33 cascade state. When a struck-through paragraph is also
        # a section heading (matches the numbering pattern), the whole
        # section is treated as deleted: every subsequent block is
        # dropped until the next heading appears at depth <= the
        # struck heading's depth (a sibling or shallower section). A
        # depth=1 struck heading thus drops everything under that
        # chapter; a depth=4 struck heading drops the rest of that
        # subsection but leaves siblings untouched.
        cascade_depth: int | None = None

        def _record_paragraph_anchor(rid: str) -> None:
            if rid:
                paragraph_req_ids.add(rid)

        def _heading_depth(block: ContentBlock) -> int | None:
            """Return the heading depth (1.2.3 → 3) when `block` is a
            paragraph that the parser would classify as a section
            heading; else None.

            Delegates to `_classify_heading` so the cascade boundary
            test uses EXACTLY the same definition of "heading" as the
            rest of the parser. Body text starting with a digit
            ("3GPP TS 24.301"), numbered list items, and prose
            sentences are all rejected via the same length / punctuation
            / numbering-pattern guards already in `_classify_heading`.
            Reusing it prevents premature cascade exit on non-heading
            number-prefixed text (which would leak phantom content)
            AND ensures cascade arming on EXACTLY the headings that
            would otherwise enter the tree.
            """
            # DOCX-style HEADING blocks carry their depth explicitly via
            # block.level (1-9 from Word's Heading styles). Use it directly
            # so the strikethrough cascade arms on struck DOCX headings —
            # latent bug pre-D-061 since `_classify_heading` only matches
            # PARAGRAPH-typed blocks via numbering pattern (PDF convention).
            if block.type == BlockType.HEADING and block.level is not None:
                return block.level
            if block.type != BlockType.PARAGRAPH or not block.text:
                return None
            # docx_styles classification path: depth from the ``Heading N``
            # style suffix; section_number may be empty (TOC pairing miss)
            # so it cannot be used to compute depth.
            if self.profile.heading_detection.method == "docx_styles":
                sm = _DOCX_HEADING_STYLE_RE.match(block.style or "")
                if sm:
                    try:
                        return int(sm.group(1))
                    except (IndexError, ValueError):
                        return None
                return None
            section_num, _ = self._classify_heading(block)
            if not section_num:
                return None
            return section_num.count(".") + 1

        for block in doc.content_blocks:
            # Front-matter cutoff (Phase 4 of the generic-rules pivot).
            # Drop everything at index ≤ ``max(toc_end, revhist_end)``.
            # Categorize the drop reason for diagnostics: TOC, revhist,
            # or generic front_matter (the latter catches doc-title
            # headings, classification notices, and other preface content
            # that isn't itself TOC or revhist).
            idx = block.position.index
            if idx <= self._frontmatter_cutoff:
                if idx in self._toc_block_indices:
                    reason = "toc"
                    self._parse_stats.toc_blocks_dropped += 1
                elif idx in self._frontmatter_revhist_indices:
                    reason = "revhist"
                    self._parse_stats.revhist_blocks_dropped += 1
                else:
                    reason = "front_matter"
                    self._parse_stats.frontmatter_blocks_dropped += 1
                self._dropped_entries.append((idx, block.position.page, reason))
                continue

            # FR-33 [D-031]: drop struck-through blocks. Checked first so
            # struck content never feeds heading classification, table
            # anchoring, or zone matching. Gated by profile.ignore_strikeout.
            if (
                self.profile.ignore_strikeout
                and block.font_info is not None
                and block.font_info.strikethrough
            ):
                # Before dropping, mine any req_id patterns out of the
                # struck text — those ids are "deleted" and must not
                # surface via table cross-references either.
                if self._req_id_re and block.text:
                    for sid in self._find_req_ids(block.text):
                        struck_req_ids.add(sid)
                # If this is a struck section heading, arm the cascade
                # so subsequent siblings/descendants of this section
                # are also dropped. Refresh cascade_depth to the
                # SHALLOWEST struck heading we've seen so far — a deeper
                # struck heading inside an already-cascading section
                # doesn't tighten the boundary.
                depth = _heading_depth(block)
                if depth is not None:
                    if cascade_depth is None or depth < cascade_depth:
                        cascade_depth = depth
                self._parse_stats.struck_blocks_dropped += 1
                self._dropped_entries.append(
                    (block.position.index, block.position.page, "text_strikethrough")
                )
                continue

            # D-060 — Partial strike: this block survived the FR-33
            # cascade (it isn't fully struck), but it may carry struck
            # spans (some runs / some cells). Normalize block.text /
            # block.rows / block.headers to their "live" versions so
            # downstream code sees only the unstruck content. Mine any
            # req_ids that appear in struck spans first — those ids are
            # "deleted" and must not surface as cross-reference targets,
            # mirroring the fully-struck cascade's behavior above.
            if self.profile.ignore_strikeout:
                if block.runs and self._req_id_re:
                    struck_span_text = "".join(
                        r.text for r in block.runs if r.struck
                    )
                    if struck_span_text:
                        for sid in self._find_req_ids(struck_span_text):
                            struck_req_ids.add(sid)
                if block.runs:
                    block.text = block.live_text()
                if block.row_runs:
                    new_rows: list[list[str]] = []
                    for r_idx in range(len(block.row_runs)):
                        if block.row_all_struck(r_idx):
                            # Mine struck req_ids before dropping the row
                            if self._req_id_re:
                                for cell_runs in block.row_runs[r_idx]:
                                    rt = "".join(
                                        rr.text for rr in cell_runs if rr.struck
                                    )
                                    if rt:
                                        for sid in self._find_req_ids(rt):
                                            struck_req_ids.add(sid)
                            self._parse_stats.struck_blocks_dropped += 1
                            continue
                        new_rows.append([
                            block.cell_live_text(r_idx, c_idx)
                            for c_idx in range(len(block.row_runs[r_idx]))
                        ])
                    block.rows = new_rows
                if block.header_runs:
                    block.headers = [
                        block.header_live_text(c)
                        for c in range(len(block.header_runs))
                    ]

            # FR-33 cascade: a struck section heading deletes the whole
            # section. Drop every subsequent block until we hit a new
            # heading at depth <= cascade_depth (a sibling or shallower
            # section). Tables, images, and body paragraphs all get
            # dropped through the cascade — strike marks may not have
            # propagated to every individual block, but the section as
            # a whole is gone.
            if cascade_depth is not None:
                depth = _heading_depth(block)
                if depth is not None and depth <= cascade_depth:
                    cascade_depth = None
                    # Fall through — this block opens a new section
                    # and must be processed normally.
                else:
                    self._parse_stats.cascade_blocks_dropped += 1
                    self._dropped_entries.append(
                        (block.position.index, block.position.page, "cascade")
                    )
                    continue

            # Word's canonical TOC styles (``TOC 1``, ``TOC 2``, ...).
            # Independent of profile config — these styles are
            # unambiguously TOC entries by Word's convention. Skipping
            # them here prevents a TOC entry like "Glossary ... 5" from
            # being mis-classified as the actual Glossary section
            # heading by ``_extract_definitions`` / similar text-match
            # paths later in the pipeline.
            if block.style and _WORD_TOC_STYLE_RE.match(block.style):
                self._parse_stats.toc_blocks_dropped += 1
                self._dropped_entries.append(
                    (block.position.index, block.position.page, "toc")
                )
                continue

            # FR-34: drop entire-page TOC content (any block type) and any
            # block that matches the TOC entry pattern.
            if block.position.page in toc_pages:
                self._parse_stats.toc_blocks_dropped += 1
                self._dropped_entries.append(
                    (block.position.index, block.position.page, "toc")
                )
                continue
            if (
                self._toc_re is not None
                and block.type == BlockType.PARAGRAPH
                and block.text
                and self._toc_re.search(block.text.strip())
            ):
                self._parse_stats.toc_blocks_dropped += 1
                self._dropped_entries.append(
                    (block.position.index, block.position.page, "toc")
                )
                continue

            # FR-34: revhist consume. After the revhist heading matched,
            # drop ALL subsequent table/image blocks until the next
            # paragraph or heading (either marks post-revhist content).
            # Multi-page revhist tables — which pdfplumber slices into
            # one table block per page — all get consumed as a unit.
            if revhist_active:
                if block.type in (BlockType.PARAGRAPH, BlockType.HEADING):
                    revhist_active = False
                    # fall through — process this block normally
                else:
                    self._parse_stats.revhist_blocks_dropped += 1
                    self._dropped_entries.append(
                        (block.position.index, block.position.page, "revhist")
                    )
                    self._frontmatter_revhist_indices.add(block.position.index)
                    continue

            if (
                self._revhist_re is not None
                and block.type in (BlockType.PARAGRAPH, BlockType.HEADING)
                and block.text
                and self._revhist_re.match(self._heading_title_text(block))
            ):
                self._parse_stats.revhist_blocks_dropped += 1
                self._dropped_entries.append(
                    (block.position.index, block.position.page, "revhist")
                )
                self._frontmatter_revhist_indices.add(block.position.index)
                if not self._frontmatter_revhist_match_info:
                    self._frontmatter_revhist_match_info = {"pattern_id": "label"}
                revhist_active = True
                continue

            # Table-header fallback for revhist (bare-table-at-the-top
            # docs with no introducing heading). Match against the joined
            # column headers; drop the table and arm the same consume
            # state the label path uses so continuation slices also drop.
            if (
                self._revhist_table_header_re is not None
                and block.type == BlockType.TABLE
                and block.headers
                and self._revhist_table_header_re.search(
                    " | ".join(h.strip() for h in block.headers)
                )
            ):
                self._parse_stats.revhist_blocks_dropped += 1
                self._dropped_entries.append(
                    (block.position.index, block.position.page, "revhist")
                )
                self._frontmatter_revhist_indices.add(block.position.index)
                if not self._frontmatter_revhist_match_info:
                    self._frontmatter_revhist_match_info = {
                        "pattern_id": "table_header_regex",
                    }
                revhist_active = True
                continue

            # Signal-based revhist scoring — fires only when the label
            # and regex-header paths above didn't. Combines position +
            # column-vocabulary + cell-content fingerprints.
            if (
                self._revhist_score_enabled
                and block.type == BlockType.TABLE
            ):
                score, breakdown = self._score_revhist_table(
                    block, len(doc.content_blocks)
                )
                if score >= self._revhist_score_cfg.threshold:
                    self._parse_stats.revhist_blocks_dropped += 1
                    self._dropped_entries.append(
                        (block.position.index, block.position.page, "revhist")
                    )
                    self._frontmatter_revhist_indices.add(block.position.index)
                    if not self._frontmatter_revhist_match_info:
                        self._frontmatter_revhist_match_info = {
                            "pattern_id": "score",
                            "score_breakdown": breakdown,
                        }
                    revhist_active = True
                    continue

            if block.type in (BlockType.PARAGRAPH, BlockType.HEADING):
                # DOCX extractors emit heading-styled paragraphs as
                # BlockType.HEADING (per ``docx_extractor._paragraph_block``);
                # PDF extractors emit them as PARAGRAPH with a numbering
                # prefix in ``block.text``. Both must reach the heading
                # classifier — the small-font / no-font-info paragraph
                # gates below are PARAGRAPH-only since HEADING blocks
                # always carry FontInfo and never qualify as small-font
                # req_id markers.
                if block.type == BlockType.PARAGRAPH and not block.font_info:
                    if current_section:
                        self._append_text(current_section, block.text)
                    continue

                # Check if this is a requirement ID block (small font)
                if block.type == BlockType.PARAGRAPH and self._is_req_id_block(block):
                    req_ids = self._find_req_ids(block.text)
                    if req_ids:
                        if current_section is None:
                            # No section opened yet — hold for the first heading.
                            # (Rare; only fires when a doc-level id precedes
                            # any structural heading.)
                            pending_req_id = req_ids[0]
                        elif not current_section.req_id:
                            # Trailing-marker pattern (OA): the small-font id
                            # right after a heading is THIS section's id.
                            current_section.req_id = req_ids[0]
                            _record_paragraph_anchor(req_ids[0])
                        else:
                            # Section already has a req_id. Ignore — the first
                            # one wins. Lateralling to the next section's
                            # `pending_req_id` is wrong for trailing-marker
                            # corpora and produces an off-by-one cascade.
                            logger.debug(
                                "Extra req_id %s ignored — section %r already has %r",
                                req_ids[0],
                                current_section.section_number,
                                current_section.req_id,
                            )
                    # The req_id "closes" the current heading group: a
                    # later block, even if heading-shaped, is the start
                    # of a new heading, not a continuation of the prior
                    # one.
                    previous_block_was_heading = False
                    continue

                # Check if this is a heading block
                section_num, heading_text = self._classify_heading(block)
                # Use ``_heading_depth`` so docx_styles headings (which
                # may have an empty ``section_num`` when the TOC pre-pass
                # didn't match) are still recognized as headings.
                new_depth = _heading_depth(block)
                if new_depth is not None:
                    # Heading-continuation defense. PyMuPDF often splits a
                    # multi-line heading across blocks; when the second
                    # line happens to start with `<digits><space><uppercase>`
                    # (e.g. "13 NETWORK" continuing "1.1.7 ... BAND"),
                    # the relaxed numbering gate misclassifies it as a
                    # phantom depth-1 chapter. Fingerprint:
                    #   - depth-1 section_number
                    #   - we've already created at least one deep section
                    #   - the immediately-preceding block was also
                    #     heading-shaped (no body text intervening — req_id
                    #     blocks reset the flag).
                    # When all three hold, the new "section" is appended
                    # to the current section's title as a continuation.
                    # Defense is OA-specific (numbering-pattern path); the
                    # docx_styles path doesn't suffer this PDF artifact.
                    if (
                        section_num
                        and new_depth == 1
                        and seen_deep_section
                        and previous_block_was_heading
                        and current_section is not None
                        and current_section.section_number
                    ):
                        cont_text = block.text.strip()
                        if cont_text:
                            current_section.title = (
                                (current_section.title + " " + cont_text).strip()
                                if current_section.title
                                else cont_text
                            )
                        # Stay in heading context — multi-line continuations stack.
                        continue

                    # FR-31: extract priority marker (if any) from heading text
                    # before storing — title carries the cleaned form.
                    priority, heading_text = self._extract_priority(heading_text)
                    # Heading-anchored req_id (anchor="last_run" / "leading_text"
                    # corpora; trailing_text mode falls back to pending_req_id
                    # from a separate small-font block per the OA convention).
                    heading_req_id = self._heading_req_id(block)
                    section_req_id = heading_req_id or pending_req_id
                    # Empty section_num (docx_styles + TOC pair miss) is
                    # never deduplicated — every such heading creates its
                    # own Requirement. Numbered sections continue to
                    # dedup on first-occurrence-wins.
                    is_new_section = (
                        not section_num or section_num not in seen_section_numbers
                    )
                    if is_new_section:
                        # New section — first occurrence wins.
                        current_section = Requirement(
                            section_number=section_num,
                            title=heading_text,
                            req_id=section_req_id,
                            zone_type=self._classify_zone(section_num),
                            priority=priority,
                            source_block_idx=block.position.index,
                        )
                        if section_req_id:
                            _record_paragraph_anchor(section_req_id)
                        pending_req_id = ""
                        sections.append(current_section)
                        if section_num:
                            seen_section_numbers.add(section_num)
                        self._heading_entries.append(
                            (section_num, new_depth, block.position.index, block.position.page)
                        )
                        previous_block_was_heading = True
                        if new_depth >= 2:
                            seen_deep_section = True
                        continue
                    # Duplicate section_number. If the prior occurrence is a
                    # "phantom" — empty req_id AND empty body AND no children
                    # yet — it likely came from a TOC residual or other
                    # noise. Replace it with this real heading rather than
                    # demote the real heading to body text. Preserves the
                    # invariant that section_numbers are unique while
                    # protecting against off-by-one cascades on req_id
                    # assignment when TOC bleed-through creates a phantom.
                    existing = next(
                        (s for s in sections if s.section_number == section_num),
                        None,
                    )
                    if (
                        existing is not None
                        and not existing.req_id
                        and not existing.text
                        and not existing.children
                    ):
                        existing.title = heading_text
                        existing.req_id = pending_req_id
                        existing.zone_type = self._classify_zone(section_num)
                        existing.priority = priority
                        if pending_req_id:
                            _record_paragraph_anchor(pending_req_id)
                        pending_req_id = ""
                        current_section = existing
                        previous_block_was_heading = True
                        if new_depth >= 2:
                            seen_deep_section = True
                        continue
                # Section number duplicate (real, with content) or no match
                # → fall through to body text path.

                # D-DRAFT-2: leading-id body-block requirement detection.
                # A body paragraph whose text BEGINS with the req_id pattern
                # is itself a Requirement (parented to the current heading,
                # which is non-requirement context); a body block that does
                # not lead with a req_id continues the most-recent such
                # Requirement, or — before any requirement in the section —
                # is preamble on the heading.
                if self._leading_id_mode:
                    # A freshly-opened heading resets the requirement cursor.
                    if previous_block_was_heading:
                        current_leading_req = None
                    leads_with_id = bool(
                        self._req_id_leading_re
                        and self._req_id_leading_re.match(block.text)
                    )
                    if leads_with_id:
                        new_req = self._create_leading_id_req(
                            block, current_section, sections
                        )
                        if new_req.req_id:
                            _record_paragraph_anchor(new_req.req_id)
                        current_leading_req = new_req
                    else:
                        target = current_leading_req or current_section
                        if target is not None:
                            self._append_text(target, block.text)
                    previous_block_was_heading = False
                    continue

                # Body text — append to current section
                if current_section:
                    self._append_text(current_section, block.text)
                    # Also check for inline req IDs in body text
                    if self._req_id_re and not current_section.req_id:
                        ids = self._find_req_ids(block.text)
                        if ids:
                            current_section.req_id = ids[0]
                            _record_paragraph_anchor(ids[0])
                # Body text breaks the heading-continuation chain.
                previous_block_was_heading = False

            elif block.type == BlockType.TABLE:
                if current_section:
                    current_section.tables.append(
                        TableData(
                            headers=block.headers,
                            rows=block.rows,
                            source="inline",
                        )
                    )
                    # Inline the table into the body text AT ITS DOCUMENT
                    # POSITION (this loop runs in block order), so text-then-
                    # table-then-text ordering is preserved for the synthesizer
                    # instead of all tables being appended at the end. `tables`
                    # stays populated as structured metadata; consumers
                    # (chunk_builder, SIRA adapter) read the faithful text.
                    self._append_text(
                        current_section,
                        render_table_markdown(block.headers, block.rows),
                    )
                    # Defer table-anchored extraction to a second pass —
                    # paragraph_req_ids and struck_req_ids must be
                    # complete before we decide what to anchor (see
                    # comment at deferred_tables initialization).
                    deferred_tables.append((block, current_section))

            elif block.type == BlockType.IMAGE:
                if current_section:
                    current_section.images.append(
                        ImageRef(
                            path=block.image_path,
                            surrounding_text=block.surrounding_text,
                        )
                    )

        # Second pass: extract table-anchored reqs (D-027) when enabled.
        # Disabled corpora (paragraph-only-requirement docs like Verizon
        # OA) drop any id that lives ONLY in a table — those are
        # cross-references, changelog entries, or other non-requirement
        # content per the corpus convention. When enabled, paragraph
        # anchors and struck ids still take precedence (skip_set).
        if self.profile.enable_table_anchored_extraction:
            skip_set = paragraph_req_ids | struck_req_ids
            for tbl_block, parent_section in deferred_tables:
                self._extract_table_anchored_reqs(
                    tbl_block, parent_section, sections, skip_set
                )

        return sections

    # ── Table-anchored requirement detection ────────────────────────

    def _extract_table_anchored_reqs(
        self,
        block: ContentBlock,
        parent_section: Requirement,
        sections: list[Requirement],
        paragraph_req_ids: set[str],
    ) -> None:
        """Detect req-IDs in table cells; append child Requirement nodes to `sections`.

        Heuristic: scan column 1 of each row first; if no IDs there, fall back
        to scanning all cells of the row. At most one anchor per row.
        Skips IDs already anchored by a paragraph elsewhere in the document.
        Within a single table, also dedups so a repeated ID across rows yields
        only one Requirement.
        """
        if not self._req_id_re or not block.rows:
            return

        seen_in_table: set[str] = set()
        for row in block.rows:
            if not row:
                continue

            anchor_id: str | None = None
            anchor_cells: list[str] = list(row)

            # Strategy 1: column 1 only.
            col1_ids = self._find_req_ids(row[0])
            for rid in col1_ids:
                if rid in paragraph_req_ids or rid in seen_in_table:
                    continue
                anchor_id = rid
                break

            # Strategy 2 (fallback): any cell.
            if anchor_id is None:
                for cell in row[1:]:
                    if not cell:
                        continue
                    cell_ids = self._find_req_ids(cell)
                    for rid in cell_ids:
                        if rid in paragraph_req_ids or rid in seen_in_table:
                            continue
                        anchor_id = rid
                        break
                    if anchor_id is not None:
                        break

            if anchor_id is None:
                continue

            seen_in_table.add(anchor_id)
            self._create_table_anchored_req(
                anchor_id, anchor_cells, block, parent_section, sections
            )

    def _create_table_anchored_req(
        self,
        req_id: str,
        row: list[str],
        block: ContentBlock,
        parent_section: Requirement,
        sections: list[Requirement],
    ) -> None:
        """Append a Requirement node anchored by a table row.

        Linkage to parent_section is done here (not via _link_parents, which
        keys on section_number — table-anchored reqs have none). Hierarchy
        path is filled later by _propagate_hierarchy_to_table_reqs once the
        paragraph-anchored sections have their paths built.
        """
        # Serialize row as text using headers when available — preserves the
        # column→value mapping that is the actual content of the requirement.
        headers = block.headers or []
        parts: list[str] = []
        for i, cell in enumerate(row):
            cell_str = (cell or "").strip()
            if not cell_str:
                continue
            if i < len(headers) and headers[i]:
                parts.append(f"{headers[i].strip()}: {cell_str}")
            else:
                parts.append(cell_str)
        text = "; ".join(parts)

        new_req = Requirement(
            req_id=req_id,
            section_number="",   # no own section — anchored by table row
            title="",
            parent_req_id=parent_section.req_id,
            parent_section=parent_section.section_number,
            hierarchy_path=[],   # filled in _propagate_hierarchy_to_table_reqs
            zone_type=parent_section.zone_type,
            text=text,
            tables=[
                TableData(
                    headers=list(block.headers),
                    rows=[list(row)],
                    source="inline",
                )
            ],
        )
        sections.append(new_req)
        if req_id and req_id not in parent_section.children:
            parent_section.children.append(req_id)

    def _create_leading_id_req(
        self,
        block: ContentBlock,
        parent_section: Requirement | None,
        sections: list[Requirement],
    ) -> Requirement:
        """Append a Requirement anchored by a leading req_id in a body block.

        D-DRAFT-2: in ``requirement_id.detection_mode == "leading_id_body"``
        corpora, each requirement is a flat body paragraph whose text begins
        with the req_id; section/subsection headings are non-requirement
        context. Structurally identical to a table-anchored Requirement — no
        own ``section_number``, linked to the enclosing heading via
        ``parent_section`` / ``parent_req_id`` and added to its ``children``;
        ``hierarchy_path`` is filled by ``_propagate_hierarchy_to_table_reqs``
        once the paragraph-anchored headings have their paths. The leading
        req_id is retained in ``text`` (the source statement is preserved
        verbatim — parser Non-goal: no content rewriting).

        ``parent_section`` may be ``None`` when a requirement precedes any
        heading; the node is then created with empty parent linkage.

        D-DRAFT-5: the header line (``block.lines[0]`` — the req_id + title)
        is split from the body (the lines beneath it) using the extractor's
        preserved line boundaries (D-DRAFT-3). ``title`` = the header line after
        the req_id; ``text`` = the body. No content is rewritten — the verbatim
        source is just partitioned on the line boundary (a multi-line title's
        overflow falls into ``text``; no per-span signal to delimit it). Falls
        back to the old behavior (empty title, whole block as text) when no line
        structure is available.
        """
        ids = self._find_req_ids(block.text)
        req_id = ids[0] if ids else ""
        lines = block.lines or []
        if lines:
            header = lines[0]
            m = (
                self._req_id_leading_re.match(header)
                if self._req_id_leading_re else None
            )
            title = header[m.end():].strip() if m else header.strip()
            body = " ".join(ln for ln in lines[1:] if ln).strip()
        else:
            title = ""
            body = block.text
        new_req = Requirement(
            req_id=req_id,
            section_number="",   # no own section — anchored by the leading id
            title=title,
            parent_req_id=parent_section.req_id if parent_section else "",
            parent_section=parent_section.section_number if parent_section else "",
            hierarchy_path=[],   # filled in _propagate_hierarchy_to_table_reqs
            zone_type=parent_section.zone_type if parent_section else "",
            text=body,
            source_block_idx=block.position.index,
        )
        sections.append(new_req)
        if parent_section is not None and req_id and req_id not in parent_section.children:
            parent_section.children.append(req_id)
        return new_req

    def _propagate_hierarchy_to_table_reqs(
        self, sections: list[Requirement]
    ) -> None:
        """Copy parent's hierarchy_path to section_number-less Requirements.

        Covers both table-anchored (D-027) and leading-id body-block
        (D-DRAFT-2) Requirements — both have no own ``section_number`` and so
        are skipped by ``_link_parents``, leaving ``hierarchy_path`` empty
        after that pass. Fill it now from the paragraph-anchored parent.
        `applicability` is propagated by `_apply_applicability` later (it
        walks document-order so parents resolve before children).
        """
        # Lookup paragraph-anchored sections by section_number.
        by_section_num: dict[str, Requirement] = {
            s.section_number: s for s in sections if s.section_number
        }
        for s in sections:
            if s.section_number or not s.parent_section:
                continue
            parent = by_section_num.get(s.parent_section)
            if parent and parent.hierarchy_path:
                s.hierarchy_path = list(parent.hierarchy_path)

    def _apply_applicability(self, sections: list[Requirement]) -> None:
        """FR-32 [D-030]: resolve `Requirement.applicability` for every section.

        Walk in document order. For each section:
          1. Try `requirement_patterns` against the section's own text.
             First-match wins; capture group 1 is split into labels.
          2. Else inherit from `parent_section`'s already-resolved value.
          3. Else fall back to a root default extracted from the
             document-level applicability section, if any.

        No-op when the profile has no patterns and no global section regex.
        Empty list = unknown; downstream stages do not filter on empty.
        """
        # Fast path: nothing to do.
        if not self._applicability_res and self._applicability_global_re is None:
            return

        # Resolve a root default by scanning for the global applicability
        # section once. Its body text is run through requirement_patterns.
        root_default: list[str] = []
        if self._applicability_global_re is not None:
            for s in sections:
                if s.title and self._applicability_global_re.search(s.title):
                    root_default = self._extract_applicability_labels(s.text)
                    if root_default:
                        break

        # Lookup table for parent inheritance — table-anchored reqs key on
        # parent_section, paragraph-anchored on section_number.
        by_section_num: dict[str, Requirement] = {
            s.section_number: s for s in sections if s.section_number
        }

        for s in sections:
            # 1. Explicit value from the section's own text.
            labels = self._extract_applicability_labels(s.text)
            if labels:
                s.applicability = labels
                continue
            # 2. Inherit from parent_section if already resolved (document
            #    order guarantees parents come first for paragraph-anchored;
            #    table-anchored point at parent via parent_section).
            parent_key = s.parent_section
            if parent_key and parent_key in by_section_num:
                parent = by_section_num[parent_key]
                if parent.applicability:
                    s.applicability = list(parent.applicability)
                    continue
            # 3. Root default.
            if root_default:
                s.applicability = list(root_default)

    # ── Parse transparency log helpers ─────────────────────────────────

    def _glossary_section_range(
        self, definitions_section_number: str, doc: "DocumentIR"
    ) -> tuple[int, int, int, int] | None:
        """Return (block_start, block_end, page_start, page_end) for the
        glossary section, or None when the section is not in heading_entries.

        block_start is the heading block; block_end is the last block
        before the next peer (same-or-shallower depth) heading.
        """
        if not definitions_section_number or not self._heading_entries:
            return None

        target_pos: int | None = None
        target_depth: int = 0
        block_start: int = 0
        page_start: int = 1

        for i, (sec_num, depth, block_idx, page) in enumerate(self._heading_entries):
            if sec_num == definitions_section_number:
                target_pos = i
                target_depth = depth
                block_start = block_idx
                page_start = page
                break

        if target_pos is None:
            return None

        # Find the next heading at same or shallower depth → that is where
        # the glossary section ends.
        block_end: int | None = None
        for sec_num, depth, block_idx, page in self._heading_entries[target_pos + 1:]:
            if depth <= target_depth:
                block_end = block_idx - 1
                break

        if block_end is None:
            # Glossary is the last section — use the last block in the doc.
            block_end = (
                doc.content_blocks[-1].position.index if doc.content_blocks else block_start
            )

        # Scan content blocks for the max page within [block_start, block_end].
        page_end = page_start
        for b in doc.content_blocks:
            if block_start <= b.position.index <= block_end:
                if b.position.page > page_end:
                    page_end = b.position.page

        return block_start, block_end, page_start, page_end

    def _build_parse_log(
        self,
        doc: "DocumentIR",
        definitions_section_number: str,
        definitions_section_title: str,
        acronym_entries: list[tuple[str, str, str]],
    ) -> "ParseLog":
        """Assemble the ParseLog from the drop and heading entries collected
        during this parse run."""
        from datetime import datetime, timezone

        from core.src.parser.parse_log import (
            AcronymEntry,
            DroppedRange,
            GlossaryInfo,
            ParseLog,
            ParseLogSummary,
            SectionRange,
            TocPairMissEntry,
        )

        doc_id = Path(doc.source_file).stem

        # Sort by block_index and merge consecutive same-reason runs.
        entries = sorted(self._dropped_entries, key=lambda e: e[0])
        ranges: list[DroppedRange] = []

        if entries:
            cur_idx, cur_page, cur_reason = entries[0]
            run_start_idx, run_start_page = cur_idx, cur_page
            run_end_idx, run_end_page = cur_idx, cur_page

            for block_idx, page, reason in entries[1:]:
                if reason == cur_reason and block_idx == run_end_idx + 1:
                    run_end_idx = block_idx
                    run_end_page = page
                else:
                    ranges.append(DroppedRange(
                        block_start=run_start_idx,
                        block_end=run_end_idx,
                        page_start=run_start_page,
                        page_end=run_end_page,
                        block_count=run_end_idx - run_start_idx + 1,
                        reason=cur_reason,
                    ))
                    run_start_idx, run_start_page = block_idx, page
                    run_end_idx, run_end_page = block_idx, page
                    cur_reason = reason

            ranges.append(DroppedRange(
                block_start=run_start_idx,
                block_end=run_end_idx,
                page_start=run_start_page,
                page_end=run_end_page,
                block_count=run_end_idx - run_start_idx + 1,
                reason=cur_reason,
            ))

        # Quick-access TOC and revhist (spanning all ranges of that reason).
        def _span(reason: str) -> SectionRange | None:
            rs = [r for r in ranges if r.reason == reason]
            if not rs:
                return None
            return SectionRange(
                block_start=rs[0].block_start,
                block_end=rs[-1].block_end,
                page_start=rs[0].page_start,
                page_end=rs[-1].page_end,
            )

        toc_range = _span("toc")
        revhist_range = _span("revhist")

        # Glossary section location.
        glossary_info: GlossaryInfo | None = None
        if definitions_section_number:
            result = self._glossary_section_range(definitions_section_number, doc)
            if result:
                g_bs, g_be, g_ps, g_pe = result
                glossary_info = GlossaryInfo(
                    section_number=definitions_section_number,
                    section_title=definitions_section_title,
                    block_start=g_bs,
                    block_end=g_be,
                    page_start=g_ps,
                    page_end=g_pe,
                    acronym_count=len(acronym_entries),
                )

        # Summary counters (from raw entry list, not merged ranges, so they
        # match parse_stats exactly).
        by_reason: dict[str, int] = {}
        for _, _, reason in entries:
            by_reason[reason] = by_reason.get(reason, 0) + 1

        summary = ParseLogSummary(
            toc_blocks_dropped=by_reason.get("toc", 0),
            revhist_blocks_dropped=by_reason.get("revhist", 0),
            struck_blocks_dropped=by_reason.get("text_strikethrough", 0),
            cascade_blocks_dropped=by_reason.get("cascade", 0),
            total_dropped=len(entries),
            glossary_acronyms=len(acronym_entries),
            toc_pair_misses=len(self._toc_pair_misses),
            frontmatter_blocks_dropped=by_reason.get("front_matter", 0),
        )

        toc_pair_miss_entries = [
            TocPairMissEntry(
                block_index=m.block_index,
                page=m.page,
                depth=m.depth,
                req_id=m.req_id,
                title=m.title,
            )
            for m in self._toc_pair_misses
        ]

        return ParseLog(
            doc_id=doc_id,
            source_file=doc.source_file,
            mno=doc.mno,
            release=doc.release,
            generated_at=datetime.now(timezone.utc).isoformat(),
            dropped_blocks=ranges,
            toc=toc_range,
            revision_history=revhist_range,
            glossary_section=glossary_info,
            acronyms=[AcronymEntry(a, e, s) for a, e, s in acronym_entries],
            toc_pair_misses=toc_pair_miss_entries,
            summary=summary,
        )

    @staticmethod
    def _drop_section_subtree(
        sections: list[Requirement],
        section_num: str,
    ) -> list[Requirement]:
        """Return ``sections`` with ``section_num`` + its descendants removed.

        A Requirement belongs to the subtree when its ``section_number``
        equals ``section_num`` or starts with it followed by a dot, OR its
        ``parent_section`` matches the same conditions (catches table-anchored
        Requirements which carry no ``section_number``). Mirrors
        ``vectorstore.chunk_builder._belongs_to_definitions``. No logging —
        callers log with their own reason.
        """
        if not section_num:
            return sections
        prefix = section_num + "."

        def _belongs(r: Requirement) -> bool:
            if r.section_number == section_num:
                return True
            if r.section_number.startswith(prefix):
                return True
            if r.parent_section == section_num:
                return True
            if r.parent_section.startswith(prefix):
                return True
            return False

        return [r for r in sections if not _belongs(r)]

    def _drop_glossary_subtree(
        self,
        sections: list[Requirement],
        defs_section_num: str,
    ) -> list[Requirement]:
        """Remove the glossary section and all of its descendants from
        ``sections``. Used when ``profile.embed_glossary == False``."""
        if not defs_section_num:
            return sections
        kept = self._drop_section_subtree(sections, defs_section_num)
        dropped = len(sections) - len(kept)
        if dropped:
            logger.info(
                "parser.glossary_dropped: section=%s count=%d (embed_glossary=False)",
                defs_section_num, dropped,
            )
        return kept

    def _apply_content_end_marker(self, sections: list[Requirement]) -> None:
        """Truncate each requirement's text at ``profile.content_end_marker``
        and drop its trailing appendix tables/images (D-DRAFT-13).

        The marker is matched per body line. For a requirement whose text
        contains a matching line, the text is cut to the lines *before* the
        marker and the requirement's ``tables`` + ``images`` are cleared — they
        are the trailing non-normative appendix (e.g. a test-case traceability
        matrix the parser glued onto the last real requirement). Mutates in
        place. No-op when the marker is empty.
        """
        marker = self.profile.content_end_marker
        if not marker:
            return
        try:
            rx = re.compile(marker)
        except re.error as e:
            logger.warning(
                "parser.content_end_marker: bad pattern %r (%s)", marker, e,
            )
            return
        n = 0
        for r in sections:
            if not r.text:
                continue
            lines = r.text.split("\n")
            cut = next((i for i, ln in enumerate(lines) if rx.search(ln)), None)
            if cut is not None:
                r.text = "\n".join(lines[:cut]).rstrip()
                r.tables = []
                r.images = []
                n += 1
        if n:
            logger.info(
                "parser.content_end_marker: truncated %d requirement(s) "
                "(+ dropped their trailing tables/images)", n,
            )

    def _drop_excluded_sections(
        self,
        sections: list[Requirement],
    ) -> list[Requirement]:
        """Drop sections whose *title* matches ``profile.exclude_section_pattern``
        plus their descendants (D-DRAFT-13).

        For non-normative sections that carry a heading (sometimes a trailing
        req_id) but aren't requirements — bibliographies (``REFERENCES``),
        requirement→test-case traceability matrices, etc. A matched section
        with a ``section_number`` drops its whole subtree; a matched node
        without one drops just itself. No-op when the pattern is empty.
        """
        pattern = self.profile.exclude_section_pattern
        if not pattern:
            return sections
        try:
            rx = re.compile(pattern)
        except re.error as e:
            logger.warning(
                "parser.exclude_section: bad exclude_section_pattern %r (%s)",
                pattern, e,
            )
            return sections
        matched = [r for r in sections if rx.search(r.title or "")]
        if not matched:
            return sections
        kept = sections
        for m in matched:
            if m.section_number:
                kept = self._drop_section_subtree(kept, m.section_number)
            else:
                kept = [r for r in kept if r is not m]
        dropped = len(sections) - len(kept)
        if dropped:
            logger.info(
                "parser.excluded_sections: dropped %d node(s) across %d "
                "title-matched section(s) (exclude_section_pattern)",
                dropped, len(matched),
            )
        return kept

    def _build_parse_summary(
        self,
        doc: DocumentIR,
        plan_meta: dict[str, str],
        definitions_section_title: str,
        glossary_table_headers: list[str],
        definitions_map: dict[str, str],
        sections: list[Requirement],
    ) -> "DocSummary":
        """Build the per-doc summary record for the corpus-level
        parse_summary.json artifact.

        Pulls data already captured during the parse pass:
          * Revhist evidence from ``self._frontmatter_revhist_indices``
            (the pre-pass's accepted block-index set).
          * Glossary evidence from ``_extract_definitions`` returns +
            ``self._parse_stats.defs_extracted``.
          * Format-error counts from ``self._toc_pair_misses``.

        The format-error sub-counters (``empty_runs_heading``,
        ``concatenated_run_heading``) are derived from log records —
        for now we surface only the toc_pair_miss count which is
        already on parse_stats. The runs-fallback counters will be
        promoted to parse_stats in a future pass when the user starts
        filtering on them.
        """
        from core.src.parser.parse_summary import (
            DocSummary, RevhistMatch, GlossaryMatch,
        )

        # Revhist evidence — find the label block (first paragraph/
        # heading in the indices set) and the following table.
        revhist_match: RevhistMatch | None = None
        if self._frontmatter_revhist_indices:
            sorted_idx = sorted(self._frontmatter_revhist_indices)
            label_block = None
            table_block = None
            for idx in sorted_idx:
                if idx >= len(doc.content_blocks):
                    continue
                b = doc.content_blocks[idx]
                if (
                    label_block is None
                    and b.type in (BlockType.PARAGRAPH, BlockType.HEADING)
                ):
                    label_block = b
                elif table_block is None and b.type == BlockType.TABLE:
                    table_block = b
            info = self._frontmatter_revhist_match_info or {}
            pid = info.get("pattern_id") or "configured"
            if label_block is not None:
                revhist_match = RevhistMatch(
                    pattern_id=pid,
                    matched_text=self._heading_title_text(label_block)[:120],
                    label_block_index=label_block.position.index,
                    table_headers=(
                        list(getattr(table_block, "headers", []) or [])
                        if table_block else []
                    ),
                    score_breakdown=info.get("score_breakdown", {}) or {},
                )
            elif table_block is not None and pid in ("table_header_regex", "score"):
                # Scoring / regex-header path with no preceding label —
                # use the table itself as the anchor for the evidence row.
                revhist_match = RevhistMatch(
                    pattern_id=pid,
                    matched_text="(no label — table-shape match)",
                    label_block_index=table_block.position.index,
                    table_headers=list(getattr(table_block, "headers", []) or []),
                    score_breakdown=info.get("score_breakdown", {}) or {},
                )

        # Glossary evidence — set whenever ``_extract_definitions``
        # found a matching section, regardless of how many entries
        # finally landed in the map.
        glossary_match: GlossaryMatch | None = None
        glossary_sections = 0
        if definitions_section_title:
            glossary_sections = 1
            glossary_match = GlossaryMatch(
                pattern_id="configured",
                matched_heading=definitions_section_title[:120],
                table_headers=list(glossary_table_headers or []),
                entries_extracted=len(definitions_map),
            )

        format_errors = {
            "toc_pair_miss": self._parse_stats.toc_pair_misses,
        }

        # Cap plan_name length. A poorly-anchored ``plan_name`` regex
        # in the profile (e.g. ``Plan\s+Name:\s*(.+?)(?:\n|Plan\s+Id|$)``
        # over space-joined first-page text where neither delimiter
        # appears) can match the entire rest of page-1 — the Summary
        # tab then renders an unusable full-doc dump in the Plan
        # column. Truncate at 80 chars for the summary; the underlying
        # tree's ``plan_name`` field is untouched so downstream
        # consumers still see whatever the regex captured.
        raw_name = plan_meta.get("plan_name", "") or doc.mno or ""
        display_name = (
            raw_name[:77] + "…" if len(raw_name) > 80 else raw_name
        )

        # Cross-reference tallies for the Summary table. Sum across
        # every Requirement's CrossReferences — duplicates within a
        # single req count separately (the parser doesn't dedup at
        # extraction time; the resolver does at a later stage).
        internal_refs = sum(
            len(r.cross_references.internal) for r in sections
        )
        spec_refs = sum(
            len(r.cross_references.standards) for r in sections
        )

        return DocSummary(
            plan_name=display_name,
            plan_id=plan_meta.get("plan_id", ""),
            doc_id=Path(doc.source_file).stem if doc.source_file else "",
            source_file=doc.source_file or "",
            toc_entries=self._parse_stats.toc_blocks_dropped,
            req_count=len(sections),
            internal_refs=internal_refs,
            spec_refs=spec_refs,
            revhist_sections=1 if revhist_match else 0,
            revhist_match=revhist_match,
            glossary_sections=glossary_sections,
            glossary_match=glossary_match,
            format_errors=format_errors,
        )

    def _extract_definitions(
        self, sections: list[Requirement]
    ) -> tuple[dict[str, str], str, str, list[tuple[str, str, str]], list[str]]:
        """FR-35 [D-032]: extract `term -> expansion` pairs and the
        section number of the definitions / acronyms / glossary section.

        Section detection runs `definitions_section_pattern` against each
        section's title. Two layouts are supported per OA-corpus
        observation:

        - **Body-text** layout (line-based). The section's `text` is
          scanned via `definitions_entry_pattern` (default targets
          `TERM — expansion`-style lines). Used by docs that inline
          their glossary as paragraphs.
        - **Table-anchored** layout (the OA convention). The section's
          `tables` carry the actual term→expansion pairs as 2-col rows
          (`Acronym/Term | Definition`, `Term | Definition`,
          `Term [Abbreviation] | Definition`, etc.). For each row, col[0]
          is the term, col[1] is the expansion; the column header is
          already in `headers`, not `rows`, so no skip needed. Whitespace
          (incl. embedded newlines from PDF wrap) is collapsed in both
          fields. Both layouts contribute to the same map; on duplicate
          terms the first occurrence wins (body-text first, then
          tables in document order).

        The section itself stays in the parsed tree; the map and
        section_number are returned for downstream stages.

        Per-document scope — the returned map is stored on
        `RequirementTree.definitions_map` and never aggregated across
        trees. `RAT` may mean different things in different MNO documents.

        Returns (definitions_map, section_number, section_title, acronym_entries).
        acronym_entries is a list of (acronym, expansion, source) where source
        is "body_text" or "table". When no section matches, returns ({}, "", "", []).
        """
        if (
            self._definitions_section_re is None
            and self._definitions_table_header_re is None
        ):
            logger.debug("definitions: pattern unset — extraction disabled")
            return {}, "", "", [], []

        target: Requirement | None = None
        if self._definitions_section_re is not None:
            for s in sections:
                if not (s.title and self._definitions_section_re.search(s.title)):
                    continue
                # Density gate: the regex is a permissive substring
                # match, so titles like "Section 2.3 Acronyms list and
                # notes" slip through. Require that ≥75% of the title's
                # meaningful tokens are glossary keywords (req_id and
                # stopwords excluded) before accepting the match.
                hits, total, density, _ = _glossary_label_density(
                    s.title, self._req_id_re,
                )
                if total == 0 or density < _GLOSSARY_LABEL_MIN_DENSITY:
                    logger.info(
                        "definitions: regex matched section=%r but rejected by "
                        "density gate (hits=%d/total=%d, density=%.2f < %.2f)",
                        s.title, hits, total, density,
                        _GLOSSARY_LABEL_MIN_DENSITY,
                    )
                    continue
                target = s
                break

        # Table-header fallback when no section title matched (bare-table
        # glossary, no introducing heading). Walk every section's tables
        # and use the first whose joined headers match. The "section" we
        # attribute the find to is the one that contained the table.
        if target is None and self._definitions_table_header_re is not None:
            for s in sections:
                for tbl in (s.tables or []):
                    headers = getattr(tbl, "headers", None) or []
                    if not headers:
                        continue
                    joined = " | ".join(h.strip() for h in headers)
                    if self._definitions_table_header_re.search(joined):
                        target = s
                        logger.info(
                            "definitions: matched by table-header fallback "
                            "(section=%r headers=%r)",
                            s.title, headers,
                        )
                        break
                if target is not None:
                    break

        if target is None:
            # Diagnostic: surfaces *why* defs is zero. Lists the first
            # few section titles so the user can see what's being
            # pattern-matched against.
            sample_titles = [
                s.title for s in sections[:8] if s.title
            ]
            logger.info(
                "definitions: no section matched pattern=%r in %d sections; "
                "sample titles=%r",
                self._definitions_section_re.pattern if self._definitions_section_re else None,
                len(sections),
                sample_titles,
            )
            return {}, "", "", [], []

        logger.info(
            "definitions: matched section=%r section_number=%r tables=%d body_len=%d",
            target.title, target.section_number,
            len(target.tables or []), len(target.text or ""),
        )

        defs: dict[str, str] = {}
        acronym_entries: list[tuple[str, str, str]] = []

        # Layout 1 — body-text line scan (when entry pattern is set).
        if self._definitions_entry_re is not None and target.text:
            for m in self._definitions_entry_re.finditer(target.text):
                if not m.groups() or len(m.groups()) < 2:
                    continue
                term = m.group(1).strip()
                expansion = m.group(2).strip()
                if not term or not expansion:
                    continue
                if term not in defs:
                    defs[term] = expansion
                    acronym_entries.append((term, expansion, "body_text"))

        # Layout 2 — table-anchored (OA convention). Every 2+ col row
        # in the section's tables is a candidate definition. We also
        # walk `tbl.headers` because some markdown extractors split a
        # glossary into multiple tables when a divider line (`|---|`)
        # appears mid-table — the first row of the second table then
        # ends up in `headers`, not `rows`, and would otherwise be
        # silently dropped (real corpus bug: SDM row in VZ LTEOTADM).
        # We filter the obvious "Acronym | Definition" canonical
        # header row so it doesn't pollute the map.
        for tbl_idx, tbl in enumerate(target.tables):
            candidates: list[tuple[str, str]] = []
            headers = getattr(tbl, "headers", None) or []
            header_is_canonical = False
            if len(headers) >= 2:
                h0 = re.sub(r"\s+", " ", (headers[0] or "")).strip()
                h1 = re.sub(r"\s+", " ", (headers[1] or "")).strip()
                header_is_canonical = self._looks_like_definition_column_header(h0, h1)
                if h0 and h1 and not header_is_canonical:
                    candidates.append((h0, h1))
            for row in tbl.rows:
                if len(row) < 2:
                    continue
                term = re.sub(r"\s+", " ", (row[0] or "")).strip()
                expansion = re.sub(r"\s+", " ", (row[1] or "")).strip()
                if not term or not expansion:
                    continue
                candidates.append((term, expansion))
            kept_before = len(defs)
            for term, expansion in candidates:
                if term not in defs:
                    defs[term] = expansion
                    acronym_entries.append((term, expansion, "table"))
            logger.info(
                "definitions: table[%d] headers=%r canonical=%s rows=%d candidates=%d kept=%d",
                tbl_idx,
                list(headers)[:4],
                header_is_canonical,
                len(tbl.rows),
                len(candidates),
                len(defs) - kept_before,
            )

        # First table's headers go to the parse summary so the
        # debug page can show what column shape the glossary
        # detector saw.
        first_table_headers: list[str] = []
        if target.tables:
            first_table_headers = list(getattr(target.tables[0], "headers", []) or [])

        return (
            defs,
            target.section_number,
            target.title or "",
            acronym_entries,
            first_table_headers,
        )

    def _extract_reference_list(
        self, sections: list[Requirement]
    ) -> tuple[dict[int, dict[str, Any]], str]:
        """D-059, D-061: extract `entry_number -> {spec, title?, section?}`
        pairs from the document's references / bibliography section.

        Mirrors :meth:`_extract_definitions`. Section detection runs
        ``reference_list_section_pattern`` against each section's title;
        the first match wins. Two layouts are supported, both contributing
        to the same map (first-occurrence wins on duplicate numbers):

        - **Body-text** layout (paragraph list). The section's ``text`` is
          scanned via ``reference_list_entry_pattern`` (default tolerates
          ``[N]``, ``(N)``, and ``N.`` numbering). The captured content
          (group 2) is split into ``spec`` (everything up to the first
          comma / quote / em-dash) and ``title`` (the rest).
        - **Table-anchored** layout (rare for references but supported
          for parity with definitions). 2+ col rows where col[0] parses
          as a number and col[1] is the entry content. Headers folded
          like definitions when they don't look canonical.

        Per-document scope; consumed by the resolver when it sees an
        indirect spec citation (``[5]`` → look up ``5`` in this map).

        Returns (reference_list_map, section_number). When no section
        matches, returns ({}, "").
        """
        if self._reference_list_section_re is None:
            return {}, ""

        target: Requirement | None = None
        for s in sections:
            if s.title and self._reference_list_section_re.search(s.title):
                target = s
                break
        if target is None:
            return {}, ""

        refs: dict[int, dict[str, Any]] = {}

        def _record(num: int, content: str) -> None:
            if num in refs or not content:
                return
            spec, title = self._split_reference_entry(content)
            if not spec:
                return
            entry: dict[str, Any] = {"spec": spec}
            if title:
                entry["title"] = title
            refs[num] = entry

        # Layout 1 — body-text line scan
        if self._reference_list_entry_re is not None and target.text:
            for m in self._reference_list_entry_re.finditer(target.text):
                if not m.groups() or len(m.groups()) < 2:
                    continue
                num_str = m.group(1).strip()
                content = m.group(2).strip()
                try:
                    num = int(num_str)
                except (TypeError, ValueError):
                    continue
                _record(num, content)

        # Layout 2 — table-anchored (parity with definitions)
        for tbl in target.tables:
            for row in tbl.rows:
                if len(row) < 2:
                    continue
                num_str = re.sub(r"[^\d]", "", (row[0] or "")).strip()
                content = re.sub(r"\s+", " ", (row[1] or "")).strip()
                if not num_str or not content:
                    continue
                try:
                    num = int(num_str)
                except ValueError:
                    continue
                _record(num, content)

        return refs, target.section_number

    @staticmethod
    def _split_reference_entry(content: str) -> tuple[str, str]:
        """Split a reference entry's content into (spec, title).

        Heuristic: spec is everything up to (but not including) the
        first comma, opening quote, or em-dash; title is the remainder
        (with surrounding quotes / dashes / commas stripped).

        Examples:
          "3GPP TS 24.301, \\"Non-Access-Stratum...\\"" → ("3GPP TS 24.301", "Non-Access-Stratum...")
          "GSMA SGP.22 v3.0"                          → ("GSMA SGP.22 v3.0", "")
          "ETSI TS 133 401 — Security Architecture"    → ("ETSI TS 133 401", "Security Architecture")
        """
        if not content:
            return "", ""
        # Find earliest split delimiter
        candidates = []
        for delim in (",", "—", "–", "—", "–", '"', "“"):
            i = content.find(delim)
            if i >= 0:
                candidates.append(i)
        if not candidates:
            return content.strip(), ""
        cut = min(candidates)
        spec = content[:cut].strip()
        rest = content[cut:].lstrip(",—–—–\"“ ").strip()
        # Strip a trailing close-quote if the rest starts/ends with one
        if rest.endswith('"') or rest.endswith("”"):
            rest = rest.rstrip('"”').rstrip()
        return spec, rest

    def _looks_like_definition_column_header(self, h0: str, h1: str) -> bool:
        """True when (h0, h1) looks like the canonical column-header
        row of a glossary table — ``Acronym | Definition``, ``Term |
        Description``, etc. Used to decide whether to fold table
        headers into the definitions map.

        When ``profile.definitions_table_term_column`` and
        ``definitions_table_definition_column`` are both set
        (default), each column header is matched against those
        regexes (column order is irrelevant — either ``(term, def)``
        or ``(def, term)`` qualifies). Otherwise falls back to a
        token-set check against the legacy canonical vocabulary.
        """
        h0_norm = re.sub(r"\s+", " ", (h0 or "").strip())
        h1_norm = re.sub(r"\s+", " ", (h1 or "").strip())
        if not (h0_norm and h1_norm):
            return False

        if self._definitions_table_term_re and self._definitions_table_definition_re:
            term_h0 = bool(self._definitions_table_term_re.match(h0_norm))
            def_h1 = bool(self._definitions_table_definition_re.match(h1_norm))
            term_h1 = bool(self._definitions_table_term_re.match(h1_norm))
            def_h0 = bool(self._definitions_table_definition_re.match(h0_norm))
            return (term_h0 and def_h1) or (term_h1 and def_h0)

        # Legacy canonical-set fallback (when profile patterns are
        # explicitly cleared).
        canonical = {
            "acronym", "acronyms", "term", "terms",
            "abbreviation", "abbreviations", "abbr",
            "definition", "definitions", "description",
            "meaning", "expansion",
        }
        def normalize(s: str) -> set[str]:
            tokens = re.split(r"[\s/]+", s.strip().lower())
            return {t.rstrip("./:") for t in tokens if t}
        h0_tokens = normalize(h0_norm)
        h1_tokens = normalize(h1_norm)
        return (
            bool(h0_tokens) and bool(h1_tokens)
            and h0_tokens.issubset(canonical)
            and h1_tokens.issubset(canonical)
        )

    def _extract_applicability_labels(self, text: str) -> list[str]:
        """Run requirement_patterns over `text`; first match wins. Capture
        group 1 is split into individual labels via `label_split_pattern`.
        Returns [] when no pattern matches or no labels survive trimming.
        """
        if not text or not self._applicability_res:
            return []
        for rx in self._applicability_res:
            m = rx.search(text)
            if not m:
                continue
            captured = (m.group(1) if m.groups() else m.group(0)).strip()
            if not captured:
                continue
            if self._applicability_split_re is not None:
                parts = self._applicability_split_re.split(captured)
            else:
                parts = [captured]
            labels = [p.strip() for p in parts if p and p.strip()]
            # De-duplicate while preserving order.
            seen: set[str] = set()
            unique: list[str] = []
            for label in labels:
                key = label.lower()
                if key in seen:
                    continue
                seen.add(key)
                unique.append(label)
            return unique
        return []

    def _is_req_id_block(self, block: ContentBlock) -> bool:
        """Check if a block is a standalone requirement ID (small font)."""
        if not block.font_info or not self._req_id_re:
            return False
        # Req IDs in VZW docs appear at small font (7pt), distinct from
        # body text (12pt) and headings (14pt)
        body_mid = (
            self.profile.body_text.font_size_min
            + self.profile.body_text.font_size_max
        ) / 2
        if block.font_info.size < body_mid - 2.0:
            return bool(self._req_id_re.search(block.text))
        return False

    def _is_heading_font(self, block: ContentBlock) -> bool:
        """True when a block's font looks like a real heading rather than a
        same-text TOC entry (D-DRAFT-4). Chapter/section headings are bold +
        larger than body; TOC entries are body-size. Used to gate the
        content-start cutoff so a TOC line numbered N doesn't trigger it.
        Prefers the size signal (heading > ``body_text.font_size_max``); falls
        back to bold when body sizes are unconfigured."""
        fi = block.font_info
        if not fi:
            return False
        body_max = self.profile.body_text.font_size_max
        if body_max and fi.size > body_max + 0.5:
            return True
        return bool(fi.bold)

    def _find_content_start_index(self, doc: DocumentIR) -> int:
        """Block index of the first real (font-confirmed) heading whose
        top-level section number == ``profile.content_start_section``; -1 when
        disabled or not found. Drives the content-start cutoff (D-DRAFT-4) —
        the front page / TOC / intro chapters all precede it and are dropped."""
        target = self.profile.content_start_section
        if not target:
            return -1
        for block in doc.content_blocks:
            if not self._is_heading_font(block):
                continue
            section_num, _ = self._classify_heading(block)
            if section_num and section_num.split(".")[0] == target:
                return block.position.index
        return -1

    def _find_req_ids(self, text: str) -> list[str]:
        """Find all req_id patterns in `text` and canonicalize each.

        Wraps `_req_id_re.findall(text)` to absorb PDF-extraction
        artifacts (whitespace where an underscore should be) — every
        matched id is normalized via `_canonicalize_req_id` so the same
        requirement is never tracked under two different identifiers.
        """
        if not self._req_id_re or not text:
            return []
        return [_canonicalize_req_id(rid) for rid in self._req_id_re.findall(text)]

    def _log_format_error(
        self,
        kind: str,
        block: ContentBlock,
        **fields: Any,
    ) -> None:
        """Emit a structured WARN for a likely document formatting error.

        Tag prefix: ``parser.format_error``. Used for source-document
        formatting deviations the parser handles gracefully but that
        signal a likely human authoring error worth surfacing for
        review. Examples:

          * ``empty_runs_heading`` — heading with ``runs=[]``; req_id
            extracted from ``block.text`` fallback. Common DOCX
            authoring artifact.
          * ``toc_pair_miss`` — body heading didn't pair with any TOC
            entry; could be heading-styled non-section text, or a real
            heading missing from the auto-generated TOC.

        String fields are truncated to 80 chars — parse logs are
        local-only but compact RPT carries the count, not these
        per-error lines.
        """
        parts = [
            f"kind={kind}",
            f"doc={self._doc_source_file}",
            f"block={block.position.index}",
            f"page={block.position.page}",
        ]
        for k, v in fields.items():
            if isinstance(v, str) and len(v) > 80:
                v = v[:77] + "..."
            parts.append(f"{k}={v!r}")
        logger.warning("parser.format_error: " + " ".join(parts))

    def _heading_req_id(self, block: ContentBlock) -> str:
        """Extract a heading-anchored req_id when the profile opts in.

        Only the heading-anchored anchor modes look at the heading
        itself. ``"trailing_text"`` (the default / OA convention) is a
        no-op here — those corpora carry the req_id in a separate
        small-font block *after* the heading, threaded through
        ``pending_req_id`` in the body pass.

        Modes:
          * ``"last_run"`` — read ``block.runs[-1]``. If its text
            solo-matches ``pattern`` (whitespace-tolerant), return the
            canonicalized + optionally-uppercased id. **Empty-runs
            fallback**: when ``runs`` is empty (DOCX formatting error
            — heading content not run-split), the trailing match in
            ``block.text`` is used and the formatting deviation is
            logged via ``parser.format_error: kind=empty_runs_heading``.
          * ``"leading_text"`` — match ``pattern`` anchored at the start
            of the heading's live text.
          * ``"trailing_text"`` — return ``""`` (preserves OA semantics).

        Returns ``""`` when no match or when the mode doesn't extract
        from the heading.
        """
        if not self._req_id_re:
            return ""

        anchor = self.profile.requirement_id.anchor
        normalize = self.profile.requirement_id.normalize

        def _normalize(rid: str) -> str:
            rid = _canonicalize_req_id(rid)
            return rid.upper() if normalize == "upper" else rid

        if anchor == "last_run":
            # Primary path: trailing run solo-matches the req_id pattern
            # (the clean two-or-more-run heading shape).
            if block.runs:
                last = block.runs[-1].text
                if (
                    self._req_id_anchored_re
                    and self._req_id_anchored_re.match(last)
                ):
                    return _normalize(last.strip())
            # Fallback: search ``block.text`` (trailing match wins) for
            # the two source-doc formatting errors observed on the
            # work-PC corpus:
            #   * ``runs=[]`` — DOCX run-splitter produced no runs.
            #   * single-run heading whose text contains both title and
            #     req_id concatenated (run wasn't split).
            # Both gracefully recover the req_id from text so TOC
            # pair-by-req_id can succeed; the format deviation is
            # logged for the architect to find + fix the source.
            #
            # Multi-run headings (``len(runs) > 1``) where the last run
            # doesn't anchor are NOT promoted — that's the explicit
            # no-promotion semantic from Phase 2 (inline req_id
            # citations in non-trailing runs are not the section's
            # anchor).
            if block.text and len(block.runs) <= 1:
                ids = self._req_id_re.findall(block.text)
                if ids:
                    kind = (
                        "empty_runs_heading"
                        if not block.runs
                        else "concatenated_run_heading"
                    )
                    self._log_format_error(
                        kind, block,
                        note="last_run anchor missed; trailing req_id extracted from text",
                        runs_count=len(block.runs),
                        text_excerpt=block.text,
                    )
                    return _normalize(ids[-1])
            return ""

        if anchor == "leading_text":
            ids = self._req_id_re.findall(block.live_text())
            return _normalize(ids[0]) if ids else ""

        # Default ("trailing_text") — heading-text is not the anchor.
        return ""

    def _classify_heading(
        self, block: ContentBlock
    ) -> tuple[str, str]:
        """Check if a block is a heading. Returns (section_number, title) or ("", "").

        Two methods are supported, selected by
        ``profile.heading_detection.method``:

          * ``"docx_styles"`` — DOCX paragraph style (``Heading 1``,
            ``Heading 2``...) is the heading signal; depth comes from
            the style's trailing digit. The actual section_number is
            looked up against the TOC index (built by the pre-pass).
            Title is extracted from runs (last-run req_id stripped when
            ``RequirementIdPattern.anchor == "last_run"``). Headings
            with no TOC match return an empty section_number — the
            caller's dedup logic must tolerate this.
          * ``"numbering"`` (default) and ``"font_size_clustering"`` —
            a block matching the profile's ``numbering_pattern`` is a
            heading candidate; style/font is advisory only.

        Heading *recognition* reads ``block.text`` (not ``live_text``)
        so a fully struck heading is still classified — the strike
        cascade [D-037] needs the recognition. Value extraction (title,
        req_id) downstream applies the runs-over-text invariant.

        False-positive guards (numbering path):
          - text length capped (numbered list items in body text run long)
          - text doesn't end with sentence-terminal punctuation
        """
        method = self.profile.heading_detection.method
        if method == "docx_styles":
            return self._classify_heading_docx_styles(block)

        # Default / legacy: numbering-pattern path.
        if not self._num_re:
            return "", ""

        text = block.text.strip()
        m = self._num_re.match(text)
        if not m:
            return "", ""

        # Length guard: real headings are short. Numbered list items in body
        # text typically run >200 chars and end with terminal punctuation —
        # those are not headings.
        if len(text) > _HEADING_MAX_LEN:
            return "", ""
        if text and text[-1] in ".!?":
            # Allow trailing period only on very short titles like "1.1.1.".
            if len(text) > 80:
                return "", ""

        # Extract the section number with a local, known-good regex —
        # independent of the profile's gate-pattern capture shape.
        sec_m = _SECTION_NUM_RE.match(text)
        if not sec_m:
            return "", ""
        section_num = sec_m.group(0).rstrip(".")
        title = text[sec_m.end():].lstrip()

        return section_num, title

    def _classify_heading_docx_styles(
        self, block: ContentBlock
    ) -> tuple[str, str]:
        """``docx_styles`` classification path — style → depth, TOC → section_number.

        Returns ``(section_number, title)`` where ``section_number`` may
        be empty when no TOC entry pairs with this heading. Caller is
        responsible for treating empty section_number as a non-dedup
        case (one synthetic Requirement per such heading).
        """
        # Style is the recognition signal. ``block.text`` may be empty
        # (image-only heading) — that's still a non-heading from this
        # method's perspective.
        sm = _DOCX_HEADING_STYLE_RE.match(block.style or "")
        if not sm:
            return "", ""
        try:
            depth = int(sm.group(1))
        except (IndexError, ValueError):
            return "", ""

        # Title text — runs-aware. When ``anchor=last_run`` and the
        # last run *is* a req_id, strip it from the title.
        title = self._heading_title_text(block)

        # Pair against the TOC index.
        req_id = self._heading_req_id(block)
        toc_entry = self._toc_lookup(depth, req_id, title)
        section_num = toc_entry.section_number if toc_entry else ""

        if toc_entry is None:
            self._toc_pair_misses.append(
                TocPairMiss(
                    block_index=block.position.index,
                    page=block.position.page,
                    depth=depth,
                    req_id=req_id,
                    title=title,
                )
            )
            self._log_format_error(
                "toc_pair_miss", block,
                depth=depth,
                req_id=req_id or "<none>",
                title=title,
                note="body heading not in TOC — likely heading-styled non-section text or unlisted appendix",
            )

        return section_num, title

    def _heading_title_text(self, block: ContentBlock) -> str:
        """Heading title text per the runs-over-text invariant.

        When ``anchor="last_run"`` AND the last run solo-matches the
        req_id pattern, that run is the requirement_id and is stripped
        from the title. Otherwise, the full live text is returned.
        Falls back to ``block.text`` when ``runs`` is empty — and in
        that case, if the text's tail matches the req_id pattern, the
        match is stripped so TOC pair-by-title fallback can still
        succeed (the source-doc formatting error is logged separately
        by ``_heading_req_id``).
        """
        if (
            self.profile.requirement_id.anchor == "last_run"
            and block.runs
            and self._req_id_anchored_re
            and self._req_id_anchored_re.match(block.runs[-1].text)
        ):
            return "".join(
                r.text for r in block.runs[:-1] if not r.struck
            ).strip()

        text = block.live_text().strip()

        # Last-run anchor missed (runs empty OR single-run with
        # everything concatenated): strip a trailing req_id from
        # text so the TOC pair-by-title fallback can match against
        # TOC's clean title. Mirrors the ``len(runs) <= 1`` gate in
        # ``_heading_req_id`` — multi-run headings with an inline
        # req_id mention keep their full title.
        if (
            self.profile.requirement_id.anchor == "last_run"
            and len(block.runs) <= 1
            and self._req_id_re
            and text
        ):
            ids = self._req_id_re.findall(text)
            if ids:
                last_id = ids[-1]
                last_pos = text.rfind(last_id)
                if last_pos > 0:
                    text = text[:last_pos].strip()

        return text

    def _extract_priority(self, text: str) -> tuple[str, str]:
        """Extract a priority marker from heading text (FR-31).

        Returns (priority, cleaned_text). The matched substring is stripped
        from the title and the regex's first capture group becomes the
        priority value (uppercased so different casings collapse). When the
        profile pattern is empty or doesn't match, returns ("", text).
        """
        if self._priority_re is None or not text:
            return "", text
        m = self._priority_re.search(text)
        if not m:
            return "", text
        priority = (m.group(1) if m.groups() else m.group(0)).strip().upper()
        # Strip the matched span and collapse any double-spaces it leaves.
        cleaned = (text[:m.start()] + text[m.end():]).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return priority, cleaned

    def _classify_zone(self, section_number: str) -> str:
        """Classify a section into a document zone using profile rules."""
        for pattern, zone_type in self._zone_map.items():
            if re.match(pattern, section_number):
                return zone_type
        return ""

    @staticmethod
    def _append_text(section: Requirement, text: str) -> None:
        """Append text to a section, with paragraph separation."""
        text = text.strip()
        if not text:
            return
        if section.text:
            section.text += "\n" + text
        else:
            section.text = text

    # ── Parent-child linking ────────────────────────────────────────

    def _link_parents(self, sections: list[Requirement]) -> None:
        """Build parent-child relationships and hierarchy paths."""
        # Build lookup: section_number → Requirement
        by_section: dict[str, Requirement] = {}
        for sec in sections:
            by_section[sec.section_number] = sec

        for sec in sections:
            if not sec.section_number:
                continue

            # Find parent section number by trimming the last component
            parts = sec.section_number.split(".")
            if len(parts) > 1:
                parent_num = ".".join(parts[:-1])
                parent = by_section.get(parent_num)
                if parent:
                    sec.parent_section = parent_num
                    sec.parent_req_id = parent.req_id
                    if sec.req_id not in parent.children:
                        parent.children.append(sec.req_id or sec.section_number)

            # Build hierarchy path
            sec.hierarchy_path = self._build_hierarchy_path(
                sec.section_number, by_section
            )

    @staticmethod
    def _build_hierarchy_path(
        section_number: str,
        by_section: dict[str, Requirement],
    ) -> list[str]:
        """Build the hierarchy path from root to this section."""
        parts = section_number.split(".")
        path = []
        for i in range(1, len(parts) + 1):
            ancestor_num = ".".join(parts[:i])
            ancestor = by_section.get(ancestor_num)
            if ancestor:
                path.append(ancestor.title or ancestor_num)
        return path

    # ── Cross-reference extraction ──────────────────────────────────

    def _extract_cross_refs(
        self, text: str, own_plan_id: str
    ) -> CrossReferences:
        """Extract cross-references from section text."""
        if not text:
            return CrossReferences()

        refs = CrossReferences()

        # Internal requirement ID references
        if self._req_id_re:
            for rid in self._find_req_ids(text):
                ref_plan = self._extract_plan_id_from_req(rid)
                if ref_plan is not None:
                    if ref_plan != own_plan_id:
                        if ref_plan not in refs.external_plans:
                            refs.external_plans.append(ref_plan)
                    else:
                        if rid not in refs.internal:
                            refs.internal.append(rid)

        # Standards references — with section numbers
        seen_std: set[tuple[str, str]] = set()
        for m in self._std_detail_re.finditer(text):
            spec = f"3GPP TS {m.group(1)}"
            section = m.group(2)
            key = (spec, section)
            if key in seen_std:
                continue
            seen_std.add(key)
            # Try to find release nearby
            release = ""
            context = text[max(0, m.start() - 100):m.end() + 100]
            rm = self._std_release_re.search(context)
            if rm:
                release = f"Release {rm.group(1)}"
            refs.standards.append(
                StandardsRef(spec=spec, section=section, release=release)
            )

        # Standards references — spec only (no section number)
        spec_only_re = re.compile(r"3GPP\s+TS\s+(\d[\d.]*\d)")
        for m in spec_only_re.finditer(text):
            spec = f"3GPP TS {m.group(1)}"
            if not any(s.spec == spec for s in refs.standards):
                release = ""
                context = text[max(0, m.start() - 100):m.end() + 100]
                rm = self._std_release_re.search(context)
                if rm:
                    release = f"Release {rm.group(1)}"
                refs.standards.append(
                    StandardsRef(spec=spec, section="", release=release)
                )

        return refs

    def _extract_plan_id_from_req(self, req_id: str) -> str | None:
        """Extract the plan ID component from a requirement ID using profile config."""
        if self._req_id_plan_pos is None:
            return None
        parts = req_id.split(self._req_id_separator)
        pos = self._req_id_plan_pos
        if pos < len(parts):
            return parts[pos]
        return None

    # ── Standards releases ──────────────────────────────────────────

    def _extract_standards_releases(
        self, doc: DocumentIR
    ) -> dict[str, str]:
        """Extract referenced standards releases from the document.

        Scans all text for patterns like:
        - "3GPP TS 24.301 Release 10"
        - "Release 10 version of 3GPP TS 24.301"
        """
        releases: dict[str, str] = {}

        # Pattern 1: "3GPP TS X.X ... Release N"
        pat1 = re.compile(r"3GPP\s+TS\s+(\d[\d.]*\d).*?[Rr]elease\s+(\d+)")
        # Pattern 2: "Release N ... 3GPP TS X.X" (VZW style)
        pat2 = re.compile(r"[Rr]elease\s+(\d+)\s+(?:version\s+of\s+)?3GPP\s+TS\s+(\d[\d.]*\d)")

        for b in doc.content_blocks:
            for m in pat1.finditer(b.text):
                spec = f"3GPP TS {m.group(1)}"
                release = f"Release {m.group(2)}"
                if spec not in releases:
                    releases[spec] = release
            for m in pat2.finditer(b.text):
                spec = f"3GPP TS {m.group(2)}"
                release = f"Release {m.group(1)}"
                if spec not in releases:
                    releases[spec] = release

        return releases
