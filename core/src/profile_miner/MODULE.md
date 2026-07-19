# profile_miner

**Purpose**
Convert human-supplied parse corrections (from the Web UI Review tab) into proposed `DocumentProfile` regex patches. Closes the corrections-driven feedback loop established in D-024 / D-031: a reviewer marks a missed revhist heading or an undetected glossary section, the miner clusters such corrections by `expected_reason`, asks an `LLMProvider` to generalise the redacted examples into ONE regex per cluster, and emits a `profile_patch_<doc_id>.json` for human review. Serves the parser-debugging workflow: pipeline → reviewer marks corrections → miner proposes regex → human merges into `customizations/profiles/<MNO>_<plan>.json`.

**Public surface**
- `EnrichedCorrection` (records.py) — one corrections entry joined to its IR block + ±2 neighbours (`doc_id`, `kind`, `expected_reason`, `block_idx`, `pages`, `block_text`, `neighbour_texts`, `comment`).
- `ProfileFieldPatch` (records.py) — one proposed pattern for one profile field: `profile_field` (dotted path), `list_field` (True when the field is `list[str]`), `expected_reason`, `proposed_pattern`, `rationale`, `confidence`, `example_block_idxs`, `example_previews`.
- `ProfilePatch` (records.py) — top-level output: `doc_id`, `generated_at`, `field_patches: list[ProfileFieldPatch]`, `unmapped: list[ProfileFieldPatch]` (reasons with no canonical profile field yet); `save_json(path)`.
- `Redactor` (redaction.py) — bidirectional placeholder map. `redact(text) -> str` replaces operator names, plan IDs, and composed req-ids with `<MNO0>` / `<PLAN0>` / `<MNO0>_REQ_<PLAN0>_\d+` placeholders; `restore_in_regex(regex)` leaves placeholders intact for the patch (deliberately — the patch is portable across MNOs); `mno_map()` / `plan_map()` for introspection.
- `load_corrections(env_dir, doc_id=None) -> list[EnrichedCorrection]` (loader.py) — read every `<env_dir>/corrections/*_corrections.json` (or one file when `doc_id` is given), join to `<env_dir>/out/extract/<doc_id>_ir.json` via `block_idx`, attach ±2 neighbour-block texts, return a flat list. Skips entries with stale `block_idx`.
- `mine_patterns(corrections, llm) -> ProfilePatch` (miner.py) — cluster corrections by `expected_reason`, redact each example, prompt the LLM once per cluster (temperature 0.0, structured-JSON response), map known reasons to their profile field via `_REASON_TO_FIELD`, route unknown reasons to `ProfilePatch.unmapped`.
- `profile_miner_cli.main` — entrypoint (`python -m core.src.profile_miner.profile_miner_cli --env-dir <env_dir> [--doc <id>]`). Writes `<env_dir>/reports/profile_patch_<doc_id>.json` per document.
- `merge_regex(existing, new) -> str` (apply_patch.py) — alternation-merge with shared leading-`(?i)` flag dedup. Empty `existing` ⇒ replacement; identical ⇒ no-op; differing ⇒ `(?i)(?:<existing>|<new>)`.
- `apply_patch(patch_data, profile_data, source_doc="") -> ApplyReport` (apply_patch.py) — pure function. Walks `patch_data["field_patches"]`; scalar fields are set / merged via `merge_regex`; list fields are appended-if-absent. Modifies `profile_data` in place. Unmapped entries pass through to `ApplyReport.skipped_unmapped` without applying.
- `apply_patch_files(env_dir, doc_id=None, dry_run=False) -> (ApplyReport, Path | None)` (apply_patch.py) — file orchestrator. Walks every `<env_dir>/reports/profile_patch_*.json` (or one when `doc_id` is set), seeds `<env_dir>/corrections/profile.json` from `<env_dir>/out/profile/*.json` on first use, merges, writes unless `dry_run`.
- `apply_profile_patch_cli.main` — entrypoint (`python -m core.src.profile_miner.apply_profile_patch_cli --env-dir <env_dir> [--doc <id>] [--dry-run]`). Prints a grouped change report.

**Invariants**
- **Redaction is mandatory before the LLM call.** No raw operator name, plan ID, or composed req-id ever leaves the host that runs the miner. The same `Redactor` instance is reused for all examples in one cluster so placeholder indices are stable within the prompt.
- **One regex per `expected_reason` cluster**, not per correction. The LLM sees the whole cluster so it can generalise across variants (e.g. "Document History" + "Change History" → one revhist pattern).
- **`block_idx` is the join key.** Page-number-only joins are rejected — the Review tab embeds `block_idx` precisely to disambiguate multi-correction pages.
- **Block type drives field routing.** A `revhist` correction on a TABLE block emits a proposal against `revhist_table_header_pattern`; the same reason on a HEADING/PARAGRAPH block emits against `revision_history_label_pattern`. Same split for `glossary` → `definitions_table_header_pattern` vs `definitions_section_pattern`. The LLM prompt also tells the model the matching target (joined headers vs. block text) so the regex it generates is shaped for the actual parser-side comparison.
- **The miner never writes profiles.** Output goes to `<env_dir>/reports/profile_patch_<doc_id>.json` for human review; merging into `customizations/profiles/*.json` is a manual step. Mirrors the parser invariant that the profile is read-only at runtime.
- **List-valued profile fields** (e.g. `cross_reference_patterns.standards_citations`) are appended to, not replaced. `ProfileFieldPatch.list_field=True` signals this to the reviewer / future automation.
- **Unmapped reasons survive.** `expected_reason` values not present in `_REASON_TO_FIELD` are emitted to `ProfilePatch.unmapped` rather than dropped — so adding a new annotation kind to the Review tab never silently loses data.
- **LLM failures degrade gracefully.** Unparseable JSON, empty patterns, or transport errors are logged and skipped per cluster; other clusters still produce patches.

