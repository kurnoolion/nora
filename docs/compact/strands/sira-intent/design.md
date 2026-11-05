# SIRA intent: design

Status: 2026-11-05. All questions settled in a grilling session (Q1–Q25).
Nothing built yet. Phase 1 is described here; phase 2 is in §9.

## 1. Goal

Today the SIRA serving path classifies nothing. The served stack reads only
`intent.mnos` and `intent.releases` (D-254), and `query_type` mixes scope with
answer shape. Every question gets the same synthesis prompt, and History
routing hangs off a keyword regex.

This strand adds one new dimension, **intent**: the kind of answer the asker
wants. Scope is not predicted, because BM25 retrieves it. Phase 1 uses intent
in two places:

- **A.** It picks a select-synth answer prompt for each intent.
- **B.** It routes `change` questions to the History lane.

The jev decision model (Kev locally) is measured against a rules baseline.

## 2. The intent set

| Intent | The asker wants | Answer shape (§5) |
|---|---|---|
| `fact` | one value or one statement ("what is the T3402 value") | short, direct, cited |
| `explain` | how or why something works; includes "summarize" | prose |
| `list` | every item of a kind ("all SMS over IMS requirements") | exhaustive bullets |
| `compliance` | whether a behaviour meets the requirements | checklist, pass/fail per requirement |
| `change` | what moved between releases of the same document | added / removed / changed |
| `compare` | two things side by side (MNOs, plans, features) | table |
| `other` | none of the above, or unsure | today's prompt, unchanged |

Labels are single. A question with two intents gets the one whose answer shape
the asker most wants; the dev set README states this rule. Scope intents and
`traceability` / `standards_comparison` are out. `change` stays separate from
`compare` because it routes to History.

## 3. Shape in code

- **`query/schema.py`:**
  - Add a new `Intent` enum and a field `QueryIntent.intent: Intent = Intent.OTHER`.
  - Add one line to `to_dict`.
  - `query_type` is unchanged, and the NORA pipeline is untouched.
  - No wire API carries `QueryIntent`, so nothing else changes shape.
- **`query/` rules intent classifier:** a regex classifier.
  - Its `change` rule is today's `is_release_change_question` logic (`query/release_diff.py:150`), so routing on the rules arm is identical to today.
  - When no rule matches, it returns `other`.
- **`decision/analyzer.py`:**
  - A separate `INTENT_QUESTION`, generated from the `Intent` enum and glossed by `INTENT_DESCRIPTIONS: dict[Intent, str]`. A test fails if any member has no gloss.
  - `CLASSIFICATION_QUESTIONS` keeps its two NORA questions, and its "exactly 2" test stays green.
  - The SIRA path sends only `INTENT_QUESTION`.
- **Confidence routing:** reuse `ConfidenceRoutedQueryAnalyzer`.
  - Kev's intent is kept when its confidence clears `NORA_DECISION_CONFIDENCE_FLOOR` (0.50, not validated).
  - Otherwise the rules intent decides, and if no rule matches, `other`.
  - A Kev outage degrades to the rules (an existing invariant).
- **Selection:** the existing `NORA_QUERY_ANALYZER` knob (`rules` | `decision` | `confidence-routed`), read by **nora-web**. No new classifier knob.

## 4. Flow

nora-web classifies **once, first**: Kev answers in about 0.55 s warm on a Mac
with Kev-4B on MLX, and the first call takes about 3.5 s. Then:

1. **`intent == change`** → the History lane.
   - `history_lane.classify` stops building its own `MockQueryAnalyzer()`, and `history_diff_applies` reads the intent nora-web computed (Q16).
