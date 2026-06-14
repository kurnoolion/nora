# query

**Purpose**
Online query pipeline (TDD §7). A 6-stage chain that turns a natural-language question into a grounded, citation-bearing answer: `Analysis → MNO/Release Resolution → Graph Scoping → Targeted RAG → Context Assembly → LLM Synthesis`. Serves FR-9, FR-10, FR-11, FR-12, FR-13, FR-14 (one FR per stage). Implements D-001: the **graph routes, RAG ranks** — retrieval never runs unscoped, and the graph decides which subset of the corpus is even eligible.

**Public surface**
- Entry point: `QueryPipeline(graph, embedder, store, analyzer=None, synthesizer=None, rewriter=None, reranker=None, top_k=10, max_depth=None, max_context_chars=30000, enable_bm25=True, max_distance_threshold=None, enable_grouping=False, top_k_cap=None)` (pipeline.py) — `query(query_text, verbose=False, pinned_chunk_ids=None) -> QueryResponse`. Constructor knobs added since the original 6-stage shape: `max_distance_threshold` [D-047], `enable_grouping` [D-049], `top_k_cap` (hard ceiling applied AFTER per-type widening). The `pinned_chunk_ids` parameter on `query()` skips Stages 2–4.7 and synthesizes only from the named chunks — used by the disambiguation user-pick UX (D-049 Step 3c).
- Stages (each replaceable by injection):
  - `LLMQueryAnalyzer`, `MockQueryAnalyzer` (analyzer.py) — Stage 1. `make_query_analyzer(llm_provider=None)` selects `LLMQueryAnalyzer` when a provider is supplied, else `MockQueryAnalyzer` — NORA's standard configured-LLM-or-mock posture for query analysis (multi-mno-sira D-DRAFT-9). `MockQueryAnalyzer._extract_releases` captures all named releases (multi-release release-diff support), not just the first.
  - `MNOReleaseResolver` (resolver.py) — Stage 2
  - `GraphScoper` (graph_scope.py) — Stage 3
  - `QueryRewriter` Protocol (rewriter.py) — `rewrite(query) -> list[str]`. Implementations: `LLMQueryRewriter`, `MockQueryRewriter`. Stage 3.5 (optional pre-retrieval expansion; per-type gate via `_TYPE_REWRITE_ENABLED`). `expand_query(original, rewrites) -> str` concatenates the original + paraphrases for the embedder/BM25.
  - `RAGRetriever` (rag_retriever.py) — Stage 4 — accepts optional `bm25_index` and `reranker` constructor params; per-call `bm25_weight: float | None` for hybrid retrieval [D-041]; in-memory glossary index built at init for D-043 acronym pin
  - `Reranker` Protocol (reranker.py) — `rerank(query, chunks) -> list[RetrievedChunk]`. Implementations: `CrossEncoderReranker` (sentence-transformers `CrossEncoder`, degrades to passthrough when model not cached), `MockReranker` (passthrough; default).
  - Stage 4.5 — relevance threshold filter [D-047]; constants `_NOT_FOUND_ANSWER`, `_TYPE_MAX_DISTANCE` in pipeline.py.
  - Stage 4.7 — hierarchy grouping + disambiguation short-circuit [D-049]; constants `_DISAMBIGUATION_ANSWER`, `_TYPE_DISABLE_GROUPING` in pipeline.py.
  - `ContextBuilder` (context_builder.py) — Stage 5
  - `LLMSynthesizer`, `MockSynthesizer` (synthesizer.py) — Stage 6
  - Stage 6.5 — citation audit [D-052]; populates `QueryResponse.citation_audit`.
- Sparse retrieval (bm25_index.py) [D-041]:
  - `BM25Index` — telecom-aware tokenized chunk index with `from_store(store)` factory + `search(query, top_k, filter_ids, filter_metadata)` returning `[(chunk_id, score)]`
  - `tokenize(text) -> list[str]` — preserves req-ids / spec numbers / release codes as single tokens
  - `rrf_fuse(*ranked_lists, weights, k=60, top_k)` — Reciprocal Rank Fusion across ranked id lists
