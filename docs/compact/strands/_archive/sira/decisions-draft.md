## D-DRAFT-1: Verify SIRA on NORA corpus as a standalone sandbox, not via partial integration
**Status**: Active · **Date**: 2026-05-16.

**Decision**: Run SIRA's full published pipeline (`facebookresearch/sira`) as a standalone sandbox against NORA's MNO requirements corpus. Build two thin adapters only — (a) NORA parse output → BEIR-shape `corpus.jsonl` + `queries.jsonl` + `qrels.tsv` from the 18-Q eval; (b) SIRA's output ranks → NORA's req_id-level recall/MRR. Do *not* extract SIRA's primitives (corpus enrichment / query enrichment / LLM reranker) and bolt them into NORA's chunk-builder + query pipeline. The decision to integrate (or not) is deferred to Phase 2, conditional on Phase 1 results.

**Why**: Three reasons drive standalone over partial integration.

(1) **Faithful test of the paper's claim.** SIRA's contribution is end-to-end — DF-filtered dual-sided enrichment is co-designed across corpus and query stages, and the LLM reranker assumes the enriched candidates. Decomposing into "one primitive at a time" tests something the authors didn't design. If a hybrid wins 3%, attribution is ambiguous: was it SIRA's enrichment, NORA's retained structure, or interaction effects? Clean head-to-head answers the question NORA needs.

(2) **Cheaper engineering and reversibility.** Adapters are a few hours of plumbing in a `sandbox/` sibling dir. Integration would touch `vectorstore` (chunk_builder), `query` (rewrite stage), `eval` (comparison harness), `llm` (new caller path) — days of careful work in NORA core, with revert risk on every commit if SIRA flops. Sandbox loses → delete sandbox; integration loses → revert N commits with rot risk.

(3) **Attribution discipline.** NORA's weakest categories on the 18-Q eval (cross_doc 37.5%, standards_comparison 50%) are exactly the failure mode SIRA targets (concept queries where the right doc exists but doesn't rank in top-k). A standalone win/loss is the cleanest signal; the integration shape question becomes well-posed only after that signal lands.

Options considered:
- (a) Primitives-first decomposition (query expansion alone, then corpus enrichment, then reranker, each as opt-in NORA stages with per-phase eval). User rejected — SIRA's dual-sided enrichment loses its DF-filter pairing when split.
- (b) Dual-sided enrichment as a NORA-internal chunk-builder change, dense + graph + structure preserved. User rejected — tests a hybrid neither team designed; ambiguous attribution.
- (c) **Chosen**: full SIRA standalone, adapter-only, in a `sandbox/` sibling dir.

**Consequences**:
- Phase 1 work lives in `sandbox/sira/` (or sibling repo) — never under `core/src/`. Curated module surface untouched until Phase 2 decision fires.
- The 18-Q eval set + ground-truth req_id qrels become a shared artifact: NORA evals against its own pipeline, SIRA evals against its pipeline, same inputs. Eval harness adapter for SIRA-side metrics is new.
- Phase 2 decision tree: SIRA wins → integration shape discussion (adopt primitives vs run-as-service vs replace retrieval lane); SIRA loses → archive strand with finding entry to canonical DECISIONS noting "tested and rejected"; mixed → small targeted adoption per category.
- The proprietary LLM (100B+) becomes the only LLM in the loop for the first run. The 50-line FastAPI shim is operationally a new service surface that needs to stay alive while SIRA runs — Phase 0 finding will determine if `src/sira/llm.py`'s call shape requires a translation layer.
- Cost of running SIRA-as-published: per-doc + per-query LLM calls at ingestion; ~50 LLM calls per eval query at reranker top_n. The proprietary LLM's $0-marginal-cost makes this tractable; an OpenRouter run would be measurable $.

## D-DRAFT-2: Pass-through shim with env-var-driven mode selection — bypasses `proprietary_provider.complete()` when the LLM is OpenAI-compatible
**Status**: Active · **Date**: 2026-05-17.

