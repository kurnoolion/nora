# multi-mno-nora — journal

## 2026-06-14 — Parser detection gap confirmed; D-DRAFT-2 scoped (leading-id body-block mode)

### Done this session

- **Grounding finding — the plan is modeled one-per-document end-to-end** (basis
  for D-DRAFT-1, locked earlier): `RequirementTree.plan_id`/`plan_name` are
  scalars; the SIRA adapter groups multi-granularity rows by `tree.plan_id`. But
  the per-requirement plan is recoverable — req_ids carry a plan prefix and
  `_extract_plan_id_from_req` (profile `RequirementIdPattern.components`) already
  derives it transiently. → Option B (promote plan to a per-req attribute) is
  surgical. See D-DRAFT-1.

- **Detection gap CONFIRMED (not inferred) — the parser emits one `Requirement`
  per heading, never per body paragraph.** Evidence in
  `core/src/parser/structural_parser.py`:
  - A `Requirement` is constructed in exactly one place in the paragraph pass —
    `:1547`, gated on `_heading_depth(block) is not None` (block is a heading).
  - The only other source is table-cell anchors (second pass, `:1643`, behind
    `enable_table_anchored_extraction`).
  - A flat body paragraph falls through to `:1601-1611`: text is appended to the
    enclosing heading-section, and any inline req_id is captured only as *that
    section's* id (first-wins).
  - **Consequence for MNO-B:** a subsection (heading, no req_id) holding N
    leading-id body requirements collapses into ONE `Requirement` — first req_id
    becomes the section id, the other N-1 are buried as body text and lost.
  - MNO-B's model (sections = non-requirement context; requirements = flat body
    paragraphs with a leading `<PREFIX>-<PLAN>-<DIGITS>` id) is therefore
    genuinely unsupported. A profile alone cannot express it — the detection
    primitive is missing. → D-DRAFT-2.

- **Key reuse insight (drives the small scope):** a leading-id body requirement
  is structurally identical to a **table-anchored** requirement — a child
  `Requirement` with no own `section_number`, anchored to a parent heading,
  `hierarchy_path` filled post-hoc. The existing, tested machinery covers it:
  - `_create_table_anchored_req` (`:1710`) is the emission template.
  - `_propagate_hierarchy_to_table_reqs` (`:1760`) already fills `hierarchy_path`
    for every section_number-less node with a `parent_section` — covers the new
    reqs unchanged (rename to `_..._child_reqs` for honesty).
  - `_apply_applicability` (`:1782`) and `_link_parents` (`:2902`) handle them
    unchanged (document-order + parent_section; skip-on-no-section_number).

- **D-DRAFT-2 drafted** in `decisions-draft.md`: add a generic,
  profile-selectable `requirement_detection.mode: "heading" | "leading_id_body"`
  (default `"heading"`, MNO-A byte-for-byte unchanged). Rejected
  pre-processing into headings (brittle/lossy) and hacking req_ids through the
  heading path (pollutes hierarchy/zone, collides with heading-continuation
  defenses). Composes with D-DRAFT-1.

### D-DRAFT-2 implementation scope (for dev phase)

1. **Profile schema** (`profiler/profile_schema.py`, ~10 lines): add
   `requirement_source`/`requirement_detection.mode` selector, default
   `"heading"`. Reuses existing `RequirementIdPattern` for matching (no new id
   config). MNO-B: `anchor="leading_text"`, `components` separator `-` /
   `plan_id_position: 1` (config-only).

2. **Parser core** (`structural_parser.py` `_build_sections`, ~50-80 lines — the
   substance):
   - **Two cursors:** split today's single `current_section` into
     `current_heading` (parenting/hierarchy) and `current_append_target` (body
     append; starts at the heading = section preamble, switches to the latest
     leading-id requirement). Handles PyMuPDF splitting a requirement across
     multiple PARAGRAPH blocks.
   - **Predicate:** in `leading_id_body` mode, body block whose `lstrip()`
     **starts with** the req_id pattern (anchored `re.match`, NOT
     `_find_req_ids` search — so an inline cross-reference mid-text doesn't open
     a phantom requirement) → new child requirement; else append to
     `current_append_target`.
   - **`_create_leading_id_req(...)`** modeled on `_create_table_anchored_req`:
     `section_number=""`, parent linkage from `current_heading`, `zone_type`
     inherited, `hierarchy_path=[]` (filled later), add to `parent.children`,
     record in `paragraph_req_ids`.
   - Headings still open sections but carry **no req_id** (structural-only
     context nodes; stay in `sections`).

3. **Reused as-is:** `_propagate_hierarchy_to_table_reqs` (rename only),
   `_apply_applicability`, `_link_parents`, D-DRAFT-1 per-req `plan_id`.

