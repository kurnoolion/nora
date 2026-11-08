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
| `compliance` | whether a behaviour meets the requirements | today's prompt for now; a dedicated answer waits for the diff system (§9) |
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

0. **Bare requirement IDs → the `id_lookup` lane** (Q12–Q14, added
   2026-11-07 after a review with the manager).
   - Applies when the message is nothing but requirement IDs: one or several,
     separated by spaces, commas or new lines.
   - nora-web skips classification, retrieval and synthesis, and fetches each
     ID with `req_tree.find_req` (Trees store).
   - Shows the full text at once. Unknown IDs are listed as not found.
   - Release shown: the latest served by default, with an "also in:
     <releases>" line. A release named in the message wins.
   - This is a **lane**, not an intent. It is recorded like History: the
     feedback row's `lane_config.lane = "id_lookup"`, with `intent=other`, and
     the badge reads "lookup". `Intent` keeps its seven values, so Kev never
     sees a lookup option.
   - It has its own tests. It is not part of the classifier dev set: o03 is
     replaced.
   - Behind `NORA_ID_LOOKUP`, default off (§6).
   - Built 2026-11-07: `core/src/web/routes/id_lookup_lane.py`, checked in the
     ask-stream route before History. At most one release may appear; a
     release named that lacks an ID says which releases carry it. Requirement
     text renders through `req_body` (not markdown); the stored answer fences it.

nora-web classifies **once, first**: Kev answers in about 0.55 s warm on a Mac
with Kev-4B on MLX, and the first call takes about 3.5 s. Then:

1. **`intent == change`** → the History lane.
   - `history_lane.classify` stops building its own `MockQueryAnalyzer()`, and `history_diff_applies` reads the intent nora-web computed (Q16).
2. **History cannot answer** (rules below apply only with `NORA_SIRA_INTENT_PROMPTS` on, Q25):
   - **Lane disabled in config** (operator's choice) → SIRA with the `change` prompt. No error.
   - **Lane enabled, but no History store, the store won't open, or only one release for the asked MNO** → an error telling the user what is available, for example "History has only Feb2026 for this operator; ask about one release, or pick another operator". No silent fallback (Q3, Q17).
   - **With the flag off** → today's behaviour: SIRA answers as before.
   - Built 2026-11-07: `core/src/web/routes/sira_intent.py` (`classify`, run
     once in a thread before any lane; the analyzer is built once per
     process) and `history_lane.history_diff_applies(intent=, strict=)`.
     RHD-E001 / RHD-E002 errors now name the releases History has, flag or
     not (text only; those questions already ended in that error). The
     `change` prompt for a disabled lane comes with the prompts (§5).
     Kev's first call after it starts takes about 13 s (warm-up); later ones
     about 0.3–0.4 s.
3. **Any other intent** → the SIRA lane. Select-synth uses the intent's prompt when the flag is on, and today's prompt otherwise.

4. **Requirement IDs inside a question** (Q15): the named requirements are
   fetched by ID and placed first in the select-synth context, whatever BM25
   returns.
   - Reported symptom: the answer shows the requirements that cite the named
     one, but not the named one itself.
   - Reproduce it on the local stack before fixing.
   - Built last in phase 1.
   - Reproduced 2026-11-08 on the local stack ("What does <ID> require?", four
     Feb2026 IDs): BM25 left the named requirement out of its top 20, or
     ranked it near the bottom (17 of 20).
   - Built behind `NORA_PIN_NAMED_IDS` (default off): the named requirements
     come from the Trees store and go first in the context, ahead of the
     round-robin; `lane_config` records `named_ids`, `named_pinned`,
     `named_in_sira`.

The store is unchanged. It keeps rules-only scope (`mnos`, `releases`) in
`_multi_cell_query`. Phase 1 does not send intent to the store (§9).

## 5. Per-intent synthesis prompts

Prompts will be revised often as the dataset grows, so they have two layers
(Q18–Q22 of the prompt round, 2026-11-07):

- **Defaults in source:** `core/src/web/sira_intent_prompts.py`, a dict keyed
  by intent next to the select-synth code. Reviewed in PRs like any code.
- **Optional override per environment**, as JSON keyed by intent value:
  - `NORA_SIRA_INTENT_PROMPTS_FILE=<path>` names the file (matches
    `NORA_HISTORY_DIFF_CONFIG`). A served stack points it at a mounted file.
  - With the variable unset, nora-web looks for
    `<env_dir>/sira_intent_prompts.json` (`config.env_dir_path()`), so a local
    env dir can hold its own prompts with no variable.
  - The variable wins when both exist. No file means the defaults only.
- **Merged per intent:** an override may list only some intents; the rest
  come from the defaults.
- **Loud failures:** a malformed file, a non-string prompt, or an unknown
  intent name stops nora-web at startup. A file named by the variable that
  doesn't exist also stops it (a typo must not silently fall back).
- **Load once at startup;** an edit needs a restart. No reload on file change
  (can be added later if the revision loop feels slow).
- **Version stamp:** each intent's prompt gets `prompt_source`
  (`default` | `override`) and `prompt_hash` (first 8 hex of the SHA-256 of
  the exact text). The startup log lists both per intent, and each answer's
  `lane_config` records them (§7).
- **Content:** every prompt keeps today's `_SELECT_SYNTH_SYSTEM_PROMPT` skeleton (`core/src/web/routes/playground.py:139`): select by meaning, answer from the selected chunks only, cite the exact req_id inline, and say so plainly when nothing answers. Only step 2, the answer shape, changes per intent, as in the §2 table.
  - `other` is today's prompt verbatim.
  - The reasoning-sentinel suffix applies to every prompt, as it does today.
- **Review:** I draft the prompts and Hanif reviews them.
- **Built 2026-11-07:** `core/src/web/sira_intent_prompts.py`. An override
  value is the whole system prompt for that intent (what the file says is
  what the model gets), not just step 2. The prompts are loaded at startup
  whatever `NORA_SIRA_INTENT_PROMPTS` says, so a bad file is caught before the
  switch goes on. Only the select-synth path uses them; the rerank-pin path
  (NORA synthesizer) keeps its own prompt. A disabled History lane sends a
  `change` question to SIRA with the `change` prompt (§4).
- **Prompt review (2026-11-08):** `list` asks for a one-sentence summary of
  each requirement's content, not its section title. `compliance` has no
  prompt of its own (roadmap, §9).
- **Single-release fallback (2026-11-08):** the local spot-check (qwen3:8b,
  32K) showed the `change` prompt inventing Added / Changed items from chunks
  of one release, despite the prompt's instruction. So a `change` question
  whose context has no operator with two releases answers with today's
  prompt instead, and the answer opens with a note saying release history
  isn't available and naming the release it used (`lane_config`
  `change_fallback`). Rejected: a fixed no-model reply (the user still wants
  an answer from the release they have) and relying on the prompt alone.

