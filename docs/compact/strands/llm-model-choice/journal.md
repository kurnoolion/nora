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

## 2026-09-01 (later) — Correction: the plan pointed at the wrong module

### Done
- Started Phase 1 implementation and found two errors in yesterday's plan
  before writing any code.
- **The Ask page is `/test` → `core/src/web/routes/playground.py`**, not
  `/query` → `query.py`. The nav item "Ask Requirement Questions" links to
  `/test`. The plan, the journal, D-DRAFT-3 and the published page all named
  the wrong module.
- **"The ASK lane builds its provider per request" was false for the lane that
  matters.** The Ask page has two synthesis lanes:
  - Pipeline lane (`_run_query_for_test` → `_run_query_sync` →
    `_get_or_build_pipeline`, `query.py:513`) — provider *and* `LLMSynthesizer`
    are built inside `_build_pipeline` and cached on `app.state.query_pipeline`
    until process restart.
  - Select-synth lane (`playground.py:683`) — builds a provider per call.
    Free, as originally claimed.
- Ruled out the shortcut: mutating the cached provider per request is a data
  race (queries run `asyncio.create_task` → `asyncio.to_thread`), and it would
  mislabel provenance — the exact failure the provenance step exists to prevent.
- Corrected `plan.md` §2 and §4 (new steps 3b/3c/3d), corrected D-DRAFT-3 with
  the correction noted in place, and republished `plan.html`.

### Consequence for Phase 1
- No longer "the seam is free". Still no Protocol change, but the plumbing is:
  a keyword-only `synthesizer=None` override on `QueryPipeline.query()` (two
  call sites, `pipeline.py:400` and `:630`), a per-query provider + synthesizer
  in `_run_query_sync`, and the level threaded from the form POST through
  `run_query_background`. Passed as an argument, so `JobQueue.submit` and the
  jobs schema stay untouched.
- `_run_query_sync` reads `llm.call_count` before/after for timing; a per-query
  provider starts at zero, so that arithmetic has to follow the new instance.

### Next
- Implement Phase 1 against the corrected map.
- Probe deferred: no infra access from outside. Implementing `reasoning_effort`
  on the assumption of a recent vLLM, with `chat_template_kwargs` kept one line
  away as the fallback.

### Flags
- The published page and plan carried the wrong module for a day. If the plan
  was already shown to anyone, the corrected version is the one to re-share.

## 2026-09-01 (later still) — Phase 1 implemented

### Done
- Implemented Phase 1 against the corrected map. `reasoning_effort` chosen over
  `chat_template_kwargs` on the strength of a recent vLLM; the fallback is one
  line, named in a comment at the payload site.
- `OpenAICompatibleProvider(reasoning=...)` adds `reasoning_effort` to the
  request body; omitted entirely when unset, so the default path is unchanged.
  `reasoning` property added for provenance.
- `PipelineContext.llm_reasoning` threads the level into provider construction.
- `QueryPipeline.query(..., synthesizer=...)` — keyword-only per-call override
  at both synthesize sites. This is the decoupling step: a request with a
  reasoning level gets its own provider + `LLMSynthesizer` while the expensive
  pipeline stays cached.
- `_run_query_sync(reasoning=...)` builds that per-query provider and falls back
  to the cached one with a warning when no real LLM resolves. `llm_calls_before`
  now measures the per-query instance.
- Ask page: `_form_reasoning()` validates against
  `("none", "low", "medium", "high")` and degrades an unknown value to "" rather
  than failing the question. Both handlers (`/api/test/ask`,
  `/api/test/ask-stream`) read it; NORA, SIRA and select-synth lanes all honour
  it. A `reasoning` select was added to the Ask form's Options panel.
- Provenance: `reasoning_effort` rides the existing `lane_config` column for
  both lanes. No migration.

### Verified
- 15 new tests, all passing: provider payload with/without the field and across
  all four levels; the synthesizer override replacing the cached one for exactly
  one call and not persisting; form validation including the unknown-value case.
- Full suite: 1808 passed. Baseline before these changes failed the same 8
  tests (`test_web_config` x6, `test_enrich_overlay_store`,
  `test_embedding_ollama`) — zero regressions, all 8 pre-existing.
- Team-mode gate: no new routes, and `/test` + `/api/test` are already
  prefix-allowed. The control still applies under the gated SIRA-only lane.

