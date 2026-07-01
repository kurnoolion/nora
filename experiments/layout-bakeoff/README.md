# Layout-parser bake-off (spike)

Compare three document-layout engines — **Docling**, **PaddleOCR PP-Structure**,
and **Hiro-Smart-Doc** — on a couple of real MNO-C PDFs, to decide whether a
vision/layout model beats the current geometric pipeline for tables (and, later,
figures / API-spec pages).

This is exploratory. It lives outside `core/src/` on purpose and does **not**
touch the production pipeline. `layout_provider.py` is the normalized contract; if
one engine wins, that protocol is the shape to promote into a core module
(mirrors `LLMProvider` / `EmbeddingProvider`), mapping `LayoutBlock` →
`ContentBlock`.

## Layout

```
layout_provider.py   # LayoutProvider protocol + normalized LayoutBlock/LayoutResult + renderers
prov_docling.py      # Docling adapter (library)
prov_paddle.py       # PaddleOCR PP-Structure adapter (renders pages, per-page)
prov_hiro.py         # Hiro adapter (HTTP client to the Hiro FastAPI service)
run_bakeoff.py       # run ONE provider over PDFs -> normalized .json + .md
summarize.py         # combine all outputs -> summary.md (counts + tables side-by-side)
requirements-*.txt   # per-provider deps (install in SEPARATE venvs)
```

## Why one provider per run

Docling (torch) and PaddleOCR (paddle) have **conflicting heavy dependencies**,
and Hiro needs an external GPU service. So each provider runs in its **own venv**,
all writing to the same `--out` dir; `summarize.py` then merges the JSON.

## Run it

Put 1–2 MNO-C PDFs somewhere local (they are gitignored here). Then:

```bash
cd experiments/layout-bakeoff

# 1) Docling — its own venv
python -m venv .venv-docling && . .venv-docling/bin/activate
pip install -r requirements-docling.txt
python run_bakeoff.py /path/to/doc1.pdf /path/to/doc2.pdf --provider docling --out ./out
deactivate

# 2) PaddleOCR — its own venv (CPU paddlepaddle; swap for -gpu if you have one)
python -m venv .venv-paddle && . .venv-paddle/bin/activate
pip install -r requirements-paddle.txt
python run_bakeoff.py /path/to/doc1.pdf /path/to/doc2.pdf --provider paddle --out ./out
deactivate

# 3) Hiro — two services. On the SERVER: launch the MOSS-OCR vLLM (OpenAI-
#    compatible, ends in /v1) and the Hiro FastAPI app, wiring them together via
#    Hiro's own env: MOSS_VLLM_OCR_API=http://127.0.0.1:8088/v1 (this is the /v1
#    URL), MOSS_VLLM_OCR_API_KEY, MOSS_VLLM_MODEL.
#    Our client only needs `requests` and points at the HIRO APP (no /v1):
pip install -r requirements-hiro.txt
HIRO_BASE_URL=http://127.0.0.1:8000 \
  python run_bakeoff.py /path/to/doc1.pdf --provider hiro --out ./out
# endpoint defaults to /pdf/smart-doc; override with HIRO_ENDPOINT / HIRO_FILE_FIELD

# 4) Combine
python summarize.py ./out          # -> ./out/summary.md
```

Open `out/summary.md` in a Markdown preview (VS Code / GitHub): the counts+timing
table plus every extracted table rendered inline, provider-by-provider, so you can
judge table fidelity directly. Per-provider full dumps are in
`out/<doc>__<provider>.md`.

## What to compare

- **Tables** (the point): are borderless tables recovered with the right
  columns/rows, headers intact, cells not split/exploded?
- **Text correctness** on born-digital pages: does OCR-based re-reading introduce
  errors the current text-layer path doesn't have? (A real risk for Hiro.)
- **Figures / formulas / reading order**: relevant to the deferred
  API-spec / flow-image work.
- **Cost**: wall-clock per page, and whether a GPU/service is required.

## Caveats (read before trusting a zero result)

- **APIs drift.** Docling, PaddleOCR (2.x vs 3.x), and Hiro all change output
  shapes between versions. The version-sensitive calls are marked `# API:` /
  `# CONFIRM:` in each adapter — adjust them for what you installed. A provider
  that errors is reported as `ok=false` with the message, not crashed.
- **Hiro schema is unconfirmed.** The Hiro repo doesn't document the endpoint path
  or streamed-region JSON precisely. Open its `/docs` Swagger UI and fix the
  `# CONFIRM:` field mapping in `prov_hiro.py` to match.
- **Privacy.** Everything runs locally; `out/` and `*.pdf` are gitignored. Do not
  commit corpus PDFs or their parsed outputs. Prefer fully self-hosted engines
  (all three can be) — never send proprietary specs to a hosted API.
- **Not a scorer.** With no ground-truth labels this produces a side-by-side for
  human judgement, not an automated accuracy number.
