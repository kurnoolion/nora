# Releases in the golden eval — design

**Status:** design agreed 2026-10-17; not built.
**Modules:** eval (schema, runner, CLI), web (Eval Studio), docs.
**Depends on:** a serving-side intent model (section 4.9), for one
sample kind only.
Release labels below (`Apr2098`, `Oct2098`, `Apr2099`) are
placeholders.

## 1. Problem

Each expected requirement in a golden sample is stored as
`(req_id, mno, release, plan)`, with `release` a fixed label. Recall
matching rejects a row whose release differs from a set qualifier
(`golden_runner._entry_matches`). Serving answers a question that names
no release from each MNO's latest release only (FR-multi-6,
`nora_store/scope.py:112`).

So when a new release of an MNO is served, every release-less sample
for that MNO scores zero on its old-release entries, even when the
stack retrieves the same ids. A field run showed it: one MNO's Stage-1
recall fell from about 0.68 to 0.13 after its second release was
flipped in, while the other cells held.

## 2. Review findings (current implementation)

| # | Where | Finding | Effect |
|---|---|---|---|
| F1 | `golden.GroundTruthEntry.release` | Only absolute labels, or empty. | The authoring intent ("the current revision", D-207) cannot be stored; tags go stale on every new release. |
| F2 | `golden_runner._entry_matches` | Release qualifier is strict when set. | With F1, a new release gives silent zeros, not an error. |
| F3 | `golden_runner.run_stage1` | `resolved_cells` is recorded per sample but never compared with the expected entries. | "Not searched" and "searched, not found" look the same. |
| F4 | `StackStamp.set_eval_set` | Digest covers the stored entries only. | A change to the matching rule, or to what a relative reference resolves to, would not change the key. |
| F5 | Run preflight | No check that the stack serves the release the set expects. | A stale set runs for an hour and gives plausible numbers. |
| F6 | `web/routes/golden_eval.py:_gt_context` | The golden response is curated from the tagged release's text. | When that text changes in a new release, the golden response can be wrong. Re-tagging ids does not fix Stage 2. |
| F7 | `Stage2Result` | The generated answer is not saved. | Old answers cannot be inspected after a release change. |
| F8 | `core/src/query/analyzer.py:101,292` | Release extraction knows month-year names and "latest"/"current". "changed"/"diff" set a release-diff query type, but scope uses only the release list. | A relative question ("new in recent requirements") searches only the latest release. |
| F9 | Eval Studio picker | Release list comes from the authoring env's parse trees, which can hold an ingested-but-not-served release. | "Latest in the picker" can differ from "latest on the stack". |
| F10 | `run_stage1` (`:538`), `SiraRowsPipeline.query` (`:615`) | Retrieved ids are de-duplicated by `req_id` alone, and Stage-2 rows are keyed by `req_id`. | A requirement present in two releases keeps only one release's text — the case a version question tests. |
| F11 | `_gt_context` (`:562`) | An unqualified entry takes the first match, not the latest. | Its golden response may come from any release. Not verified which order `find_req` returns. |

## 3. Decisions (agreed)

1. Standard questions are scored **without release information**:
   matching on `(req_id, mno)`.
2. The release check moves from each entry to the **scope of the
   question**: each searched cell must be the MNO's `N`, and `N` comes
   from an explicit **release map**, not from the stack's own latest.
3. The release a sample was authored against is kept as
   **provenance**, and a new release triggers a **text-change check**.
4. **Version questions** use releases in their answer key:
   - a question that names a release explicitly uses absolute labels;
   - a question with relative wording ("recent", "new", "added",
     "previous") uses `N`, `N-1`, ... Serving must map that wording
     to cells (section 4.9).
5. The comparability key includes a **scoring-rule version** and the
   **resolved** releases.

## 4. Design

### 4.1 Sample kinds

`GoldenSample.kind`: `standard` (default) | `version`.

- `standard` — a question about current behaviour. Ground-truth
  entries carry `req_id` and `mno`. Their `release` is ignored in
  matching. The expert does not choose a release.
- `version` — a question about releases or the differences between
  them. Every entry carries `release`, either absolute (`Apr2098`)
  or relative (`N`, `N-1`, ...). Absolute and relative entries are
  not mixed in one sample.

`kind` is scoring-relevant, so it goes into the eval-set digest
(the D-206 rule: scoring fields in, workflow metadata out).

### 4.2 Matching

- `standard`: an entry matches a row when `req_id` and `mno` match.
  The row's release does not matter for the hit; scope (4.4) checks it.
- `version`: an entry matches when `req_id`, `mno` and the
  **resolved** release all match. This is today's rule, applied after
  resolution.

The `release` field validator accepts `""`, `MMMYYYY`, and
`N` / `N-<k>`. A relative value needs a non-empty `mno`, and is only
valid in a `version` sample.

