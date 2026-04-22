# MAP

Generated 2026-04-21 by regen-map. Do not hand-edit.

## Modules

| Module | Purpose |
| --- | --- |
| [corrections](../../src/corrections/MODULE.md) | Per-environment profile + taxonomy correction handling: store engineer-edited overrides, diff them against pipeline output, and emit compact FIX reports (no proprietary content) that are pasteable back into chat. |
| [env](../../src/env/MODULE.md) | Per-environment scoped workspace configuration. |
| [eval](../../src/eval/MODULE.md) | Evaluation framework for the query pipeline. |
| [extraction](../../src/extraction/MODULE.md) | Format-aware content extraction. |
| [graph](../../src/graph/MODULE.md) | Unified Knowledge Graph construction (TDD §5.8, D-002). |
| [llm](../../src/llm/MODULE.md) | LLM abstraction layer. |
| [models](../../src/models/MODULE.md) | Shared document intermediate representation. |
| [parser](../../src/parser/MODULE.md) | Generic, profile-driven structural parser. |
| [pipeline](../../src/pipeline/MODULE.md) | Staged, re-runnable pipeline that drives the nine-stage offline flow: `extract → profile → parse → resolve → taxonomy → standards → graph → vectorstore → eval`. |
| [profiler](../../src/profiler/MODULE.md) | Standalone, LLM-free document-structure profiler. |
| [query](../../src/query/MODULE.md) | Online query pipeline (TDD §7). |
| [resolver](../../src/resolver/MODULE.md) | Deterministic cross-reference resolver (TDD §5.5, Methods 1 & 2). |
| [standards](../../src/standards/MODULE.md) | 3GPP standards ingestion — generic, release-aware, LLM-free (TDD §5.6, D-004). |
| [taxonomy](../../src/taxonomy/MODULE.md) | Bottom-up, LLM-derived feature taxonomy for the corpus (TDD §5.7). |
| [vectorstore](../../src/vectorstore/MODULE.md) | Unified vector-store construction and configuration. |
| [web](../../src/web/MODULE.md) | FastAPI + Bootstrap 5 + HTMX Web UI for non-CLI team members (D-008). |

## Dependency graph

```mermaid
flowchart TD
    m_corrections[corrections]
    m_env[env]
    m_eval[eval]
    m_extraction[extraction]
    m_graph[graph]
    m_llm[llm]
    m_models[models]
    m_parser[parser]
    m_pipeline[pipeline]
    m_profiler[profiler]
    m_query[query]
    m_resolver[resolver]
    m_standards[standards]
    m_taxonomy[taxonomy]
    m_vectorstore[vectorstore]
    m_web[web]
    m_corrections --> m_env
    m_corrections --> m_profiler
    m_corrections --> m_taxonomy
    m_eval --> m_graph
    m_eval --> m_llm
    m_eval --> m_query
    m_eval --> m_vectorstore
    m_extraction --> m_models
    m_graph --> m_parser
    m_graph --> m_resolver
    m_graph --> m_standards
    m_graph --> m_taxonomy
    m_parser --> m_extraction
    m_parser --> m_models
    m_parser --> m_profiler
    m_pipeline --> m_corrections
    m_pipeline --> m_env
    m_pipeline --> m_eval
    m_pipeline --> m_extraction
    m_pipeline --> m_graph
    m_pipeline --> m_llm
    m_pipeline --> m_parser
    m_pipeline --> m_profiler
    m_pipeline --> m_resolver
    m_pipeline --> m_standards
    m_pipeline --> m_taxonomy
    m_pipeline --> m_vectorstore
    m_profiler --> m_extraction
    m_profiler --> m_models
    m_query --> m_graph
    m_query --> m_llm
    m_query --> m_resolver
    m_query --> m_standards
    m_query --> m_taxonomy
    m_query --> m_vectorstore
    m_resolver --> m_parser
    m_standards --> m_parser
    m_standards --> m_resolver
    m_taxonomy --> m_corrections
    m_taxonomy --> m_llm
    m_taxonomy --> m_parser
    m_vectorstore --> m_models
    m_vectorstore --> m_parser
    m_web --> m_corrections
    m_web --> m_env
    m_web --> m_pipeline
    m_web --> m_query
```
