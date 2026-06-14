# ANNOTATIONS — guide for the human annotator

This document explains every annotation kind the **Bootstrap** tab on NORA's Parse
page supports, what each kind means structurally, what the profiler/parser/resolver
do with it downstream, and concrete examples for every variant.

For the JSON-shape spec see [`cline-playbooks/annotation-schema.md`](../../../cline-playbooks/annotation-schema.md).
For the rule-derivation playbook see [`cline-playbooks/bootstrap.md`](../../../cline-playbooks/bootstrap.md).

## Why annotations exist

When a new MNO corpus arrives, every document type — Verizon OA, MNO-B DSDS,
MNO-C core, the post-2026 vendor specs that don't exist yet — has its own
structural quirks: where heading numbers sit, how requirement IDs are spelled,
whether the bibliography is a paragraph list or a 2-column table, whether
strikeouts are line-strikes or font flags. The profiler can't guess these from
nothing.

**Annotations are positive examples** that the human marks once on 3-5 docs.
Cline reads them, derives a *rule* per kind (a regex, a layout heuristic, a
geometric threshold), and emits a redacted BOOTSTRAP report you paste back to
Teacher LLM, which then writes / updates `customizations/profiles/<plan>/profile.json`
and any new parser code.

## The detection-vs-resolution split (read this first)

Annotations capture how a structural element *looks* — its surface shape on the
page. They do **not** capture where a reference resolves to (which graph node,
which other req, which standards-doc section). Those are two different stages:

```
   ANNOTATE       →    PROFILE        →    PARSE             →   RESOLVE
   (human)             (rule derive)       (rule apply)          (graph lookup)
                                                                 
   "this paragraph     "regex for         "extract req_ids,       "look up the
    holds a req_id"     <MNO>_REQ_..."      headings, refs"        target req
                                                                   in the graph"
```