**Decision**: The FastAPI shim at `sandbox/shim/openai_shim.py` supports two modes selected at startup by env vars:

  * **Pass-through** (when `NORA_LLM_BASE_URL` is set): the shim forwards SIRA's request body verbatim to the upstream OpenAI-compatible endpoint (with `Authorization: Bearer ${NORA_LLM_API_KEY}` injected and the `model` field optionally overridden via `NORA_LLM_MODEL`). The proprietary LLM's existing OpenAI-compatible `/v1/chat/completions` endpoint is the only LLM in the loop. `customizations/llm/proprietary_provider.complete()` is **not invoked at all** — its stub-`NotImplementedError` body is irrelevant on this path.

  * **Adapter** (when `NORA_LLM_BASE_URL` is unset): the shim falls back to calling `customizations/llm/proprietary_provider.complete()`. SIRA's OpenAI messages collapse into the `(system, prompt)` pair the provider expects; the provider's string response is re-enveloped into the OpenAI shape.

Env-var names (`NORA_LLM_BASE_URL` / `NORA_LLM_API_KEY` / `NORA_LLM_MODEL` / `NORA_LLM_TIMEOUT` / `NORA_LLM_SKIP_PROXY` / `NORA_LLM_VERIFY_SSL`) deliberately mirror NORA's existing OpenAI-compatible provider env vars (D-044 / D-049), so any shell that already has NORA's regular LLM configured picks up the shim's pass-through mode for free.

**Why**: Real-corpus encounter on the work PC: the company's proprietary LLM exposes a fully OpenAI-compatible `/v1/chat/completions` endpoint. With the adapter-only design from D-DRAFT-1 / strand opening, the user would have had to:

  1. Implement `proprietary_provider.complete()` in NORA's `customizations/llm/`.
  2. Inside that, build an OpenAI request, parse its response.
  3. Have the shim re-collapse SIRA's messages → `(system, prompt)` → re-build OpenAI request inside `complete()`.

That's a triple-translation: OpenAI shape (SIRA) → flattened (`complete()` interface) → OpenAI shape (LLM endpoint) → flattened (provider return) → OpenAI shape (shim response). Pure waste when the endpoint and SIRA agree on the shape natively.

Options considered:
- (a) Fork SIRA's `src/sira/llm.py` and replace the hardcoded `127.0.0.1:{port}` URL with the proprietary endpoint. Rejected — modifies upstream source, breaks the "SIRA stays whole" principle from D-DRAFT-1, lost on every `git pull`.
- (b) Always-adapter shim (the original design). Rejected — forces every deployment to author `proprietary_provider.complete()` even when the underlying LLM is OpenAI-compatible.
- (c) **Chosen**: dual-mode shim. The mode is selected at startup by whether `NORA_LLM_BASE_URL` is set. Both code paths are kept; the adapter path remains for deployments whose proprietary LLM uses a non-OpenAI API.

**Consequences**:
- The shim becomes a thin proxy in the common case (Meta-style internal LLMs typically expose OpenAI shape). Operationally this is one `uvicorn` process with five env vars.
- Lazy import of `proprietary_provider` (loaded only when `NORA_LLM_BASE_URL` is unset). Side benefit: deployments that never use adapter mode don't even have to read `proprietary_provider`'s stub.
- The shim's surface grew during this session beyond pass-through: TLS knobs (`SSL_CERT_FILE` honored via httpx `verify=<path>`; `NORA_LLM_VERIFY_SSL=false` escape hatch), proxy bypass (`NORA_LLM_SKIP_PROXY=true` → httpx `trust_env=False`), `/v1/models` handler (so SIRA's auto-detect probe in `run_pipeline.py` finds the shim and doesn't fall through to spawning sglang). All of these are corporate-environment frictions that would exist for *any* SIRA-vs-internal-LLM bridge regardless of mode choice.
- `/healthz` surfaces the active mode + resolved TLS / proxy / model-override config — single-curl debugging.
- The shim now has ~250 LOC, two distinct code paths, and six env-var knobs. Beyond the original "50-line shim" framing in D-DRAFT-1 but the additions are all corporate-friction fixes; no new architectural commitments.
- If a future deployment needs to test multiple proprietary LLMs side-by-side, restart the shim with different env vars between runs. Single-instance limitation, fine for our use case.

