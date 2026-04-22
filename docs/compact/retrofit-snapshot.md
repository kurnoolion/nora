# Retrofit snapshot

Generated 2026-04-21 by `project-init --retrofit`. Archival: do not update; re-run retrofit into a fresh project if the codebase shape changes materially.

## Detected languages

- Python — `requirements.txt` (no `pyproject.toml` / `setup.py`; `src/` layout with `__init__.py` per package)

Dependencies flagged by `requirements.txt`:

- **Extraction / parsing**: `pymupdf`, `pdfplumber`, `python-docx`, `openpyxl`, `xlrd`, `olefile`, `Pillow`
- **LLM / embeddings**: `sentence-transformers`, `httpx` (for Ollama HTTP)
- **Storage / retrieval**: `chromadb`, `aiosqlite`
- **Graph**: `networkx`
- **Web UI**: `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`

Shell / ops surfaces not in `requirements.txt`:

- `setup_env.sh` — environment bootstrap script
- `create_presentation.py` / `update_presentation.py` — out-of-tree scripts at repo root (not part of `src/`)

## Candidate modules

### Python — one candidate per package under `src/`

- `src/corrections/MODULE.md` — 3 public classes/fns observed (curated `__init__.py` exports)
- `src/env/MODULE.md` — 1 public class observed
- `src/eval/MODULE.md` — 6 public classes observed (+ `eval_cli` entrypoint)
- `src/extraction/MODULE.md` — 3 public classes + registry fns observed
- `src/graph/MODULE.md` — 4 public classes observed (+ `graph_cli`)
- `src/llm/MODULE.md` — 6 public classes observed (Protocol + providers + picker)
- `src/models/MODULE.md` — 5 public classes observed (shared document IR types)
- `src/parser/MODULE.md` — 7 public classes observed (+ `parse_cli`)
- `src/pipeline/MODULE.md` — 5 public classes observed (+ `run_cli`)
- `src/profiler/MODULE.md` — 10 public schema types + `DocumentProfiler` (+ `profile_cli`)
- `src/query/MODULE.md` — 17 public classes observed (schema + analyzer + retriever + synthesizer + pipeline; + `query_cli`)
- `src/resolver/MODULE.md` — 7 public classes observed (+ `resolve_cli`)
- `src/standards/MODULE.md` — 12 public classes observed (+ `standards_cli`)
- `src/taxonomy/MODULE.md` — 6 public classes observed (+ `taxonomy_cli`)
- `src/vectorstore/MODULE.md` — 10 public classes observed (+ `vectorstore_cli`)
- `src/web/MODULE.md` — 8 public classes observed (FastAPI app, middleware, job queue, metrics, config)

Total: 16 Python packages → 16 MODULE.md skeletons.

## Candidate public surface (per module)

### `src/corrections/` (Python)
- `FixReport` (schema.py)
- `CorrectionStore` (store.py)
- `profile_fix_report`, `taxonomy_fix_report` (compactor.py)

### `src/env/` (Python)
- `EnvironmentConfig` (config.py)
- `env_cli.main` (CLI)

### `src/eval/` (Python)
- `EvalRunner`, `ABComparison` (runner.py)
- `EvalQuestion`, `GroundTruth` (questions.py)
- `QuestionScore`, `EvalReport` (metrics.py)
- `eval_cli.main` (CLI)

### `src/extraction/` (Python)
- `BaseExtractor` (base.py, abstract)
- `PDFExtractor`, `DOCXExtractor` (pdf_extractor.py, docx_extractor.py)
- `supported_extensions`, `get_extractor`, `extract_document`, `infer_metadata_from_path` (registry.py)

### `src/graph/` (Python)
- `NodeType`, `EdgeType` (schema.py, enums)
- `KnowledgeGraphBuilder`, `GraphStats` (builder.py)
- `graph_cli.main` (CLI)

### `src/llm/` (Python)
- `LLMProvider` (base.py, Protocol)
- `OllamaProvider` (ollama_provider.py)
- `MockLLMProvider` (mock_provider.py)
- `HardwareInfo`, `ModelSpec`, `ModelChoice` (model_picker.py)

### `src/models/` (Python)
- `BlockType`, `Position`, `FontInfo`, `ContentBlock`, `DocumentIR` (document.py) — shared IR

### `src/parser/` (Python)
- `GenericStructuralParser` (structural_parser.py, main entrypoint)
- `TableData`, `ImageRef`, `StandardsRef`, `CrossReferences`, `Requirement`, `RequirementTree` (structural_parser.py, types)
- `parse_cli.main` (CLI)

### `src/pipeline/` (Python)
- `PipelineRunner`, `PipelineContext` (runner.py)
- `StageResult` (stages.py) + stage fns `run_extract`, `run_profile`, `run_parse`, `run_resolve`, `run_taxonomy`, `run_standards`, `run_graph`, `run_vectorstore`, `run_eval`
- `ErrorDef`, `PipelineError` (error_codes.py)
- `run_cli.main` (CLI)

### `src/profiler/` (Python)
- `DocumentProfiler` (profiler.py)
- `DocumentProfile` + nested schema types (`HeadingLevel`, `HeadingDetection`, `RequirementIdPattern`, `MetadataField`, `PlanMetadata`, `DocumentZone`, `HeaderFooter`, `CrossReferencePatterns`, `BodyText`) in profile_schema.py
- `profile_cli.main` (CLI)

