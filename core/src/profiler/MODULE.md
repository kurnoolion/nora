# profiler

**Purpose**
Standalone, LLM-free document-structure profiler. Analyzes representative `DocumentIR`s from [extraction](../extraction/MODULE.md) and emits a `DocumentProfile` — a JSON artifact of heading rules, requirement-ID patterns, zone classifications, header/footer filters, cross-reference patterns, applicability detection rules, definitions-section detection rules, TOC detection rules, and priority-marker detection rules. The profile drives the generic structural parser, replacing per-MNO parser code with human-editable configuration. Serves FR-2 (LLM-free profiling), FR-31 (priority markers), FR-32 (applicability detection rules), FR-33 (`ignore_strikeout` toggle), FR-34 (TOC detection), FR-35 (definitions section + entry detection). Profile JSON files are committed under `customizations/profiles/` per D-024 (human-curated, AI-scaffolded).

**Public surface**
- `DocumentProfiler` (profiler.py) — `create_profile(docs, profile_name="") -> DocumentProfile`; also `update_profile()`, `validate_profile()` (coverage check against held-out docs)
- `DocumentProfile` (profile_schema.py) — full profile container with `to_dict`, `save_json`, `load_json`
- Profile subcomponents: `HeadingLevel`, `HeadingDetection`, `RequirementIdPattern`, `MetadataField`, `PlanMetadata`, `DocumentZone`, `HeaderFooter`, `CrossReferencePatterns`, `BodyText`, `ApplicabilityDetection` (FR-32 [D-030])
- New `DocumentProfile` fields: `ignore_strikeout: bool = True` (FR-33 [D-031]); `applicability_detection` (FR-32); `toc_detection_pattern: str` + `toc_page_threshold: float` (FR-34); `definitions_entry_pattern: str` (FR-35 [D-032]); `revision_history_heading_pattern: str` (FR-34 [D-035])
- New `HeadingDetection` fields: `priority_marker_pattern: str` (FR-31); `definitions_section_pattern: str` (FR-35 [D-032])
- `profile_cli.main` — CLI: `create | update | validate`

**Invariants**
- LLM-free. Derivation uses only heuristics (font-size clustering, regex mining, frequency analysis) — runs offline, deterministically, and cheaply.
- `DocumentProfile` JSON is human-editable. Any engineer can open the file, fix a regex, save, and re-run downstream — no code change needed. This is the corrections-override pattern's upstream half.
- `create_profile` does not read or write anything outside the returned dataclass; I/O lives in CLI + `save_json`.
- Profile uses `method="font_size_clustering"` for PDF inputs and `method="docx_styles"` for DOCX — set by `_detect_headings` based on the presence of explicit style info in source blocks.
- No per-MNO branching. Every MNO gets the same profiler; differences are captured in the emitted profile.

**Key choices**
- Font clustering over fixed font-size thresholds — handles documents that ship with unusual base sizes without per-document tuning.
- Regex mining for requirement-ID patterns — surfaces the token prefix, numbering depth, and delimiter from observed samples; easier for a human to review than a hand-written regex.
- Zone classification uses section-number regex + heading-text keyword match (e.g., "hardware", "scenarios") — simple, auditable, and editable.
- Profile is a flat JSON file per document family, not one per file — one set of rules covers a whole MNO × release batch.
- Validation mode reports coverage (headings matched / expected, req IDs found / sample size) rather than pass/fail, so reviewers can judge quality at a glance.
- Per-document content (extracted definitions term→expansion pairs) is **not** stored in the profile — those land on `RequirementTree.definitions_map`. Profile carries only the *detection rules*; locality is preserved at the parsed-tree level [D-032].
- Pattern-based detection only (no keyword bag-of-words) for applicability and definitions; corrections workflow extends patterns by JSON edit, not code change [D-030, D-032].
- Revision-history heading detection is profile-driven with corpus-narrowing [D-035]: the schema default `(?i)^\s*(revision|change|version|document)\s+(history|log)\s*$` covers common MNO labels; `_detect_revision_history_pattern` walks paragraph-then-table candidates and tightens the regex (whitespace-tolerant) to the most-frequent observed phrasing in the corpus. New corpora work out-of-the-box on the broad default; profiler narrowing makes audits more legible.

**Non-goals**
- Not a parser — applying a profile to a document is [parser](../parser/MODULE.md)'s job.
- No training or ML — heuristics only. If the heuristics miss something, the fix is to edit the JSON profile, not to retrain.
- No per-block semantic classification beyond heading vs body vs zone — finer semantics (requirement vs note vs example) are the parser's concern.
- The LLM-free invariant applies to `DocumentProfiler` (the core). The sibling `profile_debug` CLI in this module has separate modes (`--emit-prompt`, `--create`, `--validate --recover`) that are explicitly LLM-driven *bootstrap helpers* for doc families where heuristics are weak. Output of those modes lands in `<env_dir>/corrections/profile.json` and is treated as a human-curated correction by the pipeline (D-011 / FR-15) — it doesn't enter the LLM-free derivation path.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._