### 4.3 The release map

One versioned file in the golden dir:

```
<golden_dir>/release-map.json
{
  "version": 4,
  "updated_at": "2098-10-20T09:00:00Z",
  "updated_by": "<user>",
  "mnos": {
    "MNO-A": ["Apr2098", "Oct2098"],
    "MNO-B": ["Apr2098", "Oct2098"],
    "MNO-C": ["Apr2098", "Oct2098"]
  }
}
```

- Per MNO, releases oldest first. `N` is the last item; `N-k` is `k`
  places before it.
- History is kept in `release-map.history.jsonl` (one line per
  version), so a run can use an older version: `--release-map-version`.
- It is golden-set data: it lives with the samples (NFR-8), and a
  frozen copy of the set includes it.
- **Explicit, not derived from the stack.** All stacks share one
  golden set and flip at different times. A stack stuck on an old
  label has that label as its own latest, so a check against the
  stack's own latest would pass it.

**Advancing** is a step in the post-flip runbook, done by whoever
flips:

```
golden_release_cli advance --env-dir <env> --mno MNO-B --release Apr2099 [--yes]
```

It checks that parse trees exist for the release, runs the
text-change check (4.5), prints that report (ids and counts only), and
writes map version `v+1`. `golden_release_cli show` prints the current
map. The serve-flip hand-off line reminds the operator to advance.

### 4.4 Scope check and scope metric

For each sample, the runner derives the **expected scope** — the set
of cells the question should search:

- `standard`: `{(mno, N(mno))}` for each MNO in the entries;
- `version`: the resolved `(mno, release)` of each entry.

It compares that set with the `resolved_cells` the stack reports:

- **scope ok** — equal;
- **scope miss** — an expected cell was not searched; entries in it are
  counted as scope misses, not retrieval misses;
- **scope extra** — cells searched beyond the expected set. Not an
  error (a broad question may search more), but counted.

A separate `scope:` line after `s1:` carries the sample counts per scope
and the scope/retrieval split of missed entries (built in step 1; kept
off the `s1:` line so that line stays byte-comparable with older runs). This measures the serving intent model
(4.9) directly: whether it maps a question to the right MNOs and
releases.

**Preflight.** Before scoring, the runner reads `/healthz` `cells`.
For each MNO in the set it compares the stack's latest release with
the map's `N`. If they differ, the run stops with `GEV-E006`
("release map and stack disagree for MNO-B: map N=…, stack latest=…"),
saying to advance the map or pin `--release-map-version` for a stack
that has not flipped. `--allow-map-mismatch` overrides this and stamps
the mismatch on the `id:` line.

### 4.5 Provenance and the text-change check

**Provenance is the entry's stored release.** On a `standard` sample the
release is not matched (m2), so the value the picker stored is free to
mean "the release this entry was written from". *(As built in step 3;
this replaces an earlier `golden_meta.authored_release` field, which
would only have duplicated it.)* Entries with no release have no
provenance; the check counts them as "unattributed" and leaves them
alone.

When `advance` moves an MNO's `N`, it checks every entry of that MNO
whose stored release is older than the new `N`:

- `standard`: compare the entry's text in its stored release and in
  the new `N` (a per-cell parse-tree reader in the command —
  whitespace-normalised; web's `req_tree` cannot be imported from
  eval): unchanged / changed / absent (not in the new release) /
  unverified (not found in its own stored release);
- relative `version`: **always flagged**. Its answer is true for one
  `(N-1, N)` pair only, so moving `N` changes both the answer and the
  expected ids;
- absolute `version`: not flagged while its releases are still served.

A sample with any changed, absent or unverified entry, and every
relative `version` sample, gets `golden_meta.review[mno] = {from, to,
changed, absent, unverified, relative, flagged_at}`. Unchanged entries
have their stored release moved to the new `N`, in flagged samples too;
the others keep their old release, so the review can show both texts.

**Runner policy for flagged samples:**
- Stage 1 still scores them. The ids are still the expectation, and an
  absent id is a real miss the expert must rule on.
- Stage 2 skips them, with `GEV-W002` "pending review", counted. A
  golden response written from older text is not a valid reference.

The expert clears the flag in Eval Studio. A review panel shows the old
and new text of each changed or absent entry, beside the golden
response. Per entry, the expert can keep, replace with a successor id,
or remove. Per sample: confirm the golden response, or re-curate it.

A known gap: the check cannot find requirements that are *new* in `N`
and should be expected. Only the expert can add them.

### 4.6 Version samples