### Corrected from the plan
- Step 3c (thread the level through the job queue) turned out unnecessary. The
  Ask page calls `_run_query_sync` directly; only the older `/query` page uses
  `JobQueue`. `jobs.py` untouched.

### Next
- Unverified against a real endpoint — no infra access from outside. On the
  internal network: ask the same question at default and at `none`, confirm the
  latency drop and that `reasoning_effort` isn't rejected. If it 400s, swap the
  payload line for `chat_template_kwargs`.
- Then decide the control's default value (still the real latency decision).

### Flags
- **No end-to-end run has happened.** Everything above is unit-tested and
  reasoned from the vLLM docs; nothing has spoken to a live model.

## 2026-09-01 (session close) — Wiring gap, rebase, cosmetics

### Done
- **Wiring gap closed.** The Phase 1 commit claimed every lane honoured the
  level; only the merged path read the form field. The legacy `sira_retrieval`
  branch and `POST /api/test/synthesize-group` still ignored it and would have
  written a `lane_config` that looked like an endpoint-default answer whatever
  the asker chose. All six synthesis paths now wired.
- **MODULE.md contracts** updated in-branch for llm / query / pipeline / web,
  per the CLAUDE.md branch rule. The llm entry carries an invariant that
  reasoning is injected at construction and never through `complete()` — the
  thing a future contributor would otherwise "fix" by widening the Protocol.
- **Rebased onto `origin/main`** (`a0d5cd3`, the `req-id-bubbles` merge). One
  conflict, in `core/src/web/MODULE.md`: both sides had edited the same Routers
  line. Resolved by keeping both additions — their `GET /api/req/{req_id}` bubble
  route and our `reasoning` form-field note. `playground.py` and
  `templates/test/index.html` auto-merged; both features verified present after.
  Force-pushed with `--force-with-lease`.
- **Ran the app** at `127.0.0.1:8000/test` against `~/work/env_demo` (the sample
  corpus lives outside the repo; `config/web.json` ships `env_dir: ""`).
- **Exercised the real provider class.** This machine's shell sets
  `NORA_LLM_PROVIDER=ollama`, which sits above `config/llm.json` in the chain and
  forces the legacy native path — nothing to do with how the team deploys.
  Hanif pointed out Ollama also serves an OpenAI-compatible surface; the probe
  confirmed it, so re-pointing at `http://localhost:11434/v1` exercises
  `OpenAICompatibleProvider` itself. The log then shows both providers:
  `reasoning=<default>` (cached at pipeline build) and `reasoning=none` (built
  for the one query). That is the decoupling working end to end on the class the
  deployment actually uses. It also made a planned fake-endpoint harness
  unnecessary.
- **Cosmetics.** Action bar right-aligned, reading into the primary action:
  Options · name · reasoning · Ask. The reasoning select moved out of the
  collapsed Options panel — a per-question choice belongs at the moment of
  asking. Moved, not copied: two inputs named `reasoning` would both submit.
  Sticky across asks, restoring a stored value only if the select still offers
  it, so the control can never sit blank while the server uses the default.
- **Deleted the `reasoning` property** added in Phase 1. Justified as provenance,
  but provenance records the form value, so its only readers were its own tests.

### Verified
- Full suite 1840 passed. Zero regressions: `origin/main` alone fails the same 8
  (`test_web_config` x6, `test_enrich_overlay_store`, `test_embedding_ollama`).
- Browser-driven: set `none` → submit → reload → still `none`; a planted stale
  value falls back to the default option rather than rendering blank.

### Next
- Decide what the control defaults to. Blank = endpoint default = reasoning on,
  so ~15s remains the experience for anyone who never touches the dropdown.
  Still the real latency decision.
- Probe vLLM for `reasoning_effort` vs `chat_template_kwargs`, and confirm the
  latency drop.
- `/land-strand` once the work merges, to promote D-DRAFT-1..3.

### Flags
- **Still never run against vLLM.** The plumbing, validation and payload shape
  are tested, and the provider path is exercised against Ollama's `/v1`, but the
  central claim — reasoning off cuts the ~15s — remains unverified. Any PR should
  say so plainly.
- The plan artifact is private; it needs sharing from its share menu before the
  manager can open it.

## 2026-09-01 (later) — Phase 2: reasoning in the eval lane

### Done
- Renumbered the phases to match how we now talk about them: eval is Phase 2
  (built), the roster is Phase 3 (awaiting the manager). Primary/secondary LLM
  selection folded into Phase 3.
