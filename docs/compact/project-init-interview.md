# NORA — project-init interview

**Mode:** retrofit
**Date:** 2026-04-21
**Source of prefill:** `docs/compact/design-inputs/` (TDD v0.6, README, SESSION_SUMMARY, SETUP_OFFLINE) + `docs/compact/retrofit-snapshot.md`

## Design inputs

- `TDD_Telecom_Requirements_AI_System.md` (v0.6, April 2026) — canonical design
- `README.md` — environment setup, CLI reference, already-implemented behaviors
- `SESSION_SUMMARY.md` — key design decisions, PoC progress, collaboration protocol
- `SETUP_OFFLINE.md` — offline Ollama + HuggingFace install instructions

## Retrofit snapshot

See `docs/compact/retrofit-snapshot.md`. Scan detected 16 Python packages under `src/`, 99+ public classes, consistent CLI-per-module pattern, and web UI + corrections surfaces.

---

## 1. What we're building

**NORA** (Network Operator Requirements Analyzer) — AI system for intelligent querying, cross-referencing, and compliance analysis of US Mobile Network Operator (Verizon, AT&T, T-Mobile) device requirement and test-case specifications, across multiple MNOs and multiple quarterly releases.

Core architecture: **unified Knowledge Graph (routes the question) + targeted vector RAG (ranks within scope) + requirement hierarchy (structural context for LLM synthesis)**. Pure RAG was tried and failed — cannot handle cross-document dependencies, destroys hierarchical structure, misses standards context, no MNO/release awareness.

Core capabilities:

- Single-doc and cross-doc Q&A
- Cross-MNO comparison ("compare VZW vs TMO IMS registration")
- Standards comparison ("how does VZW differ from 3GPP for T3402?")
- Release delta / version diff ("what changed in VZW eSIM from Oct 2025 to Feb 2026?")
- Traceability (requirement ↔ test case)
- Compliance agent (post-PoC)

## 2. How we're building

- **Language**: Python. No `pyproject.toml` / `setup.py`; `requirements.txt` + `src/` layout with `__init__.py` per package.
- **Stack**:
  - Web UI: FastAPI + uvicorn + jinja2 + HTMX + Bootstrap 5 (zero npm/JS build; vendored for offline)
  - Knowledge graph: networkx (in-memory)
  - Vector: chromadb + sentence-transformers (offline-friendly via `hf_offline.py`)
  - LLM: Ollama (Gemma 4 E4B) via `httpx` locally; Claude / proprietary LLM as alternative providers
  - Extraction: pymupdf, pdfplumber, python-docx, openpyxl, xlrd, olefile, Pillow
  - Persistence: aiosqlite for metrics + jobs DB
- **Module convention**: each `src/<package>/` directory with `__init__.py` is a module. One `MODULE.md` per package.
- **Visibility mapping**: public surface = top-level identifiers (class / def) without a leading underscore. CLIs exposed as `<module>_cli.main`. Per-package `__init__.py` may enumerate `__all__` for the curated surface (only `src/corrections/` does today).
- **Abstractions via Protocol**: `LLMProvider`, `EmbeddingProvider`, `VectorStoreProvider` — structural typing, swap by instance (no inheritance).
- **Tests**: `tests/test_*.py` (pytest), one file per module.
- **Ops surfaces outside `src/`**: `setup_env.sh`, `create_presentation.py`, `update_presentation.py`.

## 3. Stakeholder map & contribution surfaces

| Stakeholder | Role | Tech comfort | Contribution type | Required interface | Feedback loop |
|---|---|---|---|---|---|
| Solution architect (user) | AI + telecom lead; drives design & reviews | Expert | Design edits, runs pipelines, curates profiles / taxonomies | CLI + Web UI + direct JSON edits under `<doc_root>/corrections/` | Compact RPT / FIX / QC reports |
| Python devs | Implement pipeline stages & modules | Strong | Code + tests | CLI + pytest + MODULE.md | PR review + `drift-check` |
| Telecom domain experts / reviewers | Validate extracted profiles, taxonomies, and query answers | Limited CLI / Linux | Correct `profile.json` / `taxonomy.json`; flag bad citations | Web UI (Bootstrap 5 + HTMX), no terminal needed | FIX reports pasted in chat |
| Windows-PC team members | Submit pipeline jobs, browse shared docs, run queries | Varies | Job submission, Q&A | Web UI + shared folder browser with Windows↔Linux path mapping | SSE log stream, MET metrics |
| LLM (in-loop) | Feature tagging, query synthesis, citation | — | Swappable via `LLMProvider` Protocol | Structured prompts, thinking mode on production LLM | `OllamaProvider.last_call_stats` |

**Implied surfaces** (flagged by retrofit scan, confirmed):

- Web UI surface (FastAPI routes + HTMX partials under `src/web/`)
- Corrections override surface (`<doc_root>/corrections/*.json` overrides auto-generated output)
- Compact report formats (RPT, MET, FIX, QC) as chat-pasteable artifacts
- Per-module CLI pattern
- Reverse-proxy deployment (`root_path` config, `PathMapping`)

## 4. Domain constraints

