# sira-intent

**Status:** in-flight
**Opened:** 2026-11-04
**Landed:**
**Assignees:** Hanif
**Target modules:** query, decision, web, eval
**Active phase:** development

## Summary

Give the SIRA serving path a real query classification. Today the SIRA
path reads only the MNOs and releases from the analyzer, and `query_type`
mixes intent with scope. Add a separate `intent` dimension (fact, explain,
list, compliance, change, compare, other) to `QueryIntent`, next to the
unchanged `query_type`. Classify it once, first, in nora-web: a rule-based
baseline, and the jev decision model (Kev) through the existing
`NORA_QUERY_ANALYZER` knob, asked a single intent question with
confidence routing to the rules. Phase 1 uses it for a select-synth
answer shape per intent (`NORA_SIRA_INTENT_PROMPTS`, default off) and for
History-lane routing (`change` → History). Measure Kev against the rules
on a committed synthetic dev set over the public Verizon Open Access docs;
a held-out check on real feedback questions runs later, in a deployment.
Design: `design.md`.

## Notes

