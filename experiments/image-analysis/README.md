# image-analysis PoC (strand: image-ingestion)

Verify that a vision-capable, OpenAI-compatible LLM can convert requirement-doc
figures into retrievable text well enough to build on: flow diagrams → Mermaid
(`sequenceDiagram` for signaling/message-sequence charts, `flowchart TD`
otherwise), image-rendered tables → markdown tables, UX flows → numbered
screen walkthroughs, everything else → a caption.

Standalone — no NORA imports. The productized version lands behind a
`VisionProvider` protocol in `core/src/extraction` after this PoC validates
the model (see the strand's STRAND.md).

## Run

```bash
pip install requests    # the only dep

export VISION_BASE_URL=http://127.0.0.1:8000/v1   # your endpoint, up to /v1
export VISION_MODEL=<model-name>
export VISION_API_KEY=<key>                        # optional; default "none"

python analyze_image.py fig1.png fig2.jpg --out ./out
python analyze_image.py /path/to/crops_dir --out ./out    # whole directory
```

Hand-picked inputs: the Docling figure crops from an extract run live under
`<env_dir>/out/extract/<mno>/<rel>/images/` — copy a representative set
(a few signaling flows, a flowchart, an image-table, a UX flow, a logo).

## Outputs (per image, under `--out`)

- `<stem>.analysis.json` — kind, title, caption, converted content, warnings,
  timing, raw model response (for prompt debugging).
- `<stem>.md` — the eyeball file: caption + content in a fenced block
  (` ```mermaid ` for flow diagrams — paste into any Mermaid renderer to
  check the diagram visually).

Console prints one line per image (`[kind] name (Ns)` + validation warnings)
and a final kind histogram.

## Validation performed

Light only: strict-JSON parse (fence/prose salvage), known `kind`, non-empty
content, Mermaid header check for flow diagrams, `|` presence for tables.
Real Mermaid syntax validation (mermaid-cli) is out of scope for the PoC.

## Knobs (env)

| Var | Default | Purpose |
|---|---|---|
| `VISION_BASE_URL` | `http://127.0.0.1:8000/v1` | OpenAI-compatible base URL |
| `VISION_MODEL` | (required) | model name |
| `VISION_API_KEY` | `none` | bearer token |
| `VISION_TIMEOUT_S` | `300` | per-image request timeout |
| `VISION_MAX_TOKENS` | `4096` | completion cap (large diagrams need room) |
| `VISION_DEBUG` | off | dump raw model responses to stderr |

## What to judge on the hand-picked set

1. **Classification** — are kinds right (esp. signaling chart vs flowchart)?
2. **Fidelity** — every node/message/cell label preserved verbatim, nothing
   invented? (Spot-check against the source figure.)
3. **Mermaid validity** — do the diagrams render?
4. **UX flows** — is the numbered walkthrough usable, or does the format need
   rethinking (the TBD case)?

Do not commit outputs or proprietary figures — `out/` is gitignored.