- **On-premise only in production** — proprietary MNO requirements cannot leave the corporate network; no external cloud AI.
- **Offline / air-gapped install is first-class** — Ollama models and HuggingFace embeddings must install without internet (see `SETUP_OFFLINE.md`, `hf_offline.py`). Web UI assets are vendored.
- **Resource-constrained inference** — PoC on personal PC (16 GB RAM, CPU-only, Intel Ultra 9 185H). Work laptop has 16 GB NVIDIA GPU. Model picker auto-selects based on detected hardware.
- **Production LLM** — proprietary foundational model (thinking mode, context possibly up to 2M). OSS on-prem models permitted.
- **Data sensitivity** — production data is proprietary MNO requirements. PoC uses only publicly available VZW OA docs (5 PDFs, gitignored).
- **Multi-format docs** — PDF, DOC, DOCX, XLS, XLSX, with embedded OLE objects, images, diagrams. DOC is converted to DOCX via LibreOffice headless.
- **Scale** — PoC = 5 VZW docs (Feb 2026). Production = 3+ MNOs × 4+ releases / year × hundreds of docs = several GB per release.

### 4a. Chat-mediated remote collaboration

Production and work-laptop runs execute on proprietary docs and eval sets the AI partner cannot see, and full artifacts cannot be pasted through chat. Every cross-environment collaboration surface must therefore work from short, no-proprietary-content text the user can type or paste.

**Design implications (already implemented; captured here as durable requirements):**

1. **Stable error codes with paired verbose logs.** Every failure emits a unique prefixed code (`EXT-E001`, `PRF-W001`, …). Verbose logs stay on disk for self-service debugging. The collaboration surface is `code + user observation`; the AI interprets against a code catalog.
2. **Compact report formats per artifact type** (RPT / MET / FIX / QC — one record per line, no internal document content). Originals stay on disk untouched; compact reports are an additive pasteable layer.
3. **Compact QC format** for human feedback on intermediate artifacts (document profile, taxonomy, eval results) — pasteable, no proprietary content.
4. **Compact FIX format** for human-edited overrides (profile, taxonomy, eval Q&A corrections). The pipeline reads authoritative edits from `<doc_root>/corrections/*.json`; the compact FIX format is the chat-pasteable summary of those edits.

**Rule for future design work:** any new artifact type that crosses the AI-collaboration boundary needs a compact format defined alongside it. `drift-check` and `close-session` should hard-flag a new artifact that lacks a compact-format counterpart.

## 5. LLM access model

- **PoC runtime LLM**: Claude Code (AI partner, this session) for design + code + review; local Ollama (Gemma 4 E4B) for in-pipeline LLM calls (feature tagging, synthesis).
- **Production runtime LLM**: proprietary on-premise LLM. Same `LLMProvider` Protocol — swap by instance.
- **What the AI partner may see in this dev environment**: source code, TDD, README, design-inputs markdown, COMPACT scaffold.
- **What the AI partner must not see**: MNO PDFs (gitignored), any proprietary MNO document content, production run outputs, work-laptop run outputs, and proprietary eval sets. Collaboration on these goes through compact reports + error codes only, never raw artifacts.
- **Runtime LLM visibility**: extracted DocumentIR + graph-scoped chunks + standards context. Acceptable on PoC (public VZW docs). On-prem-isolated in production.

## 6. Pain points

- **Citation quality** — earlier iteration produced weak citations; mitigated by few-shot prompting + context fallback (`src/query/synthesizer.py`, `src/llm/`). Regression guardrail is the eval harness.
- **Extraction fidelity** — mixed-font blocks, tables, embedded objects. DocumentProfiler + font-group splitting + table-region deduplication.
- **Cross-reference resolution** — internal / cross-plan / standards references have separate resolution paths with different failure modes.
- **Offline install friction** — `huggingface_hub` / `httpx` behavior on restricted networks required vendoring HF cache and workarounds.
- **Accuracy regression at scale** — A/B eval harness (`src/eval/`) is the guardrail; relies on user-supplied Q&A from Excel.
- **Remote artifact opacity** — AI partner has no visibility into production / work-PC artifacts. Mitigated by error-code catalog + compact report formats. Any new artifact that lacks a compact counterpart re-opens this gap.
- **Performance at scale** — GBs per release; pipeline must be re-runnable and incremental (stage-scoped execution in `pipeline/runner.py`).

## 7. Artifact preferences

- **Design docs**: Markdown (TDD is canonical source of truth; kept at repo root as `TDD_Telecom_Requirements_AI_System.md`, copied into `design-inputs/` as archival input for COMPACT phases).
- **Profiles / taxonomies / manifests**: JSON, human-editable. Overrides live under `<doc_root>/corrections/*.json`; pipeline auto-picks these up on re-run.
- **Status reports**: compact one-line-per-record formats — `RPT` (pipeline), `MET` (metrics), `FIX` (corrections), `QC` (quality check). Safe to paste in external chat; must contain no internal document content.
- **Error codes**: stable prefixed codes (`EXT-`, `PRF-`, `PRS-`, `RES-`, `TAX-`, `STD-`, `GRA-`, `VEC-`, `EVL-`, …) with catalog in `src/pipeline/error_codes.py`.
- **CLIs**: per-module (`<module>_cli.py` with `main()` entrypoint) rather than one dispatching CLI.
- **Metrics**: persistent SQLite at `web/nora_metrics.db`. Five categories — REQ (endpoint timing), LLM (model performance), PIP (stage timing), RES (CPU / RAM / GPU sampling), MET (custom).