4. **Downstream — verify, don't assume:** heading nodes now have empty `req_id`
   (novel). Check SIRA adapter `_emit_multigranularity_rows` and graph builder
   (FR-7) tolerate req_id-less structural nodes when partitioning per-req
   `plan_id`. Likely fine (table-anchored parents can also be req_id-less), but
   confirm. `graph` not in target modules — STRAND notes it as a touched
   consumer; widen only if an assertion fails.

5. **Tests** (`core/tests/test_parser.py`): leading_id_body fixture (subsection +
   multiple leading-id reqs + preamble + inline cross-ref) asserting N reqs (not
   1), correct parent linkage/hierarchy, no phantom from the cross-ref,
   continuation block routed to the right req; plus a heading-mode regression
   (byte-identical to today). Generic ids (`ABC-FOO-001`) — redaction rule.

6. **Estimate:** ~half-day dev-phase, low risk (rides existing child-req
   infrastructure; default path untouched). Only §4 could widen it.

### Open details (resolve at wiring time — not blockers)

- Final field name/placement (`HeadingDetection.requirement_source` vs a new
  `RequirementDetection` block).
- Strip vs retain the leading req_id in requirement `text` (lean retain for
  fidelity; confirm against the real doc).
- Heading nodes retained as structural-only (table-anchored precedent says yes)
  vs elided — recommend retain; gated on §4.
- Table-anchored second pass interaction: keep the two modes composable, not
  hard-wired mutually exclusive.

### Next

- Implement D-DRAFT-2 (dev phase): schema field → parser fork + helper + tests →
  verify §4 consumers.
- Then author the MNO-B profile (small): select `leading_id_body`, set the
  req_id pattern + `components` (`-` / position 1), section-numbering for
  hierarchy context — against the real document's prefix/styles.
- Carry D-DRAFT-1 wiring (per-req `plan_id` in `Requirement` + adapter/graph
  grouping) — it composes with and is exercised by the MNO-B path.

### Flags

- D-DRAFT-1 and D-DRAFT-2 are both unlanded drafts in this strand; neither parser
  nor schema code has been written yet — this session is architecture/scoping
  only.
- An MNO-B profile authored before D-DRAFT-2 lands would silently collapse
  MNO-B's requirements — do not write the profile first.

## 2026-06-14 — D-DRAFT-2 implemented (parser + schema + tests); §4 adapter gap found

### Done this session

- **D-DRAFT-2 implemented** (dev phase, all tests green):
  - `profiler/profile_schema.py`: added `RequirementIdPattern.detection_mode`
    (`"heading"` default | `"leading_id_body"`). Back-compat — splat-constructed
    in `DocumentProfile._from_dict`, missing key → default.
  - `parser/structural_parser.py`:
    - `__init__`: `_req_id_leading_re` (`^\s*(?:pattern)`, start-anchored, NOT
      full-match) + `_leading_id_mode` flag.
    - `_build_sections`: new `current_leading_req` cursor + a guarded
      leading-id branch in the body-text path. Heading stays `current_section`
      (parent/context); the cursor resets on a freshly-opened heading (driven by
      the existing `previous_block_was_heading`), so section preamble lands on
      the heading and continuation blocks append to the open requirement. Default
      `"heading"` path untouched (byte-for-byte).
    - `_create_leading_id_req`: emits a child Requirement (no `section_number`,
      parent linkage from the heading, leading id retained in `text`), modeled on
      `_create_table_anchored_req`. Tolerates `parent_section=None` (req before
      any heading).
    - `_propagate_hierarchy_to_table_reqs`: docstring widened (now covers
      section_number-less leading-id reqs too); name kept to avoid contract churn.
  - `core/tests/test_structural_parser_leading_id.py`: 10 tests — N reqs per
    subsection, inline-ref does-not-spawn-phantom, parent/section_number, continuation
    append, preamble-on-heading, heading-reset reparent, hierarchy_path inherit,
    headings have empty req_id; + default-mode regression (no standalone reqs;
    inline id attaches to heading). Generic ids (`ABC-FOO-001`).
  - Regression: 79 existing parser tests + 61 profiler tests pass.
  - `parser/MODULE.md`: curated Invariant + Key-choice updated to the **third**
    anchor source (leading-id body) and the `detection_mode` field, citing
    D-DRAFT-2. Backed by the draft decision (not silent contract evolution) —
    flag at close-session audit.

