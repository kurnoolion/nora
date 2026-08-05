# plan-aware-sira — draft decisions

Draft decisions for this strand. Promoted to canonical `DECISIONS.md` with real
`D-XXX` IDs at `/land-strand` time.

---

## D-DRAFT-1 — Multi-granularity pointer rows + retrieval-time fan-out

**Context:** SIRA's discriminative-term (DF) filter is anti-breadth by design.
Plan-summarize queries ("summarize plan X") are structurally underserved: the
relevant content is spread across many requirement chunks, and a single broad
query can't retrieve them all. A doc-level row carrying full aggregated content
would be penalized by BM25 length normalization and would lose req-level
citation.

**Decision:** The BEIR adapter emits, alongside per-requirement rows, two extra
granularities per plan: `doc:<plan_id>` (one per plan) and
`section:<plan_id>:<section_num>` (one per section prefix, to a max depth).
Their body is a **req_id pointer list** ("Contains N requirements: …"), not full
content. The SIRA query service fans out a matched pointer row into its
constituent req-level chunks at retrieval time.

**Why:** Decouples *matching* (a short pointer row matches strongly on the plan
name with no length-norm penalty) from *content payload* (the actual req-level
chunks the synthesizer needs), and preserves req-level citation. Alternatives
considered and rejected: (a) taxonomy-guided query expansion — wouldn't capture
a plan's full content; (b) putting full aggregated content in the doc row —
BM25 length-norm penalty + loss of citation granularity.

**Consequences:** Corpus grows by ~2126 rows (124 doc + 2002 section on the
current corpus); enrichment and the BM25 index must cover them. Fan-out caps
(`_FANOUT_PER_HIT`, `top_k`) now govern summarize coverage. Pointer rows must
stay in sync with req rows — a req_id present in the pointer list but absent
from the corpus is silently dropped by fan-out. See [[D-DRAFT-4]] for why
fan-out must eventually be plan-scoped rather than unconditional.

**Note (2026-05-27): pointer rows + fan-out are coupled, and the right setting
is query-type dependent.**
- Fan-out ON expands pointer rows into req-level content — correct for
  plan-summarize ("Summarize PLAN_X"), but over-broadens feature/concept queries
  ("Summarize ADD flow") by exploding every matching plan's pointer rows.
- Fan-out OFF is correct for feature queries, but then pointer rows are dead
  weight in the index: if one ranks into top_k it returns a useless id-list
  chunk. (Confirmed they don't rank for feature queries, but they would on a
  strong plan-name match.)
- These are two coherent end-states — *plan-aware mode* (pointer rows + fan-out,
  ideally with pointer rows ranking) vs *vanilla mode* (pointer rows excluded
  from retrieval + fan-out off) — controlled today by one global
  `NORA_SIRA_FANOUT_ENABLED` flag. **Query-type routing** (detect a named-plan
  target → plan-aware; else → vanilla) is the required next step, not a global
  switch.

---

## D-DRAFT-2 — Incremental enrichment (content-hash resume + drift guard)

**Context:** Corpus growth is the steady state (more releases, more MNOs). A
full doc-enrichment pass is ~13h. Growing the corpus 10× must not force a 10×
rebuild.

**Decision:** `sandbox/sira_incremental.py` rides SIRA's native doc_id-keyed
resume and adds content-hash awareness. `prune` (pre-enrich) evicts changed +
removed doc_ids from the run's trace so they re-enrich; unchanged docs stay
skipped. `commit` (post-enrich) records the corpus content-hashes as the new
baseline. A pinned `run_name` accumulates the trace across ingests. A
cumulative-drift guard warns when corpus growth since the last full rebuild
exceeds a ratio (default 1.5×, `--strict-growth` makes it a hard stop).

**Why:** Only new + changed docs hit the LLM; the BM25 rebuild and DF filter are
cheap and always run over the full corpus. Content-hashing catches same-doc_id
content edits (e.g. a correction) that SIRA's id-only resume would wrongly skip.
The drift guard surfaces when corpus-wide DF statistics have shifted enough that
cached enrichment (DF-filtered against a smaller corpus) is meaningfully stale.

**Consequences:** Operators must pin `run_name` and run prune/commit around each
ingest (documented in SETUP). A wrong/typo'd run_name silently falls back to
best-pointer behavior. The drift warning is advisory unless `--strict-growth`.

---

## D-DRAFT-3 — `promote` subcommand; do not fork the SIRA clone

