# parser

**Purpose**
Generic, profile-driven structural parser. Consumes a `DocumentIR` + `DocumentProfile` and emits a `RequirementTree` — the typed, hierarchical form of the document that every downstream stage (resolver, graph, taxonomy, vectorstore) reads. Serves FR-3 (profile-driven generic parser; no per-MNO code paths), FR-31 (priority extraction), FR-32 (applicability inheritance), FR-33 (struck-block drop), FR-34 (TOC drop), FR-35 (definitions extraction). Implements D-003 (MNO behavior lives in profile, not code), D-030 (applicability inheritance), D-031 (struck-block drop semantics), D-032 (per-document definitions map on `RequirementTree`).

**Public surface**
- `GenericStructuralParser` (structural_parser.py) — the parser; consumes `DocumentIR` + `DocumentProfile`, returns `RequirementTree`
- `RequirementTree` (structural_parser.py) — top-level output: plan-level metadata + flat `requirements` list + `definitions_map: dict[str, str]` (FR-35 [D-032]) + `parse_stats` (incl. `struck_blocks_dropped: int` per [D-031], `toc_blocks_dropped: int` per FR-34, `defs_extracted: int` per FR-35); `to_dict`, `save_json`, `load_json`
- `Requirement` — single requirement node: `req_id`, `section_number`, `title`, `parent_req_id`, `parent_section`, `hierarchy_path`, `zone_type`, `priority` (FR-31), `applicability: list[str]` (FR-32 [D-030]), `text`, `tables`, `images`, `children`, `cross_references`
- `TableData`, `ImageRef`, `StandardsRef`, `CrossReferences` — nested types
- `parse_cli.main` — CLI entrypoint (`python -m core.src.parser.parse_cli`)