- **§4 downstream verification (SIRA adapter `nora_to_beir.py`) — finding:**
  - **No crash.** Both `_emit_corpus` (`:416`) and `_emit_multigranularity_rows`
    (`:333`) guard `if not req_id: continue`, so req_id-less heading nodes are
    tolerated. Leading-id reqs emit as per-req corpus rows.
  - **But the section-granularity tier collapses** for leading-id corpora: the
    adapter treats "a section" as "a requirement with a `section_number`"
    (`:336-342`). In the leading-id model headings have a section_number but no
    req_id (skipped), and reqs have a req_id but no section_number → `section_anchors`
    / `section_to_descendants` stay empty → **zero `section:` rows**; per-req rows
    also get an empty `title` (no section_number/title on leading-id reqs).
  - This is **adapter work, bundled with D-DRAFT-1** (which already must move
    section/plan grouping off the heading-as-requirement assumption). NOT a
    D-DRAFT-2 blocker — the parser tree is correct; only the SIRA section tier is
    affected.

### Next

- D-DRAFT-1 implementation (per-req `plan_id` on `Requirement` + adapter/graph
  per-req grouping) — fold in the §4 fix: derive section structure from reqs'
  `parent_section` / `hierarchy_path` (populated) instead of req-bearing headings;
  give leading-id reqs a non-empty corpus-row `title` (e.g. from hierarchy_path).
- Then author the MNO-B profile (`detection_mode="leading_id_body"`, req_id
  pattern, `components` `-`/pos 1) against the real document.

### Flags

- `parser/MODULE.md` curated sections edited (Invariant + Key choice) — backed by
  D-DRAFT-2; surface in close-session MODULE.md audit.
- SIRA adapter section-tier gap for leading-id corpora (above) is unfixed by
  design — tracked for the D-DRAFT-1 adapter work.

## 2026-06-14 — D-DRAFT-1 implemented (parser + adapter + graph); §4 fix folded in

### Done this session

- **D-DRAFT-1 implemented across all three layers; 161 tests green** (parser +
  profiler + graph + adapter), no regressions.

  **Parser** (`structural_parser.py`):
  - `Requirement.plan_id` field added (+ `load_json` deserialization,
    `to_dict` automatic via `asdict`).
  - `parse()` step 8c: populate `req.plan_id = _extract_plan_id_from_req(req_id)
    or tree_plan_id` for every requirement. Single-plan docs → `req.plan_id ==
    tree.plan_id` (unchanged). Heading/id-less nodes → tree plan.
  - **`tree.plan_id` semantics resolved** (the D-DRAFT-1 open detail): it stays
    the document's *primary* plan from page-1 metadata, **may be empty for a
    genuinely multi-plan doc** (e.g. MNO-B with no plan-metadata pattern); the
    per-req `plan_id` is the authority for grouping. No back-compat break — for
    single-plan docs the two coincide.
  - Tests: `test_structural_parser_leading_id.py::TestPerRequirementPlanId`
    (3) — per-req plan FOO/BAR, multi-plan in one doc, id-less heading fallback.

  **Adapter** (`sandbox/adapter/nora_to_beir.py` `_emit_multigranularity_rows`):
  - Reworked to **group `doc:`/`section:` rows by per-req `plan_id`** instead of
    `tree.plan_id` — one document now yields one `doc:` row per distinct plan.
  - **§4 fix folded in:** section structure now derives from a *section-title
    catalog* (every node with a `section_number`, incl. id-less leading-id
    headings) + each req's "home" section = `section_number` **or**
    `parent_section`. Leading-id corpora now get `section:` rows (previously
    zero). Leading-id corpus rows also get a non-empty `title` from
    `hierarchy_path`; `_build_text` now shows the req's own plan.
  - Tests: `test_nora_to_beir.py::TestMultigranularityPerReqPlan` (5) +
    `TestMultigranularitySinglePlanBackCompat` (3).

  **Graph** (`core/src/graph/builder.py` `_build_requirement_graph`):
  - New `_ensure_plan_node(...)` (internal) creates a **Plan node per distinct
    per-req plan**, not one per document; each requirement `BELONGS_TO` its own
    plan; req node carries its `plan_id`. Primary plan keeps doc-level
    `plan_name`/`version`; secondary plans get empty `plan_name`
    (first-creation-wins across trees sharing a plan in a release). Log line
    fixed (`len(plans_seen)` not `len(trees)`). No curated graph MODULE.md
    contract affected — public surface unchanged.
  - Tests: `test_graph.py::TestPerReqPlanNodes` (5).

### Next

- Author the **MNO-B profile** now that the pipeline supports it:
  `requirement_id.detection_mode = "leading_id_body"`, `pattern` for
  `<PREFIX>-<PLAN>-<DIGITS>`, `components` `{separator:"-", plan_id_position:1}`,
  heading_detection for the section hierarchy — against the real document
  (need the literal PREFIX, section-number format, heading styles).
- Work-PC: run extract → parse on the real MNO-B PDF; inspect; iterate profile.

### MNO-A-preservation (user requirement: new behavior MNO-B-only)