`profile_cli.py`
- `cmd_create` — function — pub
- `cmd_update` — function — pub
- `cmd_validate` — function — pub
- `main` — function — pub

`profile_debug.py`
- `_check_regex` — function — internal — Return (status, note) for a regex string.
- `_create_profile_via_llm` — function — internal — Call a local Ollama model to bootstrap a DocumentProfile from `files`.
- `_extract_json_object` — function — internal — Find the first balanced top-level JSON object in `text`.
- `_format_ir_lines` — function — internal — One or two lines describing an IR — no proprietary content.
- `_format_profile_lines` — function — internal — Compact summary of the emitted profile (no proprietary content).
- `_is_runaway` — function — internal — Detect runaway repetition (a small chunk repeating >= threshold times).
- `_LLM_CREATE_SYSTEM` — constant — internal
- `_LLM_CREATE_TRAILER` — constant — internal
- `_LLM_MAX_TOKENS` — constant — internal
- `_LLM_PROMPT_TEMPLATE` — constant — internal
- `_MAX_DOC_CHARS` — constant — internal
- `_MAX_REGEX_LEN` — constant — internal
- `_recover_unterminated` — function — internal — Best-effort recovery of JSON with an unterminated string at EOF.
- `_render_block_for_prompt` — function — internal — One-line, structurally-annotated rendering of a ContentBlock for the LLM.
- `_render_ir_for_prompt` — function — internal — Render a DocumentIR as text-with-structural-hints for the LLM, truncated to max_chars.
- `_REPETITION_THRESHOLD` — constant — internal
- `_RUNAWAY_CHUNK_SIZES` — constant — internal
- `_safe_style` — function — internal — Strip whitespace and truncate long style names for compact rendering.
- `_strip_markdown_fences` — function — internal — Strip ```json ... ``` or ``` ... ``` fences if the LLM wrapped its response.
- `_validate_profile` — function — internal — Validate (and optionally sanitize) an LLM-emitted profile.json.
- `_walk_regex_fields` — function — internal — Walk every regex-valued field in the profile. Return issue list.
- `main` — function — pub

`profile_schema.py`
- `ApplicabilityDetection` — dataclass — pub — Rules for extracting form-factor applicability (FR-32 [D-030]).
- `BodyText` — dataclass — pub — Characteristics of normal body text.
- `CrossReferencePatterns` — dataclass — pub — Regex patterns for detecting cross-references in text.
- `DocumentProfile` — dataclass — pub — Complete document structure profile (TDD 5.2.3).
  - `_from_dict` — classmethod — internal
  - `load_json` — classmethod — pub
  - `save_json` — method — pub
  - `to_dict` — method — pub
- `DocumentZone` — dataclass — pub — Classification of a top-level document section.
- `HeaderFooter` — dataclass — pub — Rules for detecting and stripping headers and footers.
- `HeadingDetection` — dataclass — pub — Rules for detecting headings and their hierarchy.
- `HeadingLevel` — dataclass — pub — Detection rule for a single heading level.
- `MetadataField` — dataclass — pub — Location and pattern for a single metadata field.
- `PlanMetadata` — dataclass — pub — Rules for extracting plan-level metadata.
- `RequirementIdPattern` — dataclass — pub — Detected requirement ID pattern.

`profiler.py`
- `DocumentProfiler` — class — pub — Derive document structure profiles from representative documents.
  - `_collect_header_footer` — method — internal — Collect header/footer patterns already detected by the extractor.
  - `_derive_profile_name` — staticmethod — internal — Derive a profile name from the documents' MNO.
  - `_detect_body_text` — method — internal — Identify body text characteristics by frequency analysis.
  - `_detect_cross_references` — method — internal — Detect cross-reference patterns in document text.
  - `_detect_document_zones` — method — internal — Classify top-level sections into document zones.
  - `_detect_headings` — method — internal — Detect heading levels by font size clustering.
  - `_detect_plan_metadata` — method — internal — Detect plan metadata patterns from first-page content.
  - `_detect_requirement_ids` — method — internal — Mine requirement ID patterns from document text.
  - `_detect_section_numbering` — method — internal — Detect section numbering scheme from heading text.
  - `_log_profile_summary` — staticmethod — internal
  - `create_profile` — method — pub — Create a new profile from representative documents.
  - `update_profile` — method — pub — Update an existing profile with additional representative documents.
  - `validate_profile` — method — pub — Validate a profile against a document. Returns a report dict.
<!-- END:STRUCTURE -->

**Depends on**
[models](../models/MODULE.md) (for `DocumentIR`, `BlockType`, `ContentBlock`, `FontInfo`), [extraction](../extraction/MODULE.md) (upstream producer of the IRs it consumes).

**Depended on by**
[parser](../parser/MODULE.md), [corrections](../corrections/MODULE.md) (imports `DocumentProfile` for correction IO), [pipeline](../pipeline/MODULE.md) (profile stage).