**Key choices**
- Two-tier output (`field_patches` vs `unmapped`) instead of one combined list — keeps the reviewer's eye on the high-confidence "this maps cleanly to an existing profile knob" cases and surfaces the schema-extension cases separately.
- Per-cluster redactor instances (rather than one global redactor across the whole run) — placeholder indices stay local to the prompt, reducing LLM confusion when one mining run spans many unrelated reasons.
- LLM provider injected via the standard `LLMProvider` protocol — Ollama-on-work-PC-GPU and proprietary providers use the same code path with no branching, consistent with the rest of the pipeline.
- ±2-block neighbour window — empirically enough context to disambiguate a section heading from body content without ballooning prompt size on large clusters.

**Non-goals**
- No profile mutation. The miner proposes; humans dispose.
- No regex validation or compilation testing — the reviewer is expected to compile-test the proposed pattern against the source doc before merging. Adding a `re.compile()` smoke check would catch obvious garbage but would also encourage merging untested patterns; left as a deliberate gap.
- No live re-parse of the document with the proposed regex. That belongs in a follow-on workflow tool, not in this module.
- No support for non-regex profile fields (e.g. font-size clusters). Corrections expressing "the font size threshold is wrong" need a different miner.

**Depends on**
- [llm](../llm/MODULE.md) — `LLMProvider` protocol; CLI defaults to `OllamaProvider` with `model_picker.pick_model`.
- [models](../models/MODULE.md) — `DocumentIR`, `ContentBlock`, `BlockType` for the corrections→IR join.
- [parser](../parser/MODULE.md) — consumes corrections produced by the parse-review Web UI (route at `core/src/web/routes/parse_review.py`); writes patches that target `parser`'s `DocumentProfile`.
- [profiler](../profiler/MODULE.md) — patches target `DocumentProfile` regex fields (`revision_history_label_pattern`, `heading_detection.definitions_section_pattern`, `toc_detection_pattern`, `cross_reference_patterns.*`, `reference_list_section_pattern`, `reference_list_entry_pattern`).

**Depended on by**
- (none yet — the CLI is the only entry point and is invoked manually)

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`apply_patch.py`
- `ApplyReport` — dataclass — pub
  - `modified` — property — pub
- `FieldChange` — dataclass — pub
- `_get_dotted` — function — internal
- `_iter_patch_files` — function — internal
- `_seed_corrections_profile` — function — internal — Copy ``<env_dir>/out/profile/*.json`` to
- `_set_dotted` — function — internal
- `_strip_leading_iflag` — function — internal — ``(?i)foo`` → ``(True, "foo")``; ``foo`` → ``(False, "foo")``.
- `apply_patch` — function — pub — Apply one patch (already-loaded JSON dict) on top of one profile
- `apply_patch_files` — function — pub — Walk every ``profile_patch_<doc>.json`` under
- `logger` — constant — pub
- `merge_regex` — function — pub — Combine two regex strings via alternation, deduplicating a

`apply_profile_patch_cli.py`
- `_format_value` — function — internal
- `logger` — constant — pub
- `main` — function — pub

`loader.py`
- `NEIGHBOUR_WINDOW` — constant — pub
- `_block_text` — function — internal
- `_iter_corrections_files` — function — internal
- `load_corrections` — function — pub — Load every corrections entry under ``<env_dir>/corrections/`` (or
- `logger` — constant — pub

`miner.py`
- `_JSON_OBJ` — constant — internal
- `_REASON_TO_FIELD` — constant — internal
- `_SYSTEM` — constant — internal
- `_build_prompt` — function — internal
- `_example_target` — function — internal — Render the example in the shape the regex will be tested against
- `_matching_target_hint` — function — internal — One-line hint about what string the proposed regex will be
- `_parse_llm_response` — function — internal — Tolerate code fences or stray prose. Returns None on parse
- `_resolve_field` — function — internal — Return (profile_field, is_list_field, is_mapped).
- `_safety_net_pattern` — function — internal — Compose the final proposed pattern as
- `_strip_leading_iflag` — function — internal
- `logger` — constant — pub
- `mine_patterns` — function — pub — Cluster corrections by ``expected_reason``, prompt the LLM once

`profile_miner_cli.py`
- `_build_llm` — function — internal — Resolve LLM provider + model via the project-wide chain (D-044)
- `logger` — constant — pub
- `main` — function — pub

`records.py`
- `EnrichedCorrection` — dataclass — pub — One correction joined to its source block and ±N neighbours.
- `ProfileFieldPatch` — dataclass — pub — One proposed pattern (or list of patterns) for a single profile field.
- `ProfilePatch` — dataclass — pub — Aggregate output for one document. Emitted to
  - `save_json` — method — pub

`redaction.py`
- `Redactor` — class — pub — Stateful redactor: assigns deterministic ``<MNO0>``, ``<PLAN0>``,
  - `__init__` — constructor — internal
  - `_mno_token` — method — internal
  - `_plan_token` — method — internal
  - `_redact_plan_ids` — method — internal
  - `_redact_req_ids` — method — internal
  - `mno_map` — method — pub
  - `plan_map` — method — pub
  - `redact` — method — pub — Replace MNO names, plan IDs, and composed req-ids with
  - `restore_in_regex` — method — pub — LLM output is a regex over redacted text. We *don't* want to
- `_MNO_PATTERNS` — constant — internal
<!-- END:STRUCTURE -->
