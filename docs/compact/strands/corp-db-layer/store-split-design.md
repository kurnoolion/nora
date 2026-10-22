# Store split — four orthogonal stores behind service interfaces

Strand `corp-db-layer` · v1.0 · 2026-10-21 · supersedes `storage-design.md`
§2.1 (baked enriched cell DB) and §5a step 1; replaces `phase-4-plan.md` v0.

## 1. Why

The phase-1–3 cell DB bakes three layers — corpus index, SIRA phrases,
review edits — into one enriched blob. That loses the raw SIRA output,
makes review read-only in cell-db mode (Apply → 409), and ties
corrections to a republish. Splitting data by kind, each kind behind its
own interface, keeps every consumer (query, review, diff, display) simple
and lets new uses be added without touching the others.

## 2. The four stores

| Store | Holds | Unit | Written | Read by |
|---|---|---|---|---|
| **Cells** (retrieval) | corpus rows, **vanilla** BM25 index, SIRA phrases per req (raw, per run) | one DB per (MNO, release) version | build; publish (write-once) | nora-store |
| **Trees** (parsed) | full requirement text and tables, document/section/requirement hierarchy | one DB per (MNO, release) version | build; publish (write-once) | nora-web (display, `/api/req`, diff) |
| **History** (release lineage) | one tree per MNO across releases; per node and release: added / changed / unchanged / removed; roll-ups per section and plan | one DB per MNO, rebuilt per publish | derived from Trees at publish | nora-web, eval, future apps |
| **Overlay** (review edits) | per MNO: `(req_id, fingerprint) → adds / removes / suppress_all`, with label, reason, author, time, origin release | one DB per deployment | review UI (live) | nora-store (compose), nora-web (review) |

Cells, Trees and History are immutable per version. Overlay is the only
mutable store, so corrections go live without a republish (D-DRAFT-21
withdrawn, D-DRAFT-24).

## 3. Fingerprints

Three requirement-level hashes, each with one job (D-DRAFT-27). Each value
carries its canonical-form version (`v2:<sha256>`), so values from
different versions never compare equal.

| Hash | Covers | Used by |
|---|---|---|
| `text_fp` | title + body (tables inline), canonical form | the shared join key: SIRA adapter sidecar, Cells, Overlay edits and fold, Trees |
| `node_fp` | `text_fp` + priority + applicability | History: did the node itself change? |
| `subtree_fp` | `node_fp` + one entry per child, **in document order**: hash of (child key, child location, child `subtree_fp`) | History: did anything under it change? |

**Canonical form (v2):** Unicode NFKC, case fold, typographic variants
unified (quotes, dashes, bullets), whitespace collapsed to one space,
trimmed. Display text is never altered. Numbers, units, modal verbs and
negations stay significant. Location (section number, path), plan,
context and cross-references are excluded: a move is recorded as a
location change, not a content change.

`text_fp` says the requirement's own content is unchanged; it does not say
its SIRA enrichment is still valid, because phrases are generated from the
full corpus row (context and cross-references included). Reusing
enrichment across releases must key on a hash of the corpus row.

Only `text_fp` leaves History. `node_fp` and `subtree_fp` are computed when
History is built from Trees. Data, prompt, profile and overlay digests
elsewhere in the code are a different kind of fingerprint and keep their
names.

## 4. Index composition (Cells + Overlay → BM25)

Per cell, at load:

1. Load the vanilla index (corpus text only).
2. Effective phrases per req = `suppress_all ? adds : (SIRA − removes) + adds`
   (remove wins). SIRA only adds terms; review edits add or remove SIRA
   phrases, never corpus terms.
3. An Overlay edit applies to a req when its fingerprint matches, else
   when token-set Jaccard against the origin release ≥ 0.85; otherwise it
   is **held** for re-affirmation (unchanged rule, now keyed by fingerprint).
4. `enrich_batch(effective)` → serving index.

**No composed-index cache** (D-DRAFT-23, measured 2026-10-22): composing at
load took 1.4 s for 40k rows on a schema-2 stack against 1.7 s for the
flat path. Revisit if load passes about 30 s. Nothing composed is ever
published.

Query-time expansion (with `NORA_SIRA_MAX_DF_RATIO`) is unchanged and
outside the stores.

## 5. Interfaces and services

- One Python protocol per store: `CellStore`, `TreeStore`, `HistoryStore`,
  `OverlayStore`. Each has a **local** implementation (own backend) and an
  **HTTP client** with the same methods. `NORA_<STORE>_URL` set → client;
  unset → local.
- A hosting service mounts a store's routes under `/stores/<store>/v1/…`
  (JSON). Hosts today: nora-store (`cells`), nora-web (`trees`, `history`,
  `overlay`). Moving a store to its own service = deploy the same routes,
  set the URL; no consumer change.
- **No store reads another store's backend.** Composition (§4) happens in
  the consumer: nora-store reads Overlay through `OverlayStore`.
- Overlay has one writer (nora-web). Until it moves to its own service,
  nora-store reads the same SQLite file read-only (WAL) via the local
  implementation.
