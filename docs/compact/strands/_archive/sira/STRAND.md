# sira

**Status:** in-flight
**Opened:** 2026-05-16
**Landed:**
**Assignees:** kurnoolion
**Target modules:** eval
**Active phase:**

## Summary

Verify the SIRA paper's claims (arXiv 2605.06647 — Yang/Ma/Chen/Shrivastava, Meta, May 2026; github.com/facebookresearch/sira) against NORA's MNO requirements corpus. SIRA = SuperIntelligent Retrieval Agent: LLM-driven dual-sided enrichment (corpus + query) with DF-filter on discriminative terms, plus LLM pointwise reranking — claimed SOTA on 10 BEIR benchmarks.

Run SIRA's full published pipeline as a **standalone sandbox** on our corpus + 18-Q eval set, *not* a partial integration. The decomposition-and-integrate approach was rejected: SIRA's contribution is end-to-end (DF-filter is co-designed with the enrichment), so testing primitives in isolation would lose the actual paper claim, and a hybrid system tells us nothing clean about attribution.

**Phase 0**: clone SIRA, install (Python 3.12 + Rust bm25x), run their `scifact` example end-to-end, read the actual prompt files (`doc_v06.txt`, `query_v06.txt`, `relevance_v02.txt` or closest task-family variant — `doc_claim`/`query_claim` likely). Determine SIRA's LLM call shape — OpenAI-compatible or sglang-specific — to decide between fork-`llm.py` and FastAPI shim.

**Phase 1**: build two thin adapters — (a) NORA parse output → BEIR-shape (`corpus.jsonl` per-requirement rows from `_tree.json` with `_id=req_id`, `title={section_num section_title}`, `text=` MD body with hierarchy + cross-refs; acronym-expansion pre-applied in `ACRONYM (Full Expansion)` bracket form per NORA convention / D-032; heading-only reqs included with title-only text — spec docs commonly carry the requirement in the heading itself; struck reqs already absent from the parser output so no extra filter; combined corpus across all plans), and (b) SIRA output → NORA-style metric (recall/MRR at req_id level matching the 18-Q ground-truth). Run SIRA against the proprietary 100B+ company LLM via a 50-line FastAPI `/v1/chat/completions` shim wrapping `customizations/llm/proprietary_provider.py` — keeps SIRA's source unmodified. After ~1 week (DGX Spark setup), swap the shim's backend to a local Qwen-35B via sglang for a deterministic-cost re-run. Compare per-category vs A4 baseline (88.0% overall / 67.6% accuracy).

**Phase 2** (conditional): decide. If SIRA wins meaningfully → discuss integration shape (adopt primitives vs run as service vs replace retrieval lane). If SIRA loses → archive strand with finding. If mixed → small targeted adoption per category.

Frictions to be aware of:
- SIRA's `configs/sglang/` defaults to Qwen-35B; we sidestep entirely for the first run by using the company LLM endpoint, then come back to it post-DGX-Spark.
- Sandbox install lives in a `sandbox/sira/` subdir or sibling repo — NOT under `core/src/` — so it doesn't pollute the curated module surface.
- No proprietary content lands in third-party logs: the company LLM is the only LLM ever called against our corpus.

## Notes

Decisions locked at strand opening (2026-05-16):

1. **LLM access path**: FastAPI shim exposing `/v1/chat/completions`, routing to `customizations/llm/proprietary_provider.py`. SIRA source stays unmodified. Plan to swap shim backend to local sglang + Qwen-35B post-DGX-Spark setup (~1 week out).
2. **Acronym format in SIRA-input MD**: pre-expanded with bracket form — `ETWS (Earthquake and Tsunami Warning System)` — matches NORA's existing chunk-builder output. Apples-to-apples against NORA's BM25 lane.
3. **Heading-only requirements**: included in `corpus.jsonl` with title-only text. Source spec docs commonly express the requirement in the heading itself; dropping them would lose retrievable content.
4. **Struck requirements**: already absent from `tree.requirements` (parser drops them per D-031 / D-037). No extra filtering needed in the adapter.
5. **Corpus scope**: combined `corpus.jsonl` across all parsed plans; the 18-Q eval set spans plans so per-plan corpora would fragment the test.

Next concrete piece of work: clone SIRA + run `scifact` example end-to-end (Phase 0). Once that's green, read the actual prompt files (likely `doc_claim` / `query_claim` / `relevance` variants given paper's task-family menu) to confirm transfer to telecom-requirements shape.
