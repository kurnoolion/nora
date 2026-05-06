# Retrieval Pipeline — Design

This document is the architectural reference for NORA's online
retrieval path. It covers every layer between the raw user query
and the chunks handed to the LLM synthesizer, with the rationale
for each layer and pointers to the ADRs that pinned the design.

> **Audience.** Engineers tuning retrieval, evaluating new
> reranking / fusion techniques, or debugging why a particular
> chunk did or didn't surface in a given answer. For the higher-
> level system view see [`docs/compact/PROJECT.md`](../../../docs/compact/PROJECT.md);
> for the public API surface see [`MODULE.md`](MODULE.md).

## 1. Pipeline shape

The query pipeline (`pipeline.QueryPipeline.query`) is six stages,
each replaceable by injection. This doc focuses on Stages 3–4
(graph scoping + retrieval) since that's where every retrieval
enhancement lives. Stages 1 / 2 / 5 / 6 are stable.

```
                +---------------------------------------+
  raw query --> | 1. analyzer    -> QueryIntent         |
                | 2. resolver    -> ScopedQuery         |
                | 3. graph_scope -> CandidateSet  ──┐   |
                | 3.5. rewriter  -> retrieval_query │   |  <-- query rewriting
                | 4. retriever   -> [RetrievedChunk]│   |  <-- BM25 + dense + rerank + glossary pin
                | 5. context     -> AssembledContext│   |
                | 6. synthesizer -> QueryResponse   ◄───┘
                +---------------------------------------+
```

### What's tunable, what's contract

- **Contract**: stage signatures (typed dataclasses), `graph routes
  / RAG ranks` (D-001), every stage injectable, mock-friendly.
- **Tunable** (per-query-type policy maps in `pipeline.py`):
  `_TYPE_TOP_K`, `_TYPE_BM25_WEIGHT`, `_TYPE_REWRITE_ENABLED`,
  `_TYPE_RERANK_ENABLED`. These are empirical hyperparameters,
  not architectural commitments — values shift as the eval set
  grows or as new corpora arrive.

## 2. Graph scoping (Stage 3)

`GraphScoper` produces the `CandidateSet` that gates Stage 4.
Three lookup paths run in parallel and merge:

| Path | Trigger | Purpose |
|---|---|---|
| `_entity_lookup` | analyzer extracted `req:*` IDs that exist as graph nodes | Authoritative anchor |
| `_feature_lookup` | analyzer extracted feature names | Topical breadth |
| `_plan_lookup` | analyzer extracted plan IDs | Document-level breadth |
| `_title_search` | concepts/entities matched in node titles/text | Recall fallback |

### Entity-priority shortcut (D-039)

When `_entity_lookup` matches, the other three paths are **skipped**.
This was a real fix: a query like *"What is VZ\_REQ\_LTEDATARETRY\_200?"*
matched the entity AND triggered `_feature_lookup` on
DATA\_RETRY, which expanded the candidate scope from 1 req to
~700. Vector ranking inside a 700-req scope drowned the named
chunk.

Edge traversal still runs from the entity seed, providing the
immediate neighborhood (siblings, referenced standards, parent
container). The named-req anchor is preserved without losing
useful context.

## 3. Query rewriting (Stage 3.5)

`pre-retrieval expansion`. Off by default for `SINGLE_DOC` and
`GENERAL`; on for the other types via `_TYPE_REWRITE_ENABLED`.

- `MockQueryRewriter` (default) — returns `[]`, no-op.
- `LLMQueryRewriter` — generates 3 short paraphrases (5–15
  words, same scope) using the configured LLM. The original query
  + paraphrases are concatenated for embedding/BM25.

**Why per-type**: SINGLE\_DOC entity lookups already work via
D-039; rewriting injects telecom terminology that helps concept-
shaped queries (CROSS\_DOC, STANDARDS\_COMPARISON, FEATURE\_LEVEL)
find chunks the user's exact wording missed. The rewriter
prompt was tightened from "list corpus topics" (which polluted
every rewrite with LTE/IMS/SMS/etc.) to "5–15 words, same scope"
after the first version diluted retrieval.

