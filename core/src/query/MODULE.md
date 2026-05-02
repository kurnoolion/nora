# query

**Purpose**
Online query pipeline (TDD §7). A 6-stage chain that turns a natural-language question into a grounded, citation-bearing answer: `Analysis → MNO/Release Resolution → Graph Scoping → Targeted RAG → Context Assembly → LLM Synthesis`. Serves FR-9, FR-10, FR-11, FR-12, FR-13, FR-14 (one FR per stage). Implements D-001: the **graph routes, RAG ranks** — retrieval never runs unscoped, and the graph decides which subset of the corpus is even eligible.

**Public surface**
- Entry point: `QueryPipeline(graph, embedder, store, analyzer=None, synthesizer=None, top_k=10, max_depth=None, max_context_chars=30000)` (pipeline.py) — `query(raw_query) -> QueryResponse`
- Stages (each replaceable by injection):
  - `LLMQueryAnalyzer`, `MockQueryAnalyzer` (analyzer.py) — Stage 1
  - `MNOReleaseResolver` (resolver.py) — Stage 2
  - `GraphScoper` (graph_scope.py) — Stage 3
  - `RAGRetriever` (rag_retriever.py) — Stage 4
  - `ContextBuilder` (context_builder.py) — Stage 5
  - `LLMSynthesizer`, `MockSynthesizer` (synthesizer.py) — Stage 6
- Schema (schema.py):
  - Enums: `QueryType` (single_doc, cross_doc, cross_mno_comparison, release_diff, standards_comparison, traceability, feature_level, general), `DocTypeScope`
  - Per-stage dataclasses: `QueryIntent`, `MNOScope`, `ScopedQuery`, `CandidateNode`, `CandidateSet`, `RetrievedChunk`, `StandardsContext`, `ChunkContext`, `AssembledContext`, `Citation`, `QueryResponse`
- CLI: `query_cli.main`

**Invariants**
- **Graph-first, then RAG.** Vector retrieval is always filtered to the `requirement_ids` produced by `GraphScoper`. Unscoped retrieval is a D-001 violation, not a shortcut.
- The 6 stages pass typed dataclasses — each stage's output is the next stage's only input. No stage reaches back for state.
- Every stage is injectable — `QueryPipeline(analyzer=MyAnalyzer())` swaps Stage 1 without touching the rest. Mocks (`MockQueryAnalyzer`, `MockSynthesizer`) exist so the pipeline runs without any LLM for offline debugging.
- `QueryResponse.citations` reference **specific** `(req_id, plan_id, section_number)` tuples (plus optional standards spec/section). Answers without citations are a bug in the synthesizer, not the default.
- `max_context_chars` caps Stage 5 output — truncation is deterministic (preserves top-scored chunks first), never silent.
- Graph and vector store are **inputs** to the pipeline, not owned by it — built offline by [graph](../graph/MODULE.md) and [vectorstore](../vectorstore/MODULE.md), loaded once at startup, reused per query.

