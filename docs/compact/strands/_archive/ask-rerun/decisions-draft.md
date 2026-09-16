## D-DRAFT-1 — Extend the existing history store; dedupe and cap at DISPLAY time only

**Context.** Archived strand `ask-history` already stores asked questions in
localStorage under `nora-ask-history`, unlimited, one entry per lane answer.
The request was for a "recent questions" list of the last 15-20.

**Decision.** Extend that store rather than adding a second one. Retention stays
unlimited; the 20-item cap and the dedupe-by-question-text apply only to the
Ask-page control. The History page keeps every per-lane row.

**Why.** Two localStorage keys tracking overlapping things is how they drift out
of sync. Capping the store as originally described would have *discarded*
history the History page currently keeps — a regression for anyone using it — so
the cap became a display choice, not a retention policy. Dedupe has to be
display-only too: the per-lane rows are what give engineers the lane-compare
view, but in a dropdown they would show as visible duplicates and repeated
verification of one question would crowd everything else out.

**Consequences.** One source of truth. The dropdown and the History page
deliberately show the same data differently, which has to stay understood — a
future change to the store affects both.

## D-DRAFT-2 — Record the question at SUBMIT time, not when an answer arrives

**Context.** `askRecordHistory` ran only after an answer arrived and bailed
unless it carried a `data-share-path`. So an ask that errored, or produced no
stored row, was never recorded — precisely the question a user wants to re-run
after changing a corpus or a config knob.

**Decision.** Two-phase. The question is written at submit once the existing
empty/lane guards pass; when an answer arrives, the pending entry is REPLACED by
its per-lane entries. A pending id is carried so enrichment finds its own entry
even if a concurrent ask unshifted ahead of it, and a finalize drops the marker
on paths where the stream dies before "done" — including the early return on
fetch failure, which skips the `finally`.

**Why.** Replacement rather than a second unshift is the whole trick: without it
every ask double-records. Recording at submit is what makes the feature useful
at all, since the failed ask is the interesting one.

**Consequences.** An entry may now have no `path`. `history.html` called
`entry.path.replace()` unconditionally, so the first errored ask would have
broken that page with a TypeError — pathless entries now render unlinked with a
"no saved answer" marker and an Ask-it-again button instead of a dead Share
button. Anything else reading this store must tolerate pathless entries.

## D-DRAFT-3 — Re-run uses the CURRENT form settings, not the original ask's

**Context.** Each history entry records `{q, path, lane, ts}` — no provider or
mode. The Ask page separately remembers provider/mode/lanes in
`ask-last-fields` and prefills them.

**Decision.** Re-run submits with whatever the form currently holds. Per-entry
provider/mode are deliberately NOT recorded.

**Why.** The workflow is "change a knob, re-ask, compare" — the changed knob is
the entire point, so reproducing the original conditions would defeat it. True
A/B reproduction would need new per-entry fields, which is a different feature
for a different workflow, and building it now would be speculative.

**Consequences.** Re-running an old question does not reproduce how it was
originally answered, and nothing records the difference. Adding per-entry
provider/mode later is cheap if reproduction is ever wanted.

## D-DRAFT-4 — Re-run goes through `form.requestSubmit()`, not a parallel ask path

**Context.** Both re-run entry points (Ask-page button, History-page button)
need to execute a question the user did not just type.

**Decision.** Set the textarea and call `form.requestSubmit()`.

**Why.** The submit path already carries the empty/lane guards, the
sticky-fields write, the streaming handler, history recording, and the sidebar
collapse-on-ask from strand `collapsible-sidebar`. A second ask path would have
to be kept in step with all of it; the collapse-on-ask inheritance was confirmed
in testing precisely because nothing extra was written for it.

**Consequences.** Re-run cannot diverge from a normal ask even if someone wants
it to later — any special-casing has to be added to the shared path or branched
explicitly.

## D-DRAFT-5 — History→Ask handoff via localStorage, not a `?q=` query param

**Context.** The History page has no ask form, so re-run must hand the question
to `/test`.

**Decision.** Stash the question under `nora-ask-prefill`, navigate, then read,
CLEAR, and submit. A one-shot baton, not state.

**Why.** A question can quote proprietary requirement text, and a URL lands in
reverse-proxy and server access logs — the project's standing rule keeps such
content out of logs. It also leaves `/test`'s route signature untouched, so
nothing new meets the team-mode gate. Clearing before submitting is what stops a
reload silently re-asking, which is not free: it hits the LLM.

**Consequences.** The handoff is invisible in the URL, so it cannot be shared or
bookmarked — correct here, but it means re-run is not linkable. Blocked
localStorage makes the button a no-op rather than falling back to a param.

## D-DRAFT-6 — Share copies without a dialog, from one implementation

**Context.** Reported: the Share button copies silently in Chrome but opens a
dialog needing OK in Edge. The dialog was ours — a `window.prompt` fallback.
`navigator.clipboard` is exposed only in a SECURE CONTEXT, so reaching the app
over plain http via a hostname or LAN IP leaves it undefined and drops straight
to the prompt. Verified: absent on the LAN address, present on 127.0.0.1.
Nothing Edge-specific.

**Decision.** Clipboard API when available, else a detached textarea plus
`execCommand("copy")`. The last resort is a pre-selected read-only input beside
the button, never a modal. The whole helper moved to `static/js/app.js`.

**Why.** `execCommand` is deprecated but works in non-secure contexts and needs
no dialog. A bare "Copy failed" was rejected as a *dead end* — the old prompt at
least left the URL copyable, and trading one annoyance for a worse one is not a
fix. The move to `app.js` is because the logic existed TWICE, in
`test/index.html` and `test/history.html`, prompt fallback included, so this fix
would have landed in one and not the other.

**Consequences.** `askCopyShare` keeps its name because `_answer.html` calls it
from an inline `onclick`. The copy itself is UNVERIFIED — both clipboard paths
need transient user activation the automation cannot synthesize; needs a human
click in Edge. If Edge is reached over localhost there, the diagnosis above is
wrong.

## D-DRAFT-7 — Test isolation for `NORA_LLM_CONFIG` via an autouse fixture

**Context.** `NORA_LLM_CONFIG` (#18) takes precedence over
`DEFAULT_LLM_CONFIG_PATH` — the point of it — but that silently defeats the many
tests which isolate themselves by monkeypatching `DEFAULT_LLM_CONFIG_PATH` to a
temp file: with the var set, `load()` reads the developer's real roster. Nine
tests in `test_env_config.py` fail that way. Surfaced only by actually adopting
the feature on a dev machine.

**Decision.** A new `core/tests/conftest.py` with an autouse fixture clearing the
var for every test.

**Why.** Chosen over nine individual `delenv` calls so tests added later inherit
the isolation instead of having to remember it. The failure shape is the worst
kind — green in CI, green for everyone who has not adopted the feature, red for
whoever adopts it first, with nothing pointing at the cause. The other
`NORA_LLM_*` vars need no clearing: the resolver tests that care already delete
them individually, since those vars predate the problem.

**Consequences.** A test wanting the var must set it explicitly with
`monkeypatch.setenv`, which runs after the fixture and wins. This repairs a
defect already on `main`, so it rides this branch rather than belonging to it —
called out in the PR body.
