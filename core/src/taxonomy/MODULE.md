# taxonomy

**Purpose**
Bottom-up, LLM-derived feature taxonomy for the corpus (TDD §5.7). Per-plan extraction (multi-plan documents split into per-plan units) surfaces telecom features from section headings and plan metadata; consolidation merges them into a unified taxonomy with MNO coverage, primary/referenced attribution, and dependency hints. Serves FR-6 (bottom-up feature taxonomy with required human review). Implements D-005: the taxonomy is not pre-defined — it emerges from the documents, and human review is a required curation step, not optional.

**Public surface**
- `FeatureExtractor(provider: LLMProvider, overview_dir: str | Path | None = None)` (extractor.py) — per-document extraction: consumes `RequirementTree`, prompts the LLM, returns `DocumentFeatures`; when `overview_dir` holds a `corpus_overview_<MNO>_<version>.txt` for the doc's MNO, its text is inserted as a "Corpus context" prompt section
- `resolve_corpus_overview(overview_dir, mno) -> Path | None` (extractor.py) — per-MNO overview file resolution, highest version wins; None on any miss (fail-soft)
- `LLMParseError` (extractor.py) — raised by extraction when the LLM response yields no parseable JSON object; callers treat it as a retryable per-document failure
- `split_tree_by_plan(tree) -> list[RequirementTree]` (extractor.py) — splits a multi-plan tree (one doc whose chapters are each a plan; empty tree-level plan_id, per-req plan_id set per D-DRAFT-1) into one subtree per plan (plan_name = the group's first section heading); single-plan trees pass through unchanged (a blank tree plan_id is promoted from the requirements); heading nodes (no req_id → no per-req plan) are attached to the majority plan among plan-bearing reqs whose `parent_section` sits at or under the heading's `section_number` — they're the feature-indicative outline lines; only nodes enclosing no plan-bearing req (front matter, empty reference sections) are dropped, with a logged count
- `TaxonomyConsolidator` (consolidator.py) — `consolidate(doc_features: list[DocumentFeatures]) -> FeatureTaxonomy`; dedupes by `feature_id`, builds `mno_coverage` and `is_primary_in` / `is_referenced_in` attribution
- Schema: `Feature`, `DocumentFeatures`, `TaxonomyFeature`, `FeatureTaxonomy` (all dataclasses with `to_dict`/`save_json`/`load_json` on the two top-level ones)
- `taxonomy_cli.main` — CLI: extract | consolidate | review
- Debug CLI (`tax_debug.py`) — `python -m core.src.taxonomy.tax_debug --env-dir <env_dir> [--dry-run] [--only P1,P2]`: replays the exact extraction prompt for every unit marked `failed` in `out/taxonomy/extraction_state.json` (same code path as the pipeline stage) and captures the raw LLM response per plan under `<env_dir>/reports/tax_debug/`; for diagnosing persistent per-plan failures (refusals, truncation, endpoint errors). Outputs contain corpus content — never committed

**Invariants**
- Bottom-up. No hand-written seed taxonomy, no per-MNO feature list. The only inputs are the parsed trees; the only output is what the corpus yielded plus what a reviewer added.
- Human review is **required**, not optional. `corrections/taxonomy.json` is the authoritative output of this stage once a reviewer has touched it; the raw LLM output is a starting point. This is how [corrections](../corrections/MODULE.md) participates in taxonomy.
- `FeatureExtractor` depends only on `LLMProvider` (the Protocol), never on a specific provider module — satisfies D-006.
- Consolidation is deterministic and LLM-free. The LLM is only used in Step 1 (per-document extraction). Step 2 (merge) is a dict union with attribution bookkeeping. Cross-MNO alignment (which might use an LLM) is deferred until multi-MNO data is available.
- `feature_id` is the merge key. Extractor implementations must produce stable, prefix-matchable IDs (e.g., `DATA_RETRY`, `IMS_REGISTRATION`) — a feature renamed across documents won't merge.
- `primary_features` vs `referenced_features` is a confidence split: a feature with multiple keyword matches in a doc is primary; single-match is referenced. Consolidation promotes a feature to `is_primary_in` as soon as any doc lists it as primary.
- **No empty successes.** An unparseable LLM response raises `LLMParseError` rather than returning an empty `DocumentFeatures` — an empty success would be persisted, cached, and consolidated as if the document genuinely had no features. Failure is signalled to the caller, which decides retry policy (the pipeline stage records it in `extraction_state.json` and retries on re-run; the CLI logs and continues).
- **The unit of extraction is a plan, not a file.** Extraction consumes trees whose plan_id is meaningful; multi-plan documents must be split (`split_tree_by_plan`) before `extract` so each LLM call sees one plan's outline (the prompt TOC is capped at 200 lines — an unsplit chapter-per-plan doc silently loses everything past the cap), output files are named per real plan, and consolidation attributes features to the right plan.

**Key choices**
- LLM used only where it earns its keep — inferring feature names and keywords from heading text is exactly the kind of fuzzy clustering that heuristics struggle with.
- Text-input/text-output LLM interface (via `LLMProvider.complete()`) — caller parses JSON from the response. Avoids forcing structured-output modes that some offline models lack.
- Taxonomy sorted by `is_primary_in` count then name — review order prioritizes features that matter across the corpus.
- **Optional per-MNO corpus context** (strand sira-enrichment-pe): extraction can be grounded with an AI-derived overview of the MNO's whole corpus (`corpus_overview_<MNO>_<version>.txt`, produced by the derive-sira-prompts skill; runtime artifact — per-MNO instances may carry real corpus vocabulary and are committed only if free of proprietary identifiers). Strictly fail-soft: unset dir, missing/empty file → prompt byte-identical to the no-context form (logged as TAX-W003). Wired via `NORA_TAXONOMY_OVERVIEW_DIR` (pipeline `run_taxonomy` + `taxonomy_cli --overview-dir`); overview files are hashed into the pipeline's taxonomy corpus fingerprint so changing one re-derives without `--force`.
- `DocumentFeatures` persisted as JSON per document — review can happen incrementally, and re-consolidation is cheap when one document changes.

**Non-goals**
- Not a knowledge graph — [graph](../graph/MODULE.md) attaches features to requirements and documents and spans MNOs; this module produces the feature catalog.
- Not a canonical telecom ontology — features reflect what the docs say, not what 3GPP technically defines.
- No automatic cross-MNO alignment yet — deferred until real multi-MNO data arrives. Do not build this speculatively.
- No prompt-engineering library — the system prompt lives in `extractor.py`; a different prompt style means a new extractor class, not a plugin architecture.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`consolidator.py`
- `TaxonomyConsolidator` — class — pub — Merge per-document features into a unified taxonomy.
  - `_log_summary` — staticmethod — internal
  - `_merge_features` — staticmethod — internal — Merge a list of features from one document into the taxonomy map.
  - `consolidate` — method — pub — Consolidate features from multiple documents into a taxonomy.
- `logger` — constant — pub

`extractor.py`
- `EXTRACTION_PROMPT_TEMPLATE` — constant — pub
- `FeatureExtractor` — class — pub — Extract telecom features from requirement documents using an LLM.
  - `__init__` — constructor — internal
  - `_build_corpus_context` — method — internal — Corpus-context prompt section for the doc's MNO, or "".
  - `_build_toc` — staticmethod — internal — Build a table of contents string from the requirement tree.
  - `_parse_response` — staticmethod — internal — Parse the LLM JSON response into DocumentFeatures.
  - `extract` — method — pub — Extract features from a single parsed requirement tree.
- `LLMParseError` — class — pub — LLM response could not be parsed as feature JSON.
- `SYSTEM_PROMPT` — constant — pub
- `_first_json_object` — function — internal — First balanced, parseable {...} in `text`, or None.
- `logger` — constant — pub
- `resolve_corpus_overview` — function — pub — Resolve the per-MNO corpus-overview file, highest version wins.
- `split_tree_by_plan` — function — pub — Split a multi-plan tree into one subtree per requirement plan_id.

`schema.py`
- `DocumentFeatures` — dataclass — pub — Features extracted from a single document (TDD 5.7 Step 1).
  - `load_json` — classmethod — pub
  - `save_json` — method — pub
  - `to_dict` — method — pub
- `Feature` — dataclass — pub — A single telecom feature/capability.
- `FeatureTaxonomy` — dataclass — pub — Unified feature taxonomy across all documents (TDD 5.7 output).
  - `load_json` — classmethod — pub
  - `save_json` — method — pub
  - `to_dict` — method — pub
- `TaxonomyFeature` — dataclass — pub — A feature in the unified taxonomy (TDD 5.7 output).

`tax_debug.py`
- `_failed_units` — function — internal — Yield (ledger_key, plan_subtree) for every failed unit in the ledger.
- `_resolve_active_provider` — function — internal — Construct the LLM provider via the unified D-044 chain — the same
- `logger` — constant — pub
- `main` — function — pub

`taxonomy_cli.py`
- `main` — function — pub
<!-- END:STRUCTURE -->

**Depends on**
[parser](../parser/MODULE.md) (for `RequirementTree`), [llm](../llm/MODULE.md) (for `LLMProvider`), [corrections](../corrections/MODULE.md) (for `CorrectionStore` in review flow). The debug CLI (`tax_debug.py`) additionally imports [env](../env/MODULE.md) (`resolve_llm_*`) and [pipeline](../pipeline/MODULE.md) (`PipelineContext.create_llm_provider`) at debug time — runtime callers don't pay this cost.

**Depended on by**
[graph](../graph/MODULE.md), [query](../query/MODULE.md), [corrections](../corrections/MODULE.md) (imports `FeatureTaxonomy` for correction IO), [pipeline](../pipeline/MODULE.md).