- **Phase 2 built** on branch `llm-model-choice-eval`, stacked on
  `llm-model-choice` at `d5b7390`. `golden_cli --reasoning
  {none,low,medium,high}` applies to the Stage-2 **synthesis** provider only;
  the judge keeps the endpoint default so the scoring yardstick stays fixed
  while generation varies.
- `StackStamp.reasoning_effort` recorded, carried into `to_dict()`, printed as
  `rsn=` on the GEV `id:` line, and left OUT of the comparability keys.
- 6 new tests; suite 1846 passed, same 8 pre-existing failures.

### Decision (Hanif) — recorded, not enforced
- Reasoning effort does **not** gate comparability. Two runs at different levels
  still pool: "we shouldn't stop anyone from comparing". The tool records and
  surfaces; the analyst decides what is comparable. Consequence accepted: the
  printed `rsn=` line is the only place two such runs visibly differ, so the
  tests assert both that the keys stay equal and that the line shows the level.

### Clarified — authoring vs evaluation stay separate
- Two different "stage 1 / stage 2" vocabularies were in play: dataset
  *authoring* (collect req IDs → validate synthesis → golden-ready, in Eval
  Studio) versus *evaluation* (Stage-1 retrieval recall, Stage-2 synthesis +
  judge). The knob is on evaluation Stage-2 only.
- Eval Studio's curation chat (`routes/golden_eval.py:589`) takes the provider
  default and is untouched — deliberately. The golden answer is the reference;
  authoring under a varying level would move the yardstick with the thing being
  measured. Recording the level on the sample at golden-ready time was offered
  and declined.

### Branching
- Phase 2 went on its own branch rather than onto `llm-model-choice`: PR #11 is
  under review as Phase 1, and growing its scope mid-review would leave the
  reviewer's model and the diff out of step. Same strand across both branches —
  one journal, one decision-draft set.

### Next
- Hold Phase 3 for the manager's feedback.
- Revisit Phase 1: Hanif has open questions about it, possibly changes.
- No PR for the eval branch yet — it should follow #11's merge.

## 2026-09-01 (later) — Phase 1 reworked: named providers + Fast/Think

### Done
- Reworked the Phase 1 control surface after a UX review. The 4-level reasoning
  select and the half-built primary/fallback endpoint select are both gone,
  replaced by: a **provider** select over an optional `providers` list in
  `config/llm.json`, and a **Fast / Think** toggle. Neither shipped, so nothing
  was unpicked in the field.
- `env/config.py`: `LLMProviderEntry`, `_parse_providers`, `resolve_providers()`
  and `resolve_provider(id)`. Malformed entries drop with a warning rather than
  failing the load; an unknown id falls back to the first entry.
- `query.py`: `_reasoning_for(entry, mode)` — Fast → `"none"`, Think → send
  nothing. One place owns that mapping.
- `playground.py`: `_form_provider` / `_form_mode` replace the two old
  validators; threaded through all six Ask synthesis paths; `lane_config` now
  carries `llm_provider_id` + `llm_mode`.
- Template: the two controls, sticky, with the option-existence guard; the
  toggle disables itself (with a reason) on a provider declaring no support,
  driven by a data attribute — no round trip.
- MODULE.md contracts for env / llm / web, plus the **missing `refusal.py`
  STRUCTURE block** in `llm/MODULE.md` — a pre-existing gap, not something this
  work introduced.

### Verified
- Suite 1851 passed, same 8 pre-existing failures.
- Live, two named entries through Ollama's `/v1`: picking each logged a
  different model; warm timings on the reasoning-capable entry were
  **Fast ~1s vs Think ~7s**, repeatable. The unsupported entry logged the
  "declares no reasoning support" warning and sent no field.
- First timing pass was discarded: the numbers included pipeline cold start and
  showed Fast slower than Think. Re-measured warm with repeats.

### Judgement calls, stated so they can be overruled
- Config lives in `config/llm.json` (existing LLM config home, already in the
  documented chain) rather than env vars or a new Config-page field type.
- Think sends **no** reasoning field rather than asserting "high"/"medium" — we
  don't choose an effort level on the deployment's behalf.
- Unsupported providers disable the toggle with a reason rather than hiding it;
  a control that silently vanishes leaves the user hunting.