Query enrichment (the store's `query_requirement_v01.txt`, in the SIRA clone)
is not touched in phase 1.

## 6. Switches and defaults

| Switch | Where | Default | Effect |
|---|---|---|---|
| `NORA_QUERY_ANALYZER` | nora-web | `rules` | Which classifier decides intent. `rules` reproduces today's routing. |
| `NORA_DECISION_PROVIDER` / `_BASE_URL` / `_CONFIDENCE_FLOOR` | nora-web | unset / unset / 0.50 | Kev endpoint, needed for the Kev arms only. |
| `NORA_SIRA_INTENT_PROMPTS` | nora-web | off | Per-intent prompts, plus the §4 error behaviour. |
| `NORA_ID_LOOKUP` | nora-web | off | A message made only of requirement IDs goes to the `id_lookup` lane (§4 step 0). Off: it goes to SIRA as today. |
| `NORA_PIN_NAMED_IDS` | nora-web | off | Requirement IDs named inside a question go first in the select-synth context, from the Trees store (§4 item 4). |
| `NORA_SIRA_INTENT_PROMPTS_FILE` | nora-web | unset | Override file for the prompts (§5). Unset → `<env_dir>/sira_intent_prompts.json` if present, else the source defaults. |

With every default, served answers are unchanged. Each phase turns on one
switch at a time.

## 7. UI and logging

- **Badge:** SIRA answer cards in **Ask** and **`/test`** show the intent as a small badge. There is no override control.
- **Feedback rows:** `lane_config` on the rows records `intent`, `intent_source` (`model` | `rules`; `model` rather than `kev`, since served stacks run jev) and `intent_confidence`, plus `intent_analyzer` (the knob's arm), plus `prompt_source` and `prompt_hash` when the per-intent prompts are on (§5). These logged rows become the labelled real-traffic set for the internal held-out check, for tuning the floor, and for comparing answers before and after a prompt revision.
- **Built 2026-11-07:** the badge also shows on the shared page and the History pane, read from the stored row; the id_lookup lane's badge reads `lookup`. Tested with the team gate on and off.
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
- **Roadmap: compliance answers.** The `compliance` intent is classified and
  logged, but answers with today's prompt (2026-11-08, Hanif). A planned diff
  system will be connected here later; its prompt or lane comes with it.
- **Deferred:** requirement-ID patterns for other operators. The rules match
  `VZ_REQ_\w+_\d+` only (`query/analyzer.py`). Add the AT&T and T-Mobile
  formats when those operators are ingested, not by guessing (Q17).
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
- **Prompt loader:** no override uses the defaults; the variable beats the env-dir file; a partial override merges per intent; a malformed file, unknown intent, or missing variable-named file raises; `prompt_source` and `prompt_hash` are right; the default `other` equals today's prompt.
- **Badge and `lane_config`:** fields present, with the team gate on and off.