## D-DRAFT-3: Pinned-chunk synthesizer path + two-gate score filter
**Status**: Active · **Date**: 2026-05-22.

**Decision**: Add a `pinned_chunk_ids` early-return path to `QueryPipeline.query()` that skips Stages 3-5 (graph/retrieval/rerank) and feeds caller-provided chunks into `_context_builder.build()` + `LLMSynthesizer.synthesize()`. Gate which chunks reach the synthesizer with a two-floor filter: absolute (`NORA_SIRA_PIN_MIN_SCORE=30`) AND relative (`score ≥ NORA_SIRA_PIN_REL_THRESHOLD=0.5 × max_score`). Filtered chunks render dimmed in the UI, not hidden.

**Why**: External retrievers (SIRA) need NORA's synthesizer for apples-to-apples answers. Rank-K cutoff was the obvious alternative and rejected — K has no principled value. Score-based gating uses the reranker's own signal; two floors handle two failure modes (uniform-low confidence vs relative outliers in a high-scoring set).

**Consequences**: Any external retriever is now A/B-testable against NORA via the shared synthesizer. Pinned-chunk is the only `query()` path that synthesizes without running graph/retrieval. Thresholds are eyeballed; flagged for sweep. Filtered chunks visible (not silently dropped) is deliberate — debugging needs to see exclusions.

## D-DRAFT-4: Per-query SIRA probing stays in sandbox boundary
**Status**: Active · **Date**: 2026-05-22.

**Decision**: Interactive per-query SIRA probing runs as a separate FastAPI service (`sandbox/sira_query/service.py`); NORA's Test page calls it over HTTP via `httpx`. SIRA primitives are *not* ported into `core/src/query/`.

**Why**: Extends D-DRAFT-1's "standalone sandbox" principle from bulk eval to interactive probing. Porting reranker/enrichment into NORA's retrieval lane was the alternative — rejected because Phase 1 verdict was adverse and porting would commit code to `core/` for a primitive we may archive. HTTP boundary keeps the strand archive-able by `rm -rf sandbox/sira_query/` if SIRA loses.

**Consequences**: One extra process to keep alive during Test-page use (shim:8030 + SIRA service:8040 + NORA web). Latency added vs in-process call, acceptable for probing. Service is independently shutdown-able when strand archives.

## D-DRAFT-5: Phase 1 verdict — SIRA does not improve retrieval on the NORA corpus
**Status**: Active · **Date**: 2026-05-22.

**Decision**: Record the measured Phase 1 outcome: SIRA loses to BM25 baseline on the 18-Q eval set. doc-enrich −18pp Recall@10 vs 53.4% baseline; query-enrich identical to doc-enrich; rerank 0% Recall@10 with 52% zero-scores from the LLM judge.

**Why**: Consistent with the paper's Wikipedia/factoid prompt tuning — telecom-requirements is a different text genre. DF-filtered enrichment produces low-signal terms on this corpus; the relevance-judging prompt fails to discriminate.

**Consequences**: Phase 2 question narrows to "iterate on telecom-specific prompts or archive." Per-query probe (D-DRAFT-4) is the inspection surface. If we archive, this finding promotes to canonical DECISIONS as the strand's verdict ("tested and rejected on this corpus, archived").

**Status update (2026-05-23): SUPERSEDED by D-DRAFT-7.** Measurement conditions were broken (LTE-biased prompts + service-level doc-enrichment gap). Kept in this draft file for audit-trail purposes; will NOT be promoted at land-strand time.

## D-DRAFT-6: Pin `sira_query` service to specific offline run via per-stage env vars
**Status**: Active · **Date**: 2026-05-23.