**Key choices**
- Six stages instead of a single monolithic retriever so each can be tested and swapped independently — the mock analyzer/synthesizer is what makes the pipeline testable on a work laptop without LLM access.
- `QueryType` carved into eight concrete kinds (release_diff, traceability, etc.) because each needs different graph scoping and different prompting. A generic pipeline that treats every query the same loses signal.
- `CandidateSet` keeps `requirement_nodes`, `standards_nodes`, `feature_nodes` separate — retrieval filters on req IDs, context assembly attaches standards text by node, and future reranking can use feature nodes without re-traversing.
- Prompting is few-shot + explicit grounding instructions; `LLMSynthesizer` includes a context fallback path for cases where the LLM skips citations (fix kept because dropping it caused regression in internal tests).
- Pipeline defaults (`top_k=10`, `max_context_chars=30000`) live on the class, not in env config — most callers accept defaults; eval overrides. **Per-query-type override** [D-040]: `QueryPipeline.query` picks `top_k` from a `_TYPE_TOP_K` map keyed by `intent.query_type` — list/breadth queries (CROSS_DOC / FEATURE_LEVEL / STANDARDS_COMPARISON / CROSS_MNO_COMPARISON) widen to 25 because their expected hits include parent/overview reqs whose chunks are short (heading + path only) and rank below richer leaf chunks; TRACEABILITY / RELEASE_DIFF widen to 20; lookups stay at 10. Pipeline takes `max(self._top_k, type_top_k)` so callers can still raise the floor explicitly.
- **Specific-entity queries are authoritative for graph scope** [D-039]: when `GraphScoper._entity_lookup` matches (the analyzer extracted req IDs that exist as `req:*` nodes), expansion via `_feature_lookup` / `_plan_lookup` / `_title_search` is skipped. Edge traversal from the entity seeds still runs and provides the immediate neighborhood (sibling sections, referenced standards, parent containers) — the named-req anchor isn't diluted into a feature-wide scope where vector ranking can no longer surface the specific chunk.
- Cross-doc / list-style queries are detected by phrase triggers in `_classify_query_type` [D-040]: `across all`, `across the`, `in all`, `across vzw|mnos|plans|specs`, `all the requirements`, `what are all`, `what requirements` map to `QueryType.CROSS_DOC`. FEATURE_LEVEL still wins on more-specific phrasing (`everything about`, `related to`) — the analyzer checks FEATURE_LEVEL first to preserve the existing classification contract.

**Non-goals**
- Not a compliance checker. "Is device X compliant with plan Y?" is a separate workflow that uses this pipeline as a primitive; don't collapse the two.
- No retrieval reranking layer (cross-encoder, listwise) in v1 — vector-similarity + graph-scope is the baseline; add reranking only if eval shows the gap.
- No multi-turn conversation state. Each `query()` call is independent; chat-like flows are assembled by the caller (web UI).
- No write path — query never mutates the graph or vector store.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._

`analyzer.py`
- `_FEATURE_KEYWORDS` — constant — internal
- `_MNO_ALIASES` — constant — internal
- `_PLAN_ALIASES` — constant — internal
- `_RELEASE_PATTERNS` — constant — internal
- `_REQ_ID_PATTERN` — constant — internal
- `_SPEC_PATTERN` — constant — internal
- `LLMQueryAnalyzer` — class — pub — LLM-driven query analyzer.
  - `__init__` — constructor — pub
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

`context_builder.py`
- `_CITATION_RULES` — constant — internal
- `_FEW_SHOT_EXAMPLE` — constant — internal
- `_SYSTEM_PROMPTS` — constant — internal
- `ContextBuilder` — class — pub — Assembles LLM prompt context from retrieved chunks and graph data.
  - `__init__` — constructor — pub
  - `_enrich_chunk` — method — internal — Enrich a chunk with graph context (hierarchy, standards, etc.).
  - `_format_context` — method — internal — Format enriched chunks into a context string for the LLM.
  - `_get_parent_text` — method — internal — Get the parent requirement's text for context.
  - `_get_related_ids` — method — internal — Get IDs of related requirement nodes (via depends_on).
  - `_get_standards_context` — method — internal — Get standards sections referenced by this requirement.
  - `_strip_chunk_headers` — staticmethod — internal — Strip the contextualization headers from chunk text.
  - `build` — method — pub — Build assembled context for LLM synthesis.

`graph_scope.py`
- `_DEFAULT_DEPTH` — constant — internal
- `_TRAVERSAL_EDGES` — constant — internal
- `GraphScoper` — class — pub — Scopes candidate nodes using knowledge graph traversal.
  - `__init__` — constructor — pub
  - `_entity_lookup` — method — internal — Look up nodes by entity names (req IDs, etc.).
  - `_feature_lookup` — method — internal — Look up requirements via feature nodes.
  - `_in_scope` — staticmethod — internal — Check if a node is within the resolved MNO/release scope.
  - `_plan_lookup` — method — internal — Get all requirements belonging to specified plans.
  - `_title_search` — method — internal — Search node titles/text for concepts and entities.
  - `_traverse` — method — internal — Traverse from seed nodes along allowed edge types.
  - `scope` — method — pub — Find candidate nodes for the query.

