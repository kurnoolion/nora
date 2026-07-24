# sira-enrichment-pe

**Status:** in-flight
**Opened:** 2026-07-24
**Landed:**
**Assignees:** kurnoolion
**Target modules:** unspecified (sandbox/sira scripts — outside the module system, like sira-query)
**Active phase:** architecture

## Summary

Prompt engineering + throughput for SIRA doc-enrichment: per-MNO prompts
(each MNO__Rel cell resolves its own), prompt composition = AI-scanned
corpus overview + the plan's feature-taxonomy file (feature / key
concepts / keywords, ≤5k) with an explanatory prologue, and batched
enrichment — a batch creator packs whole requirements from a single
(MNO, release, plan) group into prompts up to a configurable token
budget (default 50k of a 64k context, 14k reserved for the response);
the LLM returns phrases per requirement as strict JSON. The existing
Cline prompt-generation skill becomes MNO-parameterized and repeatable;
per-MNO prompts remain runtime artifacts (never committed). Scorecard
(D-164) later measures whether these prompt changes helped — that eval
loop is a separate future strand.

## Notes

<!-- appended to over the strand's lifetime -->