**Decision**: Three independent env vars (`NORA_SIRA_DOC_ENRICH_RUN`, `NORA_SIRA_QUERY_ENRICH_RUN`, `NORA_SIRA_RERANK_RUN`) pin the service to a specific run per stage; `NORA_SIRA_USE_LATEST_RUNS=true` is a shortcut to auto-pick newest mtime. Fallback when none resolve: SIRA's `enrichments/doc/best.jsonl` pointer + `SIRA_CLONE_ROOT` prompts (historical pre-patch behavior). `/healthz` surfaces every resolved source path so deployments can record exact provenance in one curl. Doc-side enrichment is now applied at service startup via `_bm25.enrich_batch(items)` — was inert pre-patch.

**Why**: SIRA's stage timestamps are per-stage (`enrich-<T1>`, `query-enrich-<T2>`, `rerank-<T3>` with T1<T2<T3), so a single `NORA_SIRA_RUN_NAME` doesn't span all three stages. Pure `best/`-pointer reliance is non-deterministic — the pointer follows scoring, not recency, so a re-run that scored lower would silently not propagate to the service. Three explicit env vars allow ablation experiments (mix-and-match across runs) while making provenance auditable.

**Consequences**:
- Service deployments now have end-to-end provenance via `/healthz` — save alongside experimental results.
- Doc-side enrichment finally applies; D-DRAFT-5's verdict was against an incomplete service and needs re-measurement (addressed in D-DRAFT-7).
- New env-var surface area needs SETUP.md docs (follow-up).
- Three independent env vars put consistency responsibility on the user; worth noting in launch-runbook docs.

## D-DRAFT-7: SIRA is a retrieval lane, not a wholesale replacement (supersedes D-DRAFT-5)
**Status**: Active · **Date**: 2026-05-23. Supersedes D-DRAFT-5.

**Decision**: Supersede D-DRAFT-5's "SIRA tested and rejected on this corpus." That measurement was taken against (a) LTE-EMM-biased v01 prompts that mis-pattern-matched non-LTE reqs, and (b) a `sira_query` service that silently dropped doc-side enrichment (fixed in D-DRAFT-6). With both corrected, observed behavior is consistent with SIRA's design: DF-filtered enrichment + pointwise LLM rerank correctly surfaces reqs whose enrichment phrases overlap the query, including related reqs from sibling plans; reqs without strong discriminative phrases drop out of rank by design. SIRA is structurally **a lookup retriever** — strong on specific-entity queries, weak on breadth/summarize queries (where the DF-filter prunes exactly the shared vocabulary breadth queries want). For NORA's mixed query workload, SIRA is a **candidate as one retrieval lane (the lookup lane) alongside other lanes**, not a wholesale replacement.

**Why**: "Tested and rejected" (D-DRAFT-5 as-is) is wrong because the measurement conditions were broken. "Adopt wholesale" is wrong because the 3/25-VoWiFi result on a breadth query — even after fixes — is correct for SIRA's design but unsatisfactory user behavior. "Continue iterating SIRA prompts to fix breadth queries" doesn't solve it because the limitation is in the algorithm (DF-filter pruning shared vocabulary), not the prompts; prompts can move the line but not eliminate the structural mismatch.

**Consequences**:
- The breadth-query problem becomes an independent problem; natural home is `nora-retrieval-parent-displacement` strand or a new sibling, not `sira`.
- SIRA's sandbox infrastructure (offline pipeline, per-query service, debug CLI, prompts, eval adapter) survives and remains a candidate for the lookup lane.
- Phase 2's framing shifts from "decide integration shape if SIRA wins" → "decide how to integrate SIRA as one lane in a multi-lane retrieval architecture." Deferred to architect-driven work post-landing.
- D-DRAFT-5 is marked SUPERSEDED in decisions-draft.md (kept for audit trail); only D-DRAFT-7 gets promoted to canonical DECISIONS at `/land-strand` time.

## D-DRAFT-8: Per-stage LLM routing in SIRA service
**Status**: Active · **Date**: 2026-05-25.

