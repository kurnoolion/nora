# MNO-B parsing spec

Authoritative, complete capture of the user-provided observations and parsing
rules for the **MNO-B** corpus. This is the single reference to reload at
session start (it is intentionally *not* condensed). Distilled from the design
sessions on 2026-06-13..17.

> Redaction: real names/values stay on the work PC. Here we use `MNO-B` for the
> MNO, `<PREFIX>` / `<PLAN>` placeholders, and `ABC-PLAN-12345`-style example
> ids. The literal `<PREFIX>`, section-number format, and heading styles come
> from inspecting the real document on the work PC.

## Corpus shape

- **One single PDF** holding **all plans** (not one document per plan, as in the
  open-access / heading-anchored model).
- Each **top-level chapter** in that PDF corresponds to **one plan**.

## Requirement id format

- `<PREFIX>-<PLAN>-<DIGITS>` — hyphen-delimited, three components.
  - `<PREFIX>` — a common prefix shared across the corpus (position 0).
  - `<PLAN>` — the plan code (position 1) — this is how the per-requirement plan
    is extracted (D-DRAFT-1).
  - `<DIGITS>` — the numeric id (position 2).
- Profile `requirement_id.components`: `separator: "-"`, `plan_id_position: 1`,
  `number_position: 2`.

## Document structure rules

Document layout: front/title page → Table of Contents → Chapter 1 (Preface) →
Chapter 2 → Chapter 3 … Chapter N. The TOC has **no `toc` style set** and its
leader-dot text pattern is unreliable on this PDF; Chapters 1–2 are general info
with no req_ids.

1. **Skip everything before the first requirements chapter** — front page, TOC,
   Chapter 1, Chapter 2. Don't try to detect/drop each negatively (TOC isn't
   reliably detectable here); instead use a **positive content-start anchor**.
