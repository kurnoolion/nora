# sira-enrichment-review — design

Working design notes. Decision-grade choices get drafted at close-session;
this file carries the full reasoning between sessions.

## 1. Persistence model (settled 2026-07-20)

**Delta overlay per cell, outside builds and labels, with reviewer labels.**

### Files (re-keyed per-MNO 2026-07-20 — cross-release propagation)

```
<corrections-root>/sira-enrich/<MNO>.json              # one per MNO, keyed by req_id
<corrections-root>/sira-enrich/labels.json             # {"disabled": [..]}
<corrections-root>/sira-enrich/reason-categories.json  # extensible category seed
```

Corrections are keyed by req_id per MNO (not per cell): a correction made
while viewing one release applies to every release of that MNO whose
requirement is still "the same" (fingerprint guard below). Releases left
the storage key because propagation is the default, divergence the
exception.

`<corrections-root>` is a dedicated mounted volume (pattern: the pooled
feedback dir) — NOT inside `db_root` cells and NOT inside promoted serve
labels (labels are immutable hardlink snapshots; corrections must outlive
builds and labels).

### Per-cell schema (word-record granularity — settled 2026-07-20)

Each edit is a per-word RECORD carrying its own label, reason, and
attribution. Storage granularity != input friction: the UI stamps records
from the "current label/reason" inputs as chips are clicked.

```json
{
  "REQ_12345": {
    "remove": [
      { "word": "handover", "label": "handover-noise",
        "reason": { "category": "misleading-enrichment", "note": "" },
        "by": "expert-name", "at": "2026-07-20T10:00:00Z",
        "origin": { "release": "Feb2026" } }
    ],
    "add": [
      { "word": "t3402", "label": "",
        "reason": { "category": "missing-acronym", "note": "" },
        "by": "expert-name", "at": "..." }
    ],
    "suppress_all": { "value": true, "label": "garbage-sweep",
                      "reason": { "category": "too-generic", "note": "" },
                      "by": "...", "at": "..." }
  }
}
```

One active record per (word, direction): re-editing a word replaces its
record. `suppress_all` stays entry-level (a per-req act) with its own
record fields; absent = false.

### Semantics

- Effective enrichment for a req =
  `suppress_all ? (active adds only) : (LLM words − active removes) + active adds`.
- A RECORD participates only when its `label` is not in
  `labels.json.disabled`; `""`/absent label = always-active default group.
  Word-record granularity means disabling a label suspends exactly the
  records carrying it, even when one req was edited under several campaigns.
- **Remove wins** on cross-group collisions (an active add never resurrects
  a word removed by another active record).
- Overlay is applied at the SAME points enrichments are applied today —
  key fact: sira-query loads a vanilla BM25 index and applies enrichment
  phrases IN MEMORY at startup (`_load_one_cell`), so overlay application
  at service load takes full retrieval effect on restart — no index
  rebuild, no re-promote. Batch eval paths apply it the same way (later).

### Why delta, not full-copy (divergence from D-011 corrections precedent)

Enrichments regenerate (re-runs, prompt/model changes), are thousands of
rows per cell, and the delta IS the evaluation signal — "experts removed
`handover` from 40 reqs" is the systematic-error evidence that drives
prompt fixes. Full copies fork stale on re-enrichment and bury the signal.

### Labels

Reviewer-supplied campaign tags, carried on each word record. UI: a
"current label" input; records created while set are stamped. Operations:
toggle a label (labels.json — non-destructive A/B of a hypothesis) and
bulk-delete a label's records (cleanup once a prompt fix lands — the
prompt-fix scorecard).

### Reasons (settled 2026-07-20)

Each entry carries a reviewer-supplied reason: structured category +
optional free-form note —

```json
"reason": { "category": "misleading-enrichment", "note": "confuses HO with attach retry" }
```

Categories are extensible via `<corr-root>/sira-enrich/reason-categories.json`
(seed: misleading-enrichment, too-generic, wrong-context,
hallucinated-concept, duplicate-of-req-text, missing-synonym,
missing-acronym, domain-term-missed); the UI's category dropdown offers
"add new category" which appends to the file. Free-form notes double as
the mining ground for future categories.

UX: a "current reason" selector + note box beside the "current label"
input; each chip click creates a word record stamped with both. Reasons
and labels are word-record-level (upgraded from entry-level 2026-07-20) —
no conflation when one req is edited under several campaigns/reasons.

