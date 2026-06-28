# multi-mno-nora — draft decisions

Draft decisions for this strand. Promoted to canonical `DECISIONS.md` with real
`D-XXX` IDs at `/land-strand` time.

---

## D-DRAFT-1 — Multi-plan documents: promote plan to a per-requirement attribute (Option B), not one-tree-per-plan

**Context:** NORA models "plan" as one-per-document: `RequirementTree.plan_id`
/ `plan_name` are single scalars (set from a first-page metadata regex), the
parser emits one tree per source document (a load-bearing invariant — re-run
incrementality and per-document parse logs key off it), and the SIRA adapter's
multi-granularity rows group every requirement under the single `tree.plan_id`
(one `doc:<plan>` row per document). This fits MNO-A (one docx = one plan).
MNO-B's requirements arrive as a **single PDF whose sections each correspond to
a plan**, so under the current model all of MNO-B's plans would collapse into
one. Grounding showed the plan is recoverable per-requirement: req_ids carry a
plan prefix, and `_extract_plan_id_from_req` (driven by the profile's
`RequirementIdPattern.components` = separator + plan_id_position) already
derives it — it's currently computed transiently for cross-reference
classification, then discarded.

**Decision:** Adopt **Option B** — promote plan to a per-requirement
attribute, keeping one tree per document:
- Add a `plan_id` field to `Requirement`, populated during parse via the
  existing profile-configured req_id plan-extraction.
- Change the SIRA adapter to group `doc:<plan>` / `section:<plan>` rows by
  **per-requirement `plan_id`** instead of `tree.plan_id`, so one document
  yields N plans.
- Apply the same per-req plan treatment in the graph builder (FR-7 organizes
  the KG by plan).
- The "one tree per document" invariant is unchanged; plan becomes a
  *within-tree* dimension rather than a tree-level scalar.

