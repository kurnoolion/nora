# standards

**Purpose**
3GPP standards ingestion — generic, release-aware, LLM-free (TDD §5.6, D-004). Collects references from parsed/resolved MNO docs, downloads the matching 3GPP spec archives, parses them into section trees, and extracts **only the referenced sections plus local context** (Option C "Hybrid Selective"). Every spec and version is derived from what the MNO docs actually cite — no hardcoded spec list, no per-MNO branching. Serves FR-5 (Option C Hybrid standards ingestion); covers NFR-14 (release-aware: per-release Standard_Section nodes).

**Public surface**
- Reference collection: `StandardsReferenceCollector` (reference_collector.py) — aggregates `SpecReference`s from resolver manifests and tree text into a `StandardsReferenceIndex`
- Spec resolution & download: `SpecResolver`, `ResolvedSpec` (spec_resolver.py) — maps `(spec, release)` to a 3GPP FTP URL; `SpecDownloader` (spec_downloader.py) — pluggable source (`huggingface` default | `3gpp`); shared cache layout
- Pluggable sources:
  - `HuggingFaceSource` (hf_source.py) — DOCX from the public GSMA/3GPP HF dataset; stdlib `urllib` only, no auth, no LibreOffice
  - 3GPP FTP path (inside `SpecDownloader._download_from_3gpp`) — ZIP archives with DOC/DOCX, with headless LibreOffice DOC→DOCX conversion when required
- Parsing: `SpecParser` (spec_parser.py) — turns a 3GPP DOCX into a `SpecDocument` with a nested `SpecSection` tree
- Selective extraction: `SectionExtractor` (section_extractor.py) — given referenced section numbers, returns those sections plus parents and local siblings as `ExtractedSpecContent`
- Schema (schema.py): `SpecReference`, `AggregatedSpecRef`, `StandardsReferenceIndex`, `SpecSection`, `SpecDocument`, `ExtractedSpecContent` — all dataclasses; top-level ones have `to_dict` / `save_json` / `load_json`
- CLI: `standards_cli.main` — collect | resolve | download | parse | extract; accepts `--standards-source {huggingface,3gpp}`

**Invariants**
- **Generic**: no hardcoded `ALLOWED_SPECS`. Every `AggregatedSpecRef` comes from `SpecReference`s the resolver collected from document text. Adding a new MNO or release requires no code change.
- **Release-aware**: the index key is `(spec, release)`, not spec alone. Different MNO releases often cite different 3GPP releases of the same spec, and they must produce separate ingested artifacts.
- **LLM-free**: parsing, section matching, and extraction are deterministic.
- Cache is path-structured under `data/standards/TS_{spec}/Rel-{N}/`. Manual placement is supported — if a DOC/DOCX is already in the cache dir, the downloader skips the download. This matters on offline work machines.
- **Sources are interchangeable post-download.** Both sources land DOCX in the same cache layout; `SpecParser` and `SectionExtractor` are source-agnostic. Switching sources between runs (or pointing different specs at different sources) is safe — the cache check arbitrates first.
- Source precedence (resolved by `core.src.env.config.resolve_standards_source`): CLI `--standards-source` > `NORA_STANDARDS_SOURCE` env var > `EnvironmentConfig.standards_source` > `"huggingface"` default.
- `ExtractedSpecContent` is the **only** artifact that downstream stages consume directly. The full `SpecDocument` is kept for debugging but is not what the graph/query layer indexes.
- Option C Hybrid Selective: extract referenced sections + their parent chain + immediate siblings. Not full specs (too large, low signal-to-noise), not just referenced sections (loses definitional context).

