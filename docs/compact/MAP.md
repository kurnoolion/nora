# System map

*Generated 2026-04-21 by `regen-map` (retrofit seed). Do not hand-edit.*

*All modules carry `[DRAFT]` status — their MODULE.md skeletons have the `<!-- retrofit: skeleton -->` sentinel. Dependency edges are derived from candidate `Depends on` entries in the skeletons and will tighten as modules are curated. Re-run `/regen-map` after material MODULE.md edits.*

## Modules

| Module | Purpose | Status |
|---|---|---|
| [corrections](../../src/corrections/MODULE.md) | Profile + taxonomy editing, diff, compact FIX reports; pipeline auto-picks up `<doc_root>/corrections/*.json` overrides | `[DRAFT]` |
| [env](../../src/env/MODULE.md) | Per-environment scoped workspace configuration (stages, MNO/release scope, objectives) | `[DRAFT]` |
| [eval](../../src/eval/MODULE.md) | Evaluation framework with A/B comparison between LLM providers and pipeline configurations | `[DRAFT]` |
| [extraction](../../src/extraction/MODULE.md) | Format-aware content extraction (PDF / DOC / DOCX / XLS / XLSX) to normalized `DocumentIR` | `[DRAFT]` |
| [graph](../../src/graph/MODULE.md) | Unified Knowledge Graph construction via networkx; single graph spans all MNOs × releases | `[DRAFT]` |
| [llm](../../src/llm/MODULE.md) | LLM abstraction via `LLMProvider` Protocol; structural typing, swap by instance | `[DRAFT]` |
| [models](../../src/models/MODULE.md) | Shared document intermediate representation (`DocumentIR`, `ContentBlock`, `FontInfo`, …) | `[DRAFT]` |
| [parser](../../src/parser/MODULE.md) | Generic profile-driven structural parser; LLM-free; emits `RequirementTree` | `[DRAFT]` |
| [pipeline](../../src/pipeline/MODULE.md) | Staged pipeline runner (extract → profile → parse → resolve → taxonomy → standards → graph → vectorstore → eval) with stable error codes | `[DRAFT]` |
| [profiler](../../src/profiler/MODULE.md) | Standalone LLM-free `DocumentProfiler` — derives document structure profile from representative docs | `[DRAFT]` |
| [query](../../src/query/MODULE.md) | Online query pipeline: analysis → MNO/release resolution → graph scoping → targeted RAG → context assembly → LLM synthesis | `[DRAFT]` |
| [resolver](../../src/resolver/MODULE.md) | Cross-reference resolver (internal / cross-plan / standards); emits per-document manifests | `[DRAFT]` |
| [standards](../../src/standards/MODULE.md) | 3GPP standards ingestion (Option C Hybrid Selective); generic, release-aware, LLM-free | `[DRAFT]` |
| [taxonomy](../../src/taxonomy/MODULE.md) | Bottom-up LLM-derived feature taxonomy with mandatory human review | `[DRAFT]` |
| [vectorstore](../../src/vectorstore/MODULE.md) | Unified vector store with configurable embedding / backend / metric / chunking | `[DRAFT]` |
| [web](../../src/web/MODULE.md) | FastAPI + Bootstrap 5 + HTMX Web UI (pipeline submission, queries, corrections, metrics) | `[DRAFT]` |

## Dependency graph

```mermaid
flowchart TD
  corrections
  env
  eval
  extraction
  graph
  llm
  models
  parser
  pipeline
  profiler
  query
  resolver
  standards
  taxonomy
  vectorstore
  web

  extraction --> models

  profiler --> extraction
  profiler --> models

  parser --> extraction
  parser --> models
  parser --> profiler

  resolver --> parser

  standards --> parser
  standards --> resolver

  taxonomy --> corrections
  taxonomy --> llm
  taxonomy --> parser

  graph --> parser
  graph --> resolver
  graph --> standards
  graph --> taxonomy

  vectorstore --> models
  vectorstore --> parser

  query --> graph
  query --> llm
  query --> resolver
  query --> standards
  query --> taxonomy
  query --> vectorstore

  eval --> llm
  eval --> query

  pipeline --> corrections
  pipeline --> env
  pipeline --> eval
  pipeline --> extraction
  pipeline --> graph
  pipeline --> parser
  pipeline --> profiler
  pipeline --> resolver
  pipeline --> standards
  pipeline --> taxonomy
  pipeline --> vectorstore

  web --> corrections
  web --> env
  web --> pipeline
  web --> query
```

## Structure sections

Per-module `Structure` sections inside each `src/<module>/MODULE.md` are empty in this retrofit seed. Run `/regen-map` (programmatic) after you begin curating MODULE.md skeletons; it scans code and populates the Structure sections deterministically (alphabetical, public items only) between the `<!-- BEGIN:STRUCTURE --> / <!-- END:STRUCTURE -->` markers.

## Drift notes (retrofit seed)

- All 16 MODULE.md files have `Public surface: TODO` — no declared surface to drift against. Drift check is a no-op until curation fills `Public surface` entries.
- Dependency edges above reflect retrofit *candidates* from each MODULE.md's `Depends on` section — not yet confirmed by the user. Expect tightening during architecture-phase curation.