Analysis payoff: label × category is the evaluation matrix — label = which
hypothesis/campaign, category = what kind of failure — and the section-3
export is a pivot over those two dimensions.

### Undo / non-destructiveness

LLM output is never touched. Undo = delete the word record (perfectly
targeted; attribution answers "who removed this and why" per word); undo
delete-all = drop the `suppress_all` record; restore-SIRA-originals =
delete the req's entry. No further history mechanism in v1.

### Cross-release propagation + fingerprint guard (settled 2026-07-20)

Records apply to a release iff the requirement is still substantially the
requirement the expert judged:

- Each record stamps `origin.release` (the release being VIEWED at edit
  time). No stored fingerprint — the multi-cell service loads every
  release's VANILLA index, so at application time it computes token-set
  Jaccard between (req, origin_release) and (req, current_release) using
  the vanilla BM25 term sets: the same tokenizer retrieval uses, on the
  pre-enrichment doc (the on-disk index is vanilla by construction — no
  circularity with enrichments/corrections).
- Jaccard >= 0.85 (tunable) -> record applies. Below -> HELD for review,
  not silently applied ("the correction was a judgment about that text").
- Origin release no longer loaded -> comparison impossible -> HELD (same
  posture: cannot-verify == verified-changed).
- UI: held records surface per req — "N corrections held: requirement
  changed since <origin>" with [Re-affirm] (re-stamps origin to the
  current release) and [Discard]. Re-affirm is one click; the expert has
  just re-read the requirement.
- req_id absent in a release -> records dormant there (no target, no harm).
- Implementation caveats: (a) depends on per-doc term sets being
  extractable from bm25x (or its tokenizer callable from Python) — check
  early; fallback is a faithful re-tokenization of corpus_by_id text,
  which reintroduces consistency risk; (b) the whole templated doc is
  indexed (plan stamp, cross-refs), so heavy cross-ref churn can trip the
  guard without meaning change — accepted (indexed content moved =>
  retrieval moved; re-affirm is cheap).

### Defaults for the open sub-questions (revisit if they hurt)

- `suppress_all` is STICKY across re-enrichment; the UI flags "suppressed,
  but a newer enrichment run exists" to prompt re-review.
- `remove` entries for words a new run no longer produces are KEPT — they
  encode intent and score prompt fixes (fixed = LLM stopped producing them).

## 2. Access path (settled 2026-07-20)

Ownership split along existing lines:

- **Cell data reads over HTTP from sira-query** (D-140 /cells precedent):
  new `GET /cells/<MNO>__<MMMYYYY>/enrichments` returns per-req
  {req_id, text, plan, llm_words, held: [record-refs + origin + verdict]}.
  The service already holds all of it in memory (corpus_by_id + loaded
  phrases) and already parses the plan stamp; it also computes the
  cross-release fingerprint verdicts (section 1) since it alone holds all
  releases' vanilla term sets.
- **Overlay read+write by nora-web on files** — the web app owns the
  corrections surface (as with feedback + parse-review corrections);
  corrections volume mounted rw. The UI computes the effective view itself
  (service llm_words + its own overlay) so the reviewer always sees their
  latest edits regardless of service state.
- **sira-query mounts the corrections volume ro** and applies the overlay
  inside `_load_one_cell` (same in-memory application point as enrichment
  phrases).
- Volume wiring per D-130: `CORRECTIONS_DIR` in compose env — rw into
  nora-web, ro into sira-query (+ sira-batch later for eval parity).

### Self-service application: per-cell hot reload (settled 2026-07-20)

Experts verify edits by re-querying WITHOUT an operator: sira-query gains
`POST /cells/<MNO>__<MMMYYYY>/reload` — re-runs `_load_one_cell` (which
already builds a complete fresh CellState) and swaps `_cells[cell]`
atomically under a per-cell lock; in-flight queries finish on the old
state. Seconds per cell. The review UI shows "N corrections pending" and
an **Apply** button that calls the endpoint (looping over BOTH stacks'
sira-query URLs when a/b are live), then the expert re-submits queries on
the /test page. Loop: edit → apply → re-query → judge; label toggles ride
the same mechanism (instant A/B of a hypothesis group).

Rejected for v1: mtime-based lazy auto-reload (zero-click but injects
surprise multi-second latency into an arbitrary query and hides state).
Reload is idempotent re-read-from-disk — the service stays non-stateful;
web stays the sole overlay writer.

