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
