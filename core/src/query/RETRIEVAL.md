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

## 10. ADR cross-reference

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
