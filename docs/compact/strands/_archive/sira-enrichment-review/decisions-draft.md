## D-DRAFT-1 — Enrichment corrections: delta overlay on a dedicated corrections volume

**Context.** Domain experts review/correct SIRA's per-requirement enrichment
words; edits must survive re-enrichment runs and serve-label churn.
Enrichment output lives in build artifacts (runs/doc-enrich/*); promoted
serve labels are immutable hardlink snapshots (D-132) — neither is writable
at review time.

**Decision.** Corrections are a DELTA overlay (remove/add/suppress_all per
req) stored under `<CORRECTIONS_DIR>/sira-enrich/` — a dedicated mounted
volume outside db_root cells and serve labels. LLM output is never touched.

**Why.** Divergence from the D-011 full-copy corrections precedent,
deliberately: enrichments regenerate (re-runs, prompt/model changes), are
thousands of rows per cell, and the delta IS the evaluation signal the
strand exists to produce ("experts removed X from 40 reqs" = prompt
evidence). Full copies fork stale and bury the signal. In-cell storage
would either be wiped by rebuilds or mutate immutable labels.

**Consequences.** Effective enrichment = fold(LLM output, active records);
both consumers (sira-query at load, web table) compute it. Undo = record
deletion (non-destructive by construction). New compose volume:
CORRECTIONS_DIR rw into nora-web, ro into sira-query (+ sira-batch later).

## D-DRAFT-2 — Word-record granularity: label, reason, attribution per edited word

**Context.** Edits need reviewer labels (campaign/hypothesis tags for
toggle + bulk ops) and reasons (structured category + free-form note) to
make aggregated analysis possible. Entry-level fields conflate when one
req is edited under several campaigns/reasons.

**Decision.** Every edit is a per-word RECORD:
{word, label, reason{category, note}, by, at, origin{release}}; remove/add
lists hold records; suppress_all is an entry-level record with the same
fields. One active record per (word, direction) — re-editing replaces.
Reason categories are extensible via reason-categories.json; labels toggle
via labels.json.disabled with REMOVE-WINS on cross-group collisions.

**Why.** Storage granularity != input friction: the UI stamps records from
sticky "current label/reason" inputs as chips are clicked. Word records
dissolve the conflation limitation for label AND reason at once, make undo
perfectly targeted, make label-toggling exact, and make analysis a flat
scan (pivot label x category x word).

**Consequences.** More nested schema; per-word metadata (kilobytes —
irrelevant). Label x category becomes the standing evaluation matrix.

## D-DRAFT-3 — Cross-release propagation via per-MNO keying + vanilla-index Jaccard guard

**Context.** A correction made in one release should apply to all releases
of that MNO for the same req_id — unless the requirement changed
significantly in between.

**Decision.** Corrections are keyed per MNO (`sira-enrich/<MNO>.json`),
applying to every release by default. Guard: records stamp origin.release
only; at application time the multi-cell service computes token-set
Jaccard between (req, origin_release) and (req, current_release) over the
VANILLA BM25 index term sets. >= 0.85 (tunable) -> apply; below, or origin
release not loaded -> HELD for review (UI: [Re-affirm] re-stamps origin,
[Discard] deletes).

**Why.** Per-MNO keying makes propagation a non-event rather than a copy
mechanism. Vanilla index terms beat body-text fingerprints on all axes:
the same tokenizer retrieval uses (no second normalization spec to
maintain), pre-enrichment by construction (no circularity — the on-disk
index is vanilla; phrases apply in memory), and "significant change" is
measured in the space corrections operate in. Storing no fingerprint
(origin release only) keeps the overlay lean and comparisons always-live.
Held-not-applied on mismatch is the conservative posture: the correction
was a judgment about THAT text.

**Consequences.** Effective-view computation is per-(record, release)
conditional. Depends on per-doc term extraction from bm25x (or a callable
tokenizer) — flagged as an early implementation risk; the fallback
(re-tokenization) reintroduces the consistency risk this choice avoids.
Cannot-verify == verified-changed (both HELD) also subsumes the
suppress-sticky-across-re-enrichment question.

## D-DRAFT-4 — Access split: service HTTP reads, web-owned overlay, per-cell hot reload

**Context.** The review UI (nora-web) needs sira cell data (req text,
plans, LLM words) that only sira-query mounts; experts must verify edits
by re-querying WITHOUT an operator in the loop for service reloads.

**Decision.** sira-query gains read endpoints (GET /cells/<cell>/enrichments
[?plan=], /plans; loaded_at per cell in /cells) and POST /cells/<cell>/reload
— re-runs _load_one_cell and atomically swaps _cells[cell] under a per-cell
lock. nora-web owns overlay read/WRITE on the corrections volume (flock,
last-writer-wins per record) and proxies all service reads (one gate story).
The review UI's Apply button calls reload on BOTH stacks.

**Why.** Extends the D-140 pattern (service reports on data it owns) without
turning a retrieval service into a stateful editor backend — reload mutates
nothing durable (idempotent re-read of disk). Enables the expert loop:
edit -> apply -> re-query -> judge, incl. instant A/B of a label group.
Verified enabling fact: enrichment phrases are applied IN MEMORY at cell
load, so reload suffices — no index rebuild, no re-promote, ever.
Rejected: mtime-based lazy auto-reload (surprise latency, hidden state).

**Consequences.** Web computes the effective view itself so edits render
instantly regardless of service state; pending = overlay mtime > loaded_at
(stateless). Reload is the service's only mutation-ish op; team-mode gate
must admit the new page + API prefix.

## D-DRAFT-5 — Evaluation export: label x category pivot + prompt-fix scorecard

**Context.** The strand's end product is prompt-tuning evidence, not just
corrected data: reviewers' aggregated reasons should name systematic
enrichment failures and measure whether prompt fixes worked.

**Decision.** A deterministic FIX-report-style text export (D-012 posture:
keyword tokens, req_ids, categories, counts — no requirement bodies):
label x category matrix, top removed/added words with req/plan counts +
sample req_ids, suppressions, held summary, free-form notes grouped by
category. Plus SCORECARD mode: per remove-record, does the current LLM
output still produce the word? still = unfixed, gone = fixed; aggregated
per label/category ("handover-noise 34/40 fixed"). Delivered via
GET /api/enrich-review/export (copyable + download); no automatic file
drops in v1.

**Why.** The pivot turns individual edits into named systematic issues;
the scorecard closes the loop (edits -> issue -> prompt change -> measured
fix rate) and is WHY stale remove-records are retained rather than pruned
— they are the measuring stick. Deterministic ordering keeps successive
exports diffable in chat (FIX precedent).

**Consequences.** Remove-records are permanent by default (data-retention
rule); bulk-delete by label is the sanctioned cleanup once a fix is
confirmed. Export generation needs service data (plans, current llm_words)
joined with the overlay — web-side, over the proxied reads.

## D-DRAFT-6 — Labels are branches: merge-log main, per-label serving variants, ungated Apply

**Context.** The original design had labels as toggleable tags inside one
shared view. In team use that meant an expert's in-progress corrections
hit everyone's default view immediately — no review gate between an edit
and production serving, and no way to preview a campaign in isolation.

**Decision.** A label IS a branch. Main = unlabeled records + labels
listed in `accepted-labels.json` (the merge log). A label view = main +
that label's records, served as a separate per-(cell,label) in-memory
variant built on demand. Admin-gated (team-mode `is_admin`): merge into
main, un-merge, delete-all — all merge-log or record-set operations;
records are NEVER rewritten on merge. Apply — and Apply-all, the
per-MNO staleness sweep in the Labels drawer (visible only when a
cell's view digest lags; reloads every stale cell on every configured
service) — is deliberately NOT admin-gated.

**Why.** Branch/merge is the mental model experts already have.
Merge-log-only merges are instant, reversible, and provenance-preserving
(every record keeps its label/author/reason forever — the evaluation
signal the strand exists for). Ungated Apply is safe by construction: a
reload can only sync serving to what the overlay + merge log already
contain — it can never publish an unmerged label. Rejected: rewriting
records' labels on merge (destroys provenance and the scorecard's
measuring stick); gating Apply on admin (blocks the expert self-service
loop D-DRAFT-4 established).

**Consequences.** Every read/serving path is view-parameterized
(allowed = unlabeled ∪ accepted ∪ viewer's label). A main reload drops
a cell's label variants — experts re-Apply after someone else's merge,
surfaced honestly by the pending banner (fallback state's digest
mismatches). A merged label's view is content-identical to main. A
merge stales every loaded release of the MNO at once — Apply-all is
the one-press answer to that fan-out.

## D-DRAFT-7 — Pending = view-scoped content digest, formula-locked across web and sira-query

**Context.** The designed pending signal was `overlay mtime > loaded_at`
(D-DRAFT-4). Field use showed its flaws: false positives (fully-undone
edits, touch-without-change), and no view scoping once labels became
branches. A later bug showed a subtler trap: hashing the merge log
flagged every MNO pending on any merge/un-merge — even MNOs the label
never touched.

**Decision.** Pending = sha256 over the view's FILTERED overlay records
(canonical JSON; the merge log itself is EXCLUDED from the hash),
compared between the web store's live disk state and the digest
sira-query reports for its applied state. The formula is duplicated on
both sides and must byte-match (cross-side parity test). All
pending/banner decisions read from the PRIMARY configured service
(`NORA_SIRA_QUERY_URLS[0]`) — the service every read path queries.

**Why.** Content comparison means fully-undone edits digest back to
in-sync — zero false pending. Merge-log exclusion: the filtered records
fully determine served content, so hashing the global log added only
false positives. Primary-service rule: a stale secondary (e.g. older
image, different formula) must not win a freshest-responder race and
wedge the banner. Rejected: mtime (false positives), per-record dirty
flags (server-side state to maintain; digest is stateless).

**Consequences.** Digest formula changes must ship in BOTH images
together. Service `/cells` reports per-cell view digests (feeds the
Apply-all sweep). Supersedes D-DRAFT-4's "pending = overlay mtime >
loaded_at" sentence — correct at promotion time.

## D-DRAFT-8 — Fold countermand: newer add beats older remove, ties keep remove-wins

**Context.** The fold's absolute remove-wins-over-add made a merged
label's removes permanent: a later expert's add record for the same
word existed on disk but never applied — no path to restore a word
short of un-merging the whole label (admin, all-or-nothing).

**Decision.** Per word: an applied remove suppresses an add UNLESS the
add's `at` stamp is strictly newer (ISO-8601 lexicographic compare).
Ties and missing stamps keep legacy remove-wins. Countermanded words
keep their original position; `applied_removes` excludes countermanded
words. Implemented identically in the service fold
(`apply_overlay_to_req`) and the web chip rendering (`_row_view`).

**Why.** "Latest human judgment wins" matches editing intuition and
makes every serving state reachable without admin intervention.
Tie-bias to remove preserves the behavior of historical stampless
records and fixtures. Rejected: deleting the remove record on re-add
(destroys the audit trail and the scorecard's evidence); requiring
un-merge (blocks on admin, punishes unrelated words in the label).

**Consequences.** `at` stamps become semantically load-bearing
(ordering, not just provenance). Both sides must mirror the rule
(covered by the cross-side test). A merged remove is no longer a
guarantee the word stays gone — reports/scorecard reflect latest state.