**Why:** The plan is already encoded in req_ids and the extraction mechanism
already exists, so B is surgical (store what's already computed; group by it)
and stays profile-driven — MNO behavior lives in the profile, not code
(D-003 / FR-3). It preserves the load-bearing one-tree-per-document invariant.
Alternatives rejected: **Option A** (split a document into N trees at parse
time) breaks that invariant — bigger blast radius across re-run
incrementality and parse-log-per-doc, for no benefit B doesn't already give.
**Option C** (pre-split the PDF into N files before extraction) needs a
reliable PDF-splitter and loses the single-document reality — brittle
operational machinery outside the pipeline.

**Consequences:**
- `Requirement` schema gains `plan_id` (additive). Consumers that grouped by
  `tree.plan_id` (adapter, graph) move to per-req grouping.
- `tree.plan_id` becomes ambiguous for a genuinely multi-plan document — keep
  it as the document's primary/first plan for back-compat, but it is no longer
  the authority for plan grouping. (Define its exact semantics when wiring the
  parser change.)
- The **profile-extraction mechanism is config-only when the plan prefix is
  delimited** (e.g. `PLANB-123` -> set `separator` + `plan_id_position: 0`,
  exactly the MNO-A shape). If MNO-B's prefix is **run-together**
  (`PLANB123`, no delimiter), the split-on-separator model can't separate
  prefix from number and `RequirementIdPattern` needs a small **generic**
  extension — a regex-capture-group plan-extraction mode (still profile-driven,
  usable by any future corpus, not MNO-specific). Which shape MNO-B uses is the
  one open detail, resolved on inspecting the real document. The parser /
  adapter / graph changes are identical either way; only the profile's
  extraction config differs.
- Single-tree-per-document MNO-A corpora are unaffected: their requirements
  all share one plan prefix, so per-req grouping yields the same single
  `doc:<plan>` as today.

---

## D-DRAFT-2 — Add a generic "leading-id body-block" requirement-detection mode (MNO-B flat-requirement model)

**Context:** NORA's parser detects requirements **heading-first**. In
`_build_sections` (`core/src/parser/structural_parser.py`), a `Requirement` is
constructed in exactly one place in the paragraph pass — line ~1547, gated on
`_heading_depth(block) is not None` (the block is a heading). The only other
source is table-cell anchors (second pass, behind
`enable_table_anchored_extraction`). A plain body paragraph falls through to the
body-text path (~line 1601): its text is appended to the enclosing
heading-section, and if that section has no req_id yet, the body's inline req_id
is assigned **to the section** (first-occurrence-wins). The two documented
anchor sources are "paragraph anchors (heading or standalone-ID-in-small-font)
and table-cell anchors" (line 593-596) — there is no "body block whose text
starts with a req_id" primitive.

MNO-B's document model (confirmed by inspection) is the opposite shape:
sections/subsections are **non-requirement context** (no req_ids), and each
requirement is a **flat body paragraph beginning with its req_id**
(`<COMMON PREFIX>-<PLAN>-<DIGITS>`). Under the current parser, a subsection
containing N such requirements collapses into a **single** `Requirement`: only
the first req_id survives (as the subsection's id); the remaining N-1 are buried
as plain text in the section body and lost as structured requirements. So MNO-B
cannot be onboarded by a profile alone — the detection primitive it needs does
not exist in the parser.

**Decision:** Add a **generic, profile-selectable requirement-detection mode** —
"leading-id body-block" — alongside the existing heading-anchored mode:
- When active, the parser emits **one `Requirement` per body block whose text
  begins with the configured req_id pattern**. The req_id leads the requirement
  text; the rest of the block is the requirement body.
- Section/subsection **headings remain structural context**: they maintain the
  hierarchy stack so each leading-id requirement gets a `parent_section` /
  `hierarchy_path` from the enclosing headings — but headings themselves are
  **not emitted as requirements** in this mode (they carry no req_id).
- The mode is selected by a new **profile** field (working name
  `requirement_detection.mode: "heading" | "leading_id_body"`, default
  `"heading"`); MNO behavior stays in the profile, not in code (D-003 / FR-3).
- It **composes with D-DRAFT-1**: the per-requirement `plan_id` and the
  `RequirementIdPattern.components` plan-extraction apply unchanged on top of the
  new mode. MNO-B's `<PREFIX>-<PLAN>-<DIGITS>` is hyphen-delimited, so plan
  extraction is config-only (`separator: "-"`, `plan_id_position: 1`).

**Why:** A profile cannot express MNO-B because the underlying detection
primitive ("requirement = leading-id body block") is missing — this is a
genuine capability gap, not a config gap. A new **generic** mode (usable by any
future flat-requirement corpus, not MNO-specific) keeps the
behavior-lives-in-profile contract intact, reuses the existing req_id regex /
components / plan-extraction machinery, and leaves the heading-anchored path —
and therefore all MNO-A corpora — untouched (default mode stays `"heading"`).

Alternatives rejected:
- **Pre-process MNO-B into a heading-shaped document** (promote each leading-id
  paragraph to a synthetic heading before parse): brittle document-mangling,
  loses fidelity, and pushes corpus-specific logic into an out-of-pipeline
  pre-step — the same objection as D-DRAFT-1's rejected Option C.
- **Hack leading-id paragraphs through the existing heading path** (make the
  `numbering_pattern` match req_ids so each requirement looks like a heading):
  conflates structure with requirements — it pollutes `hierarchy_path` and
  `zone_type` classification, collides with the heading-continuation defenses
  (lines ~1497-1528), and makes genuine section headings and requirements
  indistinguishable downstream.

**Consequences:**
- New **profile** field (`requirement_detection.mode`, additive; default
  preserves today's behavior). `profile_schema.py` gains the field.
- Parser **`_build_sections` gains a mode branch**: in `leading_id_body` mode,
  body blocks matching the req_id pattern construct a `Requirement` (parent =
  current heading section, `hierarchy_path` from the heading stack) instead of
  appending to the section; headings build the hierarchy but are not emitted as
  requirements.
- **Open details, resolved when wiring + against the real document:** (1) final
  config field name/shape; (2) whether non-requirement headings are retained as
  structural-only nodes in the tree (for `hierarchy_path` / context / glossary +
  applicability passes) or elided after the hierarchy is built — the
  applicability (`_apply_applicability`), glossary, and reference-list passes all
  walk `sections`, so heading retention vs elision must be chosen so those passes
  still work; (3) interaction with the table-anchored second pass (likely
  mutually exclusive with this mode for MNO-B, but should not be hard-coded off).
- MNO-A corpora unaffected — default `"heading"` mode is the current code path
  byte-for-byte.
- This is **parser/profiler architecture work that must land before** an MNO-B
  profile can be authored — a profile written against the current parser would
  silently collapse MNO-B's requirements.

---

## D-DRAFT-3 — Preserve PDF source line boundaries additively (`ContentBlock.lines`), not by changing `block.text` or splitting on color

**Context:** The PDF extractor groups several pymupdf source lines into one
paragraph block and `_make_group` flattens them with `" ".join(...)`. For
MNO-B that merges a heading/title line and the body line beneath it into a
single **run-on sentence** (e.g. `5.1.2 Idle Mode The device shall…`, or
`ABC-PLAN-123 <title> The device shall…`), which **blurs the section hierarchy
for the LLM synthesizer** — it can't tell where the heading/title ends and the
body begins (MNO-B observation #5). The obvious signal — the title's blue
color — is **not usable for this corpus**: pymupdf reports the blue title text
as `color: 0` (the blue isn't a glyph fill color it surfaces), blue is *also*
used for section titles, and purple appears in *both* hyperlinked titles and
body hyperlinks. So color cannot delimit the title. What pymupdf *does* give us
reliably is the **line structure** (`block["lines"]`), which the extractor was
discarding.

**Decision:** Preserve the source line split **additively** on the IR. Add
`ContentBlock.lines: list[str]` (one entry per pymupdf source line);
`_extract_text_segments` tags each span with its line index and `_make_group`
reconstructs the per-line strings. Keep `block.text` exactly as before with the
invariant **`" ".join(lines) == text`**, so detection regexes (heading
numbering, req-id match) read the unchanged `text`; only consumers that need to
separate a heading/title line from the body read `lines`.

**Why:** Additive → **zero detection regression and a no-op for existing
corpora** (Verizon-OA never reads `lines`); robust to the color unreliability
(uses the line structure pymupdf actually provides, not the color it doesn't);
and it keeps the extractor **generic** — the *semantic* heading/body split stays
in the profile-driven parser, not hard-coded in extraction.

Alternatives rejected:
- **Change `block.text` line-join `" "` → `"\n"`** — global blast radius on the
  parser's `^`/`$`/`\s`/`.+` regexes; needs a full re-validation for a gain a
  side field delivers risk-free.
- **Split blocks on font color** — pymupdf doesn't surface this PDF's title
  color (`0` for blue), and blue/purple are ambiguous across section titles and
  hyperlinks; not a dependable signal here.
- **Emit a separate block per source line** — fragments multi-line body
  paragraphs and changes block granularity for all corpora.

**Consequences:**
- IR gains `ContentBlock.lines` (additive; empty for legacy IRs and non-PDF
  extractors — DOCX/XLSX can populate later if needed). `models` +
  `extraction` MODULE.md updated.
- The `leading_id_body` parser will **consume** `lines` to (a) separate a
  requirement's title line from its body and (b) build the ancestor-section
  "Context" with headings distinct from body — **not yet implemented**; lands
  with the MNO-B parser design.
- Verified end-to-end through real pymupdf and on the actual MNO-B PDF.
- A **multi-line requirement title** still can't be split from the body (the
  line boundary alone can't tell where a wrapped title ends without the
  unavailable color signal) — deferred nicety; the section hierarchy the
  synthesizer needs is delivered regardless.

---

## D-DRAFT-4 — Profile-driven content-start cutoff (skip front matter + intro chapters, anchored at a configurable Chapter N)

**Context:** MNO-B's single PDF is laid out as: front/title page → Table of
Contents → Chapter 1 (Preface) → Chapter 2 → Chapter 3 … Chapter N. From a
requirements perspective only **Chapter 3 onward** matters (each top-level
chapter from the start point is a plan; Ch.1/2 are general info with no
req_ids). The parsed tree must begin at the first requirements chapter. The
existing front-matter cutoff only drops TOC + revision-history
(`max(toc_end, revhist_end)`), **not real intro chapters**. And the front matter
is hard to detect *negatively* here: the TOC has **no `toc` style set**
(style-driven TOC detection is inert) and the leader-dot text pattern is
unreliable on this PDF (page numbers split into separate blocks); Chapters 1–2
have no clean drop marker.

**Decision:** Add a **profile-driven content-start cutoff** anchored at a
**configurable** top-level chapter number **N** (NOT hardcoded). New profile
field — working name `content_start_section` (string; empty = disabled). A
parser **pre-pass drops every block before the first *real heading*** (heading-
level font: bold + heading size) whose **top-level section number equals N**.
One positive anchor subsumes all the front material — front page, TOC, and
Chapters 1…(N-1) all precede Chapter N and fall away — with **no negative
TOC/intro detection required**.

**Font-gating (the one wrinkle):** a TOC *entry* for Chapter N (`N  Title … 45`)
also carries section number N, so the cutoff must distinguish the real heading
from its TOC line. It does so by **font** — the real chapter heading is bold +
chapter-size; the TOC entry is body-size. PyMuPDF captures size/bold reliably
(unlike color, which it does not surface for this corpus), so the cutoff fires
only on a heading-font block numbered N.

**Why:** A positive "content starts here" anchor is the single reliable signal —
chapters are numbered and Chapter N's heading is bold/sized/`N`. The negative
alternatives are fragile: TOC detection has no usable style field and a flaky
text pattern; per-chapter content drop has no marker. Keeping **N configurable**
(not hardcoded to 3) means a future release that adds/removes a front chapter is
a one-line profile edit, not code. Rejected: "start at the first top-level
chapter that *contains* a req_id" (fully automatic) — needs a look-ahead pass;
more machinery than warranted for a per-corpus constant.

**Consequences:**
- New profile field `content_start_section` (additive; empty default → **no-op**,
  so Verizon-OA and every existing corpus are unaffected — no gate beyond the
  empty default).
- Parser gains a pre-pass cutoff that runs **before** the existing TOC/front-
  matter logic and drops the contiguous front region in one shot.
- For MNO-B the profile sets `content_start_section: "3"`.
- Implementation detail: the font check reuses `FontInfo.size`/`bold` +
  `heading_detection.levels` hints to decide "real heading vs TOC entry"; exact
  threshold settled when wiring.

---

## D-DRAFT-5 — Consume `ContentBlock.lines` in the leading-id parser: split requirement title/body + populate a separate `Requirement.context` field

**Context:** In `leading_id_body` mode a requirement was built with everything
glued into `Requirement.text` — the req_id, the title, and the body were one
flattened run-on (the parser built `text` from `block.text`), and the enclosing
section/subsection chain (non-requirement *context* in this model) wasn't
attached to the requirement at all. So the LLM synthesizer couldn't tell the
requirement's title from its body, nor see where the requirement sits in the
`5 → 5.1 → 5.1.2` hierarchy. D-DRAFT-3 already preserved the per-line split on
`ContentBlock.lines` (`" ".join(lines) == text`), and sections carry their
heading + preamble text — the parser just wasn't using either.

**Decision:** Two parts — a parser split, and a generic **consumer-assembled**
context (not materialized in the tree, to avoid per-requirement duplication —
the bloat concern: one PDF holds all plans, and copying each requirement's full
ancestor-section content into the tree would multiply section text by the number
of requirements under it).
- **Title/body split (parser, `leading_id_body`):** a requirement's `title` is
  the header line (`lines[0]`) after the leading req_id; `text` is the body (the
  remaining lines), via the preserved `ContentBlock.lines` (D-DRAFT-3). Falls
  back to empty title + whole-block `text` when a block has no `lines`.
- **`build_context` profile knob (generic, all MNOs):** `"none" | "path" |
  "path_and_content"`. Stamped onto `RequirementTree.build_context`. Rendering
  (settled with the user):
  - `path` → a single-line breadcrumb wrapped in a label:
    `[Context: 5 Bands > 5.1 Frequency > 5.1.2 LTE]` (number + title per hop, no
    bodies).
  - `path_and_content` → one bracketed header per ancestor followed by its body,
    top-down: `[5 Bands]` / `<5 body>` / `[5.1 Frequency]` / `<5.1 body>` /
    `[5.1.2 LTE]` / `<5.1.2 body>`.
- **`build_context_string(parent_section, section_index, mode)`** — one shared
  pure helper (in `parser/structural_parser.py`), self-labeling per the formats
  above. Anchors on `parent_section` (works in both models: leading-id's is the
  enclosing section; heading-mode's is the parent, excluding the requirement's
  own section). The **SIRA adapter** and the **NORA chunk builder** call it at
  emit time from the section nodes already in the tree, baking context into
  corpus rows / chunks. **NORA suppresses `path`** (the existing `[Path: …]`
  breadcrumb already carries it; emitting the numbered block too would
  duplicate) and emits only for `path_and_content` — which adds ancestor section
  *content* nothing else in the chunk provides. SIRA (no `[Path: …]`) emits for
  both `path` and `path_and_content`.
- **`Requirement.context`** stays as a field (the materialized shape) but is
  **left empty in the parsed tree** — context lives in the *derived* indexes
  (BEIR corpus, chunks), where self-contained rows are expected, not in the
  source-of-truth tree.

**Why:** Duplication is fine in derived retrieval indexes (each row self-
contained) but not in the source tree, which is inspected and re-run from and
must stay compact. The `path` vs `path_and_content` knob lets heading-mode
corpora (e.g. `bs_d7a2c81f` → `path`) get a lightweight breadcrumb while
leading-id corpora (`bs_5114ac92` → `path_and_content`) get the full section
bodies they need — generic across MNOs. A **separate** `context` (not prepended
into `text`) keeps requirement body vs inherited context distinguishable.
Rejected: materializing context per requirement in the tree (bloat);
per-plan-split tree files (essentially D-DRAFT-1 Option A — separate decision,
and it doesn't fix the duplication); splitting the title by font color (this PDF
doesn't expose it — D-DRAFT-3 context).

**Consequences:**
- `Requirement` gains `context` (additive, empty in tree). New profile field
  `build_context` (default `"none"` → no-op for existing profiles).
  `RequirementTree.build_context` stamped from the profile.
- Title/body split is `leading_id_body`-only (no-op for heading mode → MNO-A
  unchanged). Context assembly is generic (driven by `build_context`).
- Both consumers wired (done): **SIRA adapter** (`_build_text` appends the
  helper output as a context block in each corpus row) and **NORA chunk builder**
  (`_build_chunk_text` appends the `path_and_content` block from a per-tree
  `{section_number: (title, body)}` index; coexists with the pre-existing
  `[Path: …]` and `[Parent context: …]` blocks).
- A **multi-line requirement title** still bleeds its overflow into `text` (no
  per-span signal) — deferred. If a **section heading merges with its intro** in
  one block, that run-on carries into the context heading label — a heading-path
  `lines` split is a possible follow-up.

---

## D-DRAFT-6 — Per-cell stage-output layout + universal `(MNO, MMMYYYY)` cell convention

**Context:** The pipeline is single-`--profile`, flat-output (`out/<stage>/*`).
The strand goal is multi-MNO / multi-release ingestion (full **and**
incremental). The `multi-mno-sira` strand already organizes multi-MNO data as
`(MNO, release)` **cells** (SIRA D-DRAFT-3..6); NORA should share that unit and
vocabulary. `infer_metadata_from_path` already derives `(mno, release)` from
`input/<mno>/<release>/`. The last free-form corpus (Verizon Open Access at
`input/VZW/OA-baseline/`) is being promoted to a real cell
(`input/VZW-OA/Feb2026/`), removing the only non-MMMYYYY holdout.

**Decision:** The **`(MNO, release)` cell** is NORA's unit of layout, keyed on the
input convention `input/<MNO>/<MMMYYYY>/` (mirrors SIRA D-DRAFT-5 — the dir name
is both label and sort key, `Feb2026 → 2026-02`). Stage outputs split into two
classes:
- **Per-cell** — `out/<stage>/<mno>/<rel>/`: **extract, profile, parse, resolve,
  vectorstore**.
- **Global** — `out/<stage>/`: **standards, taxonomy, graph, eval**.

MMMYYYY is **universal and validated unconditionally** (fail-loud at ingest) via a
shared **core** util — `release_key(name) -> (label, order_key)`, raising on
non-MMMYYYY — that `infer_metadata_from_path` calls. **Verizon OA is treated as
its own MNO** `VZW-OA`, release `Feb2026`.

**Why:** The cell is the consistent unit across NORA + SIRA (same vocabulary, same
ordering, same provenance). Directory-driven partitioning removes all
metadata-grouping logic from parse — the directory *is* the partition. Per-cell
for stages whose output is document/retrieval-scoped (extract/profile/parse/
resolve/vectorstore); global for the cross-cell KG layer (graph), its shared
inputs (taxonomy, standards), and eval. Promoting OA to a real cell makes MMMYYYY
**universal**, which is what lets validation be **unconditional in core** — no
cell-mode gate — because there is no longer any free-form corpus to protect.
**This supersedes SIRA D-DRAFT-12's placement:** that decision kept MMMYYYY
validation sandbox-side *solely* to avoid breaking the free-form `OA-baseline`;
once OA is migrated, the protection is obsolete, the convention is universal, and
the logic belongs in a shared **core** util (module boundary preserved — sandbox
→ core; SIRA's `sira_preflight` calls the same util). Rejected: flat dirs +
parse-time metadata grouping (more parse logic, no structural isolation); per-MNO
(not per-cell) directories (loses the release axis that release-diff needs — SIRA
D-DRAFT-3); cell-mode-gated validation (unnecessary once OA migrates to MMMYYYY).

**Consequences:**
- New per-cell directory tree under `out/`; graph / taxonomy / standards / eval
  stay flat (global).
- Shared **core** util for MMMYYYY parse/validate/order; `infer_metadata_from_path`
  enforces it. **Amends SIRA D-DRAFT-12** (sandbox-side placement → core util) —
  flag for the `multi-mno-sira` strand to reconcile at its land time.
- **One-time migration (work PC):** `input/VZW/OA-baseline/` →
  `input/VZW-OA/Feb2026/`, then re-extract → re-ingest. Cell key
  `(VZW, OA-baseline)` → `(VZW-OA, 2026-02)`; req_ids are unchanged (`VZ_REQ_…`),
  so eval ground-truth + the integration test hold, but mno/release-keyed chunks +
  graph nodes re-key (graph/vectorstore rebuild).
- `VZW-OA` cleanly separates the public OA corpus from a future *proprietary* VZW
  corpus (its own MNO) and keeps the Verizon name confined to the OA context per
  the redaction rule.
- Single-MNO is just a **one-cell** env (`--profile` still works); no free-form
  path remains anywhere.

---

## D-DRAFT-7 — Per-cell profile binding: `<env_dir>/profiles.json` → `out/profile/<mno>/<rel>/profile.json`

**Context:** With per-cell parse (D-DRAFT-6), each cell needs its own profile
(`VZW-OA` → `bs_d7a2c81f` heading model; MNO-B → `bs_5114ac92` leading-id model).
A single `--profile` per run can't express that.

**Decision:** A binding manifest `<env_dir>/profiles.json` maps
`(mno, release) → profile`. The **profile stage resolves bindings and
materializes each cell's resolved + substituted profile to
`out/profile/<mno>/<rel>/profile.json`**; parse reads each cell's profile from its
own directory. Resolution precedence per cell: `--profile` (one-off global
override) → exact `(mno, release)` → `(mno, "*")` → `default` → **fail loud**
(`PIP-E0xx`). `load_substituted_profile` runs per cell so each MNO's placeholder
mapping applies. A bare `--profile` synthesizes a one-cell wildcard binding.

```jsonc
{ "bindings": [ { "mno": "<mno>", "release": "*", "profile": "customizations/profiles/<id>.json" } ],
  "default": null }
```

**Why:** The binding lives with the env (reproducible, works in `--env` and
`--env-dir` modes) and mirrors the existing per-profile mapping-file pattern.
Materializing the resolved profile **per cell** keeps parse purely
directory-driven (read `out/profile/<cell>/profile.json`, parse
`out/extract/<cell>/`) and gives transparency — Parse-Review can show each cell's
effective profile. Fail-loud on an uncovered cell (vs. auto-profiling) because
these corpora use hand-authored profiles. Rejected: CLI-only `--profile-map` (not
reproducible); per-input-dir sidecars (scatters config across the runtime tree);
auto-`DocumentProfiler` per cell (wrong for hand-authored corpora).

**Consequences:**
- New `<env_dir>/profiles.json`; `ProfileBindings` loader/resolver (`env`);
  `EnvironmentConfig.profile_bindings` for `--env` mode.
- `run_profile` → resolve + validate + materialize per cell; `run_parse` reads the
  per-cell profile (no in-memory grouping).
- Back-compat preserved via wildcard synthesis (single-MNO `--profile` unchanged).

---

## D-DRAFT-8 — Incremental cell ingestion: per-cell stages skip/scope; global stages rebuild over all cells

**Context:** Beyond initial full ingestion, the strand must support **incremental**
adds — a new cell (new MNO or new release) dropped in later, ingested without
redoing existing cells. The per-cell layout (D-DRAFT-6) makes a cell's on-disk
outputs the natural state.

**Decision:** Per-cell stages (extract/profile/parse/resolve/vectorstore) are
**idempotent + scopable**:
- **Skip-if-present-and-unchanged** by default: a cell's per-cell outputs are
  reused when its inputs are unchanged. Parse stamps a **`profile_fingerprint`**
  (hash of the substituted profile) onto each tree, so a profile/mapping edit
  invalidates exactly that cell (mtime alone can't see a mapping edit).
- **`--mno` / `--release`** flags (comma-separable) scope the per-cell stages to
  specific cells; **`--force` / `--no-skip`** reprocesses regardless.
- **Global stages (taxonomy/graph/eval) always rebuild over all cells** — they
  must, to merge. Taxonomy cost is bounded by its fingerprint cache (D-DRAFT-9);
  graph is cheap; the vectorstore is per-cell, so only new cells embed.

Full and incremental are the **same command** — `run_cli --start extract --end
graph` (or `--end vectorstore`): full builds all cells; incremental skips
unchanged cells, builds only the new one, and rebuilds the global graph/taxonomy
over the union.

**Why:** Cell-presence + fingerprint is the state (no separate DB). One command
for both cases replaces the scratch-env-and-copy workaround in
`mno-b-spec.md`. Fingerprinting the **substituted** profile (not file mtime)
catches profile/mapping edits safely. Per-cell vectorstore means incremental
**embedding** falls out for free (only the new cell's store builds). Rejected: a
processed-docs state DB (redundant with on-disk cells); mtime-only skip (misses
mapping edits); incremental global graph (correctness risk; rebuild is cheap).

**Consequences:**
- `RequirementTree` gains `profile_fingerprint` (additive, serialized).
- Per-cell stages gain skip + `--mno` / `--release` / `--force`; global stages
  always run full.
- Supersedes the scratch-env workaround (the `mno-b-spec.md` runbook is updated at
  implementation time).

---

## D-DRAFT-9 — Global taxonomy + corpus-fingerprint cache + temperature=0

**Context:** `taxonomy` is the one global stage that is LLM-driven, expensive, and
**non-deterministic** (standing STATUS flag — a 3-run accuracy spread traced to
taxonomy producing different feature mappings → graph-topology shifts). With
incremental ingestion (D-DRAFT-8) every new-cell add would otherwise re-derive
over all cells. Yet a **single union** taxonomy is wanted: shared cross-MNO
features are what make comparison queries answerable (chosen over per-cell
taxonomy, which would need fuzzy cross-cell feature alignment).

**Decision:** Keep **one global** taxonomy (`out/taxonomy/taxonomy.json`) derived
over **all** cells, gated on a **corpus fingerprint** (hash of the contributing
tree set): reuse the cached taxonomy when the set is unchanged; re-derive over the
union only when it changes; run the LLM at **temperature=0**.

**Why:** A union taxonomy gives the shared feature space the global graph links
every cell's reqs to — so "compare VZW-OA vs TMO on IMS registration" works
without feature alignment. The fingerprint cache makes incremental adds cheap and
stops silent feature drift on unrelated adds; temp=0 makes a forced re-derivation
reproducible. Rejected: per-cell taxonomy + merge (loses shared features, adds a
fuzzy alignment problem); always re-derive (cost + non-determinism); dropping
union taxonomy (breaks comparison queries).

**Consequences:**
- `run_taxonomy` gains a corpus-fingerprint check + cache; the taxonomy LLM call
  is temp=0.
- Incremental runs that don't change the tree set skip the taxonomy LLM entirely;
  `--skip-taxonomy` / `--rag-only` remain valid escapes.
- A deliberate re-derivation (prompt change) needs a cache-bust flag rather than
  manual file deletion.
- Resolves the standing taxonomy-non-determinism STATUS flag for the multi-MNO
  path.

---

## D-DRAFT-10 — MNO-scoping is structural via per-cell resolve; cross-cell relations live in the global graph

**Context:** D-DRAFT-6 runs `resolve` per cell (`out/resolve/<mno>/<rel>/`) over
only that cell's trees. Cross-plan references are id-shaped
(`<PREFIX>-<PLAN>-<NUM>` / `<MNO>_REQ_<PLAN>_<NUM>`) and plan codes / numbers are
**not** globally unique across MNOs or releases — two cells can carry the same
plan or number.

**Decision:** Cross-reference resolution is **structurally cell-scoped** — the
resolver runs per cell over that cell's trees, so it can never match a reference
across MNOs or releases. No explicit mno-filter in resolver code is needed; the
per-cell layout enforces it. **Cross-cell relationships** (release-diff, shared
features, cross-MNO comparison) are **not** resolver concerns — they live in the
**global graph**. **Assumption:** cross-references stay within a `(mno, release)`
cell; if a release ever cites a *prior* release of the same MNO, resolve would
widen to per-MNO-across-release (flagged here, not built).

**Why:** Per-cell resolve makes the multi-MNO no-leak property a **layout
invariant** rather than resolver code that could regress. Clean separation:
intra-cell cross-refs = `resolve`; inter-cell relations = `graph`. Replaces the
earlier "add an mno filter to the resolver candidate set" decision (now
unnecessary). Rejected: global resolve + mno filter (more code, regressable);
assuming globally-unique ids (false across cells).

**Consequences:**
- `resolve` becomes a per-cell loop (no resolver-internal change beyond running
  per directory).
- New test: same plan/number present in two cells resolve independently (no leak).
- The **cross-release-reference** assumption is a watch item — revisit if a corpus
  is found to cite across releases of one MNO.

---

## D-DRAFT-12 — SIRA adapter reads nested `out/parse/<mno>/<rel>/`

**Context:** The SIRA adapter (`sandbox/adapter/nora_to_beir.py`) discovers NORA
parse output via `_load_trees` globbing `<env>/out/parse/*_tree.json` (flat).
D-DRAFT-6 nests parse output to `out/parse/<mno>/<rel>/*_tree.json`.

**Decision:** Update the adapter's `_load_trees` to walk the **nested**
`out/parse/<mno>/<rel>/*_tree.json` layout. The adapter's downstream `(mno,
release)` partitioning + `--multi-cell` cell emission are unchanged (trees still
carry mno/release) — only the discovery glob changes.

**Why:** The layout change is a NORA-side decision (D-DRAFT-6) that the SIRA
adapter consumes; they must move in lockstep or the adapter silently reads zero
trees. Walking the nested dirs is also more direct (the cell *is* the directory).
Rejected: keeping parse output flat just for the adapter (defeats D-DRAFT-6's
per-cell layout); a shim globbing both layouts (carries the old layout forward
needlessly once NORA migrates).

**Consequences:**
- Cross-strand lockstep change — landing D-DRAFT-6 requires this adapter update in
  the same migration.
- `sandbox/adapter` stays informal (no MODULE.md, SIRA D-DRAFT-8); track via the
  SIRA journal. The `multi-mno-sira` strand should note the coupling.

---

## D-DRAFT-13 — Profile-driven exclusion of non-normative sections + trailing appendices (REFERENCES, traceability matrices)

**Context:** The MNO-A (heading model) corpus carries content that is
structurally a "requirement" to the parser but is **not normative** and badly
bloats RAG chunks (driving them past the embedder's 8000-char input limit):
(1) **REFERENCES / bibliography** sections — a titled heading (with a trailing
req_id) whose body is a citation list; (2) a **requirement→test-case
traceability appendix** — NOT a titled section: a marker line followed by a
section→req_id matrix and test-case tables (`Test Case Name | Test Plan Id | …`)
that the parser glued onto the **last real requirement's** `text` + `tables`
(no new req_id or heading breaks it). Both surfaced as oversize chunks during
multi-MNO work-PC verification. The existing per-doc `kind=remove` annotation
(D-061) is manual; these are systematic for the corpus and want a profile rule.

**Decision:** Two complementary profile-driven mechanisms (additive, empty =
no-op):
- **`exclude_section_pattern`** — regex matched on a section **title**. Matching
  sections + descendants are dropped from the parsed tree (never become
  Requirements or chunks). Runs **after** `reference_list_section_pattern`
  extraction, so a REFERENCES section still populates `reference_list_map`
  (citation resolution preserved) before being dropped from RAG. Generalizes the
  glossary drop into a shared `_drop_section_subtree` helper.
- **`content_end_marker`** — regex matched per **body line**, for a trailing
  appendix glued onto a requirement (no heading to match). For a requirement
  whose text has a matching line, the parser truncates the text **before** that
  line and clears that requirement's `tables`/`images`. Symmetric to
  `content_start_section` (D-DRAFT-4, front cut).
- Both accept placeholders (`<TRACEABILITY>`) resolved from the work-PC mapping
  file. `bs_d7a2c81f`: `exclude_section_pattern` = `(?i)^\s*references\b`;
  `content_end_marker` = `<TRACEABILITY>`.

**Why:** Title-based exclusion is right for *sectioned* non-requirements
(REFERENCES has a heading); a *content marker* is required for the traceability
appendix because it has no heading — it's trailing text+tables on the last req,
so nothing title-based can reach it. Splitting into two knobs keeps each
mechanism simple and each matches what it targets. Extracting references to
`reference_list_map` *before* dropping keeps future indirect-citation resolution
free. Rejected: manual `kind=remove` per doc (not systematic); a single combined
knob (the two cases are structurally different — title vs body line); hard
char-cap truncation of all chunks (would silently drop *legitimate* long
requirements — kept those, see Consequences).

**Consequences:**
- New profile fields `exclude_section_pattern`, `content_end_marker` (both
  empty-default no-ops; substituted like other regex fields). New parser passes
  `_drop_excluded_sections` + `_apply_content_end_marker`; shared
  `_drop_section_subtree` (also backs the glossary drop). MODULE.md updated
  (parser, profiler).
- `content_end_marker` clears **all** of a marked requirement's tables/images on
  the assumption the marker begins a trailing appendix — over-drops if a legit
  table ever *precedes* the marker in the same req. Safe for this corpus (marker
  is a document-end delimiter); flagged if another corpus differs.
- **Legitimately long requirements** (category 1 — table-heavy normative reqs)
  are deliberately NOT truncated; they still exceed the embedder limit (vector
  on the prefix, full text stored). Chunk-splitting remains deferred (standing
  token-dense-chunks STATUS flag).
- Generic + reusable: any corpus can name its non-normative sections / trailing
  appendices via profile, no code change.

<!-- D-DRAFT-15 intentionally unused: code comments + multi-mno-sira cross-refs
     pegged NORA's balanced pin at D-DRAFT-16 before this strand's drafts caught
     up (they ran to 13). Path-B took 14; balanced pin took 16 to match the
     existing references. All renumber to canonical D-XXX at land. -->

## D-DRAFT-14 — Path-B: LLM-select synthesis (drop the reranker; the LLM picks relevant chunks)

**Context:** Even with the rerank-413 and balance fixes, a cross-MNO band query
still missed the source-of-truth MNO-A chunk. Root cause is fundamental to the
cross-encoder reranker: it scores surface query↔passage similarity and does not
bridge telecom term variants — the chunk says "SA NR", the query says "5G", and
the reranker scores it low and drops it (while picking keyword-matching but
irrelevant chunks). Telecom-pretrained LLMs (Qwen3) handle that association
natively. The DGX was provisioned to 128K context to make a stuff-the-context
approach feasible.

**Decision:** Add `NORA_SIRA_SYNTH_MODE=llm-select` (default `rerank-pin` =
unchanged). Path-B drops the cross-encoder entirely: fetch all BM25 candidates
with full text (SIRA `text_chars`, rerank off so the top_k cut is BM25), then on
the NORA side round-robin-pack them across cells under a token budget
(`NORA_SIRA_SYNTH_TOKEN_BUDGET`, default 120K), group the context by
(MNO, release) with headers, and feed everything to the LLM in ONE call that
both SELECTS the relevant chunks (instructed that "SA NR" ≡ "5G NR standalone",
band aliases, etc.) and SYNTHESIZES. Citations are extracted corpus-agnostically
by matching the packed candidates' actual req_ids against the answer (the
synthesizer's regex only matched `VZ_REQ_*`). Implemented as a dedicated lane in
`playground.py`, bypassing the graph-heavy `pipeline.query`.

**Why:** The reranker's term-variant blindness is a hard limit, not a tuning
knob — no fusion/pin change fixes a chunk dropped before fusion. An LLM is robust
to granularity + terminology, and at 128K we can give it a bounded, balanced
candidate set and let it do relevance from content. Rejected: more reranker
tuning (can't bridge ontology); pure per-cell balance (the right chunk was never
scored); a smaller-context stuff (band tables don't fit). Kept rerank-pin as the
default + fallback behind the flag.

**Consequences:**
- New Path-B helpers (`_pack_pathb`, `_build_pathb_context`,
  `_pathb_synthesize`, `_pathb_extract_citations`, `_run_pathb_lane`) + the
  `_PATHB_*` / `_SYNTH_*` env knobs; SIRA service gained `text_chars`.
- Cost/latency shifts from cheap rerank to one large-context LLM call — eval-grade,
  not production throughput; `synth_ms` surfaces it.
- Citations depend on the LLM writing req_ids verbatim; a paraphrased id is missed.
- Requires the SIRA service run with `NORA_SIRA_RERANK_ENABLED=false` so the
  returned top_k is BM25 (else the reranker re-introduces the drop).
- Open: whether Path-B replaces the rerank lane or stays opt-in (decide after eval).

## D-DRAFT-17 — Per-model reasoning sentinel for select-synth (untagged chain-of-thought)

**Context:** select-synth makes one LLM call that selects + synthesizes. A
proprietary "thinking" LLM emitted its chain-of-thought *into* the answer
content; Qwen3/Gemma did not (they skip thinking natively or split it into a
`reasoning_content` field we don't read). An opt-in raw dump
(`NORA_LLM_DEBUG_RAW`) proved the proprietary model's CoT is **untagged** — no
`<think>` tags, empty `reasoning_content` — so tag/pattern stripping has nothing
to match.

**Decision:** A final-answer **sentinel**, gated per model by
`NORA_LLM_REASONING_SENTINEL` (default off). When on: (a) the select-synth
system prompt instructs the model to print a line containing exactly
`===FINAL_ANSWER===` before its answer, and (b)
`OpenAICompatibleProvider._strip_reasoning` drops everything up to the *last*
marker occurrence. The marker constant and the flag live in the provider and are
imported by the prompt builder, so the instruction and the strip cannot drift.
`<think>`-tag stripping stays always-on (harmless when absent). The toggle is
read per process → naturally per-stack; `run_stack.sh` exposes
`--reasoning-sentinel`.

**Why:** Untagged CoT can't be split structurally and the boundary isn't
otherwise discoverable, so we *make* it explicit via the prompt. Per-model
opt-in (not global) because most models skip thinking natively and shouldn't
have output reshaped — a stray marker inside reasoning is mitigated by taking
the LAST occurrence. Chosen over: native thinking-disable
(`chat_template_kwargs={"enable_thinking": false}` / `/no_think`) — cleanest but
depends on the proprietary server's unknown API and needs provider `extra_body`
support; and over relying on `reasoning_content` — the endpoint leaves it empty.
A brief configurable-marker-*text* design was reverted once the user clarified
they wanted an on/off switch, not a configurable string.

**Consequences:**
- Provider gains `FINAL_ANSWER_MARKER` (fixed) + `REASONING_SENTINEL_ENABLED`
  (env) + sentinel logic in `_strip_reasoning`; the select-synth prompt appends
  the instruction only when enabled; startup log shows `reasoning_sentinel=<bool>`.
- **Token-waste / truncation risk:** the model still *generates* the thinking
  (counts against `max_tokens`); long reasoning could truncate the answer. The
  cleaner native-disable fix is deferred (needs provider `extra_body`).
- A/B integrity: enabling the sentinel only on the stack that needs it keeps
  retrieval+synthesis otherwise identical across the Qwen3-vs-proprietary
  comparison.
- Renumbers to a canonical D-XXX at land.