- A table-anchored req (heading model) and a leading-id req (leading_id_body
  model) are **indistinguishable in the serialized tree** (both: empty
  `section_number` + a `parent_section`). So the parser now **stamps
  `RequirementTree.detection_mode`** (mirrors the profile) and both consumers
  gate on it:
  - **Adapter**: per-req-plan grouping + parent_section-based section
    derivation + hierarchy_path title + per-req plan in body — **only when
    `detection_mode == "leading_id_body"`**. Heading corpora keep one doc row
    per tree, sections off own `section_number`, original plan display.
  - **Graph**: per-req plan nodes only in leading mode; heading mode = one plan
    per document, every req attached — byte-identical to original (also guards
    the cross-plan table-anchored-ref scatter case).
  - `Requirement.plan_id` is still *populated* for all corpora (additive
    metadata) but only *acted on* in leading mode.
- Gating tests added: `test_graph.py::...test_heading_mode_keeps_single_plan_
  even_with_mixed_req_plans`; `test_nora_to_beir.py::...test_heading_mode_table_
  anchored_excluded_and_no_plan_split`. Total now 163 green.

### Flags

- `parser/MODULE.md` curated edits (D-DRAFT-1 + D-DRAFT-2: new Invariants,
  Public-surface `plan_id`, detection-mode) pending close-session audit — all
  decision-backed.
- MNO-B profile still the next step; needs the real document (work PC).

## 2026-06-14 — Work committed (a56cc8e); MNO-name redaction convention applied

### Done this session
- Committed D-DRAFT-1 + D-DRAFT-2 implementation as `a56cc8e`
  (feat(parser): multi-plan documents — per-req plan_id + leading-id detection mode).
- Applied MNO-name redaction across docs + code comments + both TDD copies:
  - Mapping: MNO-A = Verizon, MNO-B = AT&T, MNO-C = T-Mobile.
  - Rule: actual names allowed in *general* context; templatized (MNO-A/B/C) in
    *requirement-structure / content* contexts; Verizon name only in
    open-access (OA) corpus context (public), else MNO-A.
  - Functional code identifiers (VZW/TMO/ATT mno-field values, cell dirnames,
    query-analyzer aliases, redaction-test inputs, `<MNO0_NAME>` substitution
    examples) left intact — renaming breaks code/tests or the redaction layer.
  - Captured as a user-memory feedback rule.

### Next
- Author the MNO-B profile (detection_mode=leading_id_body) against the real
  document (work PC): literal req_id prefix, components (`-` / pos 1),
  section-number/heading styles.
- Work-PC validation of the multi-plan / leading-id path on real corpora.

### Flags
- D-DRAFT-1 / D-DRAFT-2 remain unlanded strand drafts; `parser/MODULE.md`
  curated edits (committed in a56cc8e) are backed by them — promote at /land-strand.
- Both TDD copies still contain VZW/TMO/ATT *code* references in
  requirement-content examples — left per codes-are-functional, but they're
  transparently the three MNOs; scrubbing them is a separate decision (would
  diverge schema examples from real mno-field values).
- MODULE.md Structure sections are stale (new `_create_leading_id_req`,
  `_ensure_plan_node`, `plan_id`/`detection_mode` not listed) — regen-map not
  auto-triggered (no module-level structural change), run if desired.

## 2026-06-17 — MNO-B extractor design + PDF line-boundary preservation; ingestion docs + cline playbook

### Done this session
- Multi-MNO/release ingestion docs (4984adb): README "Ingesting multiple MNOs /
  releases" (input/<MNO>/<release>/ cells, one-run rglob, MMMYYYY); fixed stale
  env instructions (--doc-root→--env-dir; documents/→input/out/state). SIRA
  SETUP.md cross-link. STRAND.md landing gate.
- Cline profile-corpus playbook (ffd34f0): PROF report now captures detection
  mode, req_id plan encoding (sep + plan_id_position), distinct plans per doc.
