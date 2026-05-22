# derive-sira-prompts

Derive corpus-grounded SIRA prompts (`doc_requirement_*.txt`,
`query_requirement_*.txt`, `relevance_requirement_*.txt`) by reading
the parsed requirements documents and harvesting the actual subdomain
distribution, spec citations, vocabulary, and discriminative shapes
that exist in the corpus.

Replaces hand-written prompts, which tend to inherit whatever
subdomain bias the human author was thinking about. Corpus-derived
prompts pattern the LLM after what's actually present — proportional
to plan counts — so a VoWiFi-heavy corpus produces VoWiFi-rich
prompts automatically.

## When to invoke

- After the parser has finished a fresh corpus build and you want to
  refresh the prompts before re-running the SIRA pipeline.
- When the corpus composition changes meaningfully (new plans added,
  old plans dropped, a new subdomain enters scope) and the existing
  prompts feel mismatched to what `sira_debug phrases --filter X`
  surfaces.
- As the first step when adopting SIRA on a brand-new corpus that
  doesn't resemble the corpus the existing prompts were tuned to.

## Argument

1. **Version slug** (optional, default `v02`) — written as the suffix
   on the three output files. Example: `v02` → produces
   `sandbox/prompts/doc_requirement_v02.txt` etc. Refuses to overwrite
   an existing version unless `--force` is passed.

## Procedure

### 1. Locate the parsed corpus

Look for parsed `_tree.json` files in this order:

1. `<env_dir>/out/parse/*/tree.json` — canonical NORA parser output.
   `env_dir` is resolved from `environments/<env>.json` or the
   `ENV_DIR` env var. If multiple envs are configured, ask the user
   which one to read.
2. If no parsed trees are available, fall back to the BEIR-shape
   corpus at `sandbox/adapter/out/<dataset>/raw/corpus.jsonl`. Note
   that this loses hierarchy and definitions but still works for
   subdomain detection.

Abort if neither source is present.

### 2. Inventory the corpus

Build a table of `plan_id → req_count`. Sort descending by count.
Report the top-20 plans plus total req count. If there are more than
20 plans, surface that count and note that the long tail will be
sampled but not individually classified.

### 3. Sample representative requirements per plan

For each plan in the inventory, pull 5-10 requirements that are
representative of the plan's content:

- Prefer requirements with non-empty `text` (real normative content,
  not heading-only).
- Spread across the section hierarchy — don't sample 10 reqs from one
  subsection. Sort by `section_number` and pick evenly.
- For long-tail plans (≤20 reqs total), include all of them.

For each sampled req, surface: `req_id`, `section_number`, `title`,
first 400 chars of `text`. Tables and image captions ignored at this
stage.

### 4. Classify each plan into a subdomain

The skill recognizes this taxonomy (extend if your corpus has a
subdomain not listed — note the addition in the summary):

| Subdomain | Heuristic plan-name signals | Example spec citations |
|---|---|---|
| LTE EMM/NAS | EMM, NAS, ATTACH, DATARETRY, DATA_RETRY | TS 24.301, TS 23.401 |
| LTE RRC/Access | RRC, RACH, ACCESS, B13NAC, NAC, BAND | TS 36.331, TS 36.304 |
| 5G NR/NAS | 5GNR, NR, 5GS, 5GSA, 5GNRSA, 5GBANDS | TS 24.501, TS 38.331 |
| 5G Core | 5GC, AMF, SMF, SLICE, SLICING | TS 23.501, TS 23.502 |
| VoLTE/IMS | IMS, VOLTE, SIP, CSCF, VOICECALL | TS 24.229, GSMA IR.92 |
| VoWiFi/Untrusted-WLAN | VOWIFI, EPDG, WIFICALLING, WLAN, IPSEC, IKE | TS 23.402, TS 33.402 |
| WLAN/WiFi | WIFI, WLAN, ANQP, HOTSPOT, 802_11 | IEEE 802.11, Hotspot 2.0 |
| eSIM/RSP | ESIM, RSP, SIM, EUICC, PROFILE_DOWNLOAD | SGP.22, GSMA TS.34 |
| SMS | SMS, CMAS, WEA, CELL_BROADCAST, CB | TS 23.040, TS 23.041 |
| Device Management | OTADM, DM, FOTA, LWM2M, BOOTSTRAP | OMA-DM 2.0, TS.42 |
| Voice continuity / SRVCC | SRVCC, HANDOVER, CONTINUITY | TS 23.216 |
| Device capabilities | CAPABILITIES, FORM_FACTOR, BANDS_SUPPORTED | TS 38.101, TS 36.101 |

