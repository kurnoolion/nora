# vectorstore

**Purpose**
Unified vector-store construction and configuration. Defines two structural-typing Protocols (`EmbeddingProvider`, `VectorStoreProvider`) per D-007, wraps them with a chunk-builder + builder pipeline, and persists a single vector index spanning all MNOs × releases × doc_types. Metadata filters scope retrieval at query time — there is no per-MNO store. Serves FR-8 (unified vector store with metadata filtering), FR-35 (per-document definitions/acronyms expansion in chunk text before embedding [D-032]); covers NFR-2 (offline HF install via `hf_offline`), NFR-7 (configurable embedding model / backend / metric / chunk strategy via `VectorStoreConfig`).

**Public surface**
- Protocols:
  - `EmbeddingProvider` (embedding_base.py) — `embed(texts)`, `embed_query(text)`, `dimension`, `model_name`
  - `VectorStoreProvider` (store_base.py) — `add()`, `query()`, `count`, `reset()`, `get_all() -> QueryResult` (full-corpus dump used by companion sparse retrievers and audit tooling; distances empty)
  - `QueryResult` (store_base.py) — `ids`, `documents`, `metadatas`, `distances`
- Implementations:
  - `SentenceTransformerEmbedder` (embedding_st.py) — ST backend; respects offline HF cache
  - `OllamaEmbedder` (embedding_ollama.py) — Ollama `/api/embeddings` backend; loopback-aware (bypasses HTTP_PROXY for localhost); same offline-distribution path as the LLM provider (`ollama pull <model>`)
  - `ChromaDBStore` (store_chroma.py) — persistent ChromaDB collection
- Provider factory:
  - `make_embedder(config)` (`__init__.py`) — dispatches by `config.embedding_provider` to `SentenceTransformerEmbedder` or `OllamaEmbedder`; reads provider-specific settings from `extra` (e.g., `ollama_url`, `ollama_timeout_s`). Accepts the aliases `huggingface`/`hf`/`st`/`sentence_transformers` for `sentence-transformers`.
- Builder / chunking:
  - `VectorStoreBuilder` (builder.py) — orchestrates load → chunk → embed → store
  - `BuildStats` — per-build metrics: chunks_by_plan, embedding model/dim, backend, metric, collection
  - `ChunkBuilder`, `Chunk` (chunk_builder.py) — builds contextualized chunks with configurable headers (MNO / Release / Plan / Path / Req ID) and optional inline tables/image context. Accepts an optional per-document `definitions_map: dict[str, str]` and expands the first occurrence of each known term inline before chunk text is finalized [D-032]. Optionally appends `[Subsections: ...]` line to thin-bodied parents when `config.include_children_titles=True` (default off — see Key choices) [D-042]. Also emits one short `glossary:<plan_id>:<acronym>` chunk per `definitions_map` entry (`doc_type=glossary_entry`, `metadata.{acronym, expansion}`) so acronym queries can hit a high-precision answer surface [D-043].
- Config: `VectorStoreConfig` (config.py) — every tuneable parameter (embedding provider/model/batch/device, store backend/metric/persist_dir, chunk contextualization toggles, defaults). Provider options: `'sentence-transformers'` (default) | `'ollama'`.
- Offline support:
  - `hf_offline.enable_offline_if_cached(model_name)` — switches HF to offline mode when the cache already has the model (sentence-transformers path)
  - Ollama path is offline by construction — once `ollama pull` has run, no further network calls
- CLI: `vectorstore_cli.main`

**Invariants**
- The two Protocols are the only seams. No direct `chromadb` or `sentence_transformers` imports live outside this module. Switching providers means adding a new file, not touching callers.
- One unified collection per persist directory — metadata (`mno`, `release`, `plan_id`, `doc_type`) carries the scope; `where` filters enforce it at query time. Implements D-002.
- `VectorStoreConfig` is the single source of truth for a build. `BuildStats` captures the actual values used — pair them to reproduce any retrieval result.
- Embeddings are L2-normalized by default (`normalize_embeddings=True`) because the default `distance_metric="cosine"` requires it; turning off one without the other is a configuration bug.
- `embed_query()` is a separate method from `embed()` because some models use asymmetric encoders (different prefixes for docs vs queries). Callers must never bypass it by calling `embed([text])[0]` directly.
- Chunk context (hierarchy path, req ID, MNO header) is **prepended to the chunk text before embedding**, not just stored as metadata — this is what lets retrieval surface the right chunk when the user asks by path or req ID.
- Definitions expansion (when `definitions_map` is supplied) is **per-document scoped** — each `RequirementTree`'s map is threaded into the chunker only for that tree's chunks, never aggregated across trees. Preserves locality (`RAT` may mean different things in different MNO docs) [D-032, FR-35].
- Definitions expansion is **idempotent and first-occurrence-per-chunk only**; re-running on already-expanded text is a no-op (`\bETWS\b` does not match inside a previously-expanded `ETWS (Earthquake...)`).
- Chunks belonging to the definitions section itself are excluded from expansion to avoid `ETWS (Earthquake...) (Earthquake...)` double-anchoring [D-032].