- Hierarchy grouping (grouping.py) [D-049]:
  - `group_chunks_by_hierarchy(chunks) -> list[ChunkGroup]` — greedy-LCP cluster on `hierarchy_path` metadata
  - `gap_between_top_groups(groups) -> float` — Stage 4.7 auto-commit decision rule
- Citation audit (citation_audit.py) [D-052]: `audit_answer_citations(answer, available_req_ids) -> CitationAudit` — per-sentence regex scan flagging uncited factual claims and fabricated citations (req IDs in the answer not in the retrieved context).
- Schema (schema.py):
  - Enums: `QueryType` (single_doc, cross_doc, cross_mno_comparison, release_diff, standards_comparison, traceability, feature_level, summarize, fact, general — last two added per D-051), `DocTypeScope`
  - Per-stage dataclasses: `QueryIntent`, `MNOScope`, `ScopedQuery`, `CandidateNode`, `CandidateSet`, `RetrievedChunk`, `StandardsContext`, `ChunkContext`, `AssembledContext`, `Citation`, `QueryResponse` (gained `disambiguation_required`, `groups`, `assembled_context`, `citation_audit` fields), `ChunkGroup` (Stage 4.7 output [D-049]), `SentenceAudit` + `CitationAudit` (Stage 6.5 output [D-052]).
- Graph helpers (pipeline.py):
  - `load_graph(path) -> nx.DiGraph` — loads the full knowledge graph from JSON.
  - `build_stub_graph_from_store(store) -> nx.DiGraph` — derives a minimal MNO/Release/Plan-only graph from vectorstore metadata. Used by web/query and eval when the graph stage was skipped (RAG-only mode); pair with `pipeline._bypass_graph = True` so Stage 3 emits an empty CandidateSet.
- CLI: `query_cli.main`; `retrieval_debug.main` (`--compare-envs --env-dir <path>` — 4-section machine / model / vectorstore / retrieval fingerprint for cross-machine retrieval-quality diff).

**Invariants**
- **Graph-first, then RAG.** Vector retrieval is always filtered to the `requirement_ids` produced by `GraphScoper`. Unscoped retrieval is a D-001 violation, not a shortcut. *Exception*: RAG-only mode (`pipeline._bypass_graph = True`) skips Stage 3 entirely; Stage 4 falls back to MNO/release metadata filtering. Used when the graph stage was deliberately skipped (`--rag-only` / `--skip-graph` / `config/llm.json:skip_graph=true`); the stub graph from `build_stub_graph_from_store` keeps the resolver constructible without forcing a full graph build.
- The 6 numbered stages plus 3 decimal sub-stages (4.5 / 4.7 / 6.5) pass typed dataclasses — each stage's output is the next stage's only input. No stage reaches back for state.
- Every stage is injectable — `QueryPipeline(analyzer=MyAnalyzer())` swaps Stage 1 without touching the rest. Mocks (`MockQueryAnalyzer`, `MockSynthesizer`, `MockReranker`, `MockQueryRewriter`) exist so the pipeline runs without any LLM for offline debugging.
- `QueryResponse.citations` reference **specific** `(req_id, plan_id, section_number)` tuples (plus optional standards spec/section). Answers without citations are a bug in the synthesizer, not the default.
- `max_context_chars` caps Stage 5 output — truncation is deterministic (preserves top-scored chunks first), never silent.
- Graph and vector store are **inputs** to the pipeline, not owned by it — built offline by [graph](../graph/MODULE.md) and [vectorstore](../vectorstore/MODULE.md), loaded once at startup, reused per query.
- **Stage 4.5 not-found short-circuit** [D-047]: when `max_distance_threshold` filters every retrieved chunk, the pipeline returns `_NOT_FOUND_ANSWER` (deterministic message) and skips Stages 5/6. Synthesizing from weak chunks is the primary hallucination class; this prevents it. Per-type override map `_TYPE_MAX_DISTANCE` (FACT=0.4, SUMMARIZE=0.7) overrides the constructor-level default per `intent.query_type`.
- **Stage 4.7 disambiguation short-circuit** [D-049]: when `enable_grouping=True` and the gap between top-two groups is below the resolved `gap_threshold`, the pipeline returns `QueryResponse(disambiguation_required=True, groups=[…])` and skips Stages 5/6. `_TYPE_DISABLE_GROUPING` opts a query type out (SUMMARIZE merges all groups by design). The user picks a group via the web UI; pinned chunks come back through `pinned_chunk_ids` on a re-issued `query()`. Off by default; opt in via `enable_grouping=True` or `config/retrieval.json`.
- **Stage 6.5 citation audit always runs post-synthesis** [D-052]: every synthesis path (normal + pinned-chunks) populates `QueryResponse.citation_audit` with per-sentence breakdown — `cited` / `uncited` / `fabricated` (req IDs in the answer not in retrieved context). Cheap (regex, no LLM call). No-op on disambiguation / not-found paths (no real answer to audit).

