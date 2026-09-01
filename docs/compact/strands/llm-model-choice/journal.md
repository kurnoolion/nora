## 2026-09-01 — Strand opened; plan for per-question reasoning control

### Done this session
- Opened the strand. Deliverable for this phase is a **plan to discuss with the
  manager**, not implementation. karpathy-guidelines adopted as a design
  constraint (minimum change, no speculative configurability, no DIY where an
  existing seam works).
- Investigated how LLM configuration actually works, rather than assuming it was
  generic:
  - `LLMProvider.complete()` has no reasoning parameter, and `core/src/llm/MODULE.md`
    marks a signature change as a coordinated break across taxonomy, query, eval.
  - Configuration resolves **one** provider (D-044/D-053 chain), flat fields in
    `core/src/web/config_schema.py`. No roster exists.
  - The web ASK lane already builds its provider **per request**
    (`core/src/web/routes/query.py:244`, called at `:464`, uncached).
  - Key finding: the existing reasoning code (`_strip_reasoning`,
    `NORA_LLM_REASONING_SENTINEL`) strips chain-of-thought *after* generation.
    The ~15s is paid in full. This explains why the sentinel work never made
    answers faster.
  - Provenance needs no migration: `test_feedback.llm_model` and `lane_config`
    already exist and `record_qa()` already accepts them.
- Verified the vLLM reasoning knob against the docs instead of memory
  (Context7, vLLM stable `features/reasoning_outputs`): per-request
  `chat_template_kwargs.enable_thinking`, the OpenAI-standard `reasoning_effort`
  which vLLM maps to `enable_thinking` server-side, and the server-wide
  `--default-chat-template-kwargs` flag.
- Wrote `plan.md` (source of record) and `plan.html`, published as a private
  artifact (URL in STRAND.md).
- Drafted D-DRAFT-1..3 in `decisions-draft.md`.
- Committed as `d8cbe99` on branch `llm-model-choice`. Nothing on `main`.

### Scope narrowed twice, both by decision
- **Serving default stays reasoning-on** (manager). The server-wide off switch
  would remove the latency with zero code but forecloses the experiment. Knob
  goes in the ASK request instead.
- **Reasoning effort only; model choice deferred.** A `vllm serve` process loads
  a single model, so a picker today has nothing to pick. Model choice lands with
  the Phase 2 roster, when a second endpoint makes it meaningful.

### Course corrections worth remembering
- I first proposed a per-model **capability registry** so the UI would only offer
  supported knobs. The vLLM docs killed it: `reasoning_effort` is standard and
  the server does the mapping. The normalization already existed.
- I first put a **model field** in Phase 1. One process, one model — it would
  have shipped a control whose only valid value was the loaded model.
- Both were caught by checking the source rather than reasoning from the shape
  of the request.

### Next
- Probe the deployed vLLM: `reasoning_effort` vs `chat_template_kwargs`
  (`llm_debug --complete`, `NORA_LLM_DEBUG_RAW=1`). Answers open question 1
  regardless of version.
- Walk the plan with the manager. The decision that matters is **what the ASK
  control defaults to** — with the serving default unchanged, ~15s stays the
  default experience until a user turns it down.
- Then Phase 1 implementation, or `/land-strand` if the plan is rejected.

### Flags
- Plan is **unreviewed by the manager**. All three drafted decisions are
  provisional until that conversation happens.
- `plan.md` and `plan.html` carry the same content in two files. They were
  written and edited together this session; a future edit must touch both, and
  `plan.html` is the file to republish to the recorded artifact URL.
- Branch `llm-model-choice` is committed but **not pushed**. The artifact is
  private — it must be shared from the page's share menu before the manager can
  open it.