**Key choices**
- Protocol + injection: `VectorStoreBuilder(embedder, store, config)` — builder never constructs providers, so tests can use in-memory stubs without monkey-patching.
- ChromaDB + sentence-transformers as defaults because both run fully local with no API keys; switching to an API-backed embedder only requires a new class.
- Offline HF cache loader (`hf_offline`) specifically supports locked-down work machines — `SentenceTransformerEmbedder` calls it on init so models ship via tarball if needed.
- Config includes chunk-contextualization toggles so A/B tests can isolate retrieval gains from chunk decoration vs. model changes.
- `BuildStats` saved alongside the store — a build is reproducible from `(VectorStoreConfig, BuildStats)` without re-reading any tree.
- Definitions expansion happens at **chunk-build time, not query-time** — vectors are computed from expanded text, so retrieval recall on acronym-shaped queries (`"ETWS"`, `"SUPL requirements"`) actually improves. Query-time expansion would leave un-expanded vectors in the store [D-032].
- **Parent-chunk subsection augmentation is opt-in (default off)** [D-042]. `config.include_children_titles=True` appends `[Subsections: child1; child2; (+N more)]` to parents whose body is below `children_titles_body_threshold` (default 300 chars), capped at `max_children_titles` (default 3). Default off because empirical OA tuning showed +8pp single_doc / -10..14pp cross_doc tradeoff (augmented parents displace their own children from top-k; breadth queries want the children). Available behind the flag for corpora with rich-bodied parents or lookup-heavy question mixes.
- **Glossary chunks are additive, not a replacement** [D-043]. Each `definitions_map` entry becomes a small `glossary:<plan_id>:<slug>` chunk *in addition to* the requirement chunk for the definitions section. Short queries ("What is SDM?") get the high-precision per-acronym chunk; longer "show me the glossary" queries still hit the rich req chunk. Slug strips non-`[A-Za-z0-9_-]+`. Chunk text leads with `<ACRONYM>: <expansion>` so BM25 (high TF) and dense (concise) both rank it top. Pairs with the query-side pin in [query](../query/MODULE.md).