**Key choices**
- Two interchangeable sources, default = HuggingFace. HF (`GSMA/3GPP`) ships DOCX directly — no auth, no LibreOffice DOC→DOCX, single domain (easier proxy whitelisting on locked-down work hosts). The 3GPP FTP source remains for full-coverage / fallback (HF mirrors authoritative content but lags upstream by hours-to-days).
- DOCX (not MD) from HF: `SpecParser` already targets DOCX, so the HF MD side (only Rel-8..19, structurally derived from the same DOCX) buys nothing for this pipeline. Picking DOCX dodges a parser fork and a release-coverage gap.
- `SpecResolver` encodes the 3GPP FTP naming convention; the HF source uses the same `release_to_prefix` table to find the right version code in the HF tree listing — one source-of-truth for release encoding.
- DOC→DOCX via headless LibreOffice subprocess only on the 3GPP FTP path (older specs ship in .doc). HF source emits DOCX so this dependency is optional when source=huggingface.
- `StandardsReferenceCollector` accepts both structured `ResolvedStandardsRef` from the resolver manifests and raw regex hits from tree text — text hits catch citations the parser missed.
- Separate `SpecDocument` and `ExtractedSpecContent` schemas so a single spec parse can serve many selective extractions without re-parsing.

**Non-goals**
- Not a general document scraper — only 3GPP TS/TR specs. GSMA / RFC citations are out of scope for v1.
- Not a reference resolver for MNO-to-MNO cross-plan links — that's [resolver](../resolver/MODULE.md).
- No full-spec storage in the graph or vector store — Hybrid Selective is the contract; changing to full-spec ingestion is a D-004-level decision.
- No LLM-based section matching or summarization — if heuristics miss, the fix is to improve the regex or extend the collector, not add a language model.
- Not consuming the HF `marked/` Markdown side — DOCX-only path keeps `SpecParser` as the single parser implementation. (Revisit only if the HF MD coverage extends to all releases AND we have a downstream reason — e.g., human-review-friendly cleaned text — to justify a parallel parser.)

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._

`hf_source.py`
- `_DEFAULT_API_BASE` — constant — internal
- `_DEFAULT_DATASET_BASE` — constant — internal
- `_DEFAULT_TIMEOUT` — constant — internal
- `HuggingFaceSource` — class — pub — Download 3GPP spec DOCX files from the GSMA/3GPP HuggingFace dataset.
  - `__init__` — constructor — pub
  - `_download_file` — method — internal — Stream-download a URL to `dest`. Cleans up partial files on failure.
  - `_list_directory` — method — internal — Return filenames at the HF tree path. Empty list on error.
  - `_pick_latest_docx` — staticmethod — internal — Pick the latest version DOCX matching `{compact}-{prefix}*.docx`.
  - `download` — method — pub — Download a DOCX for `(spec_number, release_num)` into `dest_dir`.

`reference_collector.py`
- `_AccEntry` — class — internal — Accumulator for aggregating references.
  - `__init__` — constructor — pub
- `_clean_spec_number` — function — internal — Normalize a spec number: strip prefix, trailing dots.
- `_parse_release_num` — function — internal — Extract numeric release from strings like 'Release 11', 'Rel-15'.
- `_RELEASE_PATTERN` — constant — internal
- `_SECTION_PATTERNS` — constant — internal
- `StandardsReferenceCollector` — class — pub — Collect and aggregate standards references from requirement documents.
  - `_collect_from_manifest` — method — internal — Extract spec references from a resolved cross-reference manifest.
  - `_collect_sections_from_tree` — method — internal — Extract section-level references by scanning requirement text.
  - `_resolve_files` — staticmethod — internal
  - `collect` — method — pub — Collect all standards references and produce an aggregated index.

`schema.py`
- `AggregatedSpecRef` — dataclass — pub — Aggregated reference for a unique (spec, release) pair.
- `ExtractedSpecContent` — dataclass — pub — Content extracted from a spec for a specific set of referenced sections.
  - `load_json` — classmethod — pub
  - `save_json` — method — pub
  - `to_dict` — method — pub
- `SpecDocument` — dataclass — pub — A parsed 3GPP specification document.
  - `get_section` — method — pub — Find a section by its number.
  - `get_section_with_ancestors` — method — pub — Get a section and all its ancestors up to root.
  - `load_json` — classmethod — pub
  - `save_json` — method — pub
  - `to_dict` — method — pub
- `SpecReference` — dataclass — pub — A single reference to a standards spec from a requirement document.
- `SpecSection` — dataclass — pub — A parsed section from a 3GPP spec document.
- `StandardsReferenceIndex` — dataclass — pub — Complete index of all standards references from ingested documents.
  - `load_json` — classmethod — pub
  - `save_json` — method — pub
  - `to_dict` — method — pub