2. **The parsed tree captures only from the start chapter onward** via a
   **profile-driven content-start cutoff** (D-DRAFT-4): a parser pre-pass drops
   every block before the first *real heading* (bold + heading-size font, so a
   same-numbered TOC entry doesn't trigger it) whose top-level section number
   equals a **configurable** `content_start_section` (= `"3"` for this corpus,
   not hardcoded). One anchor subsumes all four skip items.
3. **Each top-level chapter (start, start+1, …, N) corresponds to a plan.** So
   one document fans out to many plans (D-DRAFT-1 per-req `plan_id`).

## Sections / subsections → "Context", not requirements

4. Sections and subsections **do not have req_ids**, so we do **not** capture
   them as separate requirements. They are kept as **context** nodes in the tree
   (each section's heading + body stored once), and each requirement's enclosing
   ancestor-section chain is surfaced to it as **"Context"** at *emit time by
   consumers* — **not** materialized per-requirement in the tree (D-DRAFT-5; that
   would multiply section text by the requirement count in this single all-plans
   doc).
   - Example: for a requirement `<PREFIX>-<PLAN>-12345` under section `5.1.2.3`,
     the context is its ancestor chain `5 → 5.1 → 5.1.2 → 5.1.2.3` (numbers +
     titles, plus each ancestor's body — MNO-B uses `build_context:
     "path_and_content"`). Confirmed ancestor chain (the original "5.1.3" was a
     sibling typo): `5 → 5.1 → 5.1.2 → 5.1.2.3`.
   - Mechanism: profile field `build_context` (`"none" | "path" |
     "path_and_content"`), shared helper `parser.build_context_string`, called by
     the SIRA BEIR adapter and the NORA chunk builder. Generic across MNOs.

## Requirement structure & detection

5. Each requirement is laid out as:
   ```
   <PREFIX>-<PLAN>-<DIGITS>  <Requirement Title>
   <body paragraph 1>

   <body paragraph 2>

   <body paragraph 3>
   ```
   - The **req_id leads the requirement** (start of the header line).
   - The **title** follows the req_id on the header line and **may wrap across
     multiple lines**.
   - Then one or more **body paragraphs**.
6. **Once a requirement is encountered, all following text/paragraphs belong to
   it until the next req_id OR the next section/subsection heading** is
   encountered.

→ Detection model: `requirement_id.detection_mode = "leading_id_body"`
(D-DRAFT-2), `anchor = "leading_text"`. A requirement is a body block whose text
begins with the req_id pattern; section/subsection headings are non-requirement
context.

## Visual formatting (reference only — NOT a usable parse signal)

- req_id: **black**.
- requirement title: **blue** (same size as body, **not** bold).
- body text: **black**.
- section / subsection numbers + titles: **blue, bold, different size**.
- hyperlinks: **external URLs only**, rendered purple/similar (link annotations).

**Color is not usable for parsing this corpus** — PyMuPDF reports the blue title
text as `color: 0` (the blue is not a glyph fill color it surfaces), blue is
also used for section titles, and purple appears in both hyperlinked titles and
body hyperlinks. So **do not split on color.** Sections are distinguishable by
**bold + size** (which PyMuPDF captures reliably) + the section-number pattern;
requirements by the **leading req_id**.

## Extractor support — DONE (commit bfd4312, D-DRAFT-3)

- The PDF extractor used to flatten a block's source lines with `" ".join`,
  merging a heading/title line and the body line beneath it into one run-on
  sentence (blurs hierarchy for the LLM synthesizer).
- Fixed: additive **`ContentBlock.lines`** preserves the per-line split;
  invariant `" ".join(lines) == text` (so `block.text` is byte-identical, no
  detection regression). Verified on the real MNO-B PDF: req_id / title / body
  now land on separate lines; sections/subsections too.

## Still to design / build (next: profile stage, then parser)

- **Profile** — `customizations/profiles/bs_5114ac92.json`
  (placeholdered, like `bs_d7a2c81f.json`): `detection_mode = leading_id_body`,
  `anchor = leading_text`, `components` (`-` / pos 1), `content_start_section =
  "3"`, `enable_table_anchored_extraction = false`, `numbering_pattern` matching
  `Chapter N.` + `N.M.` (trailing-dot tolerant), `body_text` 11.5–12.5. Work-PC:
  map `<MNO0>` → the real req_id prefix (or replace inline), and confirm the
  numbering pattern / chapter-heading font against the real doc.
- **Content-start cutoff (D-DRAFT-4)** — **DONE** (commit 41a6f57): profile field
  `content_start_section` + font-gated parser pre-pass. Verified on the real doc
  (front matter dropped, parse starts at Chapter 3).
- **Parser title/body split (D-DRAFT-5)** — **DONE**: leading-id requirement
  `title` = header line after req_id, `text` = body, split on `ContentBlock.lines`.
  Gated on `leading_id_body`.
- **Generic `build_context` + consumer-assembled Context (D-DRAFT-5)** — **DONE**:
  profile field `build_context` + shared `parser.build_context_string` helper;
  context is assembled by consumers at emit time (NOT materialized in the tree,
  to avoid bloat). **SIRA BEIR adapter** emits for `path` + `path_and_content`;
  **NORA chunk builder** emits `path_and_content` only (`path` is already carried
  by its `[Path: …]` breadcrumb — suppressed to avoid duplication). Formats:
  `path` → `[Context: 5 <T> > 5.1 <T> > 5.1.2 <T>]`; `path_and_content` →
  `[5 <T>]` + body per ancestor, top-down. `bs_d7a2c81f` → `"path"`,
  `bs_5114ac92` → `"path_and_content"`.
- **Deferred nicety:** split a *multi-line* requirement title from the body —
  no reliable per-span signal (color unavailable); the line-boundary fix already
  delivers the section hierarchy the synthesizer needs.

## Runbook — parse, verify Context, ingest into SIRA

Work-PC only (real corpus + mapping live there). `$ENV` = NORA env dir, `$DB` =
SIRA db-root. Verifying the D-DRAFT-5 Context code **is** the SIRA ingest path —
context is assembled at adapter emit time, so running the adapter and inspecting
its rows is the verification.

### Prereqs

- `git pull origin main` includes the D-DRAFT-5 commit (`e4d0e92`).
- MNO-B doc at `$ENV/input/<MNO>/<MMMYYYY>/<doc>.pdf` — the release dir **must**
  be `MMMYYYY` (e.g. `Mar2025`) or `--multi-cell` fails loud. `<MNO>`/`<release>`
  are inferred from this path and stamped on the tree.
- `customizations/mappings/bs_5114ac92.json` maps `<MNO0>` → the real req_id
  prefix (work-PC only; never pushed). Resolves via `_provenance.bootstrap_id`
  even though the pipeline copies the profile to `out/profile/profile.json`.
- For an MNO-B-only SIRA cell, use a dedicated `$ENV` (the adapter loads **all**
  `out/parse/*_tree.json` and emits one cell per `(mno, release)`).

### A. Parse + verify the tree

```bash
python -m core.src.pipeline.run_cli \
  --env-dir "$ENV" --profile customizations/profiles/bs_5114ac92.json \
  --start extract --end parse
```

```bash
T=$ENV/out/parse/*_tree.json
jq '{build_context, detection_mode}' $T
#   expect {"build_context":"path_and_content","detection_mode":"leading_id_body"}
jq '{n_sections:([.requirements[]|select((.section_number//"")!="")]|length),
     n_reqs:    ([.requirements[]|select((.req_id//"")!="")]|length)}' $T
jq '[.requirements[]|select((.req_id//"")!="")][0]
    | {req_id, section_number, title, text:(.text[0:80]), context, parent_section}' $T
#   expect req_id substituted (no "<MNO0>"), parent_section like "5.1.2", context=""
jq '[.requirements[]|select((.context//"")!="")]|length' $T   # expect 0 (consumer-assembled)
```

If `req_id` still shows `<MNO0>`, the mapping didn't resolve — recheck the
mapping file (this was the historical failure mode).

### B. Run the adapter (= Context proof + SIRA ingest artifact)

```bash
python -m sandbox.adapter.nora_to_beir \
  --env-dir "$ENV" --output "$DB" --multi-cell --wipe-all-derived
```

`--wipe-all-derived` on the **first** ingest (establishes the cell baseline).
Verify Context baked into the per-req rows:

```bash
C=$DB/<mno>__<release>/raw/corpus.jsonl
jq -r 'select((._id|startswith("doc:")|not) and (._id|startswith("section:")|not)) | .text' $C | sed -n '1,40p'
#   expect, before the body, the bracketed ancestor chain top-down:
#     [5 Bands]\n<intro>\n[5.1 Frequency]\n<intro>\n[5.1.2 LTE]\n<intro>
jq -r 'select((._id|startswith("doc:")|not) and (._id|startswith("section:")|not)) | .text' $C | grep -c '^\['
```

Then build/enrich the `<mno>__<release>` cell on the SIRA side (corpus rows are
self-contained — retrieval doesn't depend on NORA's graph).

### C. Incremental adds (next MNO / release / docs)

Wipe semantics per cell (`$DB/<mno>__<release>/`):

| flag | wipes | keeps | effect |
|---|---|---|---|
| `--wipe-stale-index` | `index/ enrichments/ eval/ retrieval/` | **`runs/`** (enrich cache) | index rebuilds; only new/changed docs re-enriched |
| `--wipe-all-derived` | the above **+ `runs/`** | — | full rebuild; re-enriches everything |
| neither | — | — | warn-only; SIRA then panics `doc_id N out of range` |

Steady-state add (enrichment prompt **unchanged**):

```bash
# 1. drop new corpus at $ENV/input/<MNO>/<MMMYYYY>/  then parse it
python -m core.src.pipeline.run_cli --env-dir "$ENV" \
  --profile customizations/profiles/<that-profile>.json --start extract --end parse
# 2. re-emit cells, incremental-safe
python -m sandbox.adapter.nora_to_beir --env-dir "$ENV" --output "$DB" \
  --multi-cell --wipe-stale-index
# 3. SIRA build/enrich: new docs LLM-enriched, unchanged docs hit runs/ cache,
#    all cells re-index (cheap)
```

Escalate to `--wipe-all-derived` only when: the **enrichment prompt changed**
(cache is stale), a **major composition shift** re-baselines the DF-filter
(1 MNO → 3), or you changed `--section-max-depth` (alters section-row counts in
every cell). **Always pass one wipe flag after any corpus change** — forgetting
leaves SIRA on a stale BM25 index that panics with `doc_id N out of range`.

## Related decisions

- **D-DRAFT-1** — per-requirement `plan_id` (one document → N plans).
- **D-DRAFT-2** — `leading_id_body` requirement-detection mode.
- **D-DRAFT-3** — preserve PDF source line boundaries (`ContentBlock.lines`).
- **D-DRAFT-4** — profile-driven content-start cutoff (skip front matter + intro
  chapters, anchored at a configurable Chapter N).
- **D-DRAFT-5** — consume `lines` for title/body split; generic `build_context`
  config with consumer-assembled `Requirement.context` (not tree-materialized).
- All unlanded; **landing gate**: no `/land-strand` until multiple MNO releases
  are ingested and the multi-plan / leading-id path is verified on real corpora.
