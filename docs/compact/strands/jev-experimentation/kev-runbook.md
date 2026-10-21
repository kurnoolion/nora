# Running a decision model locally — Kev-4B

Strand `jev-experimentation` · Verified on 2026-10-20 · a developer machine

What was actually done to get a live `/v1/systemone` endpoint and measure
NORA's `decision` arm against it. Everything below was run, not drafted.

## What Kev-4B is

A LoRA adapter (rank 16, 33.8M parameters) plus a bilinear pointer head on a
**frozen** `Qwen/Qwen3.5-4B-Base`, serving TypeSafe's public System One API.
Apache-2.0 for the adapter, the head and the base. It is the open-weights
stand-in for Jev; the point of using it is that the wire format is identical,
so swapping in hosted Jev later is a config change.

## Two traps, both hit

**`pip install kev` is the wrong package.** PyPI's `kev` is unrelated — it
pulls jupyterlab, boto3, redis and about ninety others. The decision model is
installed from the git repository. The obvious command is the wrong one.

**The Hugging Face repo has no backbone in it.** `jaredpalmer/kev-4b` is 160 MB
of adapter, head and tokeniser. The ~8 GB is `Qwen/Qwen3.5-4B-Base`, pulled
separately, and the adapter pins the base revision (`1001bb4d`). "Download
Kev-4B" means two downloads, not one.

## Setup

```bash
# 1. the server (NOT pip install kev)
git clone https://github.com/jaredpalmer/kev.git && cd kev
uv sync --extra serve          # mlx-lm is selected automatically on arm64 darwin

# 2. the weights — adapter is 160 MB, the base it pins is ~8 GB
#    Both land in the HF cache, outside this repo. Never inside it:
#    assets/hf_cache/ is gitignored because a cache was scrubbed from
#    history once already.

# 3. serve
uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b --port PORT
```

First start downloads what is missing and loads the model; be patient rather
than assuming a hang.

## Confirm it is really serving a decider

```bash
curl -s http://127.0.0.1:PORT/v1/models
```

The fields that matter are `lora` (non-null — the adapter is attached) and
`temperature` (non-zero — the calibration is applied). A plain backbone
answers every question without erroring, so these two fields are the
difference between a measurement and a fiction. `core.src.decision.head_check.
assert_remote_head` checks exactly this, and the `decision` arm refuses to run
until it passes.

Observed on this machine:

```
name=kev-latest  base=Qwen/Qwen3.5-4B-Base  lora=16
device=mps  backend=mlx  dtype=bfloat16  temperature=2.406
```

Observed on a 16 GB CUDA host (2026-10-21), served behind a reverse proxy
that strips a path prefix, so the decision provider's base URL is
`http://<decision-host>/systemone`:

```
name=kev-latest  base=Qwen/Qwen3.5-4B-Base  lora=16
device=cuda  backend=torch  dtype=bfloat16  temperature=2.41
latency per request: p50 ~185 ms, p95 ~189 ms warm (356 ms on a cold first run)
```

That host runs without the `causal_conv1d` and `flash-linear-attention`
kernels, so it uses the fallback path; latency is higher than it would be
with them installed, and correctness is unaffected. The server lists a
second card (`jev-latest`) after `kev-latest`; `assert_remote_head` checks
the card of the model named with `--decision-model`, so the order does not
matter.

## Measure NORA's decision arm against it

```bash
python -m core.src.eval.classify_cli --arm decision \
    --decision-provider systemone \
    --decision-base-url http://127.0.0.1:PORT \
    --decision-model kev-latest \
    --repeat 3
```

`--weights-dir <hf snapshot dir>` adds the local file check when the weights
happen to sit on the same host. It is optional: the authority is the server
probe, because the weights a client can see are not the weights the server
loaded (D-DRAFT-20, D-DRAFT-22).

## Hardware notes

- The card recommends **32 GB** of unified memory. It ran with **less**
  without an OOM retry, because NORA's state is one query — a few dozen
  tokens. The recommendation is sized for long documents; a classification
  workload is far cheaper. Do not read the 32 GB figure as a hard floor for
  this use case, and do not assume it holds if the state ever becomes a
  document.
- On a 16 GB CUDA GPU host the same command serves it in bf16 with fused
  kernels. `KEV_DTYPE=fp32` would need about 16 GB for weights alone and will
  not fit — bf16 is not optional there.
- `KEV_TEMPERATURE=1.0` returns raw probabilities, `KEV_DTYPE=fp32` selects
  the evaluation path, `KEV_API_KEY` turns on bearer auth. **The server is
  open by default.** Bind it inside the corporate network only; the egress
  constraint for this strand is "no external calls at all".

## The thing that most affects the result

Send a **description per option**, not bare identifiers. Same weights, same
temperature, same 50 samples:

| Options | Accuracy |
|---|---|
| bare (`feature_level`, `cross_doc`, …) | 0.460 |
| one-line gloss each | **0.760** |

`QUERY_TYPE_DESCRIPTIONS` in `core/src/decision/analyzer.py` holds them. This
is not prompt-fiddling at the margin: it is the difference between concluding
the model is unfit and concluding it is level with the rules.
