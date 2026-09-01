## D-DRAFT-1 — Bubble anchors are the row's own req_id set, matched verbatim — never a req-ID regex

**Context:** The Ask answer is free LLM prose. To turn a req ID inside it into a
clickable bubble, something must decide which substrings are req IDs. The obvious
move is a regex — and three already exist (`query/citation_audit.py:33`,
`query/analyzer.py:97`, `eval/metrics.py:181`), all hardcoded to `VZ_REQ_`. The
corpus went multi-MNO at D-091..D-104, so all three are silently VZW-only. Taking
the regex path would mean generalizing them first, including `eval/metrics.py`,
which is load-bearing for golden-eval scoring.

**Decision:** Bubbles anchor on the **req_id set the row already carries** —
`retrieved_ids ∪ cited_ids` — matched as literal substrings of the answer text.
No pattern matching, no regex generalization, and no change to `query/` or
`eval/`.

**Why:** The set-membership approach is already the established corpus-agnostic
answer in this codebase: `playground.py:665` `_select_synth_extract_citations`
does exactly this for the SIRA lane, with a docstring that names the `VZ_REQ_`
regex as the thing it is avoiding. The union is what removes a lane asymmetry:
nora-lane `cited_ids` derives from the synthesizer's `VZ_REQ_` regex and is
therefore empty on MNO-B/C answers, but nora-lane `retrieved_ids` is built from
chunk metadata (`playground.py:326`) and is corpus-agnostic. Anchoring on the
union means both lanes bubble correctly on every MNO without touching the query
pipeline. A req ID the LLM invented is absent from the set and simply gets no
bubble — the correct behavior, and a quieter restatement of what the Stage-6.5
citation audit already flags. Rejected: a generic req-ID regex (drags
`eval/metrics.py` and golden-eval scoring into a UI strand); a web-local regex
(same VZW-only bug, new home).

**Consequences:**
- Strand scope collapses to `web` alone; `query` drops off the target modules.
- The three `VZ_REQ_` sites remain a latent multi-MNO bug. Recorded as a Flag —
  `analyzer.py:97` is the sharp one, since D-039 entity-priority scoping silently
  never fires on a non-VZW corpus.
- **Lane parity is structural, not tested.** The set is derived once in the
  shared per-lane context block (`playground.py:488`) from `rag_chunks` +
  `llm_citations` — both already populated identically for nora and sira (SIRA
  builds `rag_chunks` with `req_id` at `:703`). No branch on `lane` anywhere in
  the bubble path, so the feature cannot work on one lane and fail on the other.
  This matters because the two lanes are exercised on different machines: nora
  locally, sira only in the office.
- Stored surfaces use the same set through its persisted carriers —
  `retrieved_ids ∪ cited_ids` on the row — so `/ask/s/` and history need one
  added line in `_render_stored_ask`.
- Rows written by `/api/test/ask` (`:1343`) and `/api/test/synthesize-group`
  (`:1548`) omit both columns; only the live merged path (`:468`) persists them.
  Deliberately NOT fixed: the Ask form posts to `/api/test/ask-stream`, which
  goes through the merged path, and grouping is off by default, so neither
  endpoint is on a live user path. Those rows degrade to `cited_ids`-only
  bubbles. Revisit if either endpoint returns to use.

---

## D-DRAFT-2 — Bubble text comes from the parse tree, not from RAG chunk text

**Context:** Having anchored the req ID, the bubble has to show the requirement.
Two sources exist: the RAG chunk text sitting in the live response payload, or
the parse tree via `req_tree.find_req`.

**Decision:** Bubble content is read from the parse tree through the existing
shared loader — `req_tree.find_req`, with `req_tree.latest_match` resolving a
req_id that appears in several releases. Chunk text is not used.

**Why:** D-209 persists the normal user view only; RAG chunks are engineering
internals and are deliberately not stored on the row. A chunk-text bubble would
therefore work on the live answer and come up empty on `/ask/s/` and history —
exactly the surfaces a teammate opens. The parse tree is also authoritative
rather than a retrieval artifact, and `find_req` already serves the Eval Studio
picker's direct-entry validation, so this reuses a proven path instead of adding
one. `latest_match` follows D-207's latest-on-conflict contract rather than
inventing a second ambiguity rule. Rejected: chunk text (breaks on two of three
surfaces, and shows what retrieval happened to return rather than what the
requirement says); persisting chunk text on the row to fix that (reverses D-209
and grows the row for a UI convenience).

**Consequences:**
- Bubbles work identically on live, shared, and history surfaces with no schema
  change and no new persistence.
- The bubble can show a requirement whose text has been re-ingested since the
  answer was written. Acceptable and arguably correct — the bubble answers "what
  does this requirement say", not "what did retrieval see that day".
- A req_id resolving across several releases shows the latest with a muted
  notice, mirroring the Eval Studio picker's existing treatment.
- `find_req` scans parse trees, so bubbles stay strictly click-on-demand. Eager
  preloading of every bubble in an answer would mean N tree loads per render.

---

## D-DRAFT-3 — Linkification runs after markdown on text nodes only; the req endpoint joins the team gate

**Context:** The answer renders through `render_markdown` (`answer | md`), whose
module invariant is that it strips dangerous tags before parsing. Bubbles must
inject markup into that output without corrupting it, and the fragment they fetch
must be reachable by the people who read shared answers.

**Decision:** Linkification is a distinct step applied to the rendered HTML,
walking **text nodes only** — never a string replace over the Markup, and never a
pre-markdown substitution. The bubble body is served by a new read-only endpoint
returning an HTMX fragment, and that endpoint's prefix is added to
`team_mode._TEAM_ALLOWED` in the same change, verified with
`NORA_WEB_TEAM_MODE=1`.

**Why:** A naive replace over rendered Markup would hit req IDs inside `<code>`
elements, inside tag attributes, and inside the `Synthesized by …` provenance
epilogue, producing broken or nested markup. Text-node-only walking is the only
form that cannot corrupt the tree. Serving the body as an HTMX fragment keeps the
zero-npm / server-rendered invariant. The gate entry is not an afterthought:
`ea3edda` exists because shared-answer and history paths shipped without it and
gated experts were redirected away from the feature — CLAUDE.md's branch-flow
rule now requires integration surfaces to be verified with the gate ON, not only
in an ungated dev run.

**Consequences:**
- `web` Public surface gains one read-only endpoint and one Jinja filter;
  `MODULE.md` ships in this branch alongside the code.
- The renderer invariant extends: answer text is filtered, then linkified;
  raw chunk text in the engineering click-to-expand view stays unfiltered and
  un-linkified.
- Open sub-question for implementation: whether a req ID the LLM already wrapped
  in backticks gets a bubble or is left as literal code. Decide before coding.

---
