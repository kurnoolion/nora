# Plan — user-selectable LLM model + reasoning level

**Strand:** llm-model-choice · **Status:** proposal for review · **Date:** 2026-09-01

## 1. Two problems, not one

The trigger was RAG synthesis taking ~15s. Investigation traced it to the Qwen3
30B model running in reasoning mode on our vLLM endpoint. That produced a
feature idea: let the user choose model and reasoning level per question.

These are separate and should be decided separately:

| | Problem | Where it is addressed |
|---|---|---|
| **A** | Synthesis latency ~15s — thinking tokens we never show | Per-request, via the Ask knob. |
| **B** | No way to compare models / reasoning settings | Per-question controls. This plan. |

A server-wide off switch exists — launching vLLM with
`--reasoning-parser <parser> --default-chat-template-kwargs '{"enable_thinking": false}'`
would remove the latency with zero code. **We are deliberately not using it.**
Decision (manager, 2026-09-01): the vLLM server default stays reasoning-**on** for our
models; the knob belongs in the Ask lane, per request, so we can try both rather
than commit the whole serving stack to one mode.

Consequence to accept knowingly: with the server default unchanged, reasoning-on
remains the default experience, so **~15s stays the default latency until a user
turns it down**. That makes §8 Q2 — what the Ask control defaults to — the real
latency decision, not the server flag.

## 2. What exists today

Verified in code, not assumed:

- **`LLMProvider` Protocol** (`core/src/llm/base.py`) —
  `complete(prompt, system, temperature, max_tokens) -> str`. No reasoning
  parameter. `core/src/llm/MODULE.md` marks a signature change as a coordinated
  break across taxonomy / query / eval. **We do not touch it.**
- **One active provider, not a roster.** Resolution chain (D-044 / D-053):
  Config-page DB > `NORA_LLM_*` env > `config/llm.json` > environment JSON >
  default. Fields are flat and hand-curated in `core/src/web/config_schema.py`.
- **The Ask page is `/test` (`core/src/web/routes/playground.py`)**, not
  `/query`. The nav item "Ask Requirement Questions" points at `/test`.
- **It has two synthesis lanes, with opposite caching behaviour** — and this is
  the plumbing cost of the whole feature:
  - *Pipeline lane* — `_run_query_for_test` → `_run_query_sync` →
    `_get_or_build_pipeline` (`core/src/web/routes/query.py:513`). The provider
    **and** the `LLMSynthesizer` holding it are built once inside
    `_build_pipeline` and cached on `app.state.query_pipeline` until process
    restart. A per-question knob cannot reach it without a change here.
  - *Select-synth lane* — `_select_synth_synthesize`
    (`core/src/web/routes/playground.py:683`) builds a provider per call and
    calls `complete()` directly. Free.
  - Mutating the cached provider per request is **not** an option: queries run
    via `asyncio.create_task` → `asyncio.to_thread`, so concurrent asks at
    different levels would clobber each other and mislabel provenance.
- **Existing reasoning code strips, it does not disable.** `_strip_reasoning`,
  `NORA_LLM_REASONING_SENTINEL`, `FINAL_ANSWER_MARKER` in
  `core/src/llm/openai_provider.py` discard chain-of-thought *after* generation.
  The latency is still fully paid. This is why the sentinel work did not make
  answers faster.
- **Provenance columns already exist.** `test_feedback.llm_model` and the
  `lane_config` JSON column in `core/src/web/feedback_db.py`; `record_qa()`
  already accepts `llm_model`. No migration needed to record what produced an answer.

## 3. The reasoning knob (vLLM)

From vLLM stable docs, `features/reasoning_outputs`:

- **Per request, Qwen3:** `"chat_template_kwargs": {"enable_thinking": false}`
  in the chat-completions body. Broadly supported, older path.
- **Per request, normalized:** `"reasoning_effort": "none" | "low" | "medium" | "high"`.
  vLLM maps it to `enable_thinking` itself — `none` → false, the rest → true.
- **Server default:** `--default-chat-template-kwargs '{"enable_thinking": false}'`
  (problem A above).

`reasoning_effort` is the OpenAI-standard field, so the normalization we would
otherwise hand-roll already exists server-side. **No capability registry, no
per-model table.** One passthrough field.

Support for `reasoning_effort` is vLLM-version-dependent. **Step 1 of Phase 1 is
a 5-minute probe** (`python -m core.src.llm.llm_debug --complete` with each form,
`NORA_LLM_DEBUG_RAW=1`): confirm which the deployed server accepts, and that
latency actually drops. If `reasoning_effort` 400s, we send `chat_template_kwargs`
instead — same UI, different body key.

## 4. Phase 1 — the minimum that solves the ask

Scope: Ask page (`/test`), openai-compatible provider, one endpoint,
**reasoning effort only**. No model picker — see §6 for why that is a Phase 2
concern. Both of the page's synthesis lanes are in scope.

1. **Probe** the deployed vLLM for `reasoning_effort` vs `chat_template_kwargs`.
   *Verify:* one form returns 200 with visibly lower latency and empty reasoning.
2. **`OpenAICompatibleProvider`** takes an optional `reasoning` constructor arg
   and adds the corresponding key to the request body. Protocol untouched.
   *Verify:* unit test asserts the body carries the field when set, and is
   byte-identical to today when unset.
3. **`_build_llm_from_env_or_default(reasoning=None)`** — the request value
   overrides the resolved chain; `None` keeps today's behaviour exactly. The
   model stays whatever the chain resolves.
   *Verify:* existing ASK request with no override resolves the same provider.