- **Absolute** — the question names the releases ("between Apr2098
  and Oct2098"). It works with today's serving. Stable while both
  releases are served.
- **Relative** — the question uses relative wording ("Are there new
  5G FR1 bands added for MNO-B in recent requirements?"); entries use
  `N`, `N-1`. Needs serving support (4.9). Flagged for re-curation on
  every advance (4.5).

Both kinds need the F10 fix:
- Stage 1 de-duplicates by `(req_id, mno, release)`;
- Stage 2 keys rows by the same triple.

Then both versions of a carried-over requirement reach synthesis.

The expert picks the form knowingly: relative tests current behaviour
and costs a re-curation per release; absolute is stable but tests an
older pair.

### 4.7 Comparability

- `eval_set_digest` / `eval_set_stage2_digest` are computed over the
  **scoring view** of the entries, plus `kind`: standard entries with the
  release blanked (it is not matched, so a provenance re-tag or a move of
  `N` keeps comparability); version entries with the resolved release.
  (Built in step 2; this refines "resolved entries" above.)
- `stage1_key` gains `match_rule` (a constant such as `m2`, bumped on
  any change to the matching rule), so runs before and after this
  change do not pool even when the stored samples are byte-identical.
- The `id:` line gains `rmap=v<N>` and `match=m2`.
- Flagged samples excluded from Stage 2 change the Stage-2 digest,
  which is expected.

### 4.8 Authoring (Eval Studio)

- A **kind** selector on the sample; `standard` is the default.
- `standard`: the picker still browses by MNO → Plan → Release, to
  find requirements. The stored entry keeps `mno`, `req_id` and the
  picked release as provenance (4.5). If the expert picks from a release
  other than `N`, Eval Studio warns: the text they see is not what the
  stack serves.
- `version`: each entry needs a release. A "relative" toggle stores
  `N` / `N-k` instead of the label.
- Direct entry (D-207) is unchanged for `standard`. A `version`
  sample takes the release from the picker selection.
- **QC warnings:**
  - a `standard` sample whose question names a release (it is really a
    `version` sample);
  - a relative `version` sample while serving lacks relative-scope
    support (4.9).

### 4.9 Dependency: serving intent model

Serving will use an LLM intent model to interpret a question. It maps
the question to MNOs, releases (including relative ones such as `N`
and `N-1`), narrow vs broad scope, and version-history intent. It
resolves "recent" / "new" / "previous" against the cells it serves.

The eval needs only what serving already reports: `resolved_cells` per
query. The scope check (4.4) then measures the intent model with no
eval change. Until that model ships, relative `version` samples score
scope misses on `N-1` by design, so Eval Studio warns when they are
authored (4.8).

The intent model is a requirement in its own right (an extension of
FR-multi-6) and belongs to the serving work, not to this strand.

## 5. Migration of the current set

`golden_release_cli migrate --env-dir <env> [--yes]` (dry run without `--yes`):

1. writes map `v1` from the releases the env holds, with the operator
   confirming `N` per MNO;
2. sets every sample to `kind: standard`, unless its question names a
   release; those are listed for the expert to mark as `version`;
3. runs the text-change check for entries whose stored release is
   older than `N` (relative `version` samples are not flagged, since
   `N` is not moving). The six samples behind the field finding are in
   this group. Their review uses the same side-by-side panel (4.5).

Runs after migration key apart from earlier runs (`match_rule`), as
intended.

## 6. Build order

| Step | Content | Size |
|---|---|---|
| 1 | Scope classification and counts (4.4, without the map: expected scope from entry releases) | small |
| 2 | `kind`, release grammar, `ReleaseMap` + history, resolution, release-free matching for `standard`, `match_rule`, digests over resolved entries, map/stack preflight, `rmap=` | medium |
| 3 | `golden_release_cli` show / migrate / advance (text-change check) / review (list, clear); review flag; Stage-2 skip of flagged samples | medium |
| 4 | F10 de-duplication fix — built: `row_key` = id + cell; Stage 1 `retrieved_row_keys`; sira-rows Stage 2 pins rows | small |
| 5 | Eval Studio: kind selector, release toggle, review panel, QC warnings — built, plus case normalisation at save, golden save keeping the flag, map-aware preview and chat context | medium |
| 6 | Docs: golden-eval guide, eval and web MODULE.md, decision drafts, serve-flip hand-off line | small |
| — | Serving intent model (4.9) | separate work |

Steps 1–3 fix the field case. Step 3 is needed before the next release
is flipped in. Relative `version` samples become useful only after
the intent model ships.

## 7. Open questions

1. A broad `standard` question may correctly search several MNOs. Is
   "scope extra" ever a failure for `standard` samples, or always
   informational?
2. Should F11 (unqualified entries take the first match) be fixed in
   the migration, by giving bare entries a release from the latest
   match?
3. Who curates relative `version` samples after each advance? It
   recurs every release.