**Context:** On a full resume where every doc is already enriched
(`enriched_count == 0`), SIRA's `enrich_corpus` skips its apply block (gated on
`enriched_count > 0`), so `enrichments/doc/<run>.jsonl` is never written while
`update_best` still creates the `best.jsonl` symlink → a dangling link. The
service then logs "No doc enrichments found" and retrieves on the bare index.
`sandbox/sira` is a separate, gitignored clone.

**Decision:** Add a `promote` subcommand to our tracked `sira_incremental` that
reconstructs `enrichments/doc/<run>.jsonl` from the run's
`enrichments.kept.jsonl` (merging per doc_id exactly as SIRA's apply does) and
repoints `best.jsonl` — rather than patching the SIRA clone.

**Why:** The SIRA clone is gitignored and separately maintained; a patch there
wouldn't propagate through our repo and would diverge on the next upstream pull.
Keeping the workaround in our own tooling makes it durable, tracked, and tested.

**Consequences:** One extra step in the full-resume recovery path. The upstream
SIRA quirk remains (we don't fix it there). Documented in SETUP's recovery
section.

---

## D-DRAFT-4 — No non-deterministic LLM call in the retrieval-ranking path

**Context:** Query enrichment was stochastic. Even after forcing
`temperature=0.0`, the backend LLM (an MoE model, "…A3B…") produced different
expansion phrases run-to-run, cascading into irreproducible retrieval (observed:
over 8 runs of one summarize query, 1 good / 2 no-answer / 5 over-broad).
Mixture-of-experts routing under batched serving plus FP reduction order is
non-deterministic regardless of temperature or seed.

**Decision:** Treat "no non-deterministic LLM call in the path that decides
ranking" as a design constraint for the SIRA service. For reproducible
retrieval, neutralize or disable in-path query expansion
(`NORA_SIRA_EXPANSION_WEIGHT=0`, or precomputed/cached expansions); LLM rerank
likewise cannot deterministically shape the ranking.

**Why:** A stochastic ranking can't be tuned or trusted — every other-knob
change is masked by sampling noise. The non-determinism lives in model serving,
not sampling, so temperature and seed can't reliably fix it on an MoE backend.
The deterministic value SIRA provides is the **offline** doc-enrichment applied
to the BM25 index, not live query-side LLM calls.

**Consequences:** Query expansion must be off, cached, or explicitly accepted as
noisy. Open: decide between off / cached / seeded (tracked in the journal Next).
The query-enrich temperature knob ([[D-DRAFT-5]]) reduces but does not eliminate
this — it's a serving-layer property.

**Amendment (2026-05-27, validated empirically):**
- **Rerank is the *primary* in-path non-deterministic LLM; query enrichment is
  secondary.** Rerank re-scores and re-sorts every candidate, so its variance
  dominates the final ranking (and, via the NORA pin-score threshold, drives the
  earlier good/no-answer/broad split). Query enrichment only nudges candidate
  selection.
- **`EXPANSION_WEIGHT=0` is insufficient** — `search_with_expansion` still feeds
  expansion terms into candidate selection (zero score, but they reorder tied
  candidates). The real levers are the `_ENABLED=false` flags:
  `NORA_SIRA_RERANK_ENABLED=false` + `NORA_SIRA_QUERY_ENRICH_ENABLED=false`.
- **Validated deterministic posture:** raw-query BM25 against the offline-
  doc-enriched index (no rerank, no live query enrichment) is reproducible AND
  surfaces correct chunks top-ranked. The deterministic value SIRA contributes
  is the *offline* enrichment baked into the index at load, not any live LLM
  call.

---

## D-DRAFT-5 — Query-enrichment temperature default 0.0

**Context:** The per-query service hardcoded `temperature=0.4` on the
query-expansion LLM call — the only non-zero temperature in the path — making
identical queries return different expansions (and thus different retrieval)
run-to-run.

**Decision:** Expose `NORA_SIRA_QUERY_ENRICH_TEMPERATURE`, default `0.0`
(greedy/deterministic). Surfaced in `/healthz`.

**Why:** A deterministic baseline so the other retrieval knobs are tunable;
sampling diversity becomes explicit opt-in. Cheapest first step toward
reproducibility.

**Consequences:** Changes prior default behavior. Does **not** by itself make
retrieval deterministic on a non-deterministic backend — see [[D-DRAFT-4]].