`section_extractor.py`
- `SectionExtractor` — class — pub — Extract referenced sections and surrounding context from a spec.
  - `extract` — method — pub — Extract referenced sections with context.

`spec_downloader.py`
- `_DEFAULT_CACHE_DIR` — constant — internal
- `_DEFAULT_SOURCE` — constant — internal
- `_VALID_SOURCES` — constant — internal
- `SpecDownloader` — class — pub — Download and cache 3GPP specification documents.
  - `__init__` — constructor — pub
  - `_convert_doc_to_docx` — staticmethod — internal — Convert a .doc file to .docx using LibreOffice headless.
  - `_download_file` — method — internal — Download a file from URL to local path.
  - `_download_from_3gpp` — method — internal — 3GPP FTP archive download path (ZIP → extract → DOC→DOCX).
  - `_extract_doc` — method — internal — Extract the DOC/DOCX file from a 3GPP spec ZIP archive.
  - `_find_cached` — method — internal — Check if a DOC/DOCX already exists in the cache.
  - `_spec_dir` — method — internal — Cache directory for a spec+release.
  - `download` — method — pub — Download a spec for the given release.
  - `source` — property — pub

`spec_parser.py`
- `_ANNEX_PAT` — constant — internal
- `_SECTION_NUM` — constant — internal
- `_SPEC_TITLE_PAT` — constant — internal
- `_VERSION_FROM_NAME` — constant — internal
- `SpecParser` — class — pub — Parse a 3GPP DOCX specification into a SpecDocument.
  - `_extract_from_filename` — staticmethod — internal — Extract metadata from the 3GPP filename convention.
  - `_extract_metadata` — method — internal — Extract spec number, version, title from front matter.
  - `_find_parent` — staticmethod — internal — Derive the parent section number.
  - `_parse_heading_text` — staticmethod — internal — Parse a heading into (section_number, title).
  - `_parse_sections` — method — internal — Parse all heading paragraphs into SpecSection objects.
  - `parse` — method — pub — Parse a DOCX file into a SpecDocument with sections.

`spec_resolver.py`
- `_ARCHIVE_BASE` — constant — internal
- `_encode_digit` — function — internal — Encode a single digit/number for version code.
- `_PREFIX_TO_RELEASE` — constant — internal
- `_RELEASE_TO_PREFIX` — constant — internal
- `build_candidate_urls` — function — pub — Build candidate download URLs for a spec+release.
- `build_url` — function — pub — Build 3GPP FTP download URL for a spec version.
- `code_to_version` — function — pub — Convert 3GPP file code to version string.
- `prefix_to_release` — function — pub — Map version code prefix back to release number.
- `release_to_prefix` — function — pub — Map release number to version code prefix.
- `ResolvedSpec` — dataclass — pub — A resolved spec with its download URL and metadata.
- `spec_to_compact` — function — pub — Remove dots from spec number: '24.301' → '24301'.
- `spec_to_series` — function — pub — Extract the series from a spec number: '24.301' → '24'.
- `SpecResolver` — class — pub — Resolve spec+release to download URLs.
  - `__init__` — constructor — pub
  - `_filter_by_release` — method — internal — Filter and sort available codes for a specific release.
  - `_find_best_version` — method — internal — Find the latest version for a release from available codes.
  - `_get_available_versions` — method — internal — Get available version codes from the 3GPP FTP directory.
  - `resolve` — method — pub — Resolve a spec+release to a download URL.
  - `resolve_candidates` — method — pub — Get all candidate URLs for a spec+release.
- `version_to_code` — function — pub — Convert version string to 3GPP file code.

`standards_cli.py`
- `_print_reference_summary` — function — internal — Print a summary table of collected references.
- `_process_spec` — function — internal — Download, parse, and extract sections for one spec-release pair.
- `main` — function — pub
<!-- END:STRUCTURE -->

**Depends on**
[parser](../parser/MODULE.md) (for `RequirementTree`, `StandardsRef`), [resolver](../resolver/MODULE.md) (for `CrossReferenceManifest`, `ResolvedStandardsRef`).

**Depended on by**
[graph](../graph/MODULE.md), [query](../query/MODULE.md), [pipeline](../pipeline/MODULE.md).
