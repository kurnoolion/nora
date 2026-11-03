### D-DRAFT-1 — Canonical form v3 strips Unicode format characters; the IR strips them at extraction

**Decision:** `canonical_text` removes category-Cf characters before NFKC; `FINGERPRINT_VERSION` = v3; `ContentBlock`/`TextRun` strip them at construction.
**Why:** 194 of 326 "changed" requirements between two real releases differed only by byte-order marks. Rejected: History-only normalization (breaks the shared join key); extraction-only (older trees stay noisy).
**Consequences:** Every fingerprint changed once; `overlay_restamp` recomputes stored record fingerprints from the origin-release text; unit identity carries the version (D-DRAFT-4).

### D-DRAFT-2 — `moved` needs a different parent; a changed outline number is `renumbered`

**Decision:** Location kinds: parent changed → `moved`; same parent, number changed → `renumbered` (chapter | sibling | other); a parent that vanished and reappeared with identical content → `renumbered` (parent).
**Why:** All 444 "moves" in one release pair kept their parent; a chapter insertion shifted their numbers. Rejected: masking children of renamed sections only (caught 0 of 444; parents are requirements, not sections).
**Consequences:** `req_history` still reports a parent-key change as `moved`; the diff resolves it with both releases' states.

### D-DRAFT-3 — A plan node added or removed marks the plan absent; edits exclude its rows

**Decision:** `diff_releases` reports `absent_plans`, `edits` (tallies without their rows) beside `counts`, and ranks `most_changed_sections` from edit rows only.
**Why:** 530 of 866 removals were nine plans missing from the newer release's documents, not edits. Rejected: dropping those rows (the page must say "plan not in this release").
**Consequences:** Consumers quote `edits` for change claims and `absent_plans` for coverage.

### D-DRAFT-4 — Store-version identity includes the fingerprint version (amends D-283)

**Decision:** `store_version.fingerprint_version`, an additive column `ensure()` adds to an older registry; reuse keys on fingerprint, schema and fingerprint version.
**Why:** Neither source fingerprint nor schema moves on a canonical-form change, so a republish would have kept every v2 file. Rejected: folding the version into `source_fingerprint` (breaks the flat-vs-DB identity parity check).
**Consequences:** Rows from before read as unversioned, so every unit republishes once after a bump; units with no fingerprint column reuse as before.

### D-DRAFT-5 — A change row's containing section: tree section parent, else nearest section node, else a two-level prefix

**Decision:** `containing_section()` in that order; sections aggregate and rank by it.
**Why:** On real corpora requirements hang under requirements or the plan root and nest four levels deep; per-requirement keys ranked requirements, raw prefixes split one region across levels. Rejected: a fixed two-level prefix alone (ignores real section nodes where they exist).
**Consequences:** Ranking is a region view; `section` on the row keeps the requirement's own number.

### D-DRAFT-6 — Release-change questions run on the History lane in place of SIRA

**Decision:** A release-change intent with a served History store replaces the SIRA lane in the stream; no second lane runs.
**Why:** Chunk retrieval cannot answer "what changed between releases"; History holds the exact pairs. Rejected: SIRA alongside (doubled latency, two answers to reconcile).
**Consequences:** Lane-specific answer shape, bubbles and feedback fields; a History-less deployment keeps SIRA.

### D-DRAFT-7 — Bookkeeping pairs are triaged out before the model

**Decision:** `triage_pairs` drops pairs equal once configured annotation lines and fragments are removed, plus title-only, attribute-only, layout-only pairs and heading-only entries; each becomes a deterministic note.
**Why:** 456 of 710 pairs on one MNO differed in the revision record alone; the model wrote prose about nothing. Rejected: asking the model to ignore them (unreliable).
**Consequences:** Rules in `config/release_diff.json`; a new annotation style needs a rule, not a prompt; `sent=`/`dropped=` on the RHD line.

### D-DRAFT-8 — The model never decides what the page shows about unchanged requirements