- Minimum surface (v1):
  - `CellStore`: list cells, cell meta/identity, corpus rows, vanilla index, SIRA phrases.
  - `TreeStore`: list docs/plans, get node, subtree, ancestors, find req (by id, by fingerprint).
  - `HistoryStore`: req lineage, node status per release, changed nodes between two releases, roll-ups.
  - `OverlayStore`: get/put edits per MNO, digest, labels (accept/merge).

## 6. Backends

SQLite for the published stores (Cells, Trees, History). Overlay and the
registry are SQLite in dev/test; in corp, SQLite or Postgres, decided by
§9 question 4. Schemas stay Postgres-portable (no SQLite-only types; FTS5
in Cells stays optional).

- **Cells:** `meta`, `corpus`, `index_blob` (**vanilla**), `sira_phrases(req_id, run_id, phrases)`.
  The `enrichment` (effective) table is removed.
- **Trees:** `doc`, `node(id, doc, parent_id, path, kind, req_id, section_number, title, depth)`,
  `req_body(req_id, text, tables_json, fingerprint)`. `path` is a
  zero-padded ordinal path, indexed: subtree = range scan, ancestors =
  prefixes; `WITH RECURSIVE` for the rest (Postgres: `ltree`).
- **History:** one DB per MNO (§6a).
- **Overlay:** `edit(id, mno, req_id, fingerprint, op, word, label, reason_json, by, at, origin_release)`,
  `suppress(mno, req_id, …)`, `accepted_label(label)`. Records are never
  rewritten (provenance), as today.

## 6a. History — structure and queries

**Identity.** A node's key is stable across releases: a plan is its
`plan_id`; a section is `(plan_id, section_number)`; a requirement is
`(plan_id, req_id)`, with its section kept as a location attribute. A
renumbered section is a removed node plus an added node; a requirement that
moves keeps its key and records a new location.

**Tables** (SQLite, Postgres-portable):

| Table | Holds |
|---|---|
| `release(mno, release, ord)` | release order |
| `node(key, kind, plan_id, local_id, parent_key)` | stable identity, the latest parent |
| `version(node_key, valid_from, valid_to, node_fp, subtree_fp, child_set_fp, text_fp, attrs_json, location_json)` | a row only when a hash or the location changes; `valid_to` empty while current |
| `content(text_fp, title, text)` | content-addressed: each distinct text stored once (first-seen spelling) |

`child_set_fp` hashes the same child entries sorted: when it is equal and
`subtree_fp` differs, the change is a **reorder**. A child entry carries
the child's key and location, not only its hash: with bare hashes a
renumbered section or a same-content rename would leave every ancestor's
`subtree_fp` unchanged and the diff descent would never reach it.

**Change kinds between two releases:** added, removed, changed (`node_fp`),
moved (location), reordered (above); a likely rename (a new key whose
`text_fp` equals a removed key's) is tagged on the added/removed pair, not
counted instead of it.

**Queries:**

| Question | Query |
|---|---|
| Version history of a requirement (from–to, default oldest→latest) | its `version` rows; consecutive `content` rows diffed word by word |
| Version history of a plan | the same for every node under the plan whose `subtree_fp` differs between releases |
| When did a requirement last change? | the latest `valid_from` with a new `node_fp` |
| When did a plan last change? | the latest `valid_from` with a new `subtree_fp` on the plan node |
| Changes between two releases of an MNO | descend from the plans, only into nodes whose `subtree_fp` differs; counts per change kind per plan and section, plus the diffs |
| Changes in the latest release | the same for releases N and N−1 |

Summaries are deterministic (counts, most-changed sections, diffs); an LLM
paragraph may be written over them, never in place of them. Mapping a name
such as a plan's display name to a `plan_id` belongs to the query layer.

## 7. Publish (replaces storage-design §5a step 1)

A release publishes Cells and Trees per cell, rebuilds History per MNO,
and records all three in `registry.db` (`store_versions(store, unit,
version, fingerprint, file, sha256, gate_ref)`, `active(store, unit,
version)`). Stage → golden gate → flip (one transaction per release) →
rollback by flip. Overlay is not published; it lives with the deployment.

## 8. What carries over, what changes

- Flat build files stay as dev/test build inputs (converter, verify gate,
  tools). Corp serves only stores.
- Local stacks can move to store-backed serving once Overlay is a store
  (Apply works).
- The `--overlay-root` cycle fix is dropped (nothing is baked).
- Unchanged: SIRA query-time expansion, golden eval over HTTP, team gate.

## 9. Open questions

1. ~~History keys~~ — decided 2026-10-22 (§6a).
2. Overlay retention: purge held edits after N releases?
3. Corp metrics/config DBs and transfer (storage-design §8 points 2, 3).
4. Corp backend for the mutable stores (Overlay, registry): Postgres if
   corp runs more than one writing host, keeps mutable files on a network
   share, or provides a managed Postgres; otherwise SQLite (WAL,
   `busy_timeout`). SQLite allows one writer per file and coordinates by
   OS file locks, which do not work across hosts or reliably on NFS/SMB.