**Key choices**
- Six stages instead of a single monolithic retriever so each can be tested and swapped independently — the mock analyzer/synthesizer is what makes the pipeline testable on a work laptop without LLM access.
- `QueryType` carved into ten concrete kinds (release_diff, traceability, fact, summarize, etc.) because each needs different graph scoping and different prompting. A generic pipeline that treats every query the same loses signal. The original eight values are scope-shape (how many docs the answer spans); FACT and SUMMARIZE were added per D-051 as intent-shape (precision-vs-breadth) — see [`RETRIEVAL.md`](RETRIEVAL.md) §12.
- `CandidateSet` keeps `requirement_nodes`, `standards_nodes`, `feature_nodes` separate — retrieval filters on req IDs, context assembly attaches standards text by node, and future reranking can use feature nodes without re-traversing.
- Prompting is few-shot + explicit grounding instructions; `LLMSynthesizer` includes a context fallback path for cases where the LLM skips citations (fix kept because dropping it caused regression in internal tests).
- Pipeline defaults (`top_k=10`, `max_context_chars=30000`) live on the class, not in env config — most callers accept defaults; eval overrides. **Per-query-type override** [D-040]: `QueryPipeline.query` picks `top_k` from a `_TYPE_TOP_K` map keyed by `intent.query_type` — list/breadth queries (CROSS_DOC / FEATURE_LEVEL / STANDARDS_COMPARISON / CROSS_MNO_COMPARISON) widen to 25 because their expected hits include parent/overview reqs whose chunks are short (heading + path only) and rank below richer leaf chunks; TRACEABILITY / RELEASE_DIFF widen to 20; lookups stay at 10. Pipeline takes `max(self._top_k, type_top_k)` so callers can still raise the floor explicitly.
- **Specific-entity queries are authoritative for graph scope** [D-039]: when `GraphScoper._entity_lookup` matches (the analyzer extracted req IDs that exist as `req:*` nodes), expansion via `_feature_lookup` / `_plan_lookup` / `_title_search` is skipped. Edge traversal from the entity seeds still runs and provides the immediate neighborhood (sibling sections, referenced standards, parent containers) — the named-req anchor isn't diluted into a feature-wide scope where vector ranking can no longer surface the specific chunk.
- Cross-doc / list-style queries are detected by phrase triggers in `_classify_query_type` [D-040]: `across all`, `across the`, `in all`, `across vzw|mnos|plans|specs`, `all the requirements`, `what are all`, `what requirements` map to `QueryType.CROSS_DOC`. FEATURE_LEVEL still wins on more-specific phrasing (`everything about`, `related to`) — the analyzer checks FEATURE_LEVEL first to preserve the existing classification contract. `mention` / `mentions` / `mentioned` (with trailing space) route to TRACEABILITY before the cross-doc check so concept-lookup queries get BM25 weight rather than the disabled cross-doc weight.
- **BM25 hybrid retrieval** [D-041]: `RAGRetriever` parallels dense retrieval with a sparse `BM25Index` and fuses via Reciprocal Rank Fusion (Cormack 2009, k=60, weighted). Per-query-type BM25 weight from `pipeline._TYPE_BM25_WEIGHT`: STANDARDS_COMPARISON / TRACEABILITY / SINGLE_DOC = 0.5; CROSS_DOC / FEATURE_LEVEL = 0.0 (parent chunks too thin to compete with BM25-favored leaves). BM25 filter uses `metadata.req_id` (NOT `chunk_id`, which is `req:<req_id>`) so the candidate gate matches the dense path's `where` filter. `_TYPE_BM25_WEIGHT` is empirical hyperparameter tuning, not architectural contract — values shift as the eval set grows.
- **Acronym lookup chain** [D-043]: definitional queries ("What is X?", "Define X", "Meaning of X", …) hard-pin the matching glossary chunk to top of retrieval. `RAGRetriever.__init__` builds `_glossary_by_acronym` once by scanning `store.get_all()` for `doc_type=glossary_entry` chunks (back-compat: empty on pre-D-043 corpora). `retrieve()` runs normal retrieval (graph scope → BM25+dense → rerank → diversity) FIRST, then if `_ACRONYM_QUERY_RE` matches AND the acronym is in the index, prepends matched glossary chunks with chunk_id dedup, trimmed back to top_k. Pin runs *after* the cross-encoder so it doesn't demote a chunk we know is the answer. See [`RETRIEVAL.md`](RETRIEVAL.md) for the full end-to-end retrieval architecture.
- **Cross-encoder reranker**: `CrossEncoderReranker` runs after RRF fusion, before diversity; `MockReranker` (passthrough) is the default. Per-type gate via `_TYPE_RERANK_ENABLED`. Long chunks truncated to `max_chunk_chars` (default 4000) before scoring. Off-the-shelf MS-MARCO MiniLM doesn't help on technical spec text; code stays plumbed for domain-tuned models.
- **Query rewriting**: `LLMQueryRewriter` (optional Stage 3.5) generates 3 short paraphrases (5-15 words, same scope) concatenated to the original for embedding + BM25. Off for SINGLE_DOC and GENERAL via `_TYPE_REWRITE_ENABLED` (D-039 entity priority handles single-doc lookups; rewriting just adds noise). On for CROSS_DOC, STANDARDS_COMPARISON, FEATURE_LEVEL, etc. — concept-shaped queries gain recall from telecom-specific terminology the user didn't include. SUMMARIZE is also on (D-051); FACT is intentionally off (paraphrasing a fact-shaped query risks routing to the definitional path).
- **Document-rooted hierarchy paths in chunk metadata** [D-046]: `context_builder._enrich_chunk` prefers `chunk.metadata["hierarchy_path"]` (full Document > Section > Subsection chain, with `plan_name` as root) over the graph node path. Old vectorstores without the metadata fall back to the graph automatically; new ones surface the document-level distinction in the embedding (which is what makes Stage 4.7 grouping meaningful). See [`RETRIEVAL.md`](RETRIEVAL.md) §1, §11.
- **Stage 4.5 relevance threshold filter** [D-047]: cosine-distance cap applied after retrieval; chunks above threshold dropped. `_TYPE_MAX_DISTANCE` per-type override (FACT=0.4 strict; SUMMARIZE=0.7 lenient). Default 0.5 calibrated empirically on env_vzw + qwen3-embedding:4b-q8_0 — re-sweep when the embedding model changes. See [`RETRIEVAL.md`](RETRIEVAL.md) §10.
- **Stage 4.7 hierarchy grouping + disambiguation** [D-049]: greedy-LCP clustering on `hierarchy_path` metadata; auto-commit when groups separate cleanly (gap ≥ `gap_threshold`); surface disambiguation otherwise. "System shall not pretend it is an Oracle." `enable_grouping` master toggle, `gap_threshold` tuning knob, `_TYPE_DISABLE_GROUPING` per-intent opt-out (SUMMARIZE bypass). Pinned-chunks path on `query()` for the user-pick UX. Group score = `min(c.similarity_score)` so the best chunk anchors group relevance. See [`RETRIEVAL.md`](RETRIEVAL.md) §11.
- **FACT and SUMMARIZE intent classification** [D-051]: phrasing-driven query types layered on top of the existing scope-shape types. FACT (strict precision: tight `top_k=10`, `bm25_weight=0.5`, rerank on, `max_distance_threshold=0.4`, terse contradiction-aware prompt). SUMMARIZE (broad survey: `top_k=50`, `bm25_weight=0.2`, rerank off, `max_distance_threshold=0.7`, grouping disabled, TL;DR + per-section breakdown prompt). Triggers checked before existing scope-based heuristics; FACT before SUMMARIZE so "Explain the value of T3402" routes to FACT. Comparison intent deferred (needs multi-MNO/release data). See [`RETRIEVAL.md`](RETRIEVAL.md) §12.
- **Stage 6.5 citation audit** [D-052]: post-synthesis per-sentence regex scan; cheap (no LLM call). `CitationAudit` carries per-sentence breakdown plus `cited_percent` and `fabricated_count` summaries. Fabricated detection compares answer's `(VZ_REQ_X)` tokens against `available_req_ids` (chunks the LLM actually received) — flags hallucinated req IDs that look authoritative but aren't real. Phase 5c repair (re-prompt LLM to add citations to flagged sentences) deferred. See [`RETRIEVAL.md`](RETRIEVAL.md) §13.
- **Configuration via unified resolver chain** [D-050, D-053]: `enable_grouping`, `gap_threshold`, `bm25_weight_by_type`, `max_distance_threshold`, `top_k_cap` resolve through `CLI > env var > Config-page DB > config/*.json > defaults`. Per-type maps in `pipeline.py` (`_TYPE_TOP_K`, `_TYPE_BM25_WEIGHT`, `_TYPE_RERANK_ENABLED`, `_TYPE_REWRITE_ENABLED`, `_TYPE_MAX_DISTANCE`, `_TYPE_DISABLE_GROUPING`) are the built-in defaults the resolver consults last; users edit through `/config` page (D-053) or by editing `config/retrieval.json`. The `[Query knobs]` log line at every query prints the resolved knobs for verification. See [`RETRIEVAL.md`](RETRIEVAL.md) §14.