**Non-goals**
- No retrieval logic beyond `query()` — ranking, reranking, hybrid merging, and MNO/release scoping live in [query](../query/MODULE.md).
- No per-MNO or per-release stores — multi-MNO separation is a metadata filter, never a directory split.
- No graph semantics — this module stores text+vector+metadata tuples; cross-document structure is [graph](../graph/MODULE.md)'s job.
- No LLM calls — embedding models don't count; the `LLMProvider` Protocol is separate and lives in [llm](../llm/MODULE.md).

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`__init__.py`
- `make_embedder` — function — pub — Construct the embedder named by `config.

`builder.py`
- `BuildStats` — dataclass — pub — Statistics from a vector store build.
  - `save_json` — method — pub
  - `to_dict` — method — pub
- `VectorStoreBuilder` — class — pub — Orchestrates vector store construction from ingestion outputs.
  - `__init__` — constructor — pub
  - `_compute_stats` — method — internal — Compute build statistics.
  - `_deduplicate_chunks` — staticmethod — internal — Deduplicate chunks by ID, keeping the one with more text content.
  - `_embed_batched` — method — internal — Embed texts in batches to manage memory.
  - `_load_taxonomy` — staticmethod — internal
  - `_load_trees` — staticmethod — internal
  - `build` — method — pub — Build the vector store.

`chunk_builder.py`
- `Chunk` — dataclass — pub — A single vector store chunk.
- `ChunkBuilder` — class — pub — Builds contextualized chunks from parsed requirement trees.
  - `__init__` — constructor — pub
  - `_belongs_to_definitions` — staticmethod — internal — True when the requirement is the definitions section or a
  - `_build_chunk_text` — method — internal — Build the contextualized text for a single requirement.
  - `_build_glossary_chunks` — staticmethod — internal — Build one Chunk per (acronym, expansion) pair.
  - `_build_plan_feature_map` — staticmethod — internal — Build a mapping from plan_id -> list of feature_ids.
  - `_build_tree_chunks` — method — internal — Build chunks for all requirements in one tree.
  - `_compile_definitions_regex` — staticmethod — internal — Compile a single alternation regex matching every term as a
  - `_expand_definitions` — staticmethod — internal — Inline-expand the first occurrence of each known term in `text`.
  - `_table_to_markdown` — staticmethod — internal — Convert a table dict to Markdown table format.
  - `build_chunks` — method — pub — Build chunks from all parsed trees.

`config.py`
- `VectorStoreConfig` — dataclass — pub — Configuration for the vector store pipeline.
  - `load_json` — classmethod — pub
  - `save_json` — method — pub
  - `to_dict` — method — pub

`embed_debug.py`
- `_build_embedder` — function — internal
- `_err_summary` — function — internal
- `_LOREM` — constant — internal
- `_preview` — function — internal
- `_SWEEP_LENGTHS` — constant — internal
- `cmd_check` — function — pub
- `cmd_chunks` — function — pub
- `cmd_sweep` — function — pub
- `cmd_text` — function — pub
- `main` — function — pub

`embedding_base.py`
- `EmbeddingProvider` — class — pub — Protocol for embedding providers.
  - `dimension` — property — pub — Dimensionality of the embedding vectors.
  - `embed` — method — pub — Embed a batch of texts.
  - `embed_query` — method — pub — Embed a single query text.
  - `model_name` — property — pub — Name of the embedding model (for metadata/logging).

`embedding_ollama.py`
- `_build_opener` — function — internal — Build a urllib opener that bypasses HTTP_PROXY for loopback URLs.
- `_DEFAULT_BASE_URL` — constant — internal
- `_DEFAULT_MAX_INPUT_CHARS` — constant — internal
- `_l2_normalize` — function — internal
- `_LOOPBACK_HOSTS` — constant — internal
- `OllamaEmbedder` — class — pub — Embedding provider using Ollama's /api/embeddings endpoint.
  - `__init__` — constructor — pub
  - `dimension` — property — pub
  - `embed` — method — pub — Embed a batch of texts.
  - `embed_query` — method — pub — Embed a single query.
  - `model_name` — property — pub

`embedding_st.py`
- `SentenceTransformerEmbedder` — class — pub — Embedding provider using sentence-transformers.
  - `__init__` — constructor — pub
  - `dimension` — property — pub
  - `embed` — method — pub — Embed a batch of texts.
  - `embed_query` — method — pub — Embed a single query.
  - `model_name` — property — pub

`hf_offline.py`
- `_cache_has_snapshot` — function — internal — Return True if the HF hub cache has a usable snapshot for repo_id.
- `_hf_cache_root` — function — internal — Resolve the HuggingFace hub cache directory.
- `_patch_constants_if_loaded` — function — internal
- `enable_offline_if_cached` — function — pub — If the model is already cached locally, enable HF Hub offline mode.

`store_base.py`
- `QueryResult` — dataclass — pub — Result from a vector store query.
- `VectorStoreProvider` — class — pub — Protocol for vector store backends.
  - `add` — method — pub — Add documents with their embeddings and metadata to the store.
  - `count` — property — pub — Number of documents in the store.
  - `get_all` — method — pub — Return every document in the store (id + text + metadata).
  - `query` — method — pub — Query the store for similar documents.
  - `reset` — method — pub — Delete all documents from the store.

`store_chroma.py`
- `_CHROMA_METRICS` — constant — internal
- `ChromaDBStore` — class — pub — Vector store backend using ChromaDB.
  - `__init__` — constructor — pub
  - `_deserialize_metadata` — staticmethod — internal — Reverse _sanitize_metadata — parse JSON strings back to lists/dicts.
  - `_sanitize_metadata` — staticmethod — internal — Convert non-primitive metadata values to JSON strings.
  - `add` — method — pub — Add documents to the collection.
  - `count` — property — pub
  - `get_all` — method — pub — Return every document in the collection.
  - `query` — method — pub — Query for similar documents with optional metadata filtering.
  - `reset` — method — pub — Delete and recreate the collection.

`vectorstore_cli.py`
- `_build_config` — function — internal — Build config from file + CLI overrides.
- `_create_store` — function — internal — Create a vector store backend from config.
- `cmd_build` — function — pub — Build the vector store.
- `cmd_info` — function — pub — Show info about an existing vector store.
- `cmd_query` — function — pub — Run a test query against the vector store.
- `main` — function — pub
<!-- END:STRUCTURE -->

**Depends on**
[models](../models/MODULE.md) (source_format / metadata shapes), [parser](../parser/MODULE.md) (consumes `RequirementTree` + `Requirement`).

**Depended on by**
[query](../query/MODULE.md), [pipeline](../pipeline/MODULE.md).
