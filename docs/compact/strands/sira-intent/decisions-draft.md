### D-DRAFT-1 — 2026-11-05 — Intent is a separate dimension from scope, and scope is not predicted

**Context.**
- The SIRA path reads only `mnos` and `releases` from the analyzer (D-254).
- `query_type` mixes the scope of a question (`single_doc`, `cross_doc`, `cross_mno_comparison`) with the shape of answer wanted (`fact`, `summarize`, D-051).

**Decision.**
- Add a new `Intent` enum (`fact`, `explain`, `list`, `compliance`, `change`, `compare`, `other`) and a `QueryIntent.intent` field next to the unchanged `query_type`.
- Nothing predicts scope on the SIRA path.

**Why.**
- BM25 retrieves whatever scope the question has, so predicting it adds a classification nothing would use.
- Answer shape is what the synthesis prompt and the History routing need.
- Rejected:
  - **Replacing `query_type`'s values with intents (approach 2):** it breaks the NORA consumers (`graph_scope`, per-type BM25 weights, the `/config` per-type table), and NORA is compat only.
  - **Mapping intent from `query_type` (approach 3):** it inherits the mixing (`cross_doc` doesn't separate `list` from `explain`), and jev is never asked the right question.

**Consequences.**
- `QueryIntent` gains a field with a default. The "QueryIntent does not change" invariant in decision/MODULE.md is amended to "fields are only added, with defaults".
- `summarize` folds into `explain`. On the SIRA path, intent supersedes D-051's use of `query_type` for answer shape.

### D-DRAFT-2 — 2026-11-05 — The SIRA path asks Kev one intent question, separate from the NORA classification questions

**Context.**
- `CLASSIFICATION_QUESTIONS` holds exactly two questions (`query_type`, `doc_type_scope`), with a test asserting that.
- The served SIRA stack reads neither.

**Decision.**
- A separate `INTENT_QUESTION`, generated from the `Intent` enum and glossed by `INTENT_DESCRIPTIONS`.
- The SIRA path sends only that question.
- `CLASSIFICATION_QUESTIONS` is unchanged.

**Why.**
- A third entry in `CLASSIFICATION_QUESTIONS` was the first plan. It was rejected: the SIRA path would pay for two answers it never reads, and the NORA path would gain a question it never uses.
- Glosses are load-bearing: 0.46 → 0.76 for `query_type` on a live Kev-4B.

**Consequences.**
- There are two question sets, one per path.
- A new `Intent` member without a gloss fails a test.

### D-DRAFT-3 — 2026-11-05 — Classify once, first, in nora-web; the store keeps rules-only scope

**Context.**
- Today classification happens twice:
  - the store runs the knob analyzer for scope;
  - `history_lane.classify` builds its own `MockQueryAnalyzer()`.
- Routing needs intent before a lane is chosen.

**Decision.**
- nora-web runs the intent analyzer once, before retrieval.
- `history_lane` reads that intent instead of classifying again.
- The store is unchanged.

**Why.**
- Kev answers in about 0.55 s warm (Kev-4B, MLX, measured 2026-11-05), so classifying first costs little. It also lets a later phase steer enrichment and BM25 by intent.
- Rejected: running classification in parallel with SIRA retrieval and discarding the retrieval for `change`. It saves about half a second at the price of a lane decision made after work has started, and it rules out steering retrieval by intent.

**Consequences.**
- nora-web needs `NORA_DECISION_*` env when a Kev arm is on.
- The store's `query_analyzer` healthz key keeps describing the store's own scope analyzer, not the intent arm.

### D-DRAFT-4 — 2026-11-05 — Low-confidence intent falls back to the rules, then to `other`

**Context.**
- Kev reports a calibrated confidence: on classify-dev-v1 it averaged 0.425 on correct answers and 0.268 on wrong ones.

**Decision.**
- Reuse `ConfidenceRoutedQueryAnalyzer`. Kev's intent stands above `NORA_DECISION_CONFIDENCE_FLOOR`; below it, the rules intent classifier decides.
- No rule match means `other`, which is today's prompt and lane.

**Why.**
- Rejected: asking the user to rephrase on low confidence. Correct labels average only 0.425, so it would reject many well-labelled questions.
- Rejected: labelling everything below the floor `other`. That discards the rules, which handle the phrasing Kev is unsure about.
- The routing machinery already exists for the NORA fields.

**Consequences.**
- A weak Kev label is never worse than the rules arm.
- `lane_config` records which arm decided, so the floor can be tuned on real traffic.

### D-DRAFT-5 — 2026-11-05 — A `change` question History cannot answer is an error, not a silent SIRA fallback, and only behind the intent switch

**Context.**
- Today `history_diff_applies` returns `None`, and SIRA answers, whenever the lane is disabled, no store is configured, or the store fails to open.

**Decision.**
- With `NORA_SIRA_INTENT_PROMPTS` on:
  - If the release-diff lane is enabled but History can't answer (no store, the store won't open, or only one release for the asked MNO), the user gets an error naming the available releases.
  - If the lane is disabled in config, the question goes to SIRA with the `change` prompt.