**Non-goals**
- Not a compliance checker. "Is device X compliant with plan Y?" is a separate workflow that uses this pipeline as a primitive; don't collapse the two.
- No multi-turn conversation state. Each `query()` call is independent; chat-like flows are assembled by the caller (web UI).
- No write path — query never mutates the graph or vector store.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`analyzer.py`
- `LLMQueryAnalyzer` — class — pub — LLM-driven query analyzer.
  - `__init__` — constructor — internal
  - `analyze` — method — pub — Analyze a query using LLM for extraction.
- `MockQueryAnalyzer` — class — pub — Keyword-based query analyzer (no LLM required).
  - `_classify_doc_scope` — method — internal — Determine which document types to include.
  - `_classify_query_type` — method — internal — Classify the query type based on extracted signals.
  - `_extract_concepts` — method — internal — Extract telecom concepts.
  - `_extract_entities` — method — internal — Extract named entities (req IDs, timer names, etc.).
  - `_extract_features` — method — internal — Match query against feature keywords.
  - `_extract_mnos` — method — internal — Extract MNO references.
  - `_extract_plan_ids` — method — internal — Match query against known plan aliases.
  - `_extract_releases` — method — internal — Extract release references.
  - `_extract_standards` — method — internal — Extract 3GPP spec references.
  - `analyze` — method — pub — Analyze a natural language query into structured intent.
