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

## 2026-09-01 (session close) — Rebased onto Phase 1, strand split out, PR #12 raised

### Done
- **Rebased onto `llm-model-choice`**, not `main`. Phase 2 calls
  `OpenAICompatibleProvider(reasoning=...)`, which only exists on Phase 1, so
  the branch stacks and its PR shows the eval diff alone (9 files) instead of
  replaying Phase 1.
- Two conflicts, both the same shape: append-only strand files where each
  branch had appended different content (`journal.md`, `decisions-draft.md`).
  Resolved by keeping **both** sides in order — a conflict in an append-only
  file means two sessions wrote different history, not that one is wrong.
- **Split this strand out of `llm-model-choice`.** Hanif's call, and the right
  one: `/land-strand` is terminal, so when PR #11 merges and the parent lands,
  its folder moves to `_archive/` and `/switch-strand` refuses to bind it.
  Phase 2 would then have been in-flight work pointing at an archived strand,
  with the merge timing in the reviewer's hands, not ours.
  - The Phase 2 journal entry moved here; D-DRAFT-5 became this strand's
    D-DRAFT-1, with its old number recorded so the history stays followable.
  - The parent keeps D-DRAFT-1..4 (all Phase 1) and can land the moment #11
    merges.
  - `plan.md` stays in the parent as the shared roadmap; this branch's only
    change to it is the phase renumbering (Phase 2 marked BUILT, roster →
    Phase 3), which belongs with this PR because merging it is what makes
    Phase 2 built.
  - Verified afterwards that the parent's `journal.md` and
    `decisions-draft.md` are byte-identical on both branches, so nothing
    diverges.
- Force-pushed (`--force-with-lease`) and opened **PR #12**, based on
  `llm-model-choice`. GitHub retargets it to `main` when #11 merges. Both PRs
  are cross-linked so a reviewer merging #11 knows #12 is queued behind it.

### Verified
- Suite 1860 passed after the rebase (Phase 1's 1854 + this branch's 6), same 8
  pre-existing failures.
- PR #12's claims checked against reality — 6 new tests, 1860 passed, head
  matches local. Done deliberately: the same check on #11 earlier had confirmed
  the new text was present without noticing stale text that should have gone,
  and Hanif caught a contradiction I had missed.
- Exercised the stamp's printed line for real rather than trusting assertions:

      fast : id: fp=… llm=qwen3-30b rsn=none knobs=2@fe7d87e6
      think: id: fp=… llm=qwen3-30b rsn=high knobs=2@fe7d87e6
      unset: id: fp=… llm=qwen3-30b knobs=2@fe7d87e6

  Two levels share a `stage2_key` — they pool, as decided — so `rsn=` is the
  only thing separating them on the line. Unset renders byte-identical to
  before this branch.

### Still unverified, and why
- **A real golden campaign has never run.** `--stack-url` is required, the SIRA
  service is down locally, and only 1 of the 5 demo samples is golden-ready.
  Everything reachable from this machine is exercised; a campaign against a
  live stack with `--reasoning none` versus unset is the largest remaining
  unknown in #12.
- Whether the internal vLLM honours `reasoning_effort` — same gap as #11.

### Next
- Wait on the manager: PR #11's two decisions (is DGX routing acceptable; which
  roster entry is the default) gate Phase 1, and #12 follows it.
- `/land-strand` on this strand only after #12 merges; the parent lands
  separately after #11. They are independent now — that was the point of the
  split.
- Phase 3 (provider roster management + primary/secondary failover) gets its
  own strand when it starts, reusing neither.

### Flags
- A line in the PARENT strand's session-close entry is now obsolete: it says
  `/land-strand` must wait for both branches or D-DRAFT-5 is left behind. The
  split makes that false. Left uncorrected by decision — fixing it means a
  commit and push to a branch under review, for a stale sentence in a journal.
- `config/llm.json` carries a local scratch roster (two Ollama entries) so the
  Ask controls render on :8000. Local only — `git checkout config/llm.json`
  drops it. It must not ride into a commit.
