## 2026-05-16 — strand opened; design converged on standalone sandbox

### Done this session
- Read the SIRA paper abstract + repo (arXiv 2605.06647; github.com/facebookresearch/sira; week-old, Anshumali Shrivastava et al.). Core technical claim is corpus-discriminative term selection via DF-filtered LLM enrichment — *not* just LLM synonym injection. Dual-sided (corpus + query) by design.
- Iterated on the integration shape over two pushbacks:
  - I proposed primitives-first decomposition (query-expansion alone as Phase 1). You correctly rejected: SIRA's contribution is the dual-sided enrichment paired with the DF-filter; one side alone is just noise.
  - I proposed dual-sided enrichment as a NORA-internal chunk-builder change. You correctly rejected: hybrid system tests something neither team designed; standalone is cleaner attribution + cheaper engineering + reversible.
- Converged on: **run SIRA's full published pipeline as a standalone sandbox**, with two thin adapters (NORA parse output → BEIR-shape `corpus.jsonl` + 18-Q eval → `queries.jsonl`/`qrels.tsv`; SIRA output → NORA req_id-level recall/MRR). Phase 2 (integration shape) only fires if Phase 1 shows lift.
- Locked five Phase-1 decisions into STRAND.md Notes:
  1. LLM = proprietary 100B+ via 50-line FastAPI `/v1/chat/completions` shim wrapping `customizations/llm/proprietary_provider.py` (SIRA source unmodified). Swap to local Qwen-35B + sglang post-DGX-Spark (~1 week).
  2. Acronym pre-expansion in `ACRONYM (Full Expansion)` bracket form — matches NORA's chunk-builder convention, apples-to-apples vs NORA BM25 lane.
  3. Heading-only reqs included with title-only text — spec heading often *is* the requirement.
  4. Struck reqs already absent from `tree.requirements` (parser drops per D-031/D-037); no extra adapter filter.
  5. Combined corpus across plans (18-Q eval spans plans).
- Bound session to strand.

### Out-of-scope work in same session
- Parse page UI merger (Summary + Review unified; Bootstrap tab deleted; 3 cols added to DocSummary). Commits acd6be8 + 2a2aeb5.
- `parse_debug references` diagnostic + `references-handling` strand opening. Commit 2500b2a.
- These touched parser / web / extraction modules; no overlap with sira.

### In progress
- Phase 0 not started — strand exists in design-only state.

