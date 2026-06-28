# nora-retrieval-quality

**Status:** in-flight
**Opened:** 2026-06-28
**Landed:**
**Assignees:** kurnoolion
**Target modules:** query, vectorstore
**Active phase:** development

## Summary

Make NORA's retrieval / candidate-selection actually surface the relevant chunks
so they reach synthesis. Symptoms: single-MNO queries miss critical chunks (e.g.
the FR2 band requirement); cross-MNO queries drop an entire MNO's chunks
completely. select-synth (synthesis) is sound and sira-verified — the gap is
upstream retrieval, so this strand owns getting the right chunks INTO the LLM
call. Opening hypothesis: select-synth works in the SIRA harness but not the
nora lane, which points at integration (which enrich run the nora lane reads,
candidate hand-off, top_k / fusion starvation) rather than the synthesis design.
Picks up the query-side work paused from multi-mno-nora — see the relocated
draft decisions (per-cell vectorstore + NORA routing/fusion; balanced pin).

## Notes