- `_FEATURE_KEYWORDS` — constant — internal
- `_MNO_ALIASES` — constant — internal
- `_PLAN_ALIASES` — constant — internal
- `_RELEASE_PATTERNS` — constant — internal
- `_REQ_ID_PATTERN` — constant — internal
- `_SPEC_PATTERN` — constant — internal
- `logger` — constant — pub

`bm25_index.py`
- `BM25Index` — class — pub — Wraps `rank_bm25.BM25Okapi` with the chunk corpus + telecom.
  - `__init__` — constructor — internal
  - `chunk_metadata` — method — pub
  - `chunk_text` — method — pub — Retrieve a chunk's text by id. Used by hybrid retrieval to.
  - `from_store` — classmethod — pub — Build a BM25Index from a `VectorStoreProvider.get_all()`.
  - `search` — method — pub — Return top-k `(chunk_id, bm25_score)` tuples in descending.
  - `size` — property — pub
- `_MIN_TOKEN_LEN` — constant — internal
- `_TOKEN_RE` — constant — internal
- `logger` — constant — pub
- `rrf_fuse` — function — pub — Reciprocal Rank Fusion across multiple ranked lists of chunk ids.
- `tokenize` — function — pub — Telecom-aware tokenizer used for BM25 indexing and query parsing.