### Next
- **Phase 0**: clone github.com/facebookresearch/sira to a `sandbox/` sibling dir (not under `core/src/`). Install (Python 3.12 + Rust). Run their `scifact` example end-to-end against the proprietary LLM via a stub shim. Read the actual prompt files (`doc_v06.txt` / `query_v06.txt` / `relevance_v02.txt` or whichever task-family variants ship — couldn't fetch them via WebFetch, need a clone).
- Decision after Phase 0: is the prompt shape transferable to telecom-requirements without rewriting? If `doc_claim` / `query_claim` look usable as-is, proceed to Phase 1 adapter; otherwise spike a `doc_requirement` / `query_requirement` variant.

### Flags
- SIRA's LLM call shape (sglang-native schema-constrained gen vs OpenAI-style chat-completions) is unknown until we read `src/sira/llm.py` post-clone. The shim's translation layer complexity depends on it — flagged as Phase 0 finding, not a blocker.

## 2026-05-17 — work-PC install shakedown + overnight pipeline kicked off

### Done this session
- **Phase 0 productive prep** (dev PC, commit `dd6effe`): cloned `facebookresearch/sira` into gitignored `sandbox/sira/`; confirmed LLM call shape is plain OpenAI Chat Completions (no SIRA fork needed); read actual generic prompts (`doc_v07` / `query_v07` / `relevance_v04`) — they're Wikipedia/factoid-tuned, so wrote `v01` telecom variants (req_id / plan / 3GPP-spec / network-element examples); built the FastAPI shim (`sandbox/shim/openai_shim.py`), NORA→BEIR adapter (`sandbox/adapter/nora_to_beir.py`), three SIRA hydra configs (`sira_configs/{data,enrich,rerank}/nora.yaml`), `install_configs.sh`, README + SETUP.md.
- **Phase 1 work-PC install shakedown** — 13 follow-up commits fixed corporate-env friction one error at a time:
  - TLS-CA-not-trusted ×2: `uv pip --system-certs` (`9a31097`); httpx reads `SSL_CERT_FILE` (`4b29f48`).
  - HTTPS_PROXY interception ×2: shim's outbound (`bde15e8` — `NORA_LLM_SKIP_PROXY`); SIRA's `urllib` localhost probe (`5e54116` — `activate.sh` auto-adds `127.0.0.1,localhost,::1` to NO_PROXY).
  - GPU stack inaccessible (firewall blocks download.pytorch.org): documented trimmed-install path (`164307e`); empirically pinned the actual minimum dep set — `polars[rtcompat]` for AVX2-less CPU (`b7c2211`), `beir --no-deps` + `pytrec_eval` + `numpy` for eval_bm25 (`fd05334`).
  - Pass-through shim mode so `proprietary_provider.complete()` doesn't need implementing (`669c541`) — `NORA_LLM_BASE_URL` + `NORA_LLM_API_KEY` + `NORA_LLM_MODEL` env vars (D-044 / D-049 parity).
  - `sandbox.sh` is conda-only → wrote uv-flavored `sandbox/activate.sh` (`b68e9d2`).
  - Hydra config bugs: bogus `server.auto_start=false` flag I'd invented — removed; shim now serves `/v1/models` so SIRA auto-detects (`41e8a06`). `enrich/nora.yaml` and `rerank/nora.yaml` lacked `defaults: [default, _self_]` so CLI overrides like `enrich.concurrency=8` 500'd (`a6a3dea`).
  - .gitignore hygiene: `data/` rule was eating `sandbox/sira_configs/data/nora.yaml` — added negative-glob (`66c22f0`); ignored regenerated `sandbox/adapter/out/` (`870ef7b`).
- **Verify-C cleared** on work PC against the adapter output: BM25-only baseline returned `Recall@10: 53.4%, NDCG@10: 0.427, Recall@100: 80.8%, Recall@200: 83.8%`. Mid-tier vs BEIR benchmarks; meaningful headroom for the LLM-enrichment stages.
- **Verify-D kicked off overnight.** Throughput measured at 92 calls in 7 min (~36s/call avg at `enrich.concurrency=8`); full corpus run projected at ~17h. User detaching shim + pipeline under tmux/nohup before leaving for the night.

### In progress
- Overnight pipeline run (Phase 1 full). Four `best.json` outputs expected at `sandbox/adapter/out/nora/eval/{baseline,doc-enrich,query-enrich,rerank}/`. First-light comparison tomorrow morning.

### Next
- Triage tomorrow's eval JSONs. Compare Recall@10 lift per stage against BM25 baseline (53.4%). The interesting numbers: doc-enrich (does corpus DF-filter help?), rerank (does the LLM reranker pull more correct reqs into top-10?).
- Scope per-query probe via NORA's Test page (~half-day implementation). Strand-decision-compatible because SIRA stays whole in `sandbox/sira_query/` — NORA web UI just gains a thin proxy toggle. Synthesizer reused for apples-to-apples. Wait until eval results land + I've read SIRA's per-query functions + NORA's `query.py` before writing the precise file-level plan.

### Flags
- **Throughput vs. wall-clock asymmetry**: at 36s/call, the 13k-req enrichment is the long pole. If results tomorrow look promising and we want fast iteration on prompts or `max_doc_chars`, we'd need to either (a) raise concurrency above 8 (untested if the proprietary endpoint absorbs it), or (b) sample with `max_docs=N` for iteration loops, then full-run only at end. Decide post-eval.
- **NLTK / other transitive deps**: SIRA's pipeline LLM stages (`enrich_corpus` / `enrich_query` / `rerank`) haven't been exercised yet on work PC. If they need any deps the BM25 stage didn't (nltk, scipy, pandas), we'll discover them only after overnight run progresses past BM25. Monitoring tomorrow.

## 2026-05-22 — Phase 1 verdict + Test-page per-query probe shipped

### Done this session
- **Verify-D completed.** Overnight run finished with 110 doc failures (60 socket/HTTP-502, 50 `no_phrases`); retried via SIRA's resume (`+run_name=enrich-1779146356`). All four stages have `best.json`.
- **Eval verdict — SIRA loses on this corpus** (vs BM25 baseline 53.4% Recall@10):
  - doc-enrich: **−18pp**
  - query-enrich: identical to doc-enrich (no query-side lift)
  - rerank: **0%** — 52% of candidates got zero scores from the LLM judge
  - Consistent with the paper's Wikipedia/factoid prompt tuning; telecom-requirements is a different genre.
- **Per-query SIRA probe.** New `sandbox/sira_query/service.py` (~370 LOC) exposes `POST /sira-query` with per-call rerank latency histogram. NORA's Test page gained a "SIRA Retrieval" tab; apples-to-apples via pinned-chunk path through NORA's `LLMSynthesizer`.
- **Two-gate score filter** for which chunks reach the synthesizer: absolute floor `NORA_SIRA_PIN_MIN_SCORE=30` + relative floor `NORA_SIRA_PIN_REL_THRESHOLD=0.5×max`. Replaces rank-K cutoff per user pushback. Filtered chunks render dimmed so exclusions are visible.
- **Diagnostics restored on Test page.** Refactored `_answer.html` to fold SIRA into the shared `else` branch — both tabs share BM25/dense scores, Citation Audit, Show LLM Prompt, sentence coverage. Suppressed misleading "10 candidates from graph" footer on SIRA tab (`candidate_count=0`).
- **Sandbox concurrency=1 default** in `enrich/nora.yaml` + `rerank/nora.yaml` (proxy throttling).
- **Diagnostic: NORA-tab graph/query_intent panels.** Root cause = `QueryPipeline` cached on `app.state` until process restart (`query.py:459-462`); graph stage gates on `graph_path.exists()`, not on Config-page or `config/llm.json` skip flags (those only drive the ingestion runner). Recommended: restart web process.

### In progress
- Phase 1 → Phase 2 decision pending. Open question: is the loss from reranker prompt being telecom-blind (fixable), or from DF-filter enrichment producing low-signal terms on this corpus (harder)?

### Next
- Per-query failure-mode triage on 5–10 queries where NORA wins / SIRA loses, via the Test-page probe.
- Strand decision after triage: archive (tested & rejected) vs iterate on `relevance_v01_telecom` prompt + re-rerank.
- Optional: `docs/compact/env-vars.md` consolidation (~20 distinct vars, no canonical doc today).

### Flags
- **Web-app pipeline cache + skip-flag gap.** Config-page DB doesn't reach the cached pipeline, and `skip_taxonomy` doesn't appear honored at QueryPipeline construction at all (grep miss in `pipeline.py`). Not strand-specific; worth raising separately.
- **Reranker 52% zero-score rate** → prompt mis-fit, not noise. `relevance_v04` → telecom variant is the obvious next experiment.
- **Two-gate thresholds (30 / 0.5×) are eyeballed** — sweep if the strand continues.
