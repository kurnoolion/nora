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
coordinated break across taxonomy, query and eval. The web ASK lane already
builds its provider per request (`core/src/web/routes/query.py:244`, called at
`:464`, uncached), so construction-time injection needs no new plumbing and
costs nothing at the seam.

**Rejected alternative.** A per-model capability registry so the UI only offers
supported knobs. Unnecessary: vLLM accepts the OpenAI-standard
`reasoning_effort` and maps it to `enable_thinking` server-side, so the
normalization we would have hand-maintained already exists.