- With the switch off, today's behaviour is unchanged.

**Why.**
- A SIRA answer to a release-change question, drawn from whatever chunks came back, looks authoritative and isn't. An error that lists the releases tells the user how to rephrase.
- Disabling the lane is an operator's choice, not a failure.
- Tying the error to the switch keeps served stacks unchanged until someone opts in.
- Rejected: silent fallback to SIRA with a "release history not available" note, as too lenient. It can be revisited later.

**Consequences.**
- One error path covers the missing-store and single-release cases.
- In SIRA (History lane disabled), a `change` question whose retrieved context holds only one release per operator falls back to today's prompt with a note on the answer, not the `change` prompt: on the local spot-check the model invented release diffs from one release's chunks (2026-11-08). A fixed no-model reply was rejected: the user still gets an answer from the release that is there.

### D-DRAFT-6 — 2026-11-05, revised 2026-11-07 — Per-intent synthesis prompts: defaults in source, an optional per-environment override, default off

**Context.**
- Select-synth uses one hard-coded system prompt for every question (`core/src/web/routes/playground.py:139`).
- The prompts will be revised often as the dataset grows.

**Decision.**
- Defaults live in source (`core/src/web/sira_intent_prompts.py`), keyed by intent.
- An optional JSON override per environment: the file named by `NORA_SIRA_INTENT_PROMPTS_FILE`, else `<env_dir>/sira_intent_prompts.json`. Merged per intent over the defaults.
- Read once at startup. A malformed file, an unknown intent, or a missing variable-named file stops startup.
- Each prompt is stamped with `prompt_source` (`default` | `override`) and an 8-hex `prompt_hash`, logged at startup and recorded in each answer's `lane_config`.
- Each prompt keeps today's select/answer/cite skeleton and varies only the answer shape. The default `other` is today's prompt verbatim.
- `NORA_SIRA_INTENT_PROMPTS` gates them, default off.

**Why.**
- Defaults in source are reviewed in PRs; an environment can trial new wording without a code change.
- A per-intent merge means revising one prompt doesn't copy all seven.
- The hash ties each feedback row and eval run to the exact wording, so a revision's effect can be measured.
- A loud startup failure beats answering with half a prompt set, or silently using defaults after a path typo.
- A shared skeleton keeps chunk selection and citation behaviour constant across intents, so an A/B measures answer shape only.
- Rejected:
  - a committed `config/sira_intent_prompts.json` as a third layer (the first plan): two places to look are enough;
  - hard-coded constants with no override;
  - reloading on file change: extra moving part, restart is cheap.

**Consequences.**
- A prompt edit needs a restart.
- The flag goes on in a served stack only after a golden-eval A/B internally.

### D-DRAFT-7 — 2026-11-05 — Measure intent on a synthetic public dev set; the held-out check on real questions is deferred to internal infra

**Context.**
- The only corpus available off the internal network is the public Verizon ODI docs.
- Real user questions live in the feedback DB internally.

**Decision.**
- `core/src/eval/intent_set.json`: about 70 synthetic questions, 10 per intent. It follows the `classify_set.json` conventions (MNO placeholders, labels read by a person, never rule output).
- Kev beats the rules when `change` precision and recall are each ≥ 0.9 and overall accuracy is no worse than the rules.
- A held-out check on real feedback questions (which `lane_config` will now label) runs internally later.

**Why.**
- `change` is the only label that moves a question to another lane, so it carries the highest bar. Other labels only shape the answer.
- Real questions can't leave the internal network.

**Consequences.**
- If Kev loses, phase 1 ships the rules intent with the prompts.
- The success bar is in-sample on a small synthetic set: a starting point, not a validated setting.

### D-DRAFT-8 — 2026-11-07 — Bare requirement IDs get an `id_lookup` lane with no model; IDs inside a question are pinned into the context

**Context.**
- Users often paste one or more requirement IDs and expect the requirement text back. The manager asked for no synthesis in that case.
- When an ID appears inside a question, answers reportedly show the requirements that cite it but not the named one.
- `req_tree.find_req` already fetches a requirement by ID from the Trees store.

**Decision.**
- **A message made only of requirement IDs** goes to a new `id_lookup` lane, ahead of classification:
  - each ID is fetched with `find_req` and shown in full;
  - the latest served release is shown unless the message names one, and other releases are mentioned;
  - unknown IDs are listed as not found.
- **IDs inside a question:** the named requirements are fetched by ID and placed first in the select-synth context.

**Why.**
- A pasted ID is a lookup, not a question: a model call adds latency and a chance of paraphrase for nothing.
- It is a lane, recorded as `lane_config.lane = "id_lookup"` the way History records `"history"`, not an `Intent` value. A pattern decides it, not a judgment, so it does not belong in Kev's options.
- Rejected:
  - an eighth `lookup` intent, which Kev would have to learn to pick;
  - a separate strand for the pinning fix, which would redo the same ID matching in the same nora-web code.