For the **5 reference kinds** an *optional* `target` field on each annotation
captures resolver-eval ground truth (e.g. "this `[5]` should resolve to spec
`3GPP TS 24.301`"). Cline ignores `target` during rule derivation; it's reserved
for later, when we want to validate that the resolver is making the right
graph-lookup decisions. Skip `target` while bootstrapping. Come back and fill
it in later if you want to grow a resolver-eval set.

## Universal mechanics

**Region shapes** (PDF + DOCX, since both extractors produce the same flat IR):

```json
"region": { "block_indices": [142] }                  // one block
"region": { "block_indices": [12, 13, 14, 15, 16] }   // a range / set of blocks
"region": { "block_index": 142, "row_range": [3, 5] } // rows 3-5 within table block 142
```

The **IR pane** in the Bootstrap UI shows blocks indexed `#0`, `#1`, …; click `+`
to annotate. Shift-click a second block to extend a multi-block range. Inside a
table block, click rows to build a row-range.

**Notes** (`notes` field, ≤30 chars) — structural-only. *Never* the verbatim
heading or content; describe the structure: `"deep nesting"`, `"after revhist"`,
`"struck row in table"`. Acceptable: `"OA depth 6"`. Unacceptable: any quoted
phrase from the source doc.

**Aim for 5-10 examples per kind per doc, 3-5 docs.** If a kind has fewer than
3 total examples across all annotated docs, the BOOTSTRAP report will flag
`LOW_PROV: <kind>` — not blocking, just informational.

---

# Structural kinds

These kinds describe the document's static structure — what's a heading, what's
a TOC, what's struck-out, what's a version history.

## `section_heading`

**Definition.** A line that introduces a section of the document. Headings drive
the parser's hierarchical tree: every requirement and content block lives under
the nearest preceding heading.

**Pipeline path.** Profiler clusters heading examples by font-size, boldness,
and numbering pattern → emits `heading_detection.numbering_pattern`,
`heading_detection.font_threshold`, `heading_detection.style_hints` → parser's
`_classify_heading` uses those to bucket every block as heading-or-not.

**What to mark.** Any line that introduces a section. Include variety: chapter,
section, subsection, deeply nested. If the doc has both numbered and unnumbered
headings, mark examples of each.

**Optional fields:**

- `depth` (1-9) — 1 for chapter, 2 for section, 3+ for subsection. Useful when
  the visual style alone (font/bold) doesn't disambiguate depth.
- `section_number` — e.g. `"1.2.3"`. Structural identifier, not content.
- `is_numbered` — bool. Helps Cline derive a separate rule for unnumbered
  headings (e.g. `"Introduction"`).
- `title_char_count` — integer length signal; never the title text itself.

**Examples:**

```text
1.   System Requirements                  ← depth=1, is_numbered=true
1.2  Data Retry                           ← depth=2, section_number="1.2"
1.2.3.1.4 Cause Code 22 Behavior          ← depth=5 (deep nesting)
INTRODUCTION                              ← depth=1, is_numbered=false (all caps)
Definitions and Acronyms                  ← depth=1, is_numbered=false
```

```json
{ "id": "ann_001", "kind": "section_heading",
  "region": {"block_indices": [12]},
  "depth": 1, "section_number": "1", "is_numbered": true }
```

```json
{ "id": "ann_002", "kind": "section_heading",
  "region": {"block_indices": [85]},
  "depth": 5, "section_number": "1.2.3.1.4", "is_numbered": true,
  "notes": "deep nesting" }
```

```json
{ "id": "ann_003", "kind": "section_heading",
  "region": {"block_indices": [3]},
  "depth": 1, "is_numbered": false, "notes": "all-caps unnumbered" }
```

---

## `req_id`

**Definition.** A token that uniquely identifies a requirement. The shape varies
heavily across MNOs: `VZ_REQ_LTEAT_45`, `[VZW-12345]`, `R-001`, etc.

**Pipeline path.** Profiler emits `requirement_id_pattern` (regex with capture
group for the numeric portion) plus a `placement` field (leading vs trailing) →
parser's `_extract_req_id` walks each requirement-shaped block and matches.

**What to mark.** A few representative req_id tokens, from different parts of
the doc if format varies. Mark **one full requirement-bearing block** per
example, not just the token — Cline needs the surrounding context to learn
placement. Include variety: vanilla, edge-cases (one-letter plan codes,
extra-long IDs).

**Optional fields:**

- `placement`:
  - `"leading"` — the req_id appears at the *start* of the requirement block
    (e.g. `[R-001] The UE shall ...`)
  - `"trailing"` — the req_id appears at the *end*, often as a separate
    small-font block (Verizon OA convention)
- `format_hint` — placeholdered shape: `"<MNO>_REQ_<PLAN>_<DIGITS>"` or
  `"R-<DIGITS>"` or `"[<MNO>-<DIGITS>]"`. Use redaction placeholders for any
  MNO/plan token.

**Examples:**

```text
The UE shall transmit ATTACH_REQUEST.            ← req body
                                  VZ_REQ_LTEAT_45 ← trailing req_id (Verizon OA)

[R-001] The UE shall enforce timer T3402.         ← leading req_id (square-bracket)

(REQ-2401) Upon receipt of cause code 22 ...      ← leading parenthesized
```

```json
{ "id": "ann_010", "kind": "req_id",
  "region": {"block_indices": [120]},
  "placement": "trailing",
  "format_hint": "<MNO>_REQ_<PLAN>_<DIGITS>" }
```

```json
{ "id": "ann_011", "kind": "req_id",
  "region": {"block_indices": [156]},
  "placement": "leading", "format_hint": "[R-<DIGITS>]" }
```

---

## `toc`

**Definition.** A region of the document that is the table of contents — leader
dots, indented section labels, page numbers. Not body content.

**Pipeline path.** Profiler derives `toc_detection_pattern` (regex matching
leader-dot-style entries) plus `toc_page_threshold` (fraction of lines on a page
that must match before the whole page is dropped) → parser drops matching
blocks before heading classification.

**What to mark.** The TOC region as a contiguous range of blocks. If the TOC
spans multiple pages, mark a representative slice; you do **not** need to mark
every page.

**Optional fields:**

- `pattern_hint`:
  - `"leader-dot-page"` — `1.2  Data Retry .................. 12`
  - `"indented-leveled"` — visual indent encodes depth, no leader dots
  - `"plain-list"` — flat list of titles, no page numbers (rare)

**Examples:**

```text
TABLE OF CONTENTS

1     System Requirements .................. 5
1.1   Data Retry ........................... 7
1.2   ATTACH Behavior ...................... 12
        ← leader-dot-page style
```

```json
{ "id": "ann_020", "kind": "toc",
  "region": {"block_indices": [3, 4, 5, 6, 7, 8, 9, 10]},
  "pattern_hint": "leader-dot-page" }
```

```json
{ "id": "ann_021", "kind": "toc",
  "region": {"block_indices": [11, 12, 13]},
  "pattern_hint": "indented-leveled",
  "notes": "no page nums in 2nd TOC" }
```

---

## `strikethrough`

**Definition.** A struck-out region (deleted-but-visible). Spec authors use
strike to show "this used to be a requirement; it's been removed for this
release." The parser drops these so retired requirements don't pollute downstream.

**Pipeline path** [D-060]. Extraction is **uniform across formats now**: the
extractor populates per-run strike state on `ContentBlock.runs` (paragraphs /
headings) and per-cell on `ContentBlock.header_runs` / `row_runs` (tables).
Nothing is dropped at extract time. The IR keeps every span — struck or not —
and downstream consumers decide:

- **Parser** (`profile.ignore_strikeout=True`, default): for each block,
  rebuild text from non-struck runs (`live_text()`); for tables, drop fully
  struck rows and rebuild partial-struck cells from non-struck runs.
  Fully-struck section headings cascade-drop their section (FR-33 unchanged).
- **UI** (Bootstrap IR pane + Parse Review + DOCX preview): renders struck
  spans with `text-decoration: line-through`. Whole-row / whole-table strike
  styles apply at the `<tr>` / `<table>` level.

This means **table-row and partial-text strike are auto-detected** for DOCX,
PDF, and XLSX — you don't need explicit `strikethrough` annotations to make
them work. The annotation kind is still useful for **ground-truth confirmation**
("yes, the extractor was right to mark this as struck") and for **rule-derivation
hints** when the extractor's heuristic needs tuning for a corpus quirk.

**What to mark.** Optional. Three or four examples covering the variety you
see — especially edge cases (a partially-struck table, a fully-struck section,
a struck row inside an otherwise-live table). Day-0 bootstrap can skip this
kind entirely; come back to it during Parse Review if the extractor mis-flags
something.

**Optional fields:**

- `subkind`:
  - `"full_paragraph"` — entire paragraph is struck
  - `"table_row"` — one row inside a table is struck (use `block_index +
    row_range`)
  - `"partial_cell"` — only part of a table cell is struck (rare; usually a
    PDF artifact)
  - `"section_heading"` — the heading itself is struck → parser cascades the
    drop to the whole section
- `visual`:
  - `"line"` — a horizontal line drawn over the text (PDF graphic operation)
  - `"font_flag"` — Word's strikethrough run property (DOCX)
  - `"both"` — both signals present

**Examples:**

```text
~~The UE shall not retry within 30s.~~       ← full_paragraph, font_flag
                                                _LTEAT_456  ← still trailing

| Code | Action       |
|------|--------------|
| 22   | exponential  |
| ~~23~~ | ~~stop~~ | ← table_row, font_flag (whole row struck)
| 24   | linear       |

~~1.2 Withdrawn Requirements~~                ← section_heading
   ~~Body text under withdrawn section~~      ← cascaded by parser
```

```json
{ "id": "ann_030", "kind": "strikethrough",
  "region": {"block_indices": [42]},
  "subkind": "full_paragraph", "visual": "font_flag" }
```

```json
{ "id": "ann_031", "kind": "strikethrough",
  "region": {"block_index": 156, "row_range": [3, 3]},
  "subkind": "table_row", "visual": "line",
  "notes": "PDF horiz line strike" }
```

```json
{ "id": "ann_032", "kind": "strikethrough",
  "region": {"block_indices": [78]},
  "subkind": "section_heading", "visual": "font_flag",
  "notes": "cascade-drop heading" }
```

---

## `version_history`

**Definition.** The revision-history heading + the table that follows it,
listing each release's changes. Pure metadata — never carries a requirement.

**Pipeline path.** Profiler derives `revision_history_label_pattern` →
parser drops the heading + the next paragraph/table block as a unit (the
"revhist consume" mechanism). Pattern matches against any
heading-or-paragraph block text.

**What to mark.** The heading and (optionally) the table that follows. If you
mark the heading alone, set `kind_subtype="heading_only"`; if you include the
table, use `"full_block"`.

**Optional fields:**

- `kind_subtype`:
  - `"heading_only"` — mark just the heading; parser auto-consumes the
    following block
  - `"full_block"` — mark heading + the version table as one range

**Examples:**

```text
Revision History                          ← heading
| Version | Date     | Changes        |   ← table
| 1.0     | Feb 2026 | Initial issue  |
| 1.1     | Apr 2026 | Updated §3.4   |
```

```json
{ "id": "ann_040", "kind": "version_history",
  "region": {"block_indices": [2]},
  "kind_subtype": "heading_only" }
```

```json
{ "id": "ann_041", "kind": "version_history",
  "region": {"block_indices": [2, 3]},
  "kind_subtype": "full_block",
  "notes": "VRH wrapped to 2 pgs" }
```

---

## `definitions`

**Definition.** A glossary or definitions section that maps acronyms / terms to
their expansions. Drives inline expansion in chunks (e.g., "SDM" → "Subscription
Data Management" gets injected when the chunk is built).

**Pipeline path.** Profiler derives the section's heading regex + per-entry
layout. Parser detects the section, parses each entry into
`tree.definitions_map: dict[str, str]`. Chunk builder uses the map for inline
expansion.

**What to mark.** The whole section (or the part where the entries actually
live; skip introductory paragraphs).

**Optional fields:**

- `layout`:
  - `"paragraph_list"` — each entry is its own paragraph: `**SDM** —
    Subscription Data Management.`
  - `"two_col_table"` — `| Acronym | Definition |`
  - `"three_col_table"` — `| Acronym | Definition | Source |` (rare)
  - `"inline_glossary"` — definitions interspersed in body text (rarely used
    standalone; usually paired with another layout)

**Examples:**

```text
Definitions and Acronyms          ← heading
                                    ← layout=two_col_table
| Acronym | Definition                  |
|---------|------------------------------|
| EMM     | EPS Mobility Management      |
| NAS     | Non-Access Stratum           |
| SDM     | Subscription Data Management |
```

```text
Acronyms                          ← heading
                                    ← layout=paragraph_list
EMM    EPS Mobility Management.
NAS    Non-Access Stratum.
SDM    Subscription Data Management.
```

```json
{ "id": "ann_050", "kind": "definitions",
  "region": {"block_indices": [200, 201, 202, 203]},
  "layout": "two_col_table" }
```

```json
{ "id": "ann_051", "kind": "definitions",
  "region": {"block_indices": [195, 196, 197, 198, 199]},
  "layout": "paragraph_list",
  "notes": "intro skipped" }
```

---

## `applicability`

**Definition.** A marker indicating which device class / form factor / radio
access the requirement applies to (e.g., `[LTE]`, `[5G NR]`, `[CAT-M1]`,
`[Smartphone Only]`). Inherits hierarchically per D-030: a marker on a section
heading propagates to all nested requirements.

**Pipeline path.** Profiler derives `applicability_marker_pattern` →
parser populates `Requirement.applicability: list[str]` with hierarchical
inheritance applied.

**What to mark.** Examples of the marker token in different positions —
after-heading, inline-in-paragraph, separate-block. The position teaches Cline
where to look during extraction.

**Optional fields:**

- `position`:
  - `"after_heading"` — marker appears on its own line below the heading
  - `"inline_in_para"` — marker is inline in a paragraph, often parenthesized
  - `"separate_block"` — marker is a standalone block (often bold, often before
    the requirement body)

**Examples:**

```text
1.2 Data Retry                        ← heading
[LTE, CAT-M1]                         ← position=after_heading
The UE shall ...

The UE may abort retry [Smartphone].  ← position=inline_in_para

[5G SA Only]                          ← position=separate_block
The UE shall use SUCI for ATTACH.
```

```json
{ "id": "ann_060", "kind": "applicability",
  "region": {"block_indices": [45]},
  "position": "after_heading", "notes": "[LTE,CAT-M1]" }
```

```json
{ "id": "ann_061", "kind": "applicability",
  "region": {"block_indices": [52]},
  "position": "separate_block" }
```

---

## `priority`

**Definition.** A marker indicating requirement priority: mandatory / optional /
conditional / etc. Often a single bracketed word at a fixed position.

**Pipeline path.** Profiler derives `priority_marker_pattern` (regex, capture
group 1 = priority value) → parser populates `Requirement.priority: str |
None`. Values are uppercased on extraction (`MANDATORY`, `OPTIONAL`, …).

**What to mark.** Examples of the marker. If the corpus uses multiple priority
words, include one example each.

**Optional fields:**

- `position`: same vocabulary as `applicability` (`"after_heading"` /
  `"inline_in_para"` / `"separate_block"`)

**Examples:**

```text
1.4 Cause Code 22 Retry              ← heading
[Mandatory]                          ← position=separate_block
The UE shall ...

The UE shall use exponential backoff (Mandatory).  ← position=inline_in_para

[Optional]                           ← position=separate_block
The UE may extend the backoff to 600s.
```

```json
{ "id": "ann_070", "kind": "priority",
  "region": {"block_indices": [80]},
  "position": "separate_block",
  "notes": "[Mandatory]" }
```

```json
{ "id": "ann_071", "kind": "priority",
  "region": {"block_indices": [110]},
  "position": "inline_in_para" }
```

---

# Reference kinds

These five kinds capture the different shapes of cross-references — within a
doc, across docs, and to external standards. Each can carry an optional
`target` dict with resolver-eval ground truth (skip while bootstrapping).

## `reference_intra_doc`

**Definition.** A reference to another section or requirement *within the same
document* — typically a section number (`see §3.5.2.1`) or a same-doc req_id.

**Pipeline path.** Profiler derives an intra-doc reference pattern (capture
group 1 = the target section number or req_id) → parser populates
`req.references_intra_doc: list[str]` with raw tokens → resolver looks each up
in *this doc's* section tree / req index → emits intra-ref edges in the graph.

**What to mark.** A few examples in body text. Variety helps: section-number
target, req_id target, with/without "see" prefix.

**Optional fields:**

- `inline` — bool. True if reference is inline within a paragraph (vs a
  standalone block).
- `target` — optional resolver-eval ground truth:
  - `section_number` — `"3.5.2.1"`
  - `req_id` — `"<MNO>_REQ_<PLAN>_45"`

**Examples:**

```text
The UE shall behave as defined in §3.5.2.1.        ← target.section_number="3.5.2.1"

The UE shall comply with VZ_REQ_LTEAT_45.          ← target.req_id="<MNO>_REQ_<PLAN>_45"

See section 3.5.2 for details.                     ← target.section_number="3.5.2"
```

```json
{ "id": "ann_080", "kind": "reference_intra_doc",
  "region": {"block_indices": [200]},
  "inline": true,
  "target": {"section_number": "3.5.2.1"} }
```

```json
{ "id": "ann_081", "kind": "reference_intra_doc",
  "region": {"block_indices": [205]},
  "target": {"req_id": "<MNO>_REQ_<PLAN>_45"} }
```

---

## `reference_cross_doc`

**Definition.** A reference to a requirement or section in a *different* MNO
document (other plan, MNO, or release). Resolution requires the target plan to
be ingested into the graph.

**Pipeline path.** Profiler derives a cross-doc reference pattern (capture
groups for plan + req-or-section) → parser populates `req.references_cross_doc`
→ resolver looks up `(plan, req_id)` in the graph → emits cross-ref edges (or
records `unresolved` if target plan isn't ingested).

**What to mark.** Examples of cross-doc references. Plan-prefixed req_ids are
the easy case; "see [PLAN] section X" prose forms are harder and worth
including a couple.

**Optional fields:**

- `inline` — bool
- `target` — optional resolver-eval ground truth:
  - `plan_id` — `"<PLAN1>"`
  - `section_number` — `"4.2"` (when the target is a section, not a req)
  - `req_id` — `"<MNO>_REQ_<PLAN1>_45"`

**Examples:**

```text
See VZ_REQ_LTEOTADM_300 for the OTA sequence.      ← target.req_id

Refer to LTEOTADM section 4.2 for details.         ← target.plan_id + target.section_number

As specified in plan LTESMS, requirement R-708.    ← target.plan_id + target.req_id
```

```json
{ "id": "ann_090", "kind": "reference_cross_doc",
  "region": {"block_indices": [220]},
  "target": {"req_id": "<MNO>_REQ_<PLAN1>_300"} }
```

```json
{ "id": "ann_091", "kind": "reference_cross_doc",
  "region": {"block_indices": [225]},
  "target": {"plan_id": "<PLAN1>", "section_number": "4.2"} }
```

---

## `reference_spec`

**Definition.** A reference to a public standards document (3GPP, GSMA, ETSI,
ITU, IEEE, etc.). Two structurally distinct shapes — direct and indirect — and
the schema requires you to mark which.

**Pipeline path.** Profiler derives **two** patterns: one for direct citations
(inline spec name + section), one for indirect (bracketed/parenthesized number
that resolves through the references list). Parser populates
`req.references_spec: list[{style, ...}]`. Resolver:

- For `style="direct"`: look up `(spec, section)` in the standards graph.
- For `style="indirect"`: look up the bracketed number in the doc's
  `reference_list_map` → get `(spec, section)` → look up in the standards graph.

**Required field.**

- `style`:
  - `"direct"` — inline spec name + optional section, e.g. `3GPP TS 24.301,
    §5.5.1.2.6`
  - `"indirect"` — bracketed/parenthesized number that points at a
    `reference_list` entry, e.g. `[5]` or `(5)`

**Optional fields:**

- `inline` — bool
- `target` — optional resolver-eval ground truth:
  - `spec` — `"3GPP TS 24.301"` (use for `style="direct"`; for `style="indirect"`
    use only if you've verified the bibliography entry)
  - `section` — `"5.5.1.2.6"` (use for `style="direct"`)
  - `ref_number` — integer (use for `style="indirect"`; e.g. `5` for `[5]`)

**Examples — direct:**

```text
The UE shall comply with 3GPP TS 24.301, §5.5.1.2.6.   ← classic direct

As specified in 3GPP TS 36.331 [12.5.1.2].             ← spec + section in brackets

Per GSMA SGP.22 v3.0 §3.4.1.                           ← non-3GPP spec

ETSI TS 133 401 section 6.1.                           ← ETSI variant
```

```json
{ "id": "ann_100", "kind": "reference_spec", "style": "direct",
  "region": {"block_indices": [42]},
  "target": {"spec": "3GPP TS 24.301", "section": "5.5.1.2.6"} }
```

```json
{ "id": "ann_101", "kind": "reference_spec", "style": "direct",
  "region": {"block_indices": [44]},
  "target": {"spec": "GSMA SGP.22", "section": "3.4.1"} }
```

**Examples — indirect:**

```text
The UE shall comply with [5].                          ← bracketed
The behavior shall match [5] §5.5.1.2.6.               ← bracket + section override
The UE shall ATTACH per (12) and DETACH per (13).      ← parenthesized variant
As defined in reference [5] section 4.2.              ← prose-prefixed bracket
```

```json
{ "id": "ann_110", "kind": "reference_spec", "style": "indirect",
  "region": {"block_indices": [50]},
  "target": {"ref_number": 5} }
```

```json
{ "id": "ann_111", "kind": "reference_spec", "style": "indirect",
  "region": {"block_indices": [55]},
  "target": {"ref_number": 12},
  "notes": "parenthesized variant" }
```

---

## `reference_list`

**Definition.** The bibliography / references section of the document — the
numbered table that indirect spec citations resolve through. Mirrors the
`definitions` pattern: section-level annotation marks where the lookup table
lives.

**Pipeline path.** Profiler derives:
- `reference_list_section_pattern` — heading regex (`^References$|^Bibliography$|
  ^Normative References$`)
- `reference_list_entry_pattern` — per-entry regex (capture groups for number,
  spec, optional title)

Parser detects the section, iterates its blocks/rows applying the entry
pattern, populates `tree.reference_list_map: dict[int, {spec, section?, title?}]`.
Resolver looks up bracketed numbers from `reference_spec` (style=indirect) in
this map.

**What to mark.** The whole references section as a single multi-block range
(or a `block_index + row_range` if the section is a table).

**Optional fields:**

- `numbering_style`:
  - `"bracketed"` — `[1] 3GPP TS 24.301 ...`
  - `"plain"` — `1. 3GPP TS 24.301 ...`
  - `"parenthesized"` — `(1) 3GPP TS 24.301 ...`
- `layout`:
  - `"paragraph_list"` — each entry is a paragraph
  - `"two_col_table"` — `| Number | Reference |`
  - `"three_col_table"` — `| Number | Reference | Notes |`

**Examples:**

```text
References                                 ← heading
                                              numbering_style=bracketed
                                              layout=paragraph_list
[1]  3GPP TS 23.401, "GPRS enhancements ..."
[2]  3GPP TS 24.301, "Non-Access-Stratum ..."
[3]  3GPP TS 36.331, "Radio Resource Control ..."
[4]  GSMA SGP.22, "RSP Technical Specification ..."
[5]  3GPP TS 24.301, §5.5.1.2.6
```

```text
Normative References                        ← heading
                                              numbering_style=plain
                                              layout=two_col_table
| Number | Reference                                             |
|--------|-----------------------------------------------------|
| 1      | 3GPP TS 23.401, GPRS enhancements ...               |
| 2      | 3GPP TS 24.301, Non-Access-Stratum ...              |
```

```json
{ "id": "ann_120", "kind": "reference_list",
  "region": {"block_indices": [400, 401, 402, 403, 404, 405, 406, 407]},
  "numbering_style": "bracketed", "layout": "paragraph_list" }
```

```json
{ "id": "ann_121", "kind": "reference_list",
  "region": {"block_indices": [420]},
  "numbering_style": "plain", "layout": "two_col_table",
  "notes": "header + N entries" }
```

---

## `reference_list_entry`

**Definition.** A single entry inside a `reference_list` section. **Optional
ground-truth annotation** — marking 2-3 example entries per doc (a) gives Cline
enough variety to derive the per-entry parsing rule, and (b) gives the resolver
an evaluation set ("for entry [5], the spec should be `3GPP TS 24.301`").

**Pipeline path.** Cline uses the entries (with their `number` field, source
text via the IR, and optional target) to derive `reference_list_entry_pattern`.
Parser uses that pattern at runtime to populate `reference_list_map` for every
entry, not just the marked ones.

**What to mark.** 2-3 entries per doc as examples. Pick variety: an early
entry, a middle one, an entry with a section in its target, an entry with a
non-3GPP spec.

**Optional fields:**

- `number` — the entry's number in the list (e.g. `5` for `[5]`)
- `title_hint_chars` — integer length of the entry's title (length-only signal,
  the title itself never goes in the annotation)
- `target` — optional ground truth:
  - `spec` — `"3GPP TS 24.301"`
  - `section` — `"5.5.1.2.6"` (only when the entry pins a default section)

**Examples:**

```text
[1]  3GPP TS 23.401, "GPRS enhancements ..."     ← number=1, target.spec="3GPP TS 23.401"
                                                   title_hint_chars=28

[5]  3GPP TS 24.301, §5.5.1.2.6                  ← number=5, target.spec="3GPP TS 24.301",
                                                              target.section="5.5.1.2.6"

[12] GSMA SGP.22 v3.0                            ← number=12, target.spec="GSMA SGP.22"
```

```json
{ "id": "ann_130", "kind": "reference_list_entry",
  "region": {"block_indices": [400]},
  "number": 1,
  "title_hint_chars": 28,
  "target": {"spec": "3GPP TS 23.401"} }
```

```json
{ "id": "ann_131", "kind": "reference_list_entry",
  "region": {"block_indices": [404]},
  "number": 5,
  "target": {"spec": "3GPP TS 24.301", "section": "5.5.1.2.6"} }
```

```json
{ "id": "ann_132", "kind": "reference_list_entry",
  "region": {"block_index": 420, "row_range": [11, 11]},
  "number": 12,
  "target": {"spec": "GSMA SGP.22"},
  "notes": "table row variant" }
```

---

# User-override kinds

A different category from the structural and reference kinds above: these
annotations record an explicit human decision that affects how the
pipeline processes the document, not what the document structurally
contains.

## `remove` [D-061]

**Definition.** Mark a region the user wants **excluded from downstream
pipeline ingestion**. Use cases: defer a section until prerequisite data
is ingested ("test plan mapping section — skip until test plans land");
exclude a known-bad table; prune a single retired row that the source
didn't strike out.

**Pipeline path.** Conceptually identical to strikethrough:

```
ANNOTATE     → user marks region with kind=remove on disk
PARSE pre-pass → apply_user_annotations() reads the annotation file and
                 mutates the IR — sets struck=True on every TextRun in
                 the region; sets font_info.strikethrough=True on the
                 block. (Mutation is in-memory only; the on-disk IR
                 file is not touched.)
PARSE        → existing FR-33 cascade drops the marked content. For a
                 marked section heading, the cascade auto-extends
                 through subsequent body until the next sibling /
                 parent heading.
```

The remove machinery rides on the D-060 strike rails — no new drop
path, no new cascade logic, no new visual styling. The Bootstrap UI
renders removed regions with the same `docx-struck` line-through
styling that struck content uses.

**Reversible.** Delete the annotation file entry, re-run parse — the
content comes back live. Nothing is lost from the IR.

**What to mark.**

- A whole section (heading + body): mark just the heading block with a
  single-block region. The cascade extends automatically.
- A standalone paragraph: mark the paragraph's block index.
- A specific table row or row-range: mark the table block index +
  `row_range`.
- A multi-block range that doesn't follow heading boundaries: list the
  exact block indices via `block_indices` (no auto-cascade — explicit).

**Optional fields.** None required. Use the standard `notes` field
(≤30 chars, structural-only) to record the human reason.

**Examples.**

Remove a whole section by marking its heading:

```json
{ "id": "ann_900", "kind": "remove",
  "region": {"block_indices": [42]},
  "notes": "test plan refs deferred" }
```

(Block 42 is the section heading. Parser cascade-drops the section's body.)

Remove a single paragraph (not a heading — no cascade):

```json
{ "id": "ann_901", "kind": "remove",
  "region": {"block_indices": [156]},
  "notes": "duplicate, see §3.4" }
```

Remove a specific row range from a table:

```json
{ "id": "ann_902", "kind": "remove",
  "region": {"block_index": 200, "row_range": [3, 7]},
  "notes": "rows refer to retired plans" }
```

Remove a non-contiguous set of blocks:

```json
{ "id": "ann_903", "kind": "remove",
  "region": {"block_indices": [88, 92, 95]},
  "notes": "broken cross-refs" }
```

**Difference from `strikethrough`.** `strikethrough` documents what the
*source author* marked deleted; `remove` documents what the *human
annotator* decides to exclude. Both end up dropped at parse time with
identical mechanics. If the source already strikes a region, you don't
need a `remove` annotation on top — the extractor already detects it.

---

# Annotation budget per doc — a worked example

For one Verizon-OA-style DOCX, a reasonable starting set:

| Kind                       | Examples per doc | Notes                                          |
|----------------------------|------------------|------------------------------------------------|
| `section_heading`          | 5-8              | Cover depth=1..6, plus any unnumbered headings |
| `req_id`                   | 3-5              | One per format variant                         |
| `toc`                      | 1                | Just one TOC region; multi-page covered by one |
| `strikethrough`            | 2-4              | Cover full_paragraph + table_row at minimum    |
| `version_history`          | 1                | Heading or full_block                          |
| `definitions`              | 1                | Just the section                               |
| `applicability`            | 2-3              | Different positions if the corpus mixes them   |
| `priority`                 | 2-3              | Or skip if corpus has no priority markers      |
| `reference_intra_doc`      | 3-5              | Mix of section_number and req_id targets       |
| `reference_cross_doc`      | 2-3              | If the corpus has them                         |
| `reference_spec` (direct)  | 3-5              | Mix of 3GPP / GSMA / ETSI                      |
| `reference_spec` (indirect)| 3-5              | Bracketed and parenthesized                    |
| `reference_list`           | 1                | The bibliography section                       |
| `reference_list_entry`     | 2-3              | Examples; not every entry                      |

Total: ~30-50 annotations per doc. Across 3-5 docs, you end up with ~100-250
annotations for the BOOTSTRAP report — enough variety per kind for Cline to
derive a generalizing rule, not enough to be tedious.

# What happens after you save

1. You hit **Save** in the Bootstrap tab → JSON written atomically to
   `<env_dir>/annotations/<plan>_annotations.json`
2. Once you have 3-5 docs annotated, paste the prompt from the Cline scaffold
   doc (`cline-playbooks/README.md`) into Cline
3. Cline runs `orient.md` → `mapping.md` → `bootstrap.md` and emits a redacted
   BOOTSTRAP report (≤25 lines)
4. You hand-type that report into the Teacher LLM
5. Teacher LLM commits an updated `customizations/profiles/<plan>/profile.json`
   plus any new parser code (e.g. `reference_list_map` plumbing if it's the
   first corpus to need it)
6. You `git pull` on-prem, run the pipeline, and review parser output via the
   **Review** tab on the Parse page → that drives the next iteration via
   `cline-playbooks/feedback-loop.md`

The annotation files stay on disk through the whole loop. Rule changes happen
through code (committed by Teacher LLM); annotations are the human-curated
ground truth that justifies them.