`pipeline.py`
- `load_graph` — function — pub — Load a knowledge graph from JSON.
- `QueryPipeline` — class — pub — End-to-end query pipeline.
  - `__init__` — constructor — pub — Initialize the pipeline.
  - `query` — method — pub — Run the full query pipeline.

`query_cli.py`
- `_create_pipeline` — function — internal — Create the query pipeline with all components.
- `_display_response` — function — internal — Display a query response.
- `cmd_interactive` — function — pub — Run interactive query mode.
- `cmd_query` — function — pub — Run a single query.
- `main` — function — pub

`rag_retriever.py`
- `RAGRetriever` — class — pub — Retrieves and ranks requirement chunks by vector similarity.
  - `__init__` — constructor — pub
  - `_enforce_diversity` — method — internal — Ensure at least N chunks from each contributing plan.
  - `_metadata_retrieve` — method — internal — Retrieve with MNO/release metadata filters.
  - `_scoped_retrieve` — method — internal — Retrieve from the vector store filtered to specific req_ids.
  - `_to_chunks` — staticmethod — internal — Convert a QueryResult to a list of RetrievedChunk.
  - `retrieve` — method — pub — Retrieve and rank chunks for the query.

`resolver.py`
- `MNOReleaseResolver` — class — pub — Resolves MNO and release scope from query intent + graph metadata.
  - `__init__` — constructor — pub
  - `_discover_available` — method — internal — Discover available MNOs and their releases from the graph.
  - `_match_release` — method — internal — Match a user-specified release string to an available release.
  - `available_mnos` — property — pub
  - `resolve` — method — pub — Resolve MNO/release scope.

`schema.py`
- `AssembledContext` — dataclass — pub — Assembled LLM prompt context.
- `CandidateNode` — dataclass — pub — A candidate node from graph scoping.
- `CandidateSet` — dataclass — pub — Set of candidate nodes from graph scoping.
  - `requirement_ids` — method — pub — Return req_id values (not graph node IDs) for vector store filtering.
  - `to_dict` — method — pub
  - `total` — property — pub
- `ChunkContext` — dataclass — pub — A chunk with full context for LLM prompt assembly.
- `Citation` — dataclass — pub — A citation to a specific requirement or standard.
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
- `StandardsContext` — dataclass — pub — Standards text associated with a requirement.

`synthesizer.py`
- `LLMSynthesizer` — class — pub — Generates answers from assembled context using an LLM.
  - `__init__` — constructor — pub
  - `_extract_citations` — staticmethod — internal — Extract requirement and standards citations from the answer.
  - `_recover_citations_from_context` — staticmethod — internal — Recover citations from context chunks the LLM didn't explicitly cite.
  - `synthesize` — method — pub — Generate an answer from the assembled context.
- `MockSynthesizer` — class — pub — Mock synthesizer that returns a structured summary without LLM.
  - `synthesize` — method — pub — Generate a mock answer summarizing the context.
<!-- END:STRUCTURE -->

**Depends on**
[graph](../graph/MODULE.md), [vectorstore](../vectorstore/MODULE.md), [llm](../llm/MODULE.md), [resolver](../resolver/MODULE.md) (types only), [standards](../standards/MODULE.md) (types for `StandardsContext`), [taxonomy](../taxonomy/MODULE.md) (feature nodes).

**Depended on by**
[eval](../eval/MODULE.md), [web](../web/MODULE.md), [pipeline](../pipeline/MODULE.md) (not a runtime dep — pipeline emits the artifacts query consumes; listed here because stage ordering and artifact contracts are shared).