`citation_audit.py`
- `_ABBREVIATIONS` — constant — internal
- `_REQ_ID_RE` — constant — internal
- `_SPEC_RE` — constant — internal
- `_is_label_only` — function — internal — Detect lines that are just a label like 'Direct answer:' —.
- `_is_markdown_header` — function — internal — Treat markdown headers (#, ##, **bold-only line**) as meta.
- `_split_on_punct` — function — internal — Sentence-end punct splitter that respects abbreviations.
- `_split_sentences` — function — internal — Split answer text into sentence-shaped fragments.
- `_strip_bullet_prefix` — function — internal — Return the line minus a leading bullet/numbered marker, if any.
- `audit_answer_citations` — function — pub — Walk an answer sentence-by-sentence; tag citations and meta.

`context_builder.py`
- `ContextBuilder` — class — pub — Assembles LLM prompt context from retrieved chunks and graph data.
  - `__init__` — constructor — internal
  - `_enrich_chunk` — method — internal — Enrich a chunk with graph context (hierarchy, standards, etc.).
  - `_format_context` — method — internal — Format enriched chunks into a context string for the LLM.
  - `_get_parent_text` — method — internal — Get the parent requirement's text for context.
  - `_get_related_ids` — method — internal — Get IDs of related requirement nodes (via depends_on).
  - `_get_standards_context` — method — internal — Get standards sections referenced by this requirement.
  - `_strip_chunk_headers` — staticmethod — internal — Strip the contextualization headers from chunk text.
  - `build` — method — pub — Build assembled context for LLM synthesis.
- `_CITATION_RULES` — constant — internal
- `_FEW_SHOT_EXAMPLE` — constant — internal
- `_SYSTEM_PROMPTS` — constant — internal
- `logger` — constant — pub

`graph_scope.py`
- `GraphScoper` — class — pub — Scopes candidate nodes using knowledge graph traversal.
  - `__init__` — constructor — internal
  - `_entity_lookup` — method — internal — Look up nodes by entity names (req IDs, etc.).
  - `_feature_lookup` — method — internal — Look up requirements via feature nodes.
  - `_in_scope` — staticmethod — internal — Check if a node is within the resolved MNO/release scope.
  - `_plan_lookup` — method — internal — Get all requirements belonging to specified plans.
  - `_title_search` — method — internal — Search node titles/text for concepts and entities.
  - `_traverse` — method — internal — Traverse from seed nodes along allowed edge types.
  - `scope` — method — pub — Find candidate nodes for the query.
- `_DEFAULT_DEPTH` — constant — internal
- `_TRAVERSAL_EDGES` — constant — internal
- `logger` — constant — pub

`grouping.py`
- `_DEFAULT_REPRESENTATIVE_TITLES` — constant — internal
- `_finalize_group` — function — internal — Build a ChunkGroup from accumulated chunks + their LCP.
- `_lcp` — function — internal — Longest common prefix of two path sequences.
- `_path_of` — function — internal — Read the chunk's hierarchy_path as a tuple. Returns () for.
- `gap_between_top_groups` — function — pub — Distance between the top two groups' scores.
- `group_chunks_by_hierarchy` — function — pub — Cluster chunks by longest-common hierarchy-path prefix.

`pipeline.py`
- `QueryPipeline` — class — pub — End-to-end query pipeline.
  - `__init__` — constructor — internal — Initialize the pipeline.
  - `_fetch_chunks_by_ids` — method — internal — Fetch chunks from the store by their chunk IDs.
  - `query` — method — pub — Run the full query pipeline.
- `_DISAMBIGUATION_ANSWER` — constant — internal
- `_NOT_FOUND_ANSWER` — constant — internal
- `_TYPE_BM25_WEIGHT` — constant — internal
- `_TYPE_DISABLE_GROUPING` — constant — internal
- `_TYPE_MAX_DISTANCE` — constant — internal
- `_TYPE_RERANK_ENABLED` — constant — internal
- `_TYPE_REWRITE_ENABLED` — constant — internal
- `_TYPE_TOP_K` — constant — internal
- `build_stub_graph_from_store` — function — pub — Construct a minimal MNO/Release/Plan-only graph from the.
- `load_graph` — function — pub — Load a knowledge graph from JSON.
- `logger` — constant — pub

`query_cli.py`
- `_create_pipeline` — function — internal — Create the query pipeline with all components.
- `_display_response` — function — internal — Display a query response.
- `cmd_interactive` — function — pub — Run interactive query mode.
- `cmd_query` — function — pub — Run a single query.
- `logger` — constant — pub
- `main` — function — pub

`rag_retriever.py`
- `RAGRetriever` — class — pub — Retrieves and ranks requirement chunks by similarity.
  - `__init__` — constructor — internal
  - `_build_glossary_index` — staticmethod — internal — Scan the store once for glossary chunks; index by lowercase.
  - `_detect_acronym_query` — method — internal — Return the acronym X when `query` matches an acronym.
  - `_enforce_diversity` — method — internal — Ensure at least N chunks from each contributing plan.
  - `_fuse` — method — internal — RRF-fuse dense and BM25 rankings; materialize chunks for.
  - `_metadata_retrieve` — method — internal — Retrieve with MNO/release metadata filters.
  - `_retrieve_metadata` — method — internal — Metadata-filtered retrieval — used when graph scoping is empty.
  - `_retrieve_scoped` — method — internal — Scoped retrieval: candidate req_ids gate both retrievers.
  - `_scoped_retrieve` — method — internal — Retrieve from the vector store filtered to specific req_ids.
  - `_to_chunks` — staticmethod — internal — Convert a QueryResult to a list of RetrievedChunk.
  - `retrieve` — method — pub — Retrieve and rank chunks for the query.
- `_ACRONYM_QUERY_RE` — constant — internal
- `_DEFAULT_BM25_WEIGHT` — constant — internal
- `_DENSE_WEIGHT` — constant — internal
- `_HYBRID_FANOUT_MULT` — constant — internal
- `logger` — constant — pub

`reranker.py`
- `CrossEncoderReranker` — class — pub — Wraps `sentence_transformers.CrossEncoder` to score (query,.
  - `__init__` — constructor — internal — Args:.
  - `_truncate` — method — internal — Truncate to `max_chunk_chars` so long-tail chunks don't.
  - `available` — property — pub
  - `rerank` — method — pub — Score every (query, chunk_text) pair and return chunks.
- `MockReranker` — class — pub — Deterministic no-op reranker — returns input chunks as-is.
  - `rerank` — method — pub
- `Reranker` — class — pub — Protocol for rerankers.
  - `rerank` — method — pub
- `_DEFAULT_RERANKER_MODEL` — constant — internal
- `logger` — constant — pub

`resolver.py`
- `MNOReleaseResolver` — class — pub — Resolves MNO and release scope from query intent + graph metadata.
  - `__init__` — constructor — internal
  - `_discover_available` — method — internal — Discover available MNOs and their releases from the graph.
  - `_match_release` — method — internal — Match a user-specified release string to an available release.
  - `available_mnos` — property — pub
  - `resolve` — method — pub — Resolve MNO/release scope.
- `logger` — constant — pub

`retrieval_debug.py`
- `_TRACKED_ENV_VARS` — constant — internal
- `_hr` — function — internal
- `_ollama_embed` — function — internal — Call /api/embeddings directly. Returns (vec, None) or (None, error_msg).
- `_section_embedding_model` — function — internal — Direct API probe of the embedding model — proves identical bytes.
- `_section_machine` — function — internal — Print machine fingerprint; return parsed vectorstore config (or None).
- `_section_retrieval` — function — internal — Pure-dense top-10 — rawest retrieval signal.
- `_section_vectorstore` — function — internal — Vectorstore content fingerprint — chunk count, plan distribution, hashes.
- `cmd_compare_envs` — function — pub — Run all four fingerprint sections.
- `logger` — constant — pub
- `main` — function — pub

`rewriter.py`
- `LLMQueryRewriter` — class — pub — LLM-driven rewriter — produces N short paraphrases per call.
  - `__init__` — constructor — internal
  - `_parse_rewrites` — method — internal — Pull rewrites out of the LLM response.
  - `rewrite` — method — pub
- `MockQueryRewriter` — class — pub — Deterministic no-op rewriter — keeps offline eval reproducible.
  - `rewrite` — method — pub
- `QueryRewriter` — class — pub — Protocol for query rewriters.
  - `rewrite` — method — pub
- `_REWRITE_PROMPT` — constant — internal
- `expand_query` — function — pub — Combine the original query with its rewrites into a single.
- `logger` — constant — pub

`schema.py`
- `AssembledContext` — dataclass — pub — Assembled LLM prompt context.
- `CandidateNode` — dataclass — pub — A candidate node from graph scoping.
- `CandidateSet` — dataclass — pub — Set of candidate nodes from graph scoping.
  - `requirement_ids` — method — pub — Return req_id values (not graph node IDs) for vector store filtering.
  - `to_dict` — method — pub
  - `total` — property — pub
- `ChunkContext` — dataclass — pub — A chunk with full context for LLM prompt assembly.
- `ChunkGroup` — dataclass — pub — A cluster of retrieved chunks sharing a hierarchy-path prefix.
- `Citation` — dataclass — pub — A citation to a specific requirement or standard.
- `CitationAudit` — dataclass — pub — Full audit of a synthesized answer. Output of Stage 6.5.
  - `cited_percent` — property — pub
  - `uncited_sentences` — property — pub
- `DocTypeScope` — enum — pub — Which document types to include in retrieval.
- `MNOScope` — dataclass — pub — A resolved MNO + release pair.
- `QueryIntent` — dataclass — pub — Structured intent extracted from a natural language query.
  - `to_dict` — method — pub
- `QueryResponse` — dataclass — pub — Final pipeline output.
  - `save_json` — method — pub
  - `to_dict` — method — pub
- `QueryType` — enum — pub — Types of queries the pipeline can handle.
- `RetrievedChunk` — dataclass — pub — A chunk retrieved and ranked by vector similarity.
- `ScopedQuery` — dataclass — pub — Query with resolved MNO/release scope.
  - `to_dict` — method — pub
- `SentenceAudit` — dataclass — pub — One sentence's audit result. Output of post-synthesis Stage 6.5.
- `StandardsContext` — dataclass — pub — Standards text associated with a requirement.

`synthesizer.py`
- `LLMSynthesizer` — class — pub — Generates answers from assembled context using an LLM.
  - `__init__` — constructor — internal
  - `_extract_citations` — staticmethod — internal — Extract requirement and standards citations from the answer.
  - `_recover_citations_from_context` — staticmethod — internal — Recover citations from context chunks the LLM didn't explicitly cite.
  - `synthesize` — method — pub — Generate an answer from the assembled context.
- `MockSynthesizer` — class — pub — Mock synthesizer that returns a structured summary without LLM.
  - `synthesize` — method — pub — Generate a mock answer summarizing the context.
- `logger` — constant — pub
<!-- END:STRUCTURE -->

**Depends on**
[graph](../graph/MODULE.md), [vectorstore](../vectorstore/MODULE.md), [llm](../llm/MODULE.md), [resolver](../resolver/MODULE.md) (types only), [standards](../standards/MODULE.md) (types for `StandardsContext`), [taxonomy](../taxonomy/MODULE.md) (feature nodes), [env](../env/MODULE.md) (`resolve_grouping_enabled` / `resolve_gap_threshold` / `resolve_bm25_weight` for the unified config resolver chain — D-050).

**Depended on by**
[eval](../eval/MODULE.md), [web](../web/MODULE.md), [pipeline](../pipeline/MODULE.md) (not a runtime dep — pipeline emits the artifacts query consumes; listed here because stage ordering and artifact contracts are shared).
