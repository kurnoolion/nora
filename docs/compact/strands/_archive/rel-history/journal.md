## 2026-10-26 — Part one: the History store validated on two real releases

### Done this session
- Design v0.1: validation procedure; the synthesized release diff with
  a general pair packer and parallel runner in `llm`.
- Validation on one MNO, two consecutive releases, three rounds. Found
  and fixed: `most_changed_sections` keyed per requirement (now per
  containing section, ranked by edit rows); every `moved` row was a
  renumbering with the parent unchanged (new kind `renumbered`, detail
  chapter/sibling/parent/other; `moved` needs a new parent); nine
  removed plans were absent from the newer release's documents (now
  `absent_plans` + `edits`); 194 of 326 `changed` rows were byte-order
  marks and zero-width characters (canonical form v3 strips Unicode
  format characters, the IR strips them at extraction, the Overlay
  restamp tool recomputes stored fingerprints, the registry's unit
  identity carries the fingerprint version so a bump republishes once).
- Roll-out: every stack on v3 with expansion-off Stage-1 at 0 of 39;
  the diff reads moved 0 / renumbered 444 / 9 absent plans, 530 rows /
  changed 135 / 7 renamed pairs, as predicted.
- Test image runs its files by path; the cell-DB loader suite restores
  the service module after each test (its reload leaked cell-db mode
  into later suites wherever bm25x is installed).

### Next
- Part two, development phase: `changed_pairs` on the History store;
  packer and runner in `llm`; prompts and reassembly in `query`; the
  SSE route with cancel in `web`; behind `NORA_HISTORY_DIFF_ENABLED`.

### Flags
- Trees built before the strip still carry the characters in their
  text; v3 hashes ignore them. The next build cycle's trees are clean.
- Cells converted from labels without the fingerprint sidecar have no
  fingerprint column; they republish once a sidecar label exists.
- `req_history` reports a renumbered parent as `moved` (one node's
  intervals cannot see the parent pair); the diff resolves it.

## 2026-11-03 — Parts two and three: the release-change lane, its page, the field rounds

### Done this session
- Lane: History `changed_pairs` → size-bounded batches (one plan, one kind)
  → bounded parallel runner → per-plan reassembly; SSE partials and cancel;
  the lane replaces SIRA for release-change questions.
- Eleven field rounds on the fast model settled the working-text detector
  and strip (phrase density, cuts wherever the text sits, reasoning before
  the first id, announcements, prompt echoes, lone phrases, full-width
  brackets), one blunter retry first; counts and one log line per firing.
  0 working-text blocks on five answers across the stacks.
- Gateway pages: scrubbed in logs; as a reply → failed batch; trailing a
  completed answer → cut, answer kept; per-batch timeout cap 300 s.
- Triage: bookkeeping pairs out of the model's input by configured
  annotation rules, deterministic notes; `~same` marks taken and counted
  once; headline counts store-derived, the model's verdicts beside them.
- Page: compared releases and summary first, counts table, plans collapsed,
  carrier release names and quarter phrases, side-by-side bubbles (live and
  stored); then Table / Tree / expanded views and Export (Raw Markdown,
  Text, HTML, PDF) with an Include choice; thread layout.
- Stores: `history_build --release/--exclude`; `publish --exclude`; the
  stack name on every feedback row.
- Field: blank completions from the provider (four in one run) retried,
  counted (`empty=`), the section kept with a status line; trailing-cut log
  on retries.
- Host: the third deployment stack retired (it served nothing but our checks;
  its env files kept aside); images and build cache cleaned; the two mno-c plans absent from the newer
  release confirmed as left out of ingestion by choice.

### Next
- Land the strand.
- Applicability, as its own strand: a per-carrier non-applicable list by
  document or chapter, a flag carried from publish, a default filter in
  every lane, every chapter compared with non-applicable plans marked.

### Flags
- mno-c's carrier release names for `release_names` still unset (optional).
- Parser side, next build cycle: revision record and applicability statement
  lifted into fields; mno-c segmentation (a heading line ending in the id
  starts a requirement; bodies reduced to the id; a next requirement folded
  in); inline section cross-references. The lane's filter stays as the net.
- Blank completions are a standing hazard: watch `empty=` on the RHD line.
- `test_playground_helpers.py` fails only when run with other files
  (event loop); pre-existing.
