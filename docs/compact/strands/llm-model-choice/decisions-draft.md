# Draft decisions — llm-model-choice

Drafts for `/close-session` triage; not yet promoted to DECISIONS.md.

## D-DRAFT-1 — vLLM serving default stays reasoning-on; the knob lives in the ASK request

**Context.** RAG synthesis takes ~15s because the Qwen3 30B model runs in
reasoning mode on our vLLM endpoint; the thinking tokens are stripped before
display, so the wait is paid for output nobody sees.

**Alternative considered and rejected.** Launching vLLM with
`--reasoning-parser <parser> --default-chat-template-kwargs '{"enable_thinking": false}'`
removes the latency server-wide with zero code. Rejected (manager, 2026-09-01):
committing the whole serving stack to non-reasoning forecloses the experiment
we are trying to run. Reasoning stays enabled by default at the server; the
control belongs per request, in the ASK lane.

**Consequence accepted knowingly.** With the serving default unchanged,
reasoning-on remains the default experience — ~15s stays the default latency
until a user turns it down. The latency decision therefore moves to what the
ASK control defaults to, which is still open.

## D-DRAFT-2 — Phase 1 scope is reasoning effort only; model choice deferred

> **SUPERSEDED 2026-09-01 by D-DRAFT-4.** Model choice IS in Phase 1, via the
> named provider picker. The reasoning below still explains why a *free-text
> model field* was wrong — one `vllm serve` process loads one model — and that
> argument survives: the picker chooses an ENDPOINT, each of which pins its own
> model, rather than asking for a model name against a single endpoint. Kept
> rather than deleted because the constraint it records is still true, and a
> future roster entry that lists several models per endpoint must re-read it.
> Promote D-DRAFT-4; promote this one only as historical context, if at all.

**Context.** The original framing was "choose a model and its reasoning level".

**Decision.** Phase 1 ships one control: reasoning effort. No model picker.

**Why.** A `vllm serve` process loads a single model and `GET /v1/models`
returns exactly one entry, so a model picker against the current deployment is
a control with nothing to pick — any value but the loaded one 404s. Model
choice becomes meaningful only when a second endpoint exists, which is the same
point at which the provider roster earns its keep (Phase 2). Deferred, not
withheld.

**Consequence.** Provenance still records the model that answered — recorded,
not chosen — alongside the reasoning level, reusing `test_feedback.llm_model`
and `lane_config`. No schema migration.

## D-DRAFT-3 — No `LLMProvider` Protocol change; reasoning is injected at construction

**Decision.** `complete(prompt, system, temperature, max_tokens)` is untouched.
The reasoning level is an optional constructor argument on
`OpenAICompatibleProvider`, added to the request body.

**Why.** `core/src/llm/MODULE.md` marks a Protocol signature change as a
coordinated break across taxonomy, query and eval. Construction-time injection
avoids that break entirely.

**Corrected 2026-09-01 (same session).** An earlier version of this draft
claimed the ASK lane already builds its provider per request, so the change was
free. That was wrong. The Ask page is `/test` (`playground.py`), and its
pipeline lane reuses a provider cached with the pipeline on
`app.state.query_pipeline` until process restart. Construction-time injection
still holds, but it costs real plumbing: a keyword-only `synthesizer=None`
override on `QueryPipeline.query()`, a per-query provider built in
`_run_query_sync`, and the level threaded from the form through
`run_query_background`.

**Rejected alternative (2).** Mutating the cached provider before each call
(`llm.reasoning = x`). Queries run via `asyncio.create_task` →
`asyncio.to_thread`, so concurrent asks at different levels would clobber each
other and record provenance that does not match the answer.

**Rejected alternative.** A per-model capability registry so the UI only offers
supported knobs. Unnecessary: vLLM accepts the OpenAI-standard
`reasoning_effort` and maps it to `enable_thinking` server-side, so the
normalization we would have hand-maintained already exists.

## D-DRAFT-4 — Named provider roster + a Fast/Think toggle, instead of an effort dropdown

**Context.** Phase 1 shipped a 4-level reasoning select, and a "primary /
fallback" endpoint select was half-built on top of it. Reviewing the UX: users
should not have to reason about effort levels, and "primary/secondary" says
nothing about which infrastructure answers a question.

**Decision.** One optional `providers` list in `config/llm.json` — named entries
(`{id, name, base_url, model, api_key_env, supports_reasoning_control, default_mode}`) —
surfaced on the Ask page as a provider select plus a two-way Fast/Think toggle.
Fast sends `reasoning_effort: "none"`; Think sends no reasoning field at all.

**Why this shape.**
- A name like "130B — DGX" tells the asker what will answer; "primary" does not.
- Two states beat five: the question is "should it think?", not "how much".
- Think sending *nothing* means we never assert an effort level the deployment
  did not choose for itself.
- Keys are referenced by env-var name, never written in a committed file.

**Rejected alternative — reasoning=none implies the fallback endpoint.** Raised
as the minimal way to reach a reasoning-capable model when the primary may not
support it. Rejected on three grounds: it welds two independent axes into one
control, so a reader of two answers cannot tell which variable moved; it gives
`RefusalFallbackProvider.used` two meanings (refusals and deliberate routing),
and eval reads that counter as `fb_pre` / `fb_delta`; and `llm_identity` would
name one model while another answered.

**Capability is declared, not detected.** No OpenAI-compatible endpoint
advertises reasoning support, and a probe only catches outright rejection — a
server can accept `reasoning_effort` and silently ignore it, which is
indistinguishable from honouring it. Observed both in one session (Ollama's
`/v1` honoured it; llama.cpp would likely ignore it). So each entry declares it,
and the toggle renders disabled with a reason where it would do nothing.

**Consequences.**
- No roster configured = today's behaviour exactly; the controls are not
  rendered and the single-provider chain runs unchanged. No migration.
- A roster-built provider is not refusal-wrapped: the roster names *which*
  endpoint answers, so silently rerouting would defeat the choice just made.
- Ask flow only. `golden_cli` keeps explicit `--reasoning {none,low,medium,
  high}` — an analyst running a campaign wants the exact level, not a toggle.
- Routing user traffic to a named box (e.g. the DGX) changes its load profile.
  That is a deployment decision, surfaced in the PR rather than assumed.