**Decision:** `[ID] ~same` lines are taken out and counted; headline counts are store-derived and run-independent; the model's verdicts are stated beside them.
**Why:** Verdict counts varied between runs on the same input, and a headline that moved read as a bug. Rejected: the model's count as "changed".
**Consequences:** "N changed in text (the model judged M unchanged on this run)"; the model's prose about an unchanged pair is dropped.

### D-DRAFT-9 — Working text is fixed in the detector, not by pinning a model

**Decision:** One blunter retry, then heuristic cuts; the lane runs on the roster's selected model; the pin knob stays unset.
**Why:** The fast model leaks in its own idiom but its answers were judged as good. Rejected: forcing the thinking model (slower, leaks differently).
**Consequences:** Phrase lists grow with field rounds; every firing logs what matched, never the text.

### D-DRAFT-10 — A gateway error page as content fails the batch; one trailing an answer is cut

**Decision:** `split_gateway_reply` finds a gateway page anywhere: as the reply or before any id → one retry, then a failed batch; after a completed answer → cut, answer kept, `trimmed=`.
**Why:** The corporate gateway returns its error page with HTTP 200; rendering it showed internal text.
**Consequences:** Logs scrubbed of host and URL; a partial answer before the page is the error path.

### D-DRAFT-11 — A per-batch timeout cap under the roster's timeout (default 300 s)

**Decision:** The lane's provider takes `timeout_cap`; it lowers the roster entry's timeout, never raises it.
**Why:** One stuck call held the answer for the roster's long timeout; the ceiling failed it cleanly in the field.
**Consequences:** A batch over the cap fails with its ids listed; `NORA_HISTORY_DIFF_BATCH_TIMEOUT_S`.

### D-DRAFT-12 — `publish --exclude` drops a unit from a release set; History follows

**Decision:** `--exclude <mno>__<rel>` removes that unit's cells and trees, built or inherited, from the new set; History is rebuilt without it; `excluded` in the summary.
**Why:** A stray parse directory had become a release in a lineage; a set is always complete (D-284), so removal is a publish, not an edit.
**Consequences:** The previous set stays as rollback; `history_build --release/--exclude` mirrors it.

### D-DRAFT-13 — Bubbles on a release-change answer open both releases' versions

**Decision:** Ids route to `GET /api/test/history/req/{id}?mno&plan&from&to`, the version live at each release side by side with word diffs; the id-to-plan map rides on the feedback row so stored answers keep it.
**Why:** The default bubble shows the latest text, one side of the change. Rejected: storing rendered HTML (bulk; bubbles would freeze).
**Consequences:** Older rows fall back to the latest text; a History-less stack keeps the default route.

### D-DRAFT-14 — Views and exports are client-side over one answer block

**Decision:** The answer is one structured block; a page script builds Table / Tree / expanded views by moving each plan's sections, and Raw / Text / HTML / PDF exports from the DOM; Raw always complete, the others follow the view; no new routes.
**Why:** Stored answers must work on the History pane and share page without new persistence; a copy per view doubles the bubbles. Rejected: server-side export routes (the lane result is not persisted).
**Consequences:** Earlier answers render as before; a MutationObserver finds the block; a browser test drives the script.

### D-DRAFT-15 — A blank completion is retried once, then counted and named on the page

**Decision:** A reply with no text is retried with the blunter instruction; still blank, counted (`empty=`) and logged with its raw length; the plan's Changed section stays with a status line naming the pairs.
**Why:** The provider returned empty bodies with HTTP 200, four in one run; both renderers hid the loss and the table disagreed with the sections.
**Consequences:** Every table count has a section behind it; `raw_chars` tells blank from cut.

### D-DRAFT-16 — Size-bounded batches, one plan and one kind each, through a bounded parallel runner

**Decision:** `llm.batching.pack` groups items under a byte budget (32 KB default) without splitting one, one plan and one kind per batch; `llm.parallel.run` runs up to N at once (default 4) with per-batch outcomes, cancel and reassembly by index; partials stream as batches finish.
**Why:** One call per pair was too many calls; one call per MNO exceeded the context and lost everything on one failure. Rejected: a sequential pass (minutes longer, no partial answer on cancel).
**Consequences:** The batch is the unit of retry, failure, cancel and logging; `batch_ms` on the RHD line.
