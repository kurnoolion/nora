# profiler

**Purpose**
Standalone, LLM-free document-structure profiler. Analyzes representative `DocumentIR`s from [extraction](../extraction/MODULE.md) and emits a `DocumentProfile` — a JSON artifact of heading rules, requirement-ID patterns, zone classifications, header/footer filters, cross-reference patterns, applicability detection rules, definitions-section detection rules, TOC detection rules, and priority-marker detection rules. The profile drives the generic structural parser, replacing per-MNO parser code with human-editable configuration. Serves FR-2 (LLM-free profiling), FR-31 (priority markers), FR-32 (applicability detection rules), FR-33 (`ignore_strikeout` toggle), FR-34 (TOC detection), FR-35 (definitions section + entry detection). Profile JSON files are committed under `customizations/profiles/` per D-024 (human-curated, AI-scaffolded).

For the human-annotator's guide to the 13 annotation kinds the Bootstrap web UI captures (and which the profiler turns into rules through the Cline → Teacher-LLM loop), see [`ANNOTATIONS.md`](ANNOTATIONS.md).

**Public surface**
- `load_substituted_profile(profile_path, env_dir=None) -> DocumentProfile` (profile_substitute.py) [D-062] — drop-in replacement for `DocumentProfile.load_json()` at the parser boundary. Loads the profile, finds a mapping (snapshot at `customizations/mappings/<profile_stem>.json` or fallback to `<env_dir>/state/cline-mapping.json`), and substitutes placeholders in every regex-string field. Specific placeholders (e.g. `<MNO0>`) → `re.escape(<mapped value>)`. Generic placeholders (`<MNO>`, `<PLAN>`, `<REL>`, `<DIGITS>`) → regex character class for the token's typical shape. No-op when no mapping found (covers public-corpus profiles like `vzw_oa_profile.json`).
- `substitute_placeholders(profile, mapping) -> DocumentProfile` (profile_substitute.py) — pure function variant of the above; returns a deep-copied profile with substitution applied.
- `find_mapping_file(profile_path, env_dir=None) -> Path | None` (profile_substitute.py) — discovery chain: snapshot at `customizations/mappings/<profile_stem>.json` → snapshot at `customizations/mappings/<_provenance.bootstrap_id>.json` (content-anchored fallback per D-075, for profiles that have been copied to a generic name like `out/profile/profile.json`) → `<env_dir>/state/cline-mapping.json` → None.
- `GENERIC_PLACEHOLDERS` (profile_substitute.py) — `{<DIGITS>: \d+, <MNO>: [A-Z]{2,4}, <PLAN>: [A-Z0-9_]+, <REL>: [A-Za-z0-9-]+}`.
- `DocumentProfiler` (profiler.py) — `create_profile(docs, profile_name="") -> DocumentProfile`; also `update_profile()`, `validate_profile()` (coverage check against held-out docs)
- `DocumentProfile` (profile_schema.py) — full profile container with `to_dict`, `save_json`, `load_json`
- Profile subcomponents: `HeadingLevel`, `HeadingDetection`, `RequirementIdPattern`, `MetadataField`, `PlanMetadata`, `DocumentZone`, `HeaderFooter`, `CrossReferencePatterns`, `BodyText`, `ApplicabilityDetection` (FR-32 [D-030]), `TocDetection`, `RevhistDetection` (D-073)
- New `DocumentProfile` fields: `ignore_strikeout: bool = True` (FR-33 [D-031]); `applicability_detection` (FR-32); `toc_detection_pattern: str` + `toc_page_threshold: float` (FR-34); `toc_detection: TocDetection` (style-driven TOC, generic-rules pivot); `definitions_entry_pattern: str` (FR-35 [D-032]); `definitions_table_term_column: str` + `definitions_table_definition_column: str` (table-form glossary); `revision_history_label_pattern: str` (FR-34 [D-035]; renamed from `revision_history_heading_pattern` — old field name still loads via migration in `_from_dict`); `revhist_table_header_pattern: str` (D-069 — joined-header regex for bare-table revhists); `revhist_detection: RevhistDetection` (D-073 — signal-based score path with position + vocab + cell-fingerprint signals; opt-in via `enabled=true`); `heading_detection.definitions_table_header_pattern: str` (D-069 — joined-header regex for bare-table glossaries when no section title matches); `content_start_section: str = ""` (D-DRAFT-4 — top-level chapter number where requirement content begins; parser drops everything before the first real-heading-font block numbered this, skipping front page/TOC/intro chapters; empty = disabled); `build_context: str = "none"` (D-DRAFT-5 — how consumers assemble a requirement's enclosing-section context: `"none"` / `"path"` (section numbers + titles) / `"path_and_content"` (also each ancestor's body); stamped onto `RequirementTree.build_context`; assembly happens in consumers via `parser.build_context_string`, not the parser); `build_context_max_chars: int = 0` (0 = uncapped — per-ancestor body cap for `path_and_content` context so one huge ancestor section can't push a requirement chunk past the embedder input limit; stamped onto `RequirementTree.build_context_max_chars`); `exclude_section_pattern: str = ""` (D-DRAFT-13 — regex on a section *title*; matching sections + descendants are dropped from the parsed tree, so they never become Requirements or RAG chunks; for non-normative headings like `REFERENCES` / traceability matrices; runs after `reference_list_section_pattern` extraction so a REFERENCES section still populates `reference_list_map` first; empty = no-op); `content_end_marker: str = ""` (D-DRAFT-13 — regex matched per body *line*; truncates a requirement's text before the first matching line and drops that requirement's trailing `tables`/`images`; for a non-normative appendix glued onto the last real requirement — e.g. a traceability matrix + test-case tables beginning at the marker rather than under its own heading; supports placeholders like `<TRACEABILITY>`; empty = no-op); `layout_provider: str = ""` (mno-c-ingestion — opt-in table/figure structure engine for PDF extraction, e.g. `"docling"`; `""` = current pdfplumber path); `detect_text_tables: bool = False` (geometric borderless-table detection in the PDF extractor); `header_footer_margin_mode: str = "blanket"` (`"blanket"` drops all top/bottom-margin text; `"pattern_only"` drops only header/footer-pattern matches — for corpora with requirement headings at the top of the page); `enable_table_anchored_extraction: bool = True` (table-anchored req-id mining; when off, provider tables keep HTML only and drop the flat headers/rows grid)
- New `RequirementIdPattern` fields: `anchor: str = "trailing_text" | "last_run" | "leading_text"`; `normalize: str = "none" | "upper"` (run-precise req_id extraction + mixed-case normalization); `requirement_type_pattern: str = ""` (mno-c-ingestion — narrow regex over captured req_ids; a node `is_requirement` only when its req_id matches, so a broadened `pattern` can capture structural-section ids too; empty = any req_id counts, back-compat); `id_label_pattern: str = ""` (mno-c-ingestion — heading's OWN req_id taken ONLY from an `ID:`-labeled marker, capture group 1; overrides `anchor` and also gates the small-font id-block and body-text scavenge paths, so a bare req-id reference — e.g. a release-notes citation — is never mistaken for a definition; empty = anchor behavior)
- New `HeadingDetection` fields: `priority_marker_pattern: str` (FR-31); `definitions_section_pattern: str` (FR-35 [D-032]); `require_heading_font_for_numbering: bool = False` (mno-c-ingestion — a number-prefixed block is promoted to a section only with heading-like font, so borderless-table rows don't leak into the section tree); `priority_requires_req_id: bool = False` (priority markers mined only from headings that carry a req_id; structural headings keep marker-shaped tokens in their title); `title_strip_pattern: str = ""` (regex stripped from every heading title — e.g. a trailing `ID: <id>` marker — so requirement and structural titles are clean)
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
- `_LLM_CREATE_SYSTEM` — constant — internal
- `_LLM_CREATE_TRAILER` — constant — internal
- `_LLM_MAX_TOKENS` — constant — internal
- `_LLM_PROMPT_TEMPLATE` — constant — internal
- `_MAX_DOC_CHARS` — constant — internal
- `_MAX_REGEX_LEN` — constant — internal
- `_REPETITION_THRESHOLD` — constant — internal
- `_RUNAWAY_CHUNK_SIZES` — constant — internal
- `_check_regex` — function — internal — Return (status, note) for a regex string.
- `_create_profile_via_llm` — function — internal — Call a local Ollama model to bootstrap a DocumentProfile from `files`.
- `_extract_json_object` — function — internal — Find the first balanced top-level JSON object in `text`.
- `_format_ir_lines` — function — internal — One or two lines describing an IR — no proprietary content.
- `_format_profile_lines` — function — internal — Compact summary of the emitted profile (no proprietary content).
- `_is_runaway` — function — internal — Detect runaway repetition (a small chunk repeating >= threshold times).
- `_recover_unterminated` — function — internal — Best-effort recovery of JSON with an unterminated string at EOF.
- `_render_block_for_prompt` — function — internal — One-line, structurally-annotated rendering of a ContentBlock for the LLM.
- `_render_ir_for_prompt` — function — internal — Render a DocumentIR as text-with-structural-hints for the LLM, truncated to max_chars.
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
- `RevhistDetection` — dataclass — pub — Signal-based revision-history TABLE detection.
- `TocDetection` — dataclass — pub — Style-driven TOC detection (DOCX-authoritative).
- `_load_revhist_detection` — function — internal — Build a RevhistDetection with per-field defaults preserved when

`profile_substitute.py`
- `GENERIC_PLACEHOLDERS` — constant — pub
- `_load_mapping_dict` — function — internal — Read mapping JSON. Both shapes are accepted:
- `_normalize_mapping` — function — internal — Convert any of the supported on-disk shapes into ``{NAME: real}``.
- `_project_root_from_profile` — function — internal — Resolve the project root that owns ``customizations/`` for *profile_path*.
- `find_mapping_file` — function — pub — Locate the mapping JSON for *profile_path*, in priority order:
- `load_substituted_profile` — function — pub — Load a profile and apply placeholder substitution.
- `logger` — constant — pub
- `substitute_placeholders` — function — pub — Return a copy of *profile* with placeholders substituted.

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
  - `_detect_revision_history_pattern` — method — internal — Learn the heading phrase that introduces a revision/change-log
  - `_detect_section_numbering` — method — internal — Detect section numbering scheme from heading text.
  - `_log_profile_summary` — staticmethod — internal
  - `create_profile` — method — pub — Create a new profile from representative documents.
  - `update_profile` — method — pub — Update an existing profile with additional representative documents.
  - `validate_profile` — method — pub — Validate a profile against a document. Returns a report dict.
- `logger` — constant — pub
<!-- END:STRUCTURE -->

**Depends on**
[models](../models/MODULE.md) (for `DocumentIR`, `BlockType`, `ContentBlock`, `FontInfo`), [extraction](../extraction/MODULE.md) (upstream producer of the IRs it consumes).

**Depended on by**
[parser](../parser/MODULE.md), [corrections](../corrections/MODULE.md) (imports `DocumentProfile` for correction IO), [pipeline](../pipeline/MODULE.md) (profile stage).