## 4. Hybrid retrieval (Stage 4) — D-041

Two retrievers run in parallel, fused via Reciprocal Rank Fusion
(RRF, Cormack 2009):

### 4.1 Dense

Standard vector similarity over the Chroma store. Filtered by
`metadata.req_id $in candidate_req_ids` when the candidate set is
non-empty; otherwise filtered by MNO/release.

### 4.2 Sparse — BM25 (`bm25_index.py`)

- `BM25Index` wraps `rank_bm25.BM25Okapi` with its own
  `(id, text, metadata)` cache.
- Telecom-aware tokenizer preserves req IDs (`vz_req_lteat_45`),
  spec numbers (`24.301`), release codes (`rel-9`) as single
  tokens. Lower-cased. Stopwords retained (telecom abbreviations
  often look like stopwords — `at`, `it`, `is`).
- Built once per pipeline at construction time
  (`BM25Index.from_store(store)`); rebuild requires restart. The
  store fits in memory at present scale (~800 chunks, low MB);
  this becomes a knob if/when the corpus grows.

### 4.3 RRF fusion

`rrf_fuse(*lists, weights, k=60)`. Per-query-type BM25 weight
from `_TYPE_BM25_WEIGHT`:

```python
STANDARDS_COMPARISON = 0.5  # benefits — names specific TS / cause codes
TRACEABILITY         = 0.5  # benefits when query has specific terms
SINGLE_DOC           = 0.5  # entity-priority handles the well-formed case
CROSS_DOC            = 0.0  # parent chunks too thin to compete with BM25-favored leaves
FEATURE_LEVEL        = 0.0  # same as cross_doc
```

`weight = 0.0` short-circuits BM25 entirely (no index search runs).

**Why these values**: empirical tuning on the 18-question OA eval
set. STANDARDS\_COMPARISON gained +33pp accuracy with BM25 active.
CROSS\_DOC and FEATURE\_LEVEL regressed because parent/overview
chunks (heading-only) lose to richer leaf chunks pulled up by
BM25.

### 4.4 Per-query-type top\_k (D-040)

`_TYPE_TOP_K` widens to 25 for breadth-style queries (CROSS\_DOC,
FEATURE\_LEVEL, STANDARDS\_COMPARISON, CROSS\_MNO\_COMPARISON), 20
for TRACEABILITY / RELEASE\_DIFF, default 10 for lookups. The
expected hits in breadth categories often rank at #15+, so a
tight top\_k systematically misses them.

`QueryPipeline` takes `max(self._top_k, type_top_k)` so callers
can still raise the floor explicitly.

## 5. Cross-encoder reranker

`reranker.py`. Runs after RRF fusion, before diversity, on the
top-K\*2 pool. Default model: `cross-encoder/ms-marco-MiniLM-L6-v2`.

- `MockReranker` (default) — passthrough.
- `CrossEncoderReranker` — loads sentence-transformers
  CrossEncoder; degrades gracefully to passthrough if the model
  isn't cached or `sentence-transformers` is missing
  (`available=False`).
- Pre-call sort by descending score, stable on ties (preserves
  RRF order on equal-score chunks).
- Long chunks are truncated to `max_chunk_chars` (default 4000)
  before scoring to stay under the cross-encoder's token window.

`_TYPE_RERANK_ENABLED` enables reranking for the same query types
that benefit from wider top\_k. Off for SINGLE\_DOC (D-039 handles
it).

**Empirical caveat**: the off-the-shelf MiniLM cross-encoder is
trained on MS-MARCO web search and doesn't help (slight regression)
on technical spec text. Domain-tuned cross-encoder is the natural
next step. Code stays plumbed behind the flag.