### `src/query/` (Python)
- `QueryPipeline` (pipeline.py, top-level entry)
- `LLMQueryAnalyzer`, `MockQueryAnalyzer` (analyzer.py)
- `MNOReleaseResolver` (resolver.py)
- `GraphScoper` (graph_scope.py)
- `RAGRetriever` (rag_retriever.py)
- `ContextBuilder` (context_builder.py)
- `LLMSynthesizer`, `MockSynthesizer` (synthesizer.py)
- `QueryType`, `DocTypeScope`, `QueryIntent`, `MNOScope`, `ScopedQuery`, `CandidateNode`, `CandidateSet`, `RetrievedChunk`, `StandardsContext`, `ChunkContext`, `AssembledContext`, `Citation`, `QueryResponse` (schema.py)
- `query_cli.main` (CLI)

### `src/resolver/` (Python)
- `CrossReferenceResolver` (resolver.py, main)
- `CrossReferenceManifest`, `ManifestSummary` (resolver.py)
- `ResolvedInternalRef`, `ResolvedCrossPlanRef`, `ResolvedStandardsRef`, `RefStatus` (resolver.py)
- `resolve_cli.main` (CLI)

### `src/standards/` (Python)
- `SpecResolver`, `ResolvedSpec` (spec_resolver.py)
- `SpecParser` (spec_parser.py)
- `SpecDownloader` (spec_downloader.py)
- `SectionExtractor` (section_extractor.py)
- `StandardsReferenceCollector` (reference_collector.py)
- `SpecReference`, `AggregatedSpecRef`, `StandardsReferenceIndex`, `SpecSection`, `SpecDocument`, `ExtractedSpecContent` (schema.py)
- `standards_cli.main` (CLI)

### `src/taxonomy/` (Python)
- `FeatureExtractor` (extractor.py)
- `TaxonomyConsolidator` (consolidator.py)
- `Feature`, `DocumentFeatures`, `TaxonomyFeature`, `FeatureTaxonomy` (schema.py)
- `taxonomy_cli.main` (CLI)

### `src/vectorstore/` (Python)
- `VectorStoreBuilder`, `BuildStats` (builder.py)
- `ChunkBuilder`, `Chunk` (chunk_builder.py)
- `VectorStoreProvider` (store_base.py, Protocol), `QueryResult` (store_base.py), `ChromaDBStore` (store_chroma.py)
- `EmbeddingProvider` (embedding_base.py, Protocol), `SentenceTransformerEmbedder` (embedding_st.py)
- `VectorStoreConfig` (config.py)
- `hf_offline` utility module
- `vectorstore_cli.main` (CLI)

### `src/web/` (Python)
- `app` (app.py, FastAPI application)
- `WebConfig`, `PathMapping` (config.py)
- `JobQueue`, `Job` (jobs.py)
- `MetricsStore`, `MetricRecord` (metrics.py)
- `MetricsMiddleware` (middleware.py)
- `PathMapper` (path_mapper.py)
- `ResourceSampler` (resource_sampler.py)

## Observed architectural choices (candidates for reconstructed DECISIONS entries)

These surface from the scan but are not captured in `DECISIONS.md`. User will be asked to log / skip each during step 9 of retrofit init:

- Local LLM inference via **Ollama** over HTTP (`httpx`) with a Protocol-based `LLMProvider` and a `MockLLMProvider` for tests
- Embeddings via **sentence-transformers** with an offline-friendly HF loader (`hf_offline.py`)
- Vector store via **ChromaDB** behind a `VectorStoreProvider` Protocol
- Knowledge graph via **networkx** (in-memory) — `KnowledgeGraphBuilder`
- Web UI via **FastAPI** + `uvicorn` + `jinja2` templates; metrics persisted to SQLite via `aiosqlite`
- Pipeline runner with staged execution (extract → profile → parse → resolve → taxonomy → standards → graph → vectorstore → eval) and typed `ErrorDef` / `PipelineError` codes
- Corrections workflow: auto-generated artifacts under `<doc_root>/output/`, engineer overrides under `<doc_root>/corrections/`; pipeline prefers overrides on re-run
- **Generic structural parser** driven by **DocumentProfile** (LLM-free) — per TDD, the key architectural bet vs. per-MNO hard-coded parsers
- CLI-per-module pattern (`*_cli.py` with `main()` entrypoint) rather than a single dispatching CLI

## Cross-checks for the interview

- `src/web/` + `web/config.json` + `web/nora.db` / `web/nora_metrics.db` → the system has a **web UI surface**. Topic 3 (stakeholder map) should name an end-user / reviewer persona or flag the mismatch.
- `src/corrections/` → the system has an **engineer-in-the-loop correction surface**. Topic 3 should include a "telecom engineer / domain expert" stakeholder who edits profile.json / taxonomy.json.
- `src/eval/` → the system has an **offline evaluation / A-B comparison harness**. Topic 6 (pain points) should mention accuracy / regression guardrails.
- `setup_env.sh` + `SETUP_OFFLINE.md` → **offline / air-gapped install** is a first-class constraint. Topic 4 (domain constraints) should include restricted-network requirements.