**Decision**: Three env vars (`NORA_SIRA_RERANK_LLM_URL`, `NORA_SIRA_RERANK_LLM_MODEL`, `NORA_SIRA_RERANK_LLM_API_KEY`) route SIRA's rerank stage to a different OpenAI-compatible endpoint than query enrichment. Query enrichment continues through the standard shim. When unset, rerank falls back to the shim — preserves prior behavior.

**Why**: Query enrichment quality benefits from a strong LLM (proprietary 100B+ via shim); rerank just needs to output a 0-100 integer score — small local LLMs suffice. Alternatives: (a) repoint entire shim to a local LLM (rejected — sacrifices enrichment quality); (b) run two shim processes (rejected — extra ops); (c) **chosen** — per-stage env-var overrides on the SIRA service.

**Consequences**: New env-var surface on the SIRA service (3 knobs, surfaced via `/healthz`). Asymmetric routing introduces small consistency risk — operator can point rerank at a non-OpenAI-compat endpoint. Mitigated by graceful error logging per call (falls back to score=0). Establishes a reusable pattern for any future SIRA stage that wants its own LLM endpoint.

## D-DRAFT-9: Batch rerank via `NORA_SIRA_RERANK_BATCH_SIZE`
**Status**: Active · **Date**: 2026-05-25.

**Decision**: Opt-in batch reranking via `NORA_SIRA_RERANK_BATCH_SIZE`. Default 0 = per-call (current behavior). N > 0 = batch in groups of N. New built-in batch prompt asks for JSON array `[{"id": N, "score": M}, ...]` mirroring the per-call rubric. Parser handles well-formed arrays, CoT preambles, per-object regex fallback for messy output, partial responses, and score clamping (0-100).

**Why**: Per-call dominated query latency on proxy-throttled environments (~5s/call × 25 candidates = 2 min). Batch eliminates 24/25 API round-trips. Alternatives: (a) `asyncio.gather` parallel (rejected — proxy throttling serializes anyway); (b) hardcoded mini-batches only (rejected — operator should pick the size). **Chosen** — fully configurable; default 0 preserves per-call back-compat.

**Consequences**: New failure mode is atomic per batch — one bad LLM call zeros N scores instead of 1. Quality risk on long-context LLMs (gemma3:12b can't fit 25-chunk JSON in 4096 output tokens; proprietary works but triggers aggressive pin filtering downstream). Recommendation captured: 5-10 is the practical batch-size sweet spot pending further measurement.

## D-DRAFT-10: Plan-summarize gap → Tier-3 multi-granularity rows + fan-out (not taxonomy expansion)
**Status**: Active · **Date**: 2026-05-25.

**Decision**: Address SIRA's plan-summarize blind spot via Tier-3 multi-granularity rows in the BEIR adapter (doc-level + section-level rows alongside per-requirement rows), with a fan-out step in the SIRA service that expands doc/section matches into their constituent req-level chunks at retrieval time. Doc/section rows carry **`req_id` pointer lists** rather than full content — sidesteps BM25 length-normalization penalty while preserving req-level citation through the synthesizer. Implementation lives in new strand `plan-aware-sira`.

**Why**: SIRA's DF-filter is anti-breadth by design — prunes terms shared across many chunks, which is exactly what plan-summarize queries want. Alternatives: (a) **taxonomy-guided query expansion** (inject NORA's taxonomy features/phrases for the detected plan into SIRA's expansion) — rejected after user inspected taxonomy content and found it insufficient to drive retrieval; (b) **per-source-doc rows with full content** — rejected because Okapi BM25 length-norm penalizes long rows, defeating the purpose; (c) **chosen** — doc/section rows as pointer surfaces, fan-out at retrieval time. Best of both: strong plan-level matching AND req-level content for synthesis.

**Consequences**: Adapter gains an aggregation step; SIRA service gains a fan-out step. Per-req row format unchanged → backwards-compatible. Full SIRA pipeline rebuild required after adapter change. Two new row-id prefixes (`doc:`, `section:`) become a persistent shape — downstream code indexing by `_id` must tolerate. Per-req citation preserved through fan-out (synthesizer sees req-level chunks, cites correctly).
