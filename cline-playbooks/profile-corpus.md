# Playbook: profile-corpus

**Purpose**: profile one document — extract format patterns (requirement-detection model,
heading shape, req-ID format + plan encoding, TOC, strikethrough, version-history, definitions
layout) — produce a `PROF` report Teacher LLM can act on without seeing corpus content. Handles
both document models: the **heading-anchored** model (one plan per document, requirements are
numbered section headings) and the **leading-id-body** model (a single PDF whose sections each
correspond to a plan, requirements are flat body paragraphs that begin with their req_id).

**Input**: a document path under `<env_dir>/input/<MNO>/<RELEASE>/<DOC>.{pdf,docx,xlsx}`
(`<DOC>` may be one plan, or — in the leading-id-body model — a single file carrying many plans).

## Steps

1. Identify MNO / RELEASE / PLAN from the path. If any aren't already in the redaction
   mapping, run `cline-playbooks/mapping.md` to add them BEFORE producing the report.
2. Run the profiler:
   ```
   python -m core.src.profiler.profile_debug --create --doc <path>
   ```
   This produces `<env_dir>/out/profile/<plan>_profile.json`.
3. Read the produced profile:
   - `heading_detection.numbering_pattern` (regex)
   - `requirement_id.pattern` (regex)
   - `toc_detection_pattern` + `toc_page_threshold`
   - `strikethrough_detection` method + parameters
   - `revision_history_heading_pattern`
   - `definitions_extraction.layout` (paragraph / table-2col / etc.)
3a. **Determine the requirement-detection model** — the profiler defaults this to
   `heading` and does NOT auto-detect it, so verify against the actual document:
   - **heading-anchored** — each requirement IS a numbered section heading (the
     req_id sits on, or just after, the heading); sections/subsections carry
     req_ids. → `requirement_id.detection_mode = "heading"` (the default).
   - **leading-id body** — requirements are flat body paragraphs whose text
     *begins* with the req_id; section/subsection headings are non-requirement
     context and carry NO req_ids. A single PDF with sections-as-plans is this
     shape. → set `requirement_id.detection_mode = "leading_id_body"` and
     `requirement_id.anchor = "leading_text"`.
3b. **Capture the req_id plan encoding** — from a few sample req_ids, note the
   separator and which 0-based component holds the PLAN code
   (e.g. `<PREFIX>-<PLAN>-<DIGITS>` → separator `-`, plan at position 1). This
   drives `requirement_id.components.{separator, plan_id_position}` so the parser
   extracts each requirement's plan. (If the plan is NOT in the req_id, say so.)
3c. **Enumerate the plans in this document** — one document may carry MANY plans
   (one section per plan). List the DISTINCT plan codes observed. The parser
   still emits one tree per document, but tags each requirement with its own
   plan, so the document fans out to N plans downstream.
4. Run the parser using this profile:
   ```
   python -m core.src.parser.parser_cli \
     --doc <path> \
     --profile <env_dir>/out/profile/<plan>_profile.json \
     --tree-out <env_dir>/out/parse/<plan>_tree.json
   ```
5. Run parse-audit:
   ```
   python -m core.src.parser.parse_review --create-all
   ```
   This populates `<env_dir>/reports/audit/<plan>_audit.csv` with HI/MED/LOW
   classifications per requirement.
6. Read the audit CSV; aggregate by severity. Note category of any LOW rows by
   structural pattern (e.g., `deep-nesting`, `merged-section-numbers`) — never by quoted
   content.

## Output: `PROF` report shape (apply mapping to every token before emitting)

```
PROF v=1 doc=<DOC-placeholder>
mode:      heading | leading_id_body ← requirement-detection model (step 3a)
sec_re:    <regex>                   ← from profile.json, generic
req_re:    <regex with placeholders> ← e.g., ^<MNO0>_REQ_<PLAN0>_\d+$
plan_comp: sep=<char> pos=<int>      ← plan position in req_id (step 3b); "none" if plan not in req_id
plans:     <N> [<PLAN0>,<PLAN1>,…]   ← distinct plans in THIS doc (step 3c)
toc:       <pattern> thr=<float>     ← e.g., leader-dot-page thr=0.7
strk:      <method> [<params>]       ← e.g., geom 2lines width≥0.5
ver:       <regex>                   ← e.g., ^revision\s+history$
defs:      <layout>                  ← e.g., 2col-table Acronym|Definition
N:         req=<int> sec=<int> tbl=<int> fig=<int>
audit:     HI=<float>% MED=<float>% LOW=<float>%
miss:      <count> in <severity> [+ structural-category]
notes:     <≤20-word abstract observation, optional>
```

If any new structural element was observed (not previously in the profile schema), add a
`new:` line:

```
new: <element-name> <one-line abstract description>
```

## Constraints

- **Maximum 18 lines** in the output (15 fixed + up to 3 conditional).
- Apply mapping to every token. Real plan IDs, MNO names, file paths, req IDs must all be
  redacted — including every plan code on the `plans:` line.
- Regex patterns: emit verbatim if they are already generic (numbering shapes, position
  rules). If a regex contains a real MNO/PLAN literal, redact those literals to placeholders.
- If a `MAPPING:` line was added during this run, prepend it to the report.

## Common follow-ups Teacher LLM may request after PROF

- "Set `requirement_id.detection_mode = leading_id_body` + `anchor = leading_text` for this
  corpus" → Teacher LLM commits the profile change (the leading-id-body model — sections are
  context, requirements lead with their id).
- "Set `requirement_id.components` (`separator` + `plan_id_position`) so the plan is extracted
  per requirement" → similar (drives the one-document-many-plans fan-out).
- "Tighten `sec_re` to handle X case (deep nesting, merged numbers)" → Teacher LLM commits a
  regex change to `customizations/profiles/<PLAN>/profile.json`.
- "Lower `toc.thr` to 0.6 for this plan" → similar.
- "Add `revision_history_heading_pattern` for this plan" → similar.

You apply via `git pull` + re-run profile-corpus.