### Flags
- Still unverified whether the internal primary honours `reasoning_effort`.
  Needs one `llm_debug --complete` on the infra.
- Routing user traffic to the DGX box changes its load profile — a manager
  decision, to be surfaced in the PR, not assumed.
- PR #11's title and body still describe the old 4-level knob and must be
  rewritten before review continues.

## 2026-09-01 (later) — UI iterations, a rename, and a copy-from example

### Done
- Four rounds of UI feedback on the Ask controls, each caught by Hanif looking
  at the rendered page rather than the markup:
  1. The unsupported-provider note was a full sentence at body size, competing
     with the controls. Shrunk to a muted "ⓘ not supported".
  2. It used `hidden`, so showing it reflowed the right-aligned row and moved
     the Ask button. Switched to `invisible` to reserve the slot.
  3. Reserving the slot left a permanent gap between the toggle and the button.
     Moved the note out of the row entirely, onto its own line below.
  4. A disabled select still reading "Fast" promises something the endpoint
     cannot deliver. The select now shows **"Not supported"** itself and the
     separate note is gone — one place carries the message.
  Each step was verified by measuring the Ask button's x/y across provider
  switches, not by eye.
- **Renamed `supports_reasoning` → `supports_reasoning_control`.** Hanif's
  point: a model can BE a thinking model and still expose no knob. The flag
  means "this endpoint honours the knob", but the old name read as "this model
  reasons" — someone would have ticked it for exactly such a model and the UI
  would have offered a live control that did nothing. Renamed across code,
  config docs, the template data attribute, tests and MODULE.md; nothing ships
  a roster, so no migration.
- Added `config/llm.json.example`, matching the repo's existing `.example`
  convention (`docker/env.*.example`). Three tests keep it from drifting: it
  must parse through the real loader, name every `LLMProviderEntry` field, and
  contain no literal API key.

### Verified
- Suite 1854 passed, same 8 pre-existing failures.
- Ask button holds x=1406 / y=472 across capable → unsupported → capable.

### Corrected
- `plan.html` and the published artifact had gone stale — they still described
  the 4-level dropdown while `plan.md` had moved on. STRAND.md says to edit
  both together; that slipped for several commits. Re-rendered and republished.

### Next
- Push the branch and refresh PR #11 (its body predates the rename, the
  "Not supported" control and the example file).
- Phase 3 still held for the manager. Phase 2 (eval) sits on
  `llm-model-choice-eval`, unpushed, no PR — it follows #11.

## 2026-09-01 (session close) — Pushed, PR current, decision gaps closed

### Done
- Pushed 8 commits; PR #11 verified current on all three axes — title, head
  (`4bb413b` = local HEAD), and body (carries `supports_reasoning_control`,
  "Not supported", the example file, "ships no roster"; zero mentions of the
  old 4-level dropdown).
- **Closed two decision-log gaps found in triage:**
  - `D-DRAFT-5` (on the eval branch) — the eval comparability decision existed
    only in prose. Recorded, not enforced: `reasoning_effort` stays out of
    `stage1_key()`/`stage2_key()` so runs at different levels still pool, with
    both rejected alternatives and the consequence that makes the printed
    `rsn=` line load-bearing.
  - `D-DRAFT-2` annotated as superseded by `D-DRAFT-4` — it said model choice
    was deferred out of Phase 1, which D-DRAFT-4 reverses. Left promotable,
    `/land-strand` would have carried a contradiction into DECISIONS.md.
- Tooling (not project state): statusline now shows the active strand and the
  configured caveman level; caveman default set to lite for all sessions.

### State at close
| | |
|---|---|
| Phase 1 (Ask) | pushed, PR #11 open, awaiting review |
| Phase 2 (eval) | committed on `llm-model-choice-eval`, unpushed, no PR — follows #11 |
| Phase 3 (roster + primary/secondary failover) | held for the manager |

### Waiting on people, not code
- Manager: is routing user traffic to the DGX box acceptable, and which roster
  entry becomes everyone's default?
- One probe on the infra: does the internal primary honour `reasoning_effort`?
  Until then we do not know whether the toggle is live on it at all.

### Flags
- The strand now spans two branches with one journal. `/land-strand` must run
  after BOTH merge, or D-DRAFT-5 is left behind on the eval branch.
- `plan.md` and `plan.html` drifted five commits apart this session before
  being caught. They live apart, so the STRAND.md "edit both together" note is
  the only thing holding them in step.
