# recall-metrics

**Status:** in-flight
**Opened:** 2026-11-11
**Landed:**
**Assignees:** Hanif
**Target modules:** eval, web
**Active phase:** development

## Summary

Silver recall for SIRA select-synth answers (#24): the requirements the answer cites stand in for gold, and recall@k (k = 10, 20, 30, 40, capped at the fetched count) measures how deep in the BM25 fetch they sat. The numbers go in the feedback row's `lane_config` (numbers only, no ids or text), one muted line shows under the answer, and a report CLI averages them over time and by intent. Golden-set changes are out of scope. Design: `design.md`. Branched from main after #25 merged (2026-11-13).

## Notes
