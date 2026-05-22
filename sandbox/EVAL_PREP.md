# Eval set preparation — guide

How to design a retrieval-eval set against the parsed MNO requirements corpus, and how to plug it into the SIRA pipeline (and NORA's own eval harness).

The eval set is **two files in BEIR shape** at `<sira_db_root>/<dataset>/raw/`:

- `queries-test.jsonl` — one JSON object per line, shape `{"_id": "<query_id>", "text": "<question>"}`.
- `qrels-test.jsonl` — one JSON object per line, shape `{"query-id": "<query_id>", "corpus-id": "<req_id>", "score": 1}`. Multiple qrel rows per query (one per relevant req_id).

The `corpus.jsonl` is generated automatically from the parser output and isn't part of eval design — focus is queries + qrels.

## Two paths

**Path A — Canonical (recommended).** Edit `core/src/eval/questions.py` and add `Q_<NAME>` module-level constants. The adapter (`sandbox/adapter/nora_to_beir.py`) introspects this module and writes both BEIR files. Same source-of-truth feeds NORA's own eval harness, so SIRA and NORA evaluate against identical questions.

**Path B — BEIR-direct.** Write `queries-test.jsonl` and `qrels-test.jsonl` by hand or via a one-off script. Faster for SIRA-only experiments. Doesn't reach NORA's eval.

This guide focuses on Path A. For Path B, just write the two JSONL files following the shapes above and skip the adapter.

## Path A — adding a question to `questions.py`

The two dataclasses live at `core/src/eval/questions.py`:

```python
@dataclass
class GroundTruth:
    expected_plans: list[str]      # plans that MUST appear in results
    expected_req_ids: list[str]    # req_ids that SHOULD appear (subset, not exhaustive)
    expected_features: list[str]
    expected_standards: list[str]
    min_plans: int = 1
    min_chunks: int = 1
    expected_concepts: list[str]

@dataclass
class EvalQuestion:
    id: str
    category: str
    question: str
    ground_truth: GroundTruth
    description: str = ""
```

For SIRA's eval, only `expected_req_ids` is used (the adapter turns each into a qrel row). The other fields drive NORA's per-category breakdown and are worth filling in for cross-tool comparison.

Template:

```python
Q_<CATEGORY>_<NN> = EvalQuestion(
    id="<category>_<nn>",
    category="<category>",
    question="<plain-English query as the user would type it>",
    description="<one-line context: why this question, what subdomain>",
    ground_truth=GroundTruth(
        expected_plans=["<PLAN_CODE>", ...],
        expected_req_ids=[
            "VZ_REQ_<PLAN>_<NUM>",   # short comment: which heading / section
            ...
        ],
        expected_features=["<FEATURE_FAMILY>"],
        expected_standards=["3GPP TS XX.XXX"],
        expected_concepts=["<keyword>", ...],
    ),
)
```

Add it anywhere in the file — the module gets introspected by name (`Q_*` constants of type `EvalQuestion`).

## Designing queries

**Coverage** — span every subdomain present in the corpus, weighted roughly by their share of reqs. Domains seen in US MNO device specs (use as a checklist):

- LTE EMM/NAS (attach, EMM timers, EPS bearer)
- LTE RRC/Access (RACH, PLMN/cell selection, idle/connected)
- 5G NR/NAS (5G registration, NR bands, PDU sessions)
- 5G Core (AMF, SMF, slicing)
- VoLTE/IMS (registration, SIP, P-CSCF discovery, supplementary services)
- VoWiFi/Untrusted-WLAN (ePDG, SWu, IKEv2/IPSec, EAP-AKA, Wi-Fi Calling)
- WLAN/WiFi (802.11, Hotspot 2.0)
- eSIM/RSP (profile download, EID, SM-DP+, LPA)
- SMS (SMS over LTE, Cell Broadcast, CMAS/WEA)
- Device Management (OMA-DM, FOTA)
- Voice continuity / SRVCC (VoLTE↔VoWiFi handover)
- Device capabilities (band lists, form factor, applicability)

If your corpus is single-domain (e.g., only LTE), constrain coverage accordingly. The point is no subdomain that exists in the corpus is unsampled.

**Query categories** — mix shapes so retrieval failures by category are visible:

| Category | Example | What it tests |
|---|---|---|
| `single_doc` | "What is the T3402 timer value?" | Direct lookup of a specific normative statement |
| `cross_doc` | "Summarize WiFi Calling requirements" | Breadth retrieval across many reqs in a plan |
| `traceability` | "Which reqs implement TS 24.301 Section 5.5?" | Spec-to-req resolution |
| `standards_comparison` | "How does VZW differ from 3GPP on data retry?" | Cross-corpus comparison |
| `feature_level` | "What does the IMS REGISTRATION feature cover?" | Feature-name discovery |
| `release_diff` | "What changed in eSIM between Q3 and Q4 2026?" | Release-aware diffing (post-v1) |

**Difficulty mix** — easy / medium / hard so a single mid-tier improvement doesn't show up as a flat 100% across the board:

- **Easy** — single specific entity in query (timer name, band number, spec section). Should hit @1.
- **Medium** — domain breadth ("describe Wi-Fi Calling authentication"). Should hit @10.
- **Hard** — concept-only (no entity names) ("how does a device survive transient PLMN outages"). Tests whether retrieval generalizes beyond keyword overlap.

**Phrasing** — write queries as a device engineer or compliance analyst would actually type them. Don't pre-load the answer into the query. Bad: *"What is the 720-second T3402 timer behavior?"* — gives away the value. Good: *"What is the T3402 timer behavior?"*

## Selecting ground-truth `expected_req_ids`

This is **manual SME work** — there's no automated path that produces trustworthy qrels. Steps:

1. **Identify candidate reqs.** Open the parsed `_tree.json` for the relevant plan(s) and read sections matching the query topic. Or run `python -m sandbox.sira_query.sira_debug corpus --filter <KEYWORD>` to surface req_id candidates.
2. **For each candidate, read its body.** A req qualifies as `expected_req_id` if any sentence in its body contains or implies the answer to the query (per the relevance rubric in `relevance_requirement_v01.txt`: 61+ on the 0-100 scale).
3. **Include the section heading req** if it anchors the topic (it'll score 41-60 on the rubric — still relevant for breadth queries).
4. **Exclude struck reqs.** The parser drops reqs marked struck (D-031). If a struck req shows up in your draft list, replace it with a live sibling — the eval would fail otherwise.
5. **Keep the list focused.** 3-10 req_ids per query is typical. Don't grow to 30+ — recall@10 caps out and the per-query signal flattens.
6. **Note the heading / section per req_id** in an inline comment — future ground-truth refreshes need that context.

## Validation

Before locking the eval set:

1. **Confirm each `expected_req_id` exists in the corpus:**
   ```bash
   python -m sandbox.sira_query.sira_debug req <req_id>
   ```
   Should print the req text. If it 404s, the parser dropped it (struck? schema change?). Replace with a sibling.

2. **Manual SME review of 5-10 queries.** A second pair of eyes on the question phrasing + the ground-truth list. Catches "I phrased this ambiguously" and "I forgot the obvious req."

3. **Run a quick retrieval-quality sanity check.** For each query, run:
   ```bash
   python -m sandbox.sira_query.sira_debug query "<question>" --top-n 50 --watch <one_expected_req_id>
   ```
   The watched req should appear in the top-50 BM25 candidate set. If it's outside top-50, the eval question may be genuinely hard for this corpus + retriever — that's fine but flag it so a 0% recall isn't a surprise later.

## Plugging into the SIRA pipeline

After editing `questions.py`:

```bash
# 1. Regenerate the BEIR-shape files
python -m sandbox.adapter.nora_to_beir \
    --env-dir <env_dir> \
    --out <repo>/sandbox/adapter/out/nora

# 2. Verify
head -3 <repo>/sandbox/adapter/out/nora/raw/queries-test.jsonl
head -3 <repo>/sandbox/adapter/out/nora/raw/qrels-test.jsonl
wc -l <repo>/sandbox/adapter/out/nora/raw/queries-test.jsonl
wc -l <repo>/sandbox/adapter/out/nora/raw/qrels-test.jsonl

# 3. Re-run SIRA pipeline (full or per-stage)
# See sandbox/SETUP.md "Per-stage run" section.
```

## Interpreting `best.json`

After a stage completes, the eval result lands at:

```
<sira_db_root>/<dataset>/eval/{baseline,doc-enrich,query-enrich,rerank}/best.json
```

Each file contains `Recall@{1,10,100}`, `NDCG@10`, `MRR@10` aggregated across all queries. Per-query traces (which req_ids matched at which rank) live at:

```
<sira_db_root>/<dataset>/retrieval/<stage>/best.jsonl
```

Look at per-query traces — aggregate metrics hide failure modes. A 70% mean Recall@10 might be 100% on lookup queries and 30% on breadth queries.

What "good" looks like:

- **Baseline (vanilla BM25):** establishes the floor. On the prior OA-only eval, baseline Recall@10 was 53.4%. Your numbers will differ with a new eval set.
- **doc-enrich:** should improve over baseline by 5-15pp if doc enrichment is working. If it goes down, the doc-enrich prompt is generating off-domain phrases (the same failure mode that motivated the v01 prompt rewrite — see the `sira` strand journal).
- **query-enrich:** should improve over doc-enrich by 2-8pp. Smaller delta because query-side has less surface area.
- **rerank:** should improve over query-enrich by 3-10pp if the rerank prompt is calibrated to the corpus.

A regression at any stage means that stage's prompt isn't tuned to the corpus. Iterate the prompt, re-run that stage only (not the full pipeline), and re-measure.

## Iterating

The eval set itself is iterative:

1. **First round** — 10-20 queries spanning the obvious subdomains. Run pipeline.
2. **Inspect per-query traces.** Are there queries where every retriever fails? Those are either (a) genuinely hard, (b) ambiguous in phrasing, (c) missing ground-truth req_ids the SME forgot. Triage each.
3. **Refresh ground truth.** After any parser improvement (especially when a new field is extracted or strike-handling changes), survey the existing `expected_req_ids` against the current parsed tree. Struck reqs that show up should move to a separate `STRUCK_REQ_IDS` list (see integration tests for the pattern); live siblings replace them. See `STATUS.md` 2026-05-02 ground-truth-refresh entry for an example.

## Common pitfalls

- **Eval set too LTE-heavy.** If 18 of 20 queries are LTE EMM, you're not measuring how the retriever does on VoWiFi / 5G / eSIM / SMS — you're measuring LTE recall with extra noise. Sample evenly by subdomain share.
- **`expected_req_ids` contains the *struck* sibling.** Parser drops it; eval fails forever. Always re-validate after parser changes.
- **Question phrasing leaks the answer.** "What is the 720-second T3402 timer?" gives away "720" — retrieval gets it via exact-token match. Strip values.
- **Single-rep ground truth.** Listing only the leaf normative req but omitting the heading anchor makes Recall@10 brittle when the synthesizer needs the heading for context. Include both.
- **Forgetting to re-run the adapter.** Edits to `questions.py` don't propagate to BEIR files until `nora_to_beir.py` runs.