- MNO-B model observations captured (#1–6): skip front-matter → parse from Ch.3;
  top-level chapter = plan; sections have no req_ids → content prepended as
  "Context"; requirement = req_id + blue title + black body; req owns text until
  the next req_id/section.
- Converged #5 + implemented (bfd4312): PDF extractor flattened a block's source
  lines with " ".join → heading/title and body merged into a run-on, blurring
  hierarchy for the synthesizer. Color is NOT usable (pymupdf reports the blue
  title as 0; blue reused for sections; purple hyperlinks in titles AND body) —
  parked. Fix = additive ContentBlock.lines (invariant " ".join(lines)==text →
  block.text byte-identical, no regression). 6 tests incl. real-pymupdf
  round-trip; suite 1270 passed. Committed + pushed; verified on the real
  MNO-B doc (req_id/title/body separated; sections too). Captured as D-DRAFT-3.

### Next
- Profile stage: author the MNO-B profile (detection_mode=leading_id_body,
  req_id pattern + components sep "-"/pos 1, heading_detection for sections).
- Parser design (consume lines): front-matter cutoff at Ch.3; chapter = plan;
  emit requirement title vs body from block.lines; build ancestor-section
  "Context" (#4); req-owns-until-next-boundary (#6).
- Deferred nicety: split a multi-line requirement TITLE from body (no reliable
  per-span signal; line-boundary fix already delivers section hierarchy).

### Flags
- D-DRAFT-1/2/3 unlanded; landing gate stands (verify multi-release first).
- #4 ancestor-context ambiguity: for a req under 5.1.2.3 the user listed
  "5, 5.1, 5.1.2, 5.1.3" — assuming ancestor chain 5→5.1→5.1.2→5.1.2.3 unless
  corrected.
- Parser does not yet CONSUME ContentBlock.lines — lands with the MNO-B parser design.

## 2026-06-18 — D-DRAFT-5 rework: generic, consumer-assembled requirement Context

### Done this session
- Reworked D-DRAFT-5 from "materialize context per-req in the tree" to a
  **generic, consumer-assembled** design (the all-plans-in-one-PDF bloat
  concern: copying ancestor-section content into every req would multiply
  section text by req count). Tree stays compact; `Requirement.context` is left
  empty in the tree and assembled at emit time by consumers.
- `build_context` profile knob (`"none" | "path" | "path_and_content"`, stamped
  onto `RequirementTree.build_context`) + shared `parser.build_context_string`
  helper. Formats settled with the user: `path` → single-line
  `[Context: 5 <T> > 5.1 <T> > 5.1.2 <T>]`; `path_and_content` → `[5 <T>]` +
  body per ancestor, top-down.
- Wired both consumers. **SIRA adapter** (`nora_to_beir._build_text`) emits for
  both modes; dropped the old `**Context**:` wrapper (helper self-labels now).
  **NORA chunk builder** (`_build_chunk_text`) emits `path_and_content` only —
  `path` is suppressed because the existing `[Path: …]` breadcrumb already
  carries it (avoids duplication); builds a per-tree `{section_number:
  (title, body)}` index from the empty-`req_id` section nodes.
- Profiles: `bs_d7a2c81f` → `path`, `bs_5114ac92` → `path_and_content`.
- Tests: new `test_chunk_builder_context.py` (3 cases) + reformatted
  helper/adapter assertions. Full suite **1291 passed, 109 skipped**.
- Docs: D-DRAFT-5 rewritten in decisions-draft; mno-b-spec context section +
  status reworked; parser/profiler/vectorstore MODULE.md updated.

### In progress
- (none — D-DRAFT-5 rework complete, uncommitted pending this close-session)

### Next
- Commit + push the D-DRAFT-5 rework (pre-push redaction scan).
- Ingest a real multi-MNO / multi-release set and verify the leading-id +
  per-plan + Context path end-to-end on real corpora (landing-gate prerequisite).

### Flags
- D-DRAFT-1..5 all unlanded; landing gate stands (no /land-strand until
  multi-release ingested + verified).
- #4 ancestor-chain ambiguity now resolved: `5 → 5.1 → 5.1.2 → 5.1.2.3`
  (the "5.1.3" was a sibling typo) — confirmed by the user this session.

## 2026-06-19 — Implement multi-MNO ingestion (D-DRAFT-6..10, 12)

### Done this session
- **Designed then implemented** the per-cell multi-MNO ingestion pipeline.
  Design first (decisions-draft D-DRAFT-6..12 + `multi-mno-ingestion-design.md`
  + SIRA cross-strand note), then 8 implementation commits (all pushed):
  - **D-DRAFT-6** — `core/src/extraction/release_key.py` (MMMYYYY core util,
    mirrors `sandbox/sira_cells.py`); `infer_metadata_from_path` enforces it
    fail-loud (`EXT-E004`). `core/src/pipeline/cells.py` (`Cell`, per-cell/global
    stage partition, `enumerate_input_cells`, cell-aware `stage_output`). Per-cell
    stage I/O: `run_extract` routes IRs to `out/extract/<mno>/<rel>/`, `run_parse`
    writes per-cell trees; global readers (taxonomy, graph/vectorstore builders,
    standards collector, req-browser, SIRA adapter) → `rglob`. [10f3aa9, 28866a0, faa5612]
  - **D-DRAFT-7** — `core/src/env/profile_bindings.py` (`ProfileBindings` +
    `<env_dir>/profiles.json`); `run_profile` resolves/substitutes/materializes
    per-cell `profile.json`, fail-loud (`PIP-E003`) on uncovered cells,
    auto-profiler removed; `run_parse` reads materialized profile raw. [3d9bf49, faa5612]
  - **D-DRAFT-8** — `--mno`/`--release`/`--force` scope + `profile_fingerprint`
    skip (extract mtime, parse fingerprint+mtime); `RequirementTree.profile_fingerprint`. [6072d65]
  - **D-DRAFT-9** — taxonomy corpus-fingerprint cache (`out/taxonomy/.corpus_fingerprint`);
    temp=0 was ALREADY in `FeatureExtractor`, only the cache was new. [e383e25]
  - **D-DRAFT-10** — `run_resolve` per-cell → structural mno-scoping (no resolver
    code change). [faa5612]
  - **D-DRAFT-12** — SIRA adapter `_load_trees` rglobs nested `out/parse/<mno>/<rel>/`. [faa5612]
- Both ingestion cases work end-to-end: full (`--start extract --end graph` +
  `profiles.json`) and incremental (same command, auto-skip; or `--mno <new>`).
- ~80 new tests; full suite 1356 passed. MODULE.md updated (extraction, pipeline,
  env, resolver, parser) — all additive.
- Earlier in session: D-DRAFT-5 rework follow-through, STATUS strand pointer,
  mno-b-spec SIRA-ingest runbook.

### In progress
- (none — ingestion design fully implemented + pushed)

### Next
- **D-DRAFT-11** — per-cell vectorstore + NORA query-side cell routing/fusion
  (the only unimplemented decision; large, query-side; sequenced last).
- **OA → `VZW-OA/Feb2026` migration** (work-PC data op) + real end-to-end
  validation of the per-cell pipeline on the work corpus.

### Flags
- **Stage funcs aren't exercised by the existing integration tests** (which call
  the parser directly), so the per-cell `run_*` chain is unit-tested on tiny
  fixtures but NOT validated end-to-end on real PDFs — do that on the work PC.
- D-DRAFT-9 temp=0 was pre-existing; only the fingerprint cache is new (noted so
  the STATUS taxonomy-non-determinism flag isn't assumed fully closed).
- D-DRAFT-1..12 all unlanded; landing gate stands (full + incremental ingest
  verified on a real multi-MNO/release set, cross-MNO no-leak confirmed).

## 2026-06-20 — D-DRAFT-11 (per-cell vectorstore + query routing) + D-DRAFT-13 (section/appendix exclusion) + verification fixes

### Done this session
- **D-DRAFT-11 — per-cell vectorstore + query-side cell routing** (3 slices, all pushed):
  - Slice 1 [2868ddc]: `run_vectorstore` builds one ChromaDB per `(mno, release)`
    cell at `out/vectorstore/<mno>/<rel>/`; new `vectorstore/cell_loader.py`
    (`load_cell_stores`, `FLAT_CELL` fallback).
  - Slice 2a [9ef3c7f]: cell-aware `QueryPipeline` (`cell_stores=` param,
    `_select_cells` routing). Single cell → its retriever directly; multiple
    cells → per-cell retrieve (no per-cell rerank) → merge → rerank-once
    (merge-then-rerank, user-confirmed fusion). Flat store → `{FLAT_CELL: store}`
    so single-store callers unchanged.
  - Slice 2b [b76cc83]: wired eval / web route / query_cli through
    `load_cell_stores`; `EvalRunner` gained `cell_stores`.
- **D-DRAFT-13 — non-normative section/appendix exclusion** (NEW decision):
  - `exclude_section_pattern` [abe61e3]: regex on section *title*; drops matching
    section + descendants (REFERENCES). Runs after reference-list extraction so
    `reference_list_map` is still populated. Generalized glossary drop into
    shared `_drop_section_subtree`.
  - `content_end_marker` [06edf7f]: traceability appendix isn't a titled section —
    a marker line + section→req_id matrix + test-case tables get glued onto the
    LAST real requirement. Regex per body line truncates the req's text before the
    marker and clears its trailing tables/images. Symmetric to
    `content_start_section`. Both support `<TRACEABILITY>` placeholder.
  - `bs_d7a2c81f` (MNO-A): `exclude_section_pattern` = references; `content_end_marker`
    = `<TRACEABILITY>`.
- **Work-PC verification fixes:**
  - `resolve_llm_model` wired into `run_cli` [8d20072] — `NORA_LLM_MODEL` was
    silently ignored (CLI passed args.model straight through), causing fallback
    to mock taxonomy.
  - eval reranker gated on `resolve_reranker_enabled()` [0916da5] — eval
    unconditionally constructed the cross-encoder → HF reach-out even when
    disabled. Now no HF traffic with reranker off.
  - `build_context_max_chars` per-ancestor cap [64a5a56] — `path_and_content`
    (MNO-B) produced giant chunks the embedder truncated; bs_5114ac92 → 2000.
  - oversize-chunk logging by `chunk_id` [bcd2a99] — builder pre-scan names the
    req being truncated (embedder only knows batch index).
- Docs: verification runbook + D-DRAFT-11 §D query checks [d53505d, 1f1def8].
- All suites green throughout (~110 new tests across the session). Working tree
  clean, all pushed.

### In progress
- (none code-wise — all committed/pushed)

### Next
- **WebUI evaluation** of the multi-cell query path (the one D-DRAFT-11 consumer
  not yet exercised) — deferred to a combined eval with `multi-mno-sira`.
- Switch to `multi-mno-sira`: ingest the multi-MNO corpus (adapter already reads
  nested `out/parse/<mno>/<rel>/`, D-DRAFT-12), reconcile `sira_cells.py` onto the
  core `release_key` util (cross-strand amendment), run/verify build+enrich.
- Then `/land-strand multi-mno-nora` (after WebUI sign-off) → promotes D-DRAFT-1..13.

### Flags
- **WebUI query path UNVERIFIED** — `routes/query.py` wired to `load_cell_stores`
  but not run end-to-end. If the combined eval surfaces a NORA-web bug, fix it
  bound to this strand before landing.
- **Category-1 oversize chunks remain** (legitimately long reqs, table-heavy) —
  accepted, not truncated; chunk-splitting deferred (aligns with the standing
  token-dense-chunks STATUS flag). `content_end_marker` clears ALL tables on a
  marked req (assumes marker = trailing appendix) — over-drops if a legit table
  ever precedes the marker; safe for this corpus.
- D-DRAFT-1..13 all unlanded; landing gate stands (full + incremental + WebUI
  verified, cross-MNO no-leak confirmed).

## 2026-06-27 — Path-B LLM-select synthesis, balanced pin, faithful table inlining (core)

### Done this session
- **Path-B — LLM-select synthesis** (`7c926f6`, `NORA_SIRA_SYNTH_MODE=llm-select`,
  default off). The cross-encoder reranker scores surface similarity and misses
  domain term variants — it dropped the source-of-truth MNO-A chunk because it
  says "SA NR" not "5G". Path-B bypasses the reranker: fetch all balanced BM25
  candidates with full text (SIRA `text_chars`), round-robin-pack across cells
  under a token budget (`NORA_SIRA_SYNTH_TOKEN_BUDGET`, default 120K for a 128K
  model), group by MNO/release, and feed them to the telecom LLM in ONE
  select+synthesize call. Citations extracted corpus-agnostically (any req_id
  format, not the synthesizer's VZ_REQ_-only regex). → D-DRAFT-14.
- **Balanced pin mode** (`da49ad5`, `NORA_SIRA_PIN_MODE=balanced`): round-robin
  the SIRA-pinned chunks across cells so the synthesizer sees both MNOs — the
  rerank-pin lane's counterpart to SIRA's balanced fusion (multi-mno-sira
  D-DRAFT-16). Found insufficient alone (SIRA's top_k cut starves the input
  first), which motivated the SIRA-side fusion balance. → D-DRAFT-16.
- **Faithful table inlining — core mechanism** (`deb6493`): the parser inlines
  each table's markdown into `req.text` at its document position
  (`render_table_markdown` + the block-loop append, so intro→table→note order is
  preserved); `ChunkBuilder._table_to_markdown` delegates to the shared parser
  renderer (vectorstore→parser) and no longer appends separately. NORA RAG and
  the SIRA corpus both read the faithful text. Decision drafted on multi-mno-sira
  as **D-DRAFT-17** (driven by the SIRA band-query); referenced here, not
  re-drafted. `include_tables` is now vestigial.
- **Web UX / observability:** RAG-list relabel for the SIRA lane
  ("Synthesized from", `pinned_synth`, hide dense_score) (`7ee8da7`); synth-mode
  caption (`Path-B · LLM-select` badge) + startup-log line (`73e2dda`); synth
  timing in the SIRA lane (`expand·search·rerank·synth`) (`6a5e0b3`).

### In progress
- Path-B awaiting work-PC verification (the post-table-fix full enrich is
  running). Expect Path-B to now select + cite the FR2 "SA NR" band chunk.

### Next
- Verify Path-B end-to-end once the enrich + re-emit complete (FR2 chunk lands,
  both MNOs represented, `synth_ms` tolerable on the 128K call).
- `regen-map` to refresh Structure sections (new public functions:
  `render_table_markdown`, Path-B helpers in playground, `_rerank_sorted`) and a
  `/drift-check dev-full` — both deferred from this close (see Flags).
- Decide whether Path-B becomes the default lane or stays opt-in after eval.

### Flags
- **Draft-ID numbering:** code comments + multi-mno-sira cross-refs call NORA's
  balanced pin "D-DRAFT-16", but this strand's drafts only ran to 13. Assigned
  Path-B=D-DRAFT-14, balanced-pin=D-DRAFT-16 (to match the existing refs);
  D-DRAFT-15 intentionally unused. All renumber to canonical D-XXX at land.
- **regen-map / drift-check deferred** — new functions added across parser /
  vectorstore / web; Structure sections are stale until regen-map.
- **Path-B caveats:** citations rely on the LLM writing req_ids verbatim; 128K
  single-call latency is eval-grade not production; capped giant tables still
  lose rows past the cap (re-chunking is the deeper fix).
- Two flaky tests to ticket: `MockEmbedder` hash-seed (`test_query`) + asyncio
  event-loop isolation (`test_playground_helpers`).

## 2026-06-28 — select-synth productionization: keyless provider, per-model reasoning sentinel, rename

### Done this session
- **Keyless OpenAI-compatible provider** (`9100556`): `OpenAICompatibleProvider`
  mandated `api_key` at construction, so a no-auth endpoint raised `ValueError`
  and the pipeline runner silently fell back to `MockLLMProvider` (canned
  answers). This was the *actual* cause of the "LLM calls failing / can't find
  NORA_LLM_*" report — the SIRA service was fine (`shim_url` correct,
  `/sira-query` 200); the **web** provider was the culprit. Made `api_key`
  optional; omit the `Authorization` header when empty. Tests: keyless
  construction allowed, no-header-when-keyless, with-key path unchanged.
- **Reasoning-leak handling for select-synth** (`6250e96`, `45fd4d1`, `4a720b5`,
  `60c188c`): the proprietary "thinking" LLM leaked chain-of-thought into
  answers; Qwen3/Gemma did not. Diagnosis ladder: `_strip_reasoning` for
  `<think>` tags first; then an opt-in raw dump (`NORA_LLM_DEBUG_RAW`, repr-level,
  OFF by default to keep model output out of logs) proved the model emits
  **untagged** CoT (no tags, empty `reasoning_content`). Fix: a final-answer
  sentinel (`===FINAL_ANSWER===`) — the select-synth prompt instructs the model
  to print it, `_strip_reasoning` drops everything before the last occurrence.
  Gated **per-model** by `NORA_LLM_REASONING_SENTINEL` (default off — most models
  skip thinking natively and shouldn't have output reshaped); the prompt
  instruction and the strip move together off one switch (the prompt imports the
  flag). Superseded a brief configurable-marker-text attempt (`7d74aee`) after
  the user clarified they wanted an on/off toggle, not a configurable string.
  → D-DRAFT-17.
- **`Path-B → select-synth` rename** (`c89ac72`): "Path-B" named nothing; renamed
  to state the function — the LLM **selects** relevant chunks and **synth**esizes
  in one call. Symbols `_PATHB_*`/`_pathb_*` → `_SELECT_SYNTH_*`/`_select_synth_*`;
  env vars `NORA_SIRA_PATHB_*` → `NORA_SIRA_SELECT_SYNTH_*` (legacy read with a
  deprecation log); `NORA_SIRA_SYNTH_MODE` accepts `select-synth` (legacy
  `llm-select` still enables via the new `_SELECT_SYNTH_ENABLED` flag). Startup
  log surfaces `reasoning_sentinel=<bool>` per stack.

### In progress
- Qwen3-vs-proprietary A/B is operational: both stacks keyless + select-synth;
  the proprietary stack runs with the sentinel on; the pooled feedback DB
  (`llm_model` column) keeps rows attributable per model.

### Next
- Band-chunk verification: confirm select-synth selects + cites the FR2 "SA NR"
  chunk, then compare the two LLMs via the feedback DB (`GROUP BY llm_model`).
- `regen-map` for the renamed select-synth helpers + `drift-check dev-full`
  (still deferred — see Flags).
- Decide select-synth default-vs-opt-in after the eval.

### Flags
- **Token-waste caveat:** the sentinel *extracts* the answer but the model still
  *generates* the thinking, which counts against `max_tokens` (4096) — long
  reasoning could truncate the answer. Cleaner fix is native thinking-disable
  (e.g. `chat_template_kwargs={"enable_thinking": false}` / `/no_think`), which
  needs provider `extra_body` support — not built.
- `decisions-draft.md` still names the old `_pathb_*` symbols (historical draft
  body) — reconcile at land time.
- Carried: `regen-map` / `drift-check` deferred (now also the select-synth
  rename); two flaky tests to ticket (`MockEmbedder` hash-seed, asyncio
  event-loop isolation).