## 3. Evaluation loop / exports (settled 2026-07-20)

The export turns accumulated word records into prompt-tuning evidence: a
deterministic, FIX-report-style text (D-012 posture — keyword tokens,
req_ids, category names, counts; no requirement body text) generated by
the web backend from the overlay + service data (plan lookup, current
llm_words).

### Report shape (scope: all, or filtered by label / MNO / cell)

1. Header — generated-at, scope, disabled labels, record totals.
2. Label x reason-category matrix — record counts; the campaign/failure
   pivot that names the systematic issues.
3. Top removed words — word, #reqs, #plans, categories; the systematic-
   noise list (with up to 5 sample req_ids each).
4. Top added words — the missing-vocabulary list, same columns.
5. Suppressions — count + per-category breakdown.
6. Held records — cross-release drift summary (origin release -> count).
7. Free-form notes verbatim, grouped by category — the mining ground for
   new categories and prompt clues.

Ordering is deterministic (FIX precedent) so successive exports diff
cleanly in chat.

### Prompt-fix scorecard

After a prompt change + re-enrichment run, the same generator runs in
scorecard mode: for every remove-record, is the word STILL in the current
LLM output for that req? still-produced = unfixed; gone = fixed (this is
why stale remove-records are kept). Aggregated per label and category:
"prompt fix efficacy: handover-noise 34/40 fixed". Closes the loop the
strand exists for: expert edits -> named systematic issue -> prompt change
-> measured fix rate.

### Delivery (v1)

`GET /api/enrich-review/export?label=&mno=&mode=report|scorecard` ->
text/plain, rendered on-page in a copyable <pre> + downloadable. No
automatic file drops in v1 (add an archival write under
<corr-root>/sira-enrich/reports/ if the team wants history beyond chat).

## 4. UI contract (settled 2026-07-20)

Page `/enrichment-review` (Bootstrap 5 + HTMX, server-rendered partials —
parse-review precedent). Team-mode gate: page + `/api/enrich-review/*`
join the team-allowed set (domain experts ARE the gated team). All
sira-query reads proxied through nora-web (one gate story, no CORS).

Layout: cascading MNO -> Release -> Plan selects; sticky stamping bar
(current Label [datalist+new], Reason category [select+new], Note, user
name) + pending counter + [Apply to serving]; table req_id | text
(truncated, click-expand) | enrichment chips.

Chip grammar:
- LLM word active: plain chip, x removes -> becomes struck ORANGE chip with
  undo (removed words stay visible; undo is one click on the thing itself)
- Added word: green chip, undo (same icon) un-adds
- Suppress all: row button; suppressed rows collapse to a summary line
  with Undo. Confirm dialog before suppressing (accidental-click guard).
- Held records (cross-release guard): per-req banner "N corrections held —
  requirement changed since <origin>" with [Re-affirm]/[Discard]
- Add box per row: free-form text + Add — the WHOLE input is ONE enrichment
  (SIRA enrichments are phrases: spaces, commas, periods, colons all legal
  inside one). Repeat type+Add for multiple enrichments. Never split.
Every mutation stamps label/name from the sticky bar plus reason/note from
THIS ROW's fields (reason is a per-requirement judgment, not a sweep-global
one) into a word record — no per-click dialogs; sweep-friendly.

State model (stateless): edits write the overlay immediately (no save
button). Pending = overlay mtime > cell loaded_at (sira-query reports
loaded_at per cell in /cells). Apply calls POST /cells/<cell>/reload on
BOTH stacks. Table always renders the effective view (service llm_words +
web's own overlay + held verdicts) so edits are visible instantly.

Endpoints (web): GET /enrichment-review; GET /api/enrich-review/cells,
/plans?cell=; GET /api/enrich-review/table?cell=&plan=; POST
/api/enrich-review/edit (op: remove|unremove|add|unadd|suppress|
unsuppress|reaffirm|discard-held, words, stamp) -> row partial; POST
/api/enrich-review/apply?cell=; GET/POST /api/enrich-review/labels,
/reasons. Service half: GET /cells/<cell>/enrichments[?plan=], /plans,
POST /cells/<cell>/reload, loaded_at in /cells.

v1 simplifications (accepted): no pagination (per-plan tables are
hundreds of rows; revisit past ~1k); concurrency = flock around overlay
read-modify-write, last-writer-wins per record (both stacks' web UIs
share the volume).