## 6. Glossary lookup chain — D-043

The Test page surfaced a hallucinated answer for *"What is SDM?"*.
The fix was three layered changes:

### 6.1 Parser (`structural_parser._extract_definitions`)

The OA glossary table contained:

```
| ... | ... |
| SDM | Subscriber Device Management — APN management... |
| --- | --- |          ← stray divider
| UI  | User Interface |
| ... | ... |
```

Markdown extractors split this into two tables when the divider
appears mid-table; the SDM row landed in `tbl.headers`, not
`tbl.rows`. The original `_extract_definitions` only walked
`rows`, so SDM was silently dropped.

Fix: walk both `rows` AND `headers`. Filter the canonical
`Acronym | Definition` header via a token-set check — both
columns' headers must be entirely from a known canonical set
(`acronym, term, definition, abbreviation, …`) to be treated as
a real header.

VZW LTEOTADM `definitions_map`: 18 → 19 entries (SDM recovered).

### 6.2 Glossary chunks (`vectorstore.chunk_builder`)

Each entry in `definitions_map` becomes its own chunk:

- `chunk_id = "glossary:<plan_id>:<acronym-slug>"`
- `metadata.doc_type = "glossary_entry"`
- `metadata.{acronym, expansion}` populated for direct lookup.
- Text leads with `<ACRONYM>: <expansion>` so BM25 (high TF) and
  dense (concise definition) both rank it top for short acronym
  queries.

The whole acronym table also lives in the requirement chunk for
the definitions-section req. The glossary chunks are *additional*,
not replacement — short queries get high-precision answers, while
longer "show me the glossary" queries still hit the rich req
chunk.

### 6.3 Query-side pin (`rag_retriever`)

`_ACRONYM_QUERY_RE` matches:
- "What is X" / "What does X mean" / "What does X stand for"
- "Define X" / "Definition of X" / "Meaning of X"
- "Expand acronym X" / "Expand X"

`X` must be 2–15 chars, first char a letter, rest letters/digits/
dashes/underscores. Case-insensitive.

`RAGRetriever.__init__` builds `_glossary_by_acronym: dict[str,
list[RetrievedChunk]]` once by scanning `store.get_all()` for
`doc_type=glossary_entry` chunks. Empty on pre-D-043 corpora
(back-compat).

In `retrieve()`:
1. Run normal retrieval (graph scope → BM25+dense → rerank →
   diversity).
2. If `_detect_acronym_query(query)` returns `X` AND
   `X.lower() in _glossary_by_acronym`:
   - Prepend matched glossary chunks to the result.
   - Dedup by `chunk_id` (avoid double-listing if normal
     retrieval already pulled it).
   - Trim back to top\_k so downstream context budget is unchanged.

The pin runs **after** the cross-encoder so the encoder doesn't
demote a chunk we know is the answer. Pinned chunks carry
`similarity_score=1.0` by convention — they're hard-pinned, not
similarity-ranked.

### 6.4 Why three layers

Any one alone is insufficient:

