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
   them as separate requirements. Instead their **content (including the
   section/subsection headings)** is prepended as **"Context" / front-matter**
   to each requirement beneath them.
   - Example: for a requirement `<PREFIX>-<PLAN>-12345` under section `5.1.2.3`,
     prepend the contents of its ancestor sections — `5`, `5.1`, `5.1.2`,
     `5.1.2.3` — to that requirement.
   - **Ambiguity to confirm with the user:** the user originally wrote the
     ancestor list as "5, 5.1, 5.1.2, **5.1.3**". `5.1.3` is a *sibling* of
     `5.1.2`, not an ancestor of `5.1.2.3`. **Assumed interpretation:** the
     ancestor chain `5 → 5.1 → 5.1.2 → 5.1.2.3`. Confirm before implementing.

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

- **Profile** — `customizations/profiles/mnob_leading_id_profile.json`
  (placeholdered, like `bs_d7a2c81f.json`): `detection_mode = leading_id_body`,
  `anchor = leading_text`, `components` (`-` / pos 1), `content_start_section =
  "3"`, `enable_table_anchored_extraction = false`, `numbering_pattern` matching
  `Chapter N.` + `N.M.` (trailing-dot tolerant), `body_text` 11.5–12.5. Work-PC:
  map `<MNO0>` → the real req_id prefix (or replace inline), and confirm the
  numbering pattern / chapter-heading font against the real doc.
- **Content-start cutoff (D-DRAFT-4)** — new profile field `content_start_section`
  + parser pre-pass; **not yet implemented** (loader ignores the field until
  then). Small.
- **Parser consumes `ContentBlock.lines`** (not yet implemented): separate a
  requirement's title line from its body; build the ancestor-section "Context"
  per rule #4 with headings distinct from body.
- **Deferred nicety:** split a *multi-line* requirement title from the body —
  no reliable per-span signal (color unavailable); the line-boundary fix already
  delivers the section hierarchy the synthesizer needs.

## Related decisions

- **D-DRAFT-1** — per-requirement `plan_id` (one document → N plans).
- **D-DRAFT-2** — `leading_id_body` requirement-detection mode.
- **D-DRAFT-3** — preserve PDF source line boundaries (`ContentBlock.lines`).
- **D-DRAFT-4** — profile-driven content-start cutoff (skip front matter + intro
  chapters, anchored at a configurable Chapter N).
- All unlanded; **landing gate**: no `/land-strand` until multiple MNO releases
  are ingested and the multi-plan / leading-id path is verified on real corpora.