3b. **Decouple the LLM from the cached pipeline.** `QueryPipeline.query()` takes
   a keyword-only `synthesizer=None` override (two call sites use
   `self._synthesizer`: `core/src/query/pipeline.py:400` and `:630`). When a
   request carries a reasoning level, `_run_query_sync` builds a provider +
   `LLMSynthesizer` for that query and passes it through; the expensive pipeline
   (graph, embedder, Chroma, BM25) stays cached. Construction is cheap —
   `OpenAICompatibleProvider.__init__` does no network, unlike `OllamaProvider`.
   *Verify:* cold-start cost is unchanged for a second query, and a query with
   no reasoning override still uses the cached provider.
3c. ~~**Thread the level through the job path.**~~ **Not needed.** The Ask page
   calls `_run_query_sync` directly through `_run_query_for_test`; only the
   older `/query` page goes through `JobQueue`. The level travels as a function
   argument from the form handler to the lane runners. `jobs.py` untouched, and
   the `/query` page keeps its existing behaviour via the `None` default.
3d. **Both lanes honour the knob** — the select-synth lane already builds per
   call, so it takes the level directly.
   *Verify:* both lanes answer the same question at reasoning=none.
4. **Two ASK page controls** — a **provider** select showing named entries from
   an optional `providers` list in `config/llm.json` ("130B — DGX", "14B —
   internal"), and a **Fast / Think** toggle. Fast sends
   `reasoning_effort: "none"`; Think sends no reasoning field, so the
   deployment's own behaviour applies — we never invent an effort level on its
   behalf. Blank mode = the provider's declared `default_mode`.
   *Superseded the original 4-level select (never shipped): users should not
   have to reason about effort levels, and "primary/secondary" told them
   nothing about which infrastructure answers.*
   **Capability is declared, not detected** (`supports_reasoning_control` per entry) —
   no OpenAI-compatible endpoint advertises it, and probing only catches
   outright rejection; a server can accept `reasoning_effort` and silently
   ignore it. The toggle renders disabled, with the reason, on an entry that
   declares no support.
   **No roster configured = the pre-roster page unchanged**, controls not
   rendered.
   *Verify:* Fast is measurably faster than Think on a reasoning-capable
   provider, and picking each entry logs a different model.
5. **Record what answered** — pass the served `llm_model` and put the provider
   id + mode in `lane_config` on `record_qa()`. Existing columns, no migration.
   The model is now chosen (by provider), and recorded either way. Note `record_qa` is called from
   `playground.py` only (four sites); `query.py` does not record.
   *Verify:* two asks at different reasoning levels produce two rows that differ.

Not in Phase 1: model choice, provider roster, Ollama reasoning, eval,
enrichment, taxonomy.

## 5. Phase 2 — eval (different shape) — BUILT 2026-09-01

Built on branch `llm-model-choice-eval`. Eval is **not** the ASK control moved
sideways. Golden-eval has
snapshot freeze and a serving-env rule (`docs/golden-eval-guide.md`). A
per-question override reaching an eval run would silently break cell
comparability. Correct shape: model + reasoning are **properties of a campaign
cell**, fixed for the run and recorded in the campaign manifest — an arm to flip,
not a control to twiddle. Designed when we get there, not now. Phase 1 must not
expose an override that eval can inherit by accident.

## 6. Phase 3 — the provider roster (awaiting manager feedback)

The horizontal goal — "LLM providers become a component anyone configures" —
means named provider entries in settings (name, provider type, model, base_url,
api_key), with ASK and later components choosing one.

**Model choice belongs here, not in Phase 1.** A `vllm serve` process loads a
single model; `GET /v1/models` on that endpoint returns exactly one entry. A
model picker against today's deployment would be a control with nothing to pick
— any value but the loaded one gets a 404. Model choice becomes meaningful the
moment there is a second endpoint, which is the same moment the roster does. If
an endpoint later serves several models, the picker is populated from
`GET /v1/models` rather than typed by hand.

**Honest position:** the stated problem does not require this. One vLLM endpoint
plus the reasoning knob solves the ask. The roster is worth building when we
genuinely have several endpoints to switch between, and it is a real migration
of `config_schema.py` from flat fields to a list.

Recommendation: ship Phase 1, run the experiments it enables, build the roster
when a second endpoint actually exists. Phase 1 does not block it — adding a
model field alongside the reasoning field is the same code path.

## 7. Non-goals

- No `LLMProvider` Protocol change.
- No reasoning control for `OllamaProvider` (native API, different knob).
- No change to `_strip_reasoning` — harmless with thinking off.
- **No model picker.** One vLLM process serves one model, so there is nothing
  to choose until a second endpoint exists (§6). Model choice is deferred, not
  withheld.
- No new settings mechanism — Phase 2 extends `config_db` / `config_schema.py`.

## 8. Open questions

1. Deployed vLLM version — decides `reasoning_effort` vs `chat_template_kwargs`.
   Resolved by the Phase 1 probe regardless.
2. **What does the Ask control default to?** Server default stays reasoning-on,
   so "no override" means every user keeps paying ~15s. Options: inherit the
   server default (status quo, slow-by-default), or default the Ask lane to
   `none` and let people opt into reasoning. This is now the latency decision.
3. Phase 2 roster: build now, or when a second endpoint exists?
4. Team-mode gate: ASK gains request fields, not new routes — confirm the gate
   ON path still passes before calling Phase 1 shipped (CLAUDE.md branch rule).