- **Parser fix only** — `definitions_map` correct, but acronym
  query still loses to operational chunks. (Dense embedding
  treats "What is SDM?" similarly to any other 4-token query;
  BM25 rewards the chunk with the most "SDM" mentions, which
  isn't the glossary.)
- **Chunks only** — the glossary chunk is in the store but
  doesn't make top-K either, for the same ranking reason.
- **Pin only** — fires the regex, then can't find a chunk to
  pin to. Returns nothing useful.

The full chain ensures: (a) the acronym is *known* to the system,
(b) a *findable* chunk exists for it, and (c) acronym-shaped
queries *deterministically* surface it.

## 7. Diversity guard

`_enforce_diversity(chunks, k)` ensures at least
`diversity_min_per_plan` chunks come from each contributing plan.
This runs *after* fusion + rerank but *before* the glossary pin —
a glossary chunk is a single hard-pinned answer, not a diversity
contributor.

## 8. Acronym expansion in chunk text — D-032 / D-038

Independent of the glossary chunks (§6.2). At chunk-build time,
the first occurrence of each known acronym in *every* chunk is
expanded inline — `SDM` → `SDM (Subscriber Device Management)`.

- Per-document `definitions_map` only — `RAT` may mean different
  things in different MNO documents.
- Single-shot per chunk (subsequent occurrences keep the bare
  acronym so chunk text doesn't bloat).
- Skipped for chunks belonging to the definitions section
  itself (avoids double-anchoring).

This complements §6 by giving operational chunks (which mention
the acronym in passing) a co-occurring expansion, so a query that
phrased the question with the *expansion* still hits chunks that
only had the acronym, and vice versa.

## 9. End-to-end traversal: "What is SDM?"

Before D-043:

1. Analyzer → `SINGLE_DOC`, no entities.
2. Graph scope → falls through `_title_search`, returns ~50
   candidates that mention "SDM" anywhere.
3. Dense + BM25 → top-10 are operational chunks (ADD flow,
   OTADM, SMS).
4. Glossary chunk `req:VZ_REQ_LTEOTADM_2398` ranks #15-ish.
5. LLM has no real definition in context → invents
   "SIMOTA Device Management".

After D-043:

1. Analyzer → same.
2. Graph scope → same.
3. Dense + BM25 → same operational chunks.
4. Reranker → same.
5. **Glossary pin fires**: `_ACRONYM_QUERY_RE` matches "What is
   SDM?", `"sdm" in _glossary_by_acronym`, pin
   `glossary:LTEOTADM:SDM` to position 0.
6. LLM context contains `SDM: Subscriber Device Management — in
   this document, SDM refers to APN Management and Device
   "profiling".`
7. Answer is grounded.

## 10. Debugging retrieval quality

When retrieval feels "off" — answers look fabricated, relevant
reqs don't surface, or the not-found path fires on queries that
should have hit — work down this checklist before assuming the
algorithm is broken.

### 10.1 Most common cause: embedding-model mismatch

The vectorstore stores chunks embedded by the model named in
`<env_dir>/out/vectorstore/config.json`. The query path
re-embeds the user's query with whatever model the active
config resolves at runtime. If those two differ, every distance
is meaningless — vectors live in different spaces.

Verify with:

```bash
cat <env_dir>/out/vectorstore/config.json | grep -E "embedding_(provider|model)"
```

Compare against the model the query path will use: `config/llm.json`'s
`embedding_provider` / `embedding_model`, modulo CLI / env-var
overrides. The two must match. If they don't, either rebuild
the vectorstore against the new model, or update the query
config to match the existing vectorstore.

### 10.2 Confirm chunks carry the new metadata (D-046)

After D-046, every chunk carries `hierarchy_path` (a `list[str]`
with `plan_name` as the document root). Old vectorstores built
before D-046 won't have it; the context builder falls back to
the graph node, but retrieval-side grouping (Step 3 onwards)
won't see the document distinction in the embedding.

```bash
cd <repo_root> && python -c "
from core.src.vectorstore.store_chroma import ChromaDBStore
s = ChromaDBStore(persist_directory='<env_dir>/out/vectorstore')
r = s.get_all()
print(f'Total docs: {len(r.ids)}')
total = len(r.metadatas)
with_path = sum(1 for m in r.metadatas if m.get('hierarchy_path'))
print(f'With hierarchy_path: {with_path}/{total}')
for cid, m in list(zip(r.ids, r.metadatas))[:3]:
    print(f'  {cid} -> {m.get(\"hierarchy_path\")}')
"
```

Expect: `with_path == total`, and the first element of each
path should be the document root (e.g. `LTEDATARETRY`,
`LTE_OTA_Device_Management`). Empty paths or `None` mean the
vectorstore predates D-046 — rebuild required.

### 10.3 Threshold sweep — diagnose the relevance distribution

After D-047, the pipeline filters chunks by cosine distance. If
the threshold is wrong for the active model + corpus, either
relevant chunks get falsely dropped (no answers) or off-topic
chunks slip through (hallucinations). Save as
`/tmp/threshold_sweep.py` and run:

```python
"""Probe the relevance threshold against the live vectorstore."""
import sys
sys.path.insert(0, "<repo_root>")

from pathlib import Path

from core.src.query.pipeline import (
    QueryPipeline, load_graph, build_stub_graph_from_store, _NOT_FOUND_ANSWER,
)
from core.src.vectorstore import make_embedder
from core.src.vectorstore.config import VectorStoreConfig
from core.src.vectorstore.store_chroma import ChromaDBStore

VS_DIR = "<env_dir>/out/vectorstore"
GRAPH_PATH = Path("<env_dir>/out/graph/knowledge_graph.json")

cfg = VectorStoreConfig.load_json(Path(VS_DIR) / "config.json")
embedder = make_embedder(cfg)
store = ChromaDBStore(
    persist_directory=cfg.persist_directory,
    collection_name=cfg.collection_name,
    distance_metric=cfg.distance_metric,
)

if GRAPH_PATH.exists():
    graph = load_graph(GRAPH_PATH)
    bypass = False
else:
    graph = build_stub_graph_from_store(store)
    bypass = True

def run(query, threshold):
    p = QueryPipeline(
        graph, embedder, store, top_k=5,
        max_distance_threshold=threshold, enable_bm25=False,
    )
    if bypass:
        p._bypass_graph = True
    resp = p.query(query)
    not_found = resp.answer == _NOT_FOUND_ANSWER
    scores = [round(c.similarity_score, 4) for c in resp.retrieved_chunks]
    return resp.retrieved_count, not_found, scores

# Replace these with queries appropriate to your corpus —
# pick 2 in-domain queries you know should hit, and 2
# clearly out-of-domain queries you know should miss.
queries = [
    ("RELEVANT-1  : <in-domain query 1>", "<in-domain query 1>"),
    ("RELEVANT-2  : <in-domain query 2>", "<in-domain query 2>"),
    ("OFF-TOPIC-1 : Treaty of Westphalia", "Treaty of Westphalia"),
    ("OFF-TOPIC-2 : Recipe for chocolate cake", "Recipe for chocolate cake"),
]

print(f"Embedding: {cfg.embedding_provider} / {cfg.embedding_model}")
print(f"Distance:  {cfg.distance_metric}\n")

print("=== Baseline raw scores (threshold disabled) ===\n")
for label, q in queries:
    n, nf, scores = run(q, threshold=None)
    print(f"{label}\n  scores: {scores}\n")

print("=== Threshold sweep ===")
print(f"{'threshold':<12} {'query':<40} {'kept':>5} {'not_found':>10}")
for t in [0.8, 0.6, 0.5, 0.4, 0.3, 0.2]:
    for label, q in queries:
        n, nf, _ = run(q, threshold=t)
        print(f"{t:<12} {label:<40} {n:>5} {str(nf):>10}")
    print()
```

Run with: `python /tmp/threshold_sweep.py`

Read the baseline scores first:

- **Relevant queries should cluster low** (well under the
  threshold). On qwen3-embedding:4b-q8_0 + the OA corpus, in-
  domain top-5 distances were 0.20–0.41.
- **Off-topic queries should cluster high.** Same calibration:
  off-topic distances were 0.74–0.77.
- **The gap matters more than the absolute values.** A 0.30+
  gap between worst-relevant and best-off-topic means the
  threshold can sit comfortably in the middle. A narrow gap
  (< 0.15) means the embedding isn't separating in-domain from
  out-of-domain content well — reranker, BM25 weight, or
  embedding-model choice need attention before the threshold
  can do its job.

The default `max_distance_threshold = 0.5` is calibrated to
qwen3-embedding:4b-q8_0 on the OA corpus. Different models
will need different defaults. Override at runtime via:

```bash
NORA_MAX_DISTANCE_THRESHOLD=0.6 python -m core.src.web.app ...   # stricter / looser
NORA_MAX_DISTANCE_THRESHOLD=off python -m core.src.web.app ...   # disable
```

### 10.4 Run the full pipeline with explicit provider / model flags

When the underlying state (vectorstore, graph, taxonomy) is
suspect, rebuild end-to-end. The pipeline runner accepts CLI
flags for every provider knob; resolution priority is **CLI >
env var > config/llm.json > defaults**.

**Caveat — one-way flags.** `--skip-taxonomy` / `--skip-graph` /
`--rag-only` are `store_true`. They can force-skip a stage but
cannot un-skip one set in `config/llm.json`. To run the full
pipeline when `config/llm.json` has `skip_taxonomy: true` or
`skip_graph: true`, edit the file and flip those to `false`
first (or remove them). The CLI will not override an already-
true config value.

Full-pipeline command:

```bash
cd <repo_root> && python -m core.src.pipeline.run_cli \
  --env-dir <env_dir> \
  --start extract --end eval \
  --embedding-provider <ollama | sentence-transformers | huggingface> \
  --embedding-model <model-name> \
  --llm-provider <ollama | openai-compatible | mock> \
  --model <llm-model-name> \
  --model-timeout 600 \
  --standards-source <huggingface | 3gpp> \
  --verbose
```

#### Flag reference

| Flag | Purpose | Valid values |
|---|---|---|
| `--env-dir` | Per-env runtime dir (input/, out/, state/, ...) | absolute path |
| `--start` / `--end` | Stage range | `extract`, `profile`, `parse`, `resolve`, `taxonomy`, `standards`, `graph`, `vectorstore`, `eval` |
| `--embedding-provider` | Embedding backend | `sentence-transformers` / `huggingface` (alias) / `ollama` |
| `--embedding-model` | Embedding model | provider-specific (e.g. `all-MiniLM-L6-v2`, `qwen3-embedding:4b`, `qwen3-embedding:4b-q8_0`, `nomic-embed-text`) |
| `--llm-provider` | LLM backend | `ollama` / `openai-compatible` / `mock` |
| `--model` | LLM model name | e.g. `gemma3:12b`, `gemma4:e4b`, or for openai-compatible: `qwen/qwen3-235b-a22b`, etc. |
| `--model-timeout` | LLM request timeout (sec) | typically 300–600 for local Ollama on CPU |
| `--standards-source` | 3GPP spec source | `huggingface` (DOCX-only, no auth) / `3gpp` (FTP fallback) |
| `--verbose` | Per-stage logging | flag |
| `--continue-on-error` | Don't abort the pipeline on a single-stage failure | flag |
| `--skip-taxonomy` / `--skip-graph` / `--rag-only` | Force-skip stages (one-way; see caveat above) | flag |

#### Suggested provider combos

**A. Full local (no API key needed; slowest):**
```bash
--embedding-provider ollama --embedding-model qwen3-embedding:4b-q8_0 \
--llm-provider ollama --model gemma3:12b --model-timeout 600
```

**B. Local embeddings + cloud LLM (best taxonomy quality):**
```bash
--embedding-provider ollama --embedding-model qwen3-embedding:4b-q8_0 \
--llm-provider openai-compatible --model qwen/qwen3-235b-a22b --model-timeout 600
# also: NORA_LLM_BASE_URL=https://openrouter.ai/api/v1 NORA_LLM_API_KEY=sk-...
```

**C. Fast local (smaller embedding + smaller LLM):**
```bash
--embedding-provider sentence-transformers --embedding-model all-MiniLM-L6-v2 \
--llm-provider ollama --model gemma4:e4b --model-timeout 300
```

### 10.5 Targeted vectorstore rebuild only

If only the vectorstore is suspect (taxonomy / graph already
look fine), rebuild just that stage:

```bash
cd <repo_root> && python -m core.src.vectorstore.vectorstore_cli \
  --trees-dir <env_dir>/out/parse \
  --persist-dir <env_dir>/out/vectorstore \
  --provider <ollama | sentence-transformers> \
  --model <embedding-model-name> \
  --rebuild
```

Notes:
- The trees source is `<env_dir>/out/parse/` (parsed `*_tree.json`
  files), **not** `<env_dir>/out/resolve/` (cross-reference
  outputs only).
- `--taxonomy` is optional; omit it for RAG-only setups where
  the taxonomy stage has been skipped.
- The CLI saves the resolved config to
  `<env_dir>/out/vectorstore/config.json`. Downstream query
  paths read that file to construct the embedder, so rebuilding
  propagates the model choice automatically.

### 10.6 Truncation warnings during rebuild

```
WARNING Text N length 9415 > max_input_chars 8000; truncating
        (1415 chars dropped)
```

Intentional safety cap. Ollama embedding models reject very
long inputs (qwen3-embedding fails above ~16K chars; the 8K
default is conservative). The first 8K of a chunk carries
header lines + title + opening text — the load-bearing
content for retrieval. Trailing tables / image surrounding
text get truncated, a small bounded loss.

To raise the cap if you know your model handles more, set
`extra={"ollama_max_input_chars": 12000}` on `VectorStoreConfig`.

### 10.7 Common failure modes

| Symptom | Likely cause | Diagnostic |
|---|---|---|
| Every query returns "not found" | Embedding model mismatch (built with X, queried with Y) | §10.1 — compare `<env_dir>/out/vectorstore/config.json` against the active config |
| Relevant queries return "not found"; off-topic returns answers | Threshold too strict for current model | §10.3 — sweep; raise threshold or disable |
| Off-topic queries return synthesized answers | Threshold too loose, or filter disabled | §10.3 — confirm threshold value in pipeline-build log |
| Vectorstore rebuild shows `Loaded 0 parsed trees` | Wrong `--trees-dir` (pointed at `out/resolve` instead of `out/parse`) | §10.5 |
| `Loaded 0 parsed trees` and trees-dir is correct | Parse stage hasn't run; rerun the pipeline up to parse | `--start extract --end parse` |
| Acronym queries get hallucinated answers | Glossary chunks missing from vectorstore (rebuilt without `definitions_map` populated) | Spot-check with §10.2; ensure `<doc_type>=glossary_entry` chunks exist |
| Hierarchy paths show single-element `[doc_root]` only | Requirements have no hierarchy populated by parser | Open a parsed tree JSON, inspect `requirements[*].hierarchy_path` |

## 11. ADR cross-reference

- D-001 — Graph routes, RAG ranks (foundational).
- D-032 — Per-document `definitions_map` + chunk-build inline
  expansion.
- D-038 — Table-anchored definitions extraction (extends D-032
  for table-format glossaries).
- D-039 — Entity-priority graph scoping.
- D-040 — Type-aware top\_k + cross-doc list-style detection.
- D-041 — BM25 hybrid retrieval (sparse + dense, RRF, per-type
  weights).
- D-042 — Parent-chunk subsection augmentation (opt-in, default
  off — kept available for corpora with rich-bodied parents).
- D-043 — Acronym lookup chain (parser fix + glossary chunks +
  query-side pin). Adds glossary-aware retrieval as a
  deterministic answer surface for short definitional queries.
- D-046 — Document-rooted hierarchy paths in chunk text +
  metadata. Embedding captures Document > Section > Subsection.
- D-047 — Relevance threshold + "not found" response (Stage 4.5).
  Off-topic queries return a deterministic message instead of
  LLM hallucination from weak fragments.
