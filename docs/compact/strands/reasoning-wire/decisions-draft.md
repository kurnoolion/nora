# Draft decisions — reasoning-wire

Drafts for `/close-session` triage; not yet promoted to DECISIONS.md.

## D-DRAFT-1 — The "don't think" wire form is declared per roster entry

**Context.** `_reasoning_for` hardcoded `reasoning_effort="none"` for Fast mode,
on the documented assumption (stated twice in `openai_provider.py`) that vLLM
accepts the OpenAI-standard field and injects the model's `enable_thinking`
chat-template kwarg itself, so one wire form serves every OpenAI-compatible
server. Probing the two endpoints on the internal roster disproved that on both:
a real vLLM server rejects `reasoning_effort` outright (400 `extra_forbidden`,
"Extra inputs are not permitted"), and a non-vLLM OpenAI-compatible server
accepts the field but rejects the value `"none"` (400 `literal_error`; its enum
is low|medium|high, and `low` still thinks, so no accepted value skips
thinking). `chat_template_kwargs: {"enable_thinking": false}` returned 200 and a
thinking-free answer on both.

**Decision.** `LLMProviderEntry` gains `reasoning_control`, naming the wire form
that endpoint accepts (`reasoning_effort` | `chat_template_kwargs` | `none`).
`LLMProviderEntry.reasoning_mechanism` resolves it; the provider renders the
declared form.

**Alternative considered and rejected — send `chat_template_kwargs` always.**
It is the form that works on both endpoints today, and it would be a two-line
change with no schema field. Rejected: the roster's whole premise is that its
entries are independent endpoints, and "both current boxes happen to agree" is
not a property of the design, it is a property of this month's inventory. A
hosted OpenAI-compatible endpoint that takes `reasoning_effort` and rejects
unknown keys would break, and the failure would look exactly like the one being
fixed — the toggle silently not working.

**Alternative considered and rejected — probe the endpoint and cache the
answer.** Rejected for the reason already recorded for
`supports_reasoning_control` and now doubly true: a probe only catches outright
rejection. A server can accept a field and silently ignore it, which is
indistinguishable from honouring it, so a probe cannot tell a working mechanism
from a dead one. It would also put a startup network call on the config path.
Declared, never detected — an unrecognised value is dropped at load with a
warning rather than reaching the wire.

## D-DRAFT-2 — The Ask page keeps two states; only the wire form varies

**Context.** The mechanism now differs per endpoint, which invites exposing it
(or the underlying effort levels) as a user control.

**Decision.** The Fast/Think toggle is unchanged. `reasoning_control` is
deployment configuration, not a user-facing choice, and never appears in the UI.

**Why.** This is D-216 (2026-09-02) applied to a new surface: an effort dropdown
was rejected because "users should not have to reason about effort levels" and
"two states beat five". A mechanism picker is strictly worse — it asks the user
about a wire protocol. Exact levels remain available in `golden_cli
--reasoning`, which goes through the single-provider chain, not the roster.

## D-DRAFT-3 — `chat_template_kwargs` is only ever emitted to suppress thinking

**Decision.** No `enable_thinking: true` branch exists. When `reasoning` is
unset — which is what "think" means — the payload carries neither field.

**Why.** "Think" means *let the deployment do what it is configured to do*, not
*assert that it should think*. Emitting `enable_thinking: true` would override a
deployment that had deliberately configured thinking off, which is the same
class of mistake as inventing an effort level on the model's behalf (D-216).
Written as a comment at the branch, because the symmetric-looking `true` case is
the obvious thing for a later change to "complete".

## D-DRAFT-4 — The refusal fallback resolves its own mechanism, never the primary's

**Context.** Found while implementing, not in the original report: the roster
lane built its fallback provider from `fb_entry` with no reasoning argument at
all, so a Fast question that got rerouted on a permanent refusal was answered by
a thinking model. The reroute is disclosed on the answer card; the silently
dropped mode was not.

**Decision.** The fallback resolves the asker's mode through its own entry —
`_reasoning_for(fb_entry, mode)` with `fb_entry.reasoning_mechanism`.
`build_fallback_provider` takes a matching `reasoning_control` argument for the
env-var-configured path (`NORA_LLM_FALLBACK_REASONING_CONTROL`).

**Alternative considered and rejected — inherit the primary's mechanism.**
Simpler, and it is what a single `reasoning_control` variable in the calling
function would naturally do. Rejected because it is wrong in exactly the case
that matters: the fallback is a *different box*, and on the current roster the
two endpoints reject each other's form. Inheriting would send a request the
fallback 400s on — on the one path whose entire purpose is to rescue an answer
that already failed once.

## D-DRAFT-5 — `supports_reasoning_control` stays; an unset mechanism means `reasoning_effort`

**Decision.** The boolean is retained as the support/UI declaration (the Ask
template reads `data-supports-reasoning-control` to render "Not supported"), and
`reasoning_control` is added beside it. An explicit `reasoning_control` wins and
implies support; unset falls back to `reasoning_effort` when the boolean is true.

**Why.** `reasoning_effort` is the only mechanism a pre-existing roster could
have meant, since it was the only form this code ever sent — so the fallback
preserves what those configs meant rather than silently re-pointing them at a
different wire form. It is knowingly the *broken* form for both current internal
endpoints; that is acceptable because the only committed roster is fictional
(`dgx.internal.example`), and a real deployment fixes itself by naming the
mechanism.

**Alternative considered and rejected — replace the boolean with the enum.**
One field, one source of truth, no resolution rule. Rejected as a migration this
change does not need to pay for: it would touch the Ask template, the roster
schema, every deployed `llm.json`, and two pinned tests, to express something
the added field already expresses beside it.
