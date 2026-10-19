"""Per-document parse transparency log.

Written to <env_dir>/reports/parse_log/<doc_id>_parse_log.json after
each parse run. Contains the full audit trail of what was dropped and
why, the glossary section location, and all extracted acronyms.

Block positions use position.index (sequential int from the IR) as the
primary key. page is included so reviewers can navigate the source PDF.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DroppedRange:
    """Contiguous run of dropped content blocks sharing a drop reason.

    Consecutive blocks (position.index N and N+1) with the same reason
    are merged into one range. Gaps or reason changes create a new entry.
    """
    block_start: int    # position.index of first dropped block
    block_end: int      # position.index of last dropped block (inclusive)
    page_start: int     # page of first dropped block
    page_end: int       # page of last dropped block
    block_count: int    # block_end - block_start + 1
    reason: str         # "toc" | "revhist" | "front_matter" | "text_strikethrough" | "cascade"


@dataclass
class SectionRange:
    """Block / page span for a named drop section (TOC, revision history).

    These are quick-access views into dropped_blocks — the same ranges
    also appear there. Use this when you only need one specific section.
    """
    block_start: int
    block_end: int
    page_start: int
    page_end: int


@dataclass
class GlossaryInfo:
    """Location and content summary of the definitions/acronyms section.

    The glossary section is NOT dropped — it stays in the parsed tree.
    block_start is the heading block; block_end is the last block before
    the next peer (same-or-shallower-depth) heading.
    """
    section_number: str
    section_title: str
    block_start: int
    block_end: int
    page_start: int
    page_end: int
    acronym_count: int


@dataclass
class AcronymEntry:
    """A single term → expansion pair extracted from the glossary section."""
    acronym: str
    expansion: str
    source: str  # "table" | "body_text"


@dataclass
class ParseLogSummary:
    toc_blocks_dropped: int = 0
    revhist_blocks_dropped: int = 0
    struck_blocks_dropped: int = 0
    cascade_blocks_dropped: int = 0
    total_dropped: int = 0
    glossary_acronyms: int = 0
    toc_pair_misses: int = 0
    frontmatter_blocks_dropped: int = 0
    """Body headings (docx_styles classification) that had no matching
    TOC entry. Generic-rules pivot — counter only here; the per-miss
    list (with titles, kept local) lives on ``ParseLog.toc_pair_misses``
    so compact reports stay free of proprietary content."""


@dataclass
class TocPairMissEntry:
    """A single body heading that didn't pair against the TOC index.

    Stored locally in the parse log (read by the architect via
    ``<env_dir>/reports/``); the *count* surfaces in compact RPT but
    titles never do — they may carry proprietary section names.
    """
    block_index: int
    page: int
    depth: int
    req_id: str
    title: str


@dataclass
class ParseLog:
    """Complete parse transparency log for one document.

    Written alongside the *_tree.json but NOT embedded in it — kept
    separate so the tree stays lean for downstream stages. Consumers
    that want to audit parser decisions read this file directly.
    """
    doc_id: str = ""
    source_file: str = ""
    mno: str = ""
    release: str = ""
    generated_at: str = ""

    # Every dropped block run in document order (includes TOC, revhist,
    # text_strikethrough, and cascade entries).
    dropped_blocks: list[DroppedRange] = field(default_factory=list)

    # Quick-access pointers for the two named drop sections.
    # These are also represented inside dropped_blocks.
    toc: SectionRange | None = None
    revision_history: SectionRange | None = None

    # Definitions/acronyms section (NOT dropped — kept in tree).
    glossary_section: GlossaryInfo | None = None

    # Extracted acronyms in document order.
    acronyms: list[AcronymEntry] = field(default_factory=list)

    # TOC ↔ body-heading pairing misses (generic-rules pivot). Per-miss
    # list with titles — local diagnostics only; never inlined into
    # compact RPT.
    toc_pair_misses: list[TocPairMissEntry] = field(default_factory=list)

    summary: ParseLogSummary = field(default_factory=ParseLogSummary)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