2. **History cannot answer** (rules below apply only with `NORA_SIRA_INTENT_PROMPTS` on, Q25):
   - **Lane disabled in config** (operator's choice) → SIRA with the `change` prompt. No error.
   - **Lane enabled, but no History store, the store won't open, or only one release for the asked MNO** → an error telling the user what is available, for example "History has only Feb2026 for this operator; ask about one release, or pick another operator". No silent fallback (Q3, Q17).
   - **With the flag off** → today's behaviour: SIRA answers as before.
3. **Any other intent** → the SIRA lane. Select-synth uses the intent's prompt when the flag is on, and today's prompt otherwise.

The store is unchanged. It keeps rules-only scope (`mnos`, `releases`) in
`_multi_cell_query`. Phase 1 does not send intent to the store (§9).

## 5. Per-intent synthesis prompts

- **File:** `config/sira_intent_prompts.json`, keyed by intent value.
  - nora-web reads it once at startup.
  - A missing file means the defaults in code are used.
  - A malformed file, or one naming an unknown intent, fails startup loudly.
  - Edits need a restart.
- **Content:** every prompt keeps today's `_SELECT_SYNTH_SYSTEM_PROMPT` skeleton (`web/routes/playground.py:139`): select by meaning, answer from the selected chunks only, cite the exact req_id inline, and say so plainly when nothing answers. Only step 2, the answer shape, changes per intent, as in the §2 table.
  - `other` is today's prompt verbatim.
  - The reasoning-sentinel suffix applies to every prompt, as it does today.
- **Review:** I draft the prompts and Hanif reviews them.

Query enrichment (the store's `query_requirement_v01.txt`, in the SIRA clone)
is not touched in phase 1.

## 6. Switches and defaults

| Switch | Where | Default | Effect |
|---|---|---|---|
| `NORA_QUERY_ANALYZER` | nora-web | `rules` | Which classifier decides intent. `rules` reproduces today's routing. |
| `NORA_DECISION_PROVIDER` / `_BASE_URL` / `_CONFIDENCE_FLOOR` | nora-web | unset / unset / 0.50 | Kev endpoint, needed for the Kev arms only. |
| `NORA_SIRA_INTENT_PROMPTS` | nora-web | off | Per-intent prompts, plus the §4 error behaviour. |

With every default, served answers are unchanged. Each phase turns on one
switch at a time.

## 7. UI and logging

- **Badge:** SIRA answer cards in **Ask** and **`/test`** show the intent as a small badge. There is no override control.
- **Feedback rows:** `lane_config` on the rows records `intent`, `arm` (`kev` | `rules`) and `confidence`. These logged rows become the labelled real-traffic set for the internal held-out check and for tuning the floor.
- **Team-mode gate:** there are no new routes. Verify with the team-mode gate on before calling it shipped (branch-flow rule).

## 8. Measurement

**Classification:**
- **Dev set:** `core/src/eval/intent_set.json`, about 70 synthetic questions, 10 per intent, over the public Verizon ODI docs (Feb2026 and Oct2024).
  - It follows the `classify_set.json` conventions: MNO placeholders and no MNO named in the committed file.
  - Labels are human-read intent, never rule output.
  - I draft the questions and labels; Hanif reviews them before any number is recorded.
- **Harness:** extend `eval.classify_cli` to score intent per arm.
- **Kev wins (Q1 = C) when both hold:**
  - `change` precision ≥ 0.9 and recall ≥ 0.9;
  - overall intent accuracy is no worse than the rules.
- **If Kev loses,** phase 1 still ships the rules intent and the prompts.
- **Where it runs:** locally, rules vs Kev-4B on the Mac at `127.0.0.1:8009`. The head check is verified (`lora=16`, `temperature=2.41`).

**Answer shape (A):**
- A local spot-check of about 2 questions per intent, with the flag on and off.
- Then a golden-eval A/B internally, before `NORA_SIRA_INTENT_PROMPTS` is turned on in any served stack.

## 9. Phase 2 and deferred items

- **Phase 2 (not in this strand's phase 1):**
  - Steer query enrichment and BM25 by intent, for example a larger top-k for `list` and a smaller one for `fact`.
  - Send intent to the store.
  - Guided by the logged rows and the golden A/B.
- **Deferred to internal infra:** the held-out check on real feedback questions (dev-set option C).
- **Deferred:** a user override control for intent, and multi-label intent.
- **Not moving to jev:** MNO and release stay with the rules extractors (the
  store's `_multi_cell_query` → `resolve_cells`).
  - They are explicit scope, extracted from what the user wrote, unlike the
    implicit scope this strand stops predicting.
  - A decision model emits only `noul` / `choice` / `score`, so it cannot
    output free-text fields (decision/MODULE.md). Releases are open-ended in
    any case.
  - MNO could be a `choice`, but regex already resolves names and aliases
    exactly.
  - Jev would help only with indirect references ("the latest one", an
    operator nickname). The rules already handle `latest` and `previous` for
    History.

## 10. Local stack

`~/work/env_sira/run_local.sh` (outside the repo):
- Starts Kev-4B on 8009 as well.
- Sets `NORA_QUERY_ANALYZER=confidence-routed`, `NORA_DECISION_PROVIDER=systemone` and `NORA_DECISION_BASE_URL=http://127.0.0.1:8009` on nora-web.
- Sets `NORA_SIRA_INTENT_PROMPTS=on` once the prompts exist.
- `KEV=0` skips Kev, and `stop` stops all three.

## 11. Tests (outline)

- **Rules intent:** one case per intent, and parity with `is_release_change_question` on its existing tests.
- **Enum coverage:** `INTENT_DESCRIPTIONS` has every member, and `INTENT_QUESTION` options match the enum.
- **Confidence routing:** below the floor the rules decide; no rule match gives `other`; a provider outage gives the rules, with `fallback_count` counted.
- **History lane:** reads the passed intent; covers flag off (today's behaviour), lane disabled (SIRA change prompt), and no store or a single release (the error lists releases).
- **Prompt loader:** missing file uses the defaults, a malformed file or unknown intent raises, and `other` equals today's prompt.
- **Badge and `lane_config`:** fields present, with the team gate on and off.
