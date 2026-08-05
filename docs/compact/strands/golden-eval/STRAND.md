# golden-eval

**Status:** in-flight
**Opened:** 2026-08-04
**Landed:**
**Assignees:** kurnoolion
**Target modules:** web, eval (+ runtime service edge: sandbox/sira_query, outside the module system)
**Active phase:** development

## Summary

Golden eval set for the SIRA-only NORA lane: 50 expert-curated samples growing
to 200, evaluating both releases (pre-PE and post-PE) and future improvements.
Two-stage process — Stage 1: expert authors a representative query for an
area/use-case plus ground-truth req_ids (single- or multi-plan); eval scores %
of ground-truth req_ids present in retrieved chunks. Stage 2: expert curates a
golden response via LLM chat over the ground-truth chunks; eval regenerates a
response from stage-1 retrieved chunks using nora-web's prompt and LLM-judges
similarity against the golden response. Full web UI as the expert one-stop-shop
for all authoring/curation steps; the eval run itself executes as a separate
build step (or final step of the existing build pipeline). Eval samples carry
proprietary content — they live under `<env_dir>/eval/`, never in the repo.

## Notes

<!-- appended to over the strand's lifetime -->

### 2026-08-05 — GEV artifact triple (NFR-9): concrete formats

Designed to match existing conventions in `core/src/pipeline/report.py`
(QC/FIX fixed-field dicts, RPT key=value lines) and `error_codes.py`.
Lands in code at development time; this note is the design contract.

**Error codes (`error_codes.py`):**
- `GEV-E001` — sample failed validation: unresolvable/ambiguous req_id {rid} in {sample_id} → fix ground truth in Eval Studio
- `GEV-E002` — stack {url_label} unreachable or /sira-query failed for {sample_id} → check serve stack health
- `GEV-E003` — Stage-2 synthesis failed: ground-truth/retrieved chunk `req:{rid}` not in NORA store → re-ingest or fix qualifier
- `GEV-E004` — judge call failed or verdict unparseable for {sample_id} → check judge provider / prompt version
- `GEV-W001` — sample {sample_id} skipped: status not ready for requested stage (draft vs stage1-ready vs golden-ready)

**Compact run report (RPT-style, chat-pasteable, counts/scores only):**
```
GEV {env} {stack_label} {ts} judge=v{N}
s1: n={N} recall_avg={0.xx} r@5={0.xx} r@10={0.xx} full={N} zero={N}
s2: n={N} judge_avg={x.x} judge_med={x.x}
delta {stackA}->{stackB}: recall={+/-0.xx} judge={+/-x.x}      # A/B mode only
err: GEV-Exxx({count}), ... or "none"
```
(`full` = samples with recall 1.0; `zero` = samples with recall 0. Per-sample
detail — hits, misses, ranks, judge point lists — stays in
`<env_dir>/eval/golden/runs/<run_id>/`, never in the compact report.)

**QC template (`QC_TEMPLATES["golden"]`):**
```
QC {env} golden
samples={N} draft={N} s1ready={N} golden={N}
ids_unresolved: SAMPLE_ID(count), ... or "none"
bare_ids={N} indep_missing: SAMPLE_ID, ... or "none"
judge_version: v{N} or MIXED(v{a},v{b})
notes: (free text)
```
(`bare_ids` = ground-truth entries with no cell qualifier; `indep_missing` =
samples with no independently-sourced ground-truth entry — the
retrieval-assisted-seeding bias check; `MIXED` judge_version is a QC fail.)

**FIX template (`FIX_TEMPLATES["golden"]`):**
```
FIX {env} golden SAMPLE_ID
ground_truth: +REQ_ID@CELL / -REQ_ID
golden_response: revised yes/no
status: OLD -> NEW
notes: (free text)
```
