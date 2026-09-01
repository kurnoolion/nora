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

_Moved here from the `llm-model-choice` strand when Phase 2 was split onto its
own strand: the parent lands when PR #11 merges, and `/land-strand` archives the
folder — in-flight work must not be sitting in an archived strand._