**Consequences.**
- The classifier dev set drops its bare-ID sample (o03).
- The ID pattern is Verizon-only until other operators' formats are known.
- The pinning fix is built last in phase 1, after reproducing the symptom.
- The lane sits behind `NORA_ID_LOOKUP`, default off, like the strand's other switches: served stacks change only when someone opts in.

### D-DRAFT-9 — 2026-11-08 — A pasted bug report asking for a verdict is `compliance`, not a new intent

**Context.**
- Users paste a bug description (mostly free form, description only, no logs) and ask "is this valid?", "can you comment on this issue?" or "are we wrong?".
- The rules sent these to `other` or `explain`, and a bug report saying "nothing changed" was routed as a release change.

**Decision.**
- Such a message is `compliance`: the device behaviour is described and the asker wants to know whether the requirements allow it.
- The rules match the verdict ask (`_VERDICT_ASK`) right after the change check, and `is_release_change_question` is false for it and for a negated change.
- The compliance prompt judges the observed device behaviour, never the reporter's expectation, with MEETS / VIOLATES per requirement and an overall verdict.

**Why.**
- Rejected: a new `bug` intent. The answer is the same as for a compliance question (judge a behaviour against the requirements), so a new intent would add a Kev option and a prompt with nothing different to do.
- The user asked for MEETS / VIOLATES wording, which compliance already uses.

**Consequences.**
- The dev set gains six synthetic bug reports (`kb1`–`kb6`, labelled compliance); its labelling rule says so.
- Kev alone misses most of them, since its option descriptions don't mention bug reports. The confidence routing covers that today.

### D-DRAFT-10 — 2026-11-08 — Compliance keeps its own checklist prompt, separate from the client compliance-tracking plan

**Context.**
- 6cdaa6c switched `compliance` to today's prompt, taking it for the planned client compliance-tracking (diff) system.
- They are different things: the intent is a user describing a behaviour and asking whether it is allowed; the tracking plan is a client compliance matrix.

**Decision.**
- `compliance` answers with its own prompt: MEETS / VIOLATES / UNCLEAR per governing requirement, then an overall verdict (c932a61 reverts 6cdaa6c).
- The client compliance matrix is out of this strand (design §9).

**Why.**
- Today's prompt summarizes requirements; a "is this allowed?" question needs a verdict, which only the checklist gives.

**Consequences.**
- The compliance prompt is distinct from `other` (a test asserts every intent's prompt differs).
- A later compliance-tracking strand starts from its own design, not from this prompt.

### D-DRAFT-11 — 2026-11-08 — The confidence floor stays at 0.50, on the sweep (amends D-DRAFT-4)

**Context.**
- D-DRAFT-4 routed low-confidence Kev answers to the rules at `NORA_DECISION_CONFIDENCE_FLOOR`, set to 0.50 before the dev set was reviewed.
- Reviewed intent-dev-v1 (77 samples): rules 0.753, Kev alone 0.753 (change recall 0.40), confidence-routed 0.870.

**Decision.**
- Keep the floor at 0.50.

**Why.**
- Accuracy plateaus at 0.870 for floors 0.40–0.50, but the change bar (precision and recall ≥ 0.9) is met only from 0.45. 0.50 sits inside both.
- Kev is right 89–100% of the time above 0.5; below 0.4 the rules do better. 17 of Kev's 19 wrong answers are below 0.5.
- Rejected: 0.40, same accuracy but the change bar is not met.

**Consequences.**
- At 0.50 Kev decides 40 of 77 dev questions.
- Re-measure with `python -m core.src.eval.intent_report` after any change to the rules, the set or Kev; the held-out real-question check (D-DRAFT-7) may move it.

### D-DRAFT-12 — 2026-11-08 — Eval reports are committed under a top-level `reports/` folder, one timestamped folder per run

**Context.**
- The Kev floor report was made by hand; the user wanted the same report after every evaluation, with time details so runs can be correlated.

**Decision.**
- `python -m core.src.eval.intent_report` writes `reports/intent/<YYYY-MM-DD-HHMM>_kev-floor_<set_id>/` with `run.json` (numbers plus `meta`: time, git commit and dirty flag, dataset hash, decision endpoint, floor) and a self-contained `report.html`.
- The project skill `eval-report` prepares the environment, runs it, reports against the previous run and proposes the commit.
- Only reports on synthetic sets are committed; rows carry sample ids and labels, never question text.
- New reports follow the same shape under `reports/<report>/`.

**Why.**
- The user asked for a dedicated top-level folder and a readable slug; reports outlive the strand that made them.
- Rejected: a comparison tool between runs (the user said not needed); the metadata in each `run.json` is enough to correlate.
- The script owns the numbers, so the page text can't drift from them.

**Consequences.**
- The repo is public: a run on an internal or real-traffic set stays out of it.
- A failed decision call writes nothing, so a report never scores the rules under Kev's name.