**Invariants**
- Parser is **LLM-free**. Any downstream LLM enrichment (taxonomy, query synthesis) happens in its own module; the structural layer stays deterministic and cheap to re-run.
- No per-MNO branches. A new MNO is onboarded by running the profiler on its docs, editing the resulting profile, and re-running the parser — no parser code change.
- `RequirementTree.requirements` is a flat list in document order. Parent/child relationships are encoded via `parent_req_id` / `children` ID references; consumers reconstruct the tree if they need traversal.
- **Every Requirement is anchored from exactly one source block** — either a paragraph anchor (heading-style block, or a small-font standalone-ID paragraph), or a table-cell anchor (a row whose cell matches the profile's `requirement_id.pattern`). Paragraph anchors win on duplicate `req_id`s; the table-anchor pass dedups against the paragraph-anchor set.
- Table-anchored Requirements have `section_number=""` by design — they're addressed by `req_id` and linked to their owning paragraph-anchored section via `parent_section` / `parent_req_id`. Consumers must not assume `section_number` is non-empty.
- `hierarchy_path` mirrors the heading chain from root down to the requirement — used by the graph and vectorstore to preserve structural context when chunking or indexing. Table-anchored Requirements inherit `hierarchy_path` from their parent paragraph-anchored section.
- Cross-references are structurally detected (regex from profile) but **not resolved** here. `CrossReferences.internal/external_plans/standards` are raw strings; [resolver](../resolver/MODULE.md) turns them into concrete manifests.
- `zone_type` is copied from the profile's `DocumentZone` match — used by the graph to route requirements to the right subgraph partition. Table-anchored Requirements inherit `zone_type` from their parent.
- `applicability` resolves in document order via a post-pass after `_link_parents`: explicit value on the requirement wins; else inherit from `parent_section`'s already-resolved value; else fall back to the document-level applicability section's value, if any; else empty list (downstream stages do not filter on empty) [D-030].
- Struck-through blocks (`block.font_info.strikethrough == True`) are skipped entirely by `_build_sections` when `profile.ignore_strikeout == True` (default). The IR retains them; only the parser-level drop is gated by the profile toggle [D-031].
- TOC entries (matching `profile.toc_detection_pattern`) are skipped before heading classification. Pages where ≥`toc_page_threshold` of blocks match are treated as TOC pages and dropped wholesale (FR-34).
- The definitions / acronyms / glossary section is **kept** in the parsed tree; extraction populates `RequirementTree.definitions_map` *in addition to* leaving the section as a queryable Requirement [D-032].
- Table-anchored Requirements inherit `applicability` (and `zone_type` as before) from their parent paragraph-anchored section via `_propagate_hierarchy_to_table_reqs` [D-030].

**Key choices**
- One `RequirementTree` per source document (not per MNO or release) — the unified graph is assembled later; keeping parse output 1:1 with input keeps re-runs incremental.
- Flat requirements list with ID refs rather than a nested tree — trivially JSON-serializable and safe to load partially (load header, skip body) for large plans.
- Parser reads the profile but never writes it — the profile is an input, edited only by the profiler or by a human. This enforces the corrections-override flow.
- Tables preserved with source tag (`inline` vs `embedded_xlsx`) so the graph and query layer can score table-derived answers differently from prose-derived ones.
- **Two req-ID anchor sources**: paragraph anchors (heading-block, pending-id resolution, or inline body-text id) and table-cell anchors (column-1-of-row first; all-cells fallback; one anchor per row max). Necessary because telecom requirement docs frequently define requirements through cross-reference tables — IDs that exist in tables but never as paragraph-form anchors. Detected against the same `requirement_id.pattern` regex from the profile, so adding a new MNO still requires no parser code change. Paragraph anchors win on duplicate `req_id`s to keep precedence deterministic.
- New parser passes added in document order (each strictly post-`_link_parents`): `_apply_applicability` [D-030] → `_extract_definitions` [D-032]. Strike-block and TOC drops happen earlier inside `_build_sections` because they affect what reaches the heading classifier.

**Non-goals**
- No semantic enrichment (feature tagging, standards resolution, embeddings) — done downstream.
- No content rewriting or summarization — parser preserves the source text verbatim inside each `Requirement.text`.
- No OCR or layout heuristics — those are extraction's job; parser trusts the IR.
- No schema migration — if `DocumentProfile` schema evolves, both sides change together.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._

`parse_cli.py`
- `main` — function — pub

`structural_parser.py`
- `CrossReferences` — dataclass — pub
- `GenericStructuralParser` — class — pub — Profile-driven structural parser for requirement documents.
  - `__init__` — constructor — pub
  - `_append_text` — staticmethod — internal — Append text to a section, with paragraph separation.
  - `_build_hierarchy_path` — staticmethod — internal — Build the hierarchy path from root to this section.
  - `_build_sections` — method — internal — Build the flat list of sections with hierarchy info from content blocks.
  - `_classify_heading` — method — internal — Check if a block is a heading. Returns (section_number, title) or ("", "").
  - `_classify_zone` — method — internal — Classify a section into a document zone using profile rules.
  - `_create_table_anchored_req` — method — internal — Append a Requirement node anchored by a table row.
  - `_extract_cross_refs` — method — internal — Extract cross-references from section text.
  - `_extract_plan_id_from_req` — method — internal — Extract the plan ID component from a requirement ID using profile config.
  - `_extract_plan_metadata` — method — internal — Extract plan-level metadata using profile patterns.
  - `_extract_standards_releases` — method — internal — Extract referenced standards releases from the document.
  - `_extract_table_anchored_reqs` — method — internal — Detect req-IDs in table cells; append child Requirement nodes to `sections`.
  - `_is_req_id_block` — method — internal — Check if a block is a standalone requirement ID (small font).
  - `_link_parents` — method — internal — Build parent-child relationships and hierarchy paths.
  - `_propagate_hierarchy_to_table_reqs` — method — internal — Copy parent's hierarchy_path to table-anchored Requirements.
  - `parse` — method — pub — Parse a document IR into a structured requirement tree.
- `ImageRef` — dataclass — pub
- `Requirement` — dataclass — pub
- `RequirementTree` — dataclass — pub
  - `load_json` — classmethod — pub
  - `save_json` — method — pub
  - `to_dict` — method — pub
- `StandardsRef` — dataclass — pub
- `TableData` — dataclass — pub
<!-- END:STRUCTURE -->

**Depends on**
[models](../models/MODULE.md), [profiler](../profiler/MODULE.md), [extraction](../extraction/MODULE.md) (as upstream producer).

**Depended on by**
[resolver](../resolver/MODULE.md), [vectorstore](../vectorstore/MODULE.md), [taxonomy](../taxonomy/MODULE.md), [standards](../standards/MODULE.md), [graph](../graph/MODULE.md), [pipeline](../pipeline/MODULE.md).