For each plan, choose the **primary** subdomain by reading the sampled
reqs, not the plan name alone — plan names can mislead. If a plan
genuinely spans two subdomains, record both with a `:primary` /
`:secondary` tag.

If no taxonomy entry fits, propose a new subdomain in the summary
(don't silently mis-classify).

### 5. Harvest subdomain-specific vocabulary

For each subdomain that has ≥1 plan classified to it, scan ALL reqs
in those plans (not just the samples) and extract:

- **Spec citations** — regex `TS \d{2}\.\d{3}`, `GSMA (?:IR|TS|SGP)\.\d+`,
  `RFC \d{3,5}`, `IEEE \d{3}\.\d{2}\w*`. Top 10 by frequency.
- **Timer/counter names** — regex `T\d{4}` or `\bN\d{3}\b`. Top 10.
- **Message types** — regex `\b[A-Z][A-Z_]{4,}(?:\s+[A-Z][A-Z_]+){0,4}\b`
  that look like protocol message names (filter via length + caps
  ratio). Top 10.
- **Procedure / network-element names** — bigrams and trigrams from
  section titles, lowercased, deduplicated. Filter common stopwords
  ("the", "of", "for"). Top 15.
- **Plan codes themselves** — pass through the actual plan_ids.

The harvested vocab is the example pool. Quality matters more than
quantity — drop terms that appear in <3 reqs (too rare to be
discriminative) or in >40% of reqs across all subdomains (too
generic, indistinguishable noise).

### 6. Compute example proportions

For each subdomain present, compute `weight = plan_req_count_total /
corpus_total`. Use these weights when filling examples in the
generated prompts:

- A subdomain with weight 0.40 gets ~40% of the example slots
  (rounded to a minimum of 1 example per non-zero subdomain so no
  subdomain is unrepresented).
- The doc-enrich prompt has ~8 example slots in its Priority-1 spec
  list and ~6 slots in its Priority-2 procedure list. Distribute
  proportionally.
- The query-enrich prompt has ~6 example query/expansion blocks.
  Distribute proportionally.
- The relevance prompt has ~6 scoring examples. Distribute
  proportionally.

### 7. Generate the three prompts

Open the **current v01 files** in `sandbox/prompts/` as templates.
Preserve their structure exactly:

- Same Jinja-style placeholders: `{doc_text}`, `{query}`,
  `{document}`, `{max_n}`.
- Same JSON output shape: `{{"keywords": [...]}}` for doc + query;
  `{{"score": <int>}}` for relevance.
- Same step-numbered procedure (`STEP 1`, `STEP 2`, etc.) shape.
- Same `Rules:` section style.

Replace ONLY the **example content**:

- The subdomain taxonomy table in STEP 1 should be **trimmed** to
  the subdomains actually present in this corpus. Drop unused rows.
- The Priority-1 spec citations and Priority-2 procedure shapes
  in STEP 2 of the doc-enrich prompt should be picked from the
  harvested vocab, proportional to subdomain weights.
- The "EXAMPLES — by subdomain" block in the query-enrich prompt
  should have one entry per present-subdomain, using harvested
  procedure/spec vocab.
- The scoring examples in the relevance prompt should cover the
  present subdomains.

Append a header comment to each file noting the derivation:

```
# Derived by /derive-sira-prompts on YYYY-MM-DD against the
# <env-name> corpus (N plans, M total reqs, K subdomains).
# Subdomain weights: VoWiFi 0.42, LTE-EMM 0.31, 5G-NR 0.18, ...
# Source: <env_dir>/out/parse/  OR  sandbox/adapter/out/<ds>/raw/corpus.jsonl
```

### 8. Write the files

Output paths:

- `sandbox/prompts/doc_requirement_<version>.txt`
- `sandbox/prompts/query_requirement_<version>.txt`
- `sandbox/prompts/relevance_requirement_<version>.txt`

Refuse to overwrite an existing version unless `--force` is passed.

If the version is bumped (e.g., `v02`), also update
`sandbox/install_configs.sh` to copy the new files, and
`sandbox/sira_configs/{enrich,rerank}/nora.yaml` to reference the new
prompt paths. Surface the diff to the user before writing.

### 9. Report

Print a compact summary:

```
Corpus inventory:
  source: <path>
  plans: N (top by req count: ...)
  total reqs: M

Subdomain distribution:
  LTE-EMM     31%  (3 plans, 1234 reqs)
  VoWiFi      24%  (2 plans, 956 reqs)
  5G-NR       18%  (2 plans, 714 reqs)
  IMS         12%  (1 plan,  478 reqs)
  ...

Harvested vocabulary (per subdomain):
  LTE-EMM:    TS 24.301 (87), TS 23.401 (54), T3402 (23), ATTACH REQUEST (19), ...
  VoWiFi:     TS 23.402 (62), TS 33.402 (41), IKEv2 (38), ePDG (35), SWu (29), ...
  ...

Wrote:
  sandbox/prompts/doc_requirement_v02.txt
  sandbox/prompts/query_requirement_v02.txt
  sandbox/prompts/relevance_requirement_v02.txt

Next steps:
  bash sandbox/install_configs.sh
  # then re-run SIRA pipeline (see sandbox/SETUP.md)
  # then validate via:
  python -m sandbox.sira_query.sira_debug phrases --filter <SUBDOMAIN>
```

## Validation hooks

After writing, suggest the user run these as sanity checks:

1. **Visual diff** — `git diff sandbox/prompts/` to confirm the
   subdomain coverage matches the inventory.
2. **Sample a few VOWIFI / LTE / 5G reqs** through the prompts by
   hand (the user can paste a req into a chat and see what the LLM
   proposes) — confirms the prompt teaches the right shape before
   spending hours running doc-enrich at scale.
3. **`sira_debug phrases --filter <SUBDOMAIN>`** after doc-enrich
   completes — confirms harvested vocab actually showed up in the
   generated phrases.

## Rules

- **Read-only on the parsed corpus.** Never modify `_tree.json` files
  or anything under `<env_dir>/out/parse/`. The corpus is upstream of
  this skill.
- **Never invent vocabulary.** Every term in the generated prompts
  must trace back to the harvested vocab list. No speculative spec
  numbers, no procedure names that aren't in the corpus.
- **Never silently mis-classify a plan.** If the taxonomy table
  doesn't fit, propose a new subdomain in the summary and ask the
  user to confirm before writing the prompts.
- **Preserve placeholders byte-exactly.** `{doc_text}`, `{query}`,
  `{document}`, `{max_n}`, `{{"keywords": [...]}}`, `{{"score":
  <integer>}}` — break any of these and SIRA's hydra template
  expansion will fail at runtime.
- **Don't auto-install.** Writing the prompt files is the contract;
  copying them into the SIRA clone is the user's `install_configs.sh`
  invocation. Surface the next-step command instead of running it.
- **Don't auto-trigger a pipeline re-run.** Generating prompts is a
  cheap operation; running doc-enrich is a multi-hour operation that
  the user must consent to explicitly.
