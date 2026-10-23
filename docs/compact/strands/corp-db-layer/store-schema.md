# Store schemas — the four persistent corpus stores

Strand `corp-db-layer` · 2026-10-22 · taken from the DDL in the code (branch
`store-split`). Design rationale lives in `store-split-design.md`; this file
is the reference for *what is stored where*, with diagrams.

## 1. The picture

```mermaid
flowchart LR
  subgraph batch["nora-batch (build jobs)"]
    parse["parse stage<br/>out/parse/&lt;mno&gt;/&lt;rel&gt;/*_tree.json"]
    adapter["SIRA adapter<br/>corpus.jsonl + fingerprints.jsonl"]
    sira["SIRA lane<br/>vanilla bm25x index + phrases"]
    conv["cell_db_convert"]
    tb["trees_build"]
    hb["history_build"]
    parse --> adapter --> sira --> conv
    parse --> tb --> hb
  end

  subgraph store["nora-store (one process)"]
    cells[("Cells<br/>cell-&lt;mno&gt;-&lt;rel&gt;.db")]
    trees[("Trees<br/>trees-&lt;mno&gt;-&lt;rel&gt;.db")]
    hist[("History<br/>history-&lt;mno&gt;.db")]
    ovl[("Overlay<br/>overlay.db or JSON")]
    compose["compose at load:<br/>vanilla + SIRA + Overlay → BM25"]
    query["POST /query"]
    api["/stores/&lt;store&gt;/v1"]
    cells --> compose --> query
    ovl --> compose
    cells & trees & hist & ovl --> api
  end

  conv -->|write-once| cells
  tb -->|write-once| trees
  hb -->|write-once| hist

  subgraph web["nora-web (web layer only)"]
    ask["Ask page"]
    review["Enrichment review"]
    browser["Requirement Browser,<br/>Eval Studio, /api/req"]
  end
  ask --> query
  review -->|"POST /edit (one op per call)"| api
  browser --> api
  peer["peer nora-store<br/>(A/B stack)"] -->|"NORA_OVERLAY_URL (read)"| api
```

- **Write-once:** Cells, Trees, History. A new version is a new file; a
  rebuild is the migration.
- **Mutable:** Overlay only. One writer: the nora-store that hosts it (the
  *authority*). nora-web sends edits over HTTP; a peer store reads it over
  HTTP.
- nora-web keeps no corpus data. Its file backends exist only as a dev
  fallback when the `NORA_<STORE>_URL` variables are unset.

## 2. Fingerprints (the join keys)

All values are `v2:<sha256 hex>`; the prefix is the canonical-form version.

| Hash | Over | Stored in |
|---|---|---|
| `text_fp` | canonical title + body (tables inline) | Cells `corpus.fingerprint`, Trees `node.fingerprint`, Overlay `record.fingerprint`, History `version.text_fp` |
| `node_fp` | `text_fp` + priority + applicability | History only |
| `subtree_fp` | `node_fp` + one entry per child in document order: (child key, child location, child `subtree_fp`) | History only |
| `child_set_fp` | the same child entries, sorted | History only (reorder detection) |

Canonical form v2: Unicode NFKC, quotes/dashes/bullets unified, case fold,
whitespace collapsed. Location is excluded, so a move is a location change,
not a content change. `text_fp` says the requirement's own content is the
same; it does not certify SIRA enrichment (phrases come from the full corpus
row).

## 3. Cells — retrieval

One SQLite file per (MNO, release): `cell-<mno>-<release>.db`, schema 2.
Built by `sandbox/cell_db_convert.py` from the SIRA flat build. Read by
nora-store at start; the index is composed in memory (§7).

```mermaid
erDiagram
  meta {
    TEXT key PK
    TEXT value
  }
  corpus {
    INTEGER doc PK "load order; index blobs score rows positionally"
    TEXT req_id "NOT unique: doc:/section: rows repeat; lookup = last wins"
    TEXT title
    TEXT text "the SIRA corpus row text (context included)"
    TEXT fingerprint "text_fp; empty for composite rows"
  }
  sira_phrases {
    TEXT req_id PK
    TEXT phrases "JSON array, SIRA RAW output"
    TEXT run_id
  }
  index_blob {
    TEXT filename PK "bm25x serialized file"
    TEXT codec "zlib | raw"
    BLOB data "the VANILLA index"
  }
```

- Index: `corpus(req_id)`.
- `meta` keys: `schema_version` (2), `converter_version`, `cell_mno`,
  `cell_release`, `source_label`, `source_fingerprint` (identity of the
  served data: sha256 over `corpus.jsonl` + the phrase file), `enrich_run`,
  `enrich_model`, `sira_docs`, `fingerprinted_docs`, `fingerprint_version`
  (empty when the label predates the adapter sidecar), `n_docs`,
  `bm25x_max_n`, `bm25x_ngrams`, `index_files`, `created_at`.
- **Not stored:** effective (post-overlay) phrases, review edits, an
  enriched index. Schema 1 (baked enriched index, `enrichment` table) is
  still loadable; it cannot take review edits.
- HTTP (`/stores/cells/v1`): `GET /cells`, `GET /cells/{mno}/{release}/meta`,
  `/rows?req_id=`, `/phrases?req_id=`, `/effective?req_id=&label=`. No
  index blobs over HTTP: the index is consumed in-process by `POST /query`.

## 4. Trees — parsed requirement trees

One SQLite file per (MNO, release): `trees-<mno>-<release>.db`, schema 1.
Built by `core.src.stores.trees_build` from `out/parse/<mno>/<release>/*_tree.json`.
The file backend over `out/parse` is the default; `NORA_TREES_ROOT` selects
SQLite.

```mermaid
erDiagram
  doc ||--o{ node : contains
  node ||--o{ node : "parent_id"
  meta {
    TEXT key PK
    TEXT value
  }
  doc {
    TEXT doc_id PK
    TEXT plan_id
    TEXT plan_name
    INTEGER req_key_pos "position of the requirements key (exact JSON round-trip)"
    TEXT tree_json "every tree key except requirements"
  }
  node {
    INTEGER id PK
    TEXT doc_id FK
    INTEGER seq "document order; UNIQUE(doc_id, seq)"
    INTEGER parent_id FK "NULL = root"
    TEXT path "zero-padded ordinal path 000001.000003.…"
    INTEGER depth
    TEXT req_id
    TEXT req_id_cf "casefolded req_id (ignore_case lookups)"
    TEXT section_number
    TEXT title
    TEXT plan_id "effective plan: own plan_id or the tree's"
    INTEGER is_requirement
    TEXT fingerprint "text_fp"
    TEXT req_json "the full original requirement dict"
  }
```

- Indexes: `req_id`, `req_id_cf`, `fingerprint`, `plan_id`, `(doc_id, path)`, `parent_id`.
- Hierarchy = `parent_req_id` from the tree. Subtree = range scan on
  `path`; ancestors = path prefixes. A repeated id in one document: the
  last copy is the addressable node.
- `meta` keys: `schema_version` (1), `fingerprint_version`, `mno`,
  `release`, `cell`, `n_docs`, `n_nodes`, `source_fingerprint` (sha256 over
  the tree files' names and bytes), `created_at`.
- **New parse-tree fields need no schema change:** they ride in `req_json`
  / `tree_json`. A field becomes a column only when queried or indexed.
- HTTP (`/stores/trees/v1`): `/cells`, `/docs`, `/tree`, `/plans`, `/find`,
  `/reqs`, `/node`, `/children`, `/subtree`, `/ancestors`, `/fingerprint`.

```mermaid
flowchart TD
  P["plan node (plan_id)"] --> S1["section 4 — path 000004"]
  S1 --> S11["section 4.1 — path 000004.000001"]
  S11 --> R1["REQ-A-0001 — path 000004.000001.000001"]
  S11 --> R2["REQ-A-0002 — path 000004.000001.000002"]
  S1 --> S12["section 4.2 — path 000004.000002"]
  S12 --> R3["REQ-A-0003 — path 000004.000002.000001"]
```

## 5. History — release lineage per MNO

One SQLite file per MNO: `history-<mno>.db`, schema 1. Built by
`core.src.stores.history_build` from the Trees store across the MNO's
releases, in release order. Derived data: rebuilt, never edited.
`NORA_HISTORY_ROOT` selects it.

```mermaid
erDiagram
  node ||--o{ version : "versions over time"
  content ||--o{ version : "text_fp"
  release ||--o{ version : "valid_from / valid_to"
  meta {
    TEXT key PK
    TEXT value
  }
  release {
    TEXT mno PK
    TEXT release PK
    INTEGER ord "release order"
  }
  node {
    TEXT key PK "JSON [kind, plan_id, local_id]"
    TEXT kind "plan | section | requirement"
    TEXT plan_id
    TEXT local_id "plan_id | section_number | req_id"
    TEXT parent_key "latest parent"
  }
  version {
    TEXT node_key PK,FK
    INTEGER from_ord PK
    TEXT valid_from
    TEXT valid_to "empty while current"
    INTEGER to_ord
    TEXT node_fp
    TEXT subtree_fp
    TEXT child_set_fp
    TEXT text_fp FK
    TEXT attrs_json "priority, applicability"
    TEXT location_json "parent key (+ section for a requirement)"
  }
  content {
    TEXT text_fp PK
    TEXT title
    TEXT text "first-seen spelling"
  }
```

- Indexes: unique `(kind, plan_id, local_id)`, `plan_id`, `(to_ord, from_ord)`.
- **A `version` row exists only when something changed**: a new `node_fp`,
  `subtree_fp` or location. Identical text across versions is stored once
  in `content`.
- **Identity across releases:** plan → `plan_id`; section →
  `(plan_id, section_number)`; requirement → `(plan_id, req_id)`, its
  section kept in `location_json`. A renumbered section is removed + added
  (tagged *likely renamed* when its `text_fp` matches); a requirement that
  moves keeps its key and records a new location.
- Change kinds between two releases: `added`, `removed`, `changed`
  (`node_fp`), `moved` (location), `reordered` (`child_set_fp` equal,
  `subtree_fp` different), `likely_renamed` (tag on an added/removed pair).
- `meta` keys: `schema_version` (1), `fingerprint_version`, `mno`,
  `releases`, counts, `source` (the cells' tree source fingerprints),
  `created_at`.
- HTTP (`/stores/history/v1`): `/mnos`, `/releases`, `/req`, `/plan`,
  `/req/last-changed`, `/plan/last-changed`, `/diff`, `/latest`.

How intervals read, for one requirement across four releases:

```mermaid
gantt
  title version rows for one requirement (REL-1 … REL-4)
  dateFormat X
  axisFormat %s
  section REQ-A-0001
  v1 node_fp=a, section 4.1      :done, 1, 3
  v2 node_fp=b (text changed)    :active, 3, 4
  v3 node_fp=b, section 4.2 (moved) :5, 4, 5
```

Three rows, not four: REL-1 and REL-2 share the first row (`valid_from`
REL-1, `valid_to` REL-2). "Last changed" = REL-3 (new `node_fp`); the
move at REL-4 is a location change and does not count.

A plan's `subtree_fp` descends only into changed branches:

```mermaid
flowchart TD
  P["plan — subtree_fp differs → descend"]
  P --> A["section 4 — equal → skip"]
  P --> B["section 5 — differs → descend"]
  B --> B1["REQ-A-0007 — node_fp differs → changed"]
  B --> B2["REQ-A-0008 — key new, text_fp matches a removed key → likely_renamed"]
  B --> B3["section 5.2 — equal → skip"]
```

## 6. Overlay — review edits (the only mutable store)

Per deployment. Backends: the JSON file layout (today's default,
`<corrections>/sira-enrich/{<MNO>.json, accepted-labels.json, labels.json,
reason-categories.json}`) or SQLite (`NORA_OVERLAY_DB`). Both return
byte-identical entries, so digests do not depend on the backend. Written
only by the authority nora-store, through `POST /stores/overlay/v1/edit`;
read by nora-store for composition (§7) and by nora-web for the review UI.

```mermaid
erDiagram
  record {
    TEXT mno PK
    TEXT req_id PK
    TEXT direction PK "remove | add | suppress_all"
    TEXT word PK "empty for suppress_all"
    INTEGER seq "insertion order within a direction"
    TEXT label "branch; empty = main"
    TEXT reason_json "{category, note}"
    TEXT by_user
    TEXT at "ISO-8601"
    TEXT origin_release "release the edit was made on"
    TEXT fingerprint "text_fp of the requirement at edit time"
    TEXT raw_json "the record as the JSON backend shapes it"
  }
  label {
    TEXT name PK
    INTEGER accepted "merged into main"
    INTEGER disabled
  }
  reason_category {
    INTEGER pos PK
    TEXT name
  }
  mno_version {
    TEXT mno PK
    REAL version "monotonic change marker"
  }
  meta {
    TEXT key PK
    TEXT value
  }
```

- Index: `record(label)`.
- One active record per (requirement, direction, word): re-editing a word
  replaces its record. Records are never rewritten by a merge; merging a
  label = flagging it `accepted`, so provenance survives.
- **When an edit applies to a release** (the fold, same code in server and
  local paths): its `fingerprint` equals the requirement's current
  `text_fp` → applies; else same release as `origin_release` → applies;
  else token-set Jaccard against the origin ≥ 0.85 → applies; otherwise
  **held** for re-affirmation.
- `view_digest(mno, label)`: sha256 of the records visible in a label view;
  nora-web compares it with the digest nora-store served to show "pending".
- HTTP (`/stores/overlay/v1`): GET `/mnos`, `/overlay/{mno}`,
  `/overlay/{mno}/entry`, `/overlay/{mno}/version`, `/overlay/{mno}/digest`,
  `/labels/accepted`, `/labels/disabled`, `/reasons`; PUT
  `/overlay/{mno}/entry`, `/labels/accepted`, `/labels/disabled`,
  `/reasons`; POST `/edit` (`{"op": …, "args": {…}}`, one op per call,
  run inside the store's transaction). A non-authority answers 409 on writes.

## 7. How a query is served (composition)

```mermaid
sequenceDiagram
  participant S as nora-store (start)
  participant C as Cells
  participant O as Overlay (local or NORA_OVERLAY_URL)
  participant Q as POST /query
  S->>C: load vanilla index + sira_phrases + fingerprints
  S->>O: get_overlay(mno), accepted labels
  S->>S: per req: effective = suppress_all ? adds : (SIRA − removes) + adds<br/>(fingerprint / same-release / Jaccard guard; else held)
  S->>S: enrich_batch(effective) → serving index (memory)
  Q->>S: question
  S-->>Q: scope → BM25 per cell → merge → rank
  Note over S,O: review Apply = reload one cell: re-read Cells + Overlay, recompose, swap
```

Measured 2026-10-22: 1.4 s to compose 5 cells (40k rows); no cache (D-DRAFT-23).

## 8. Configuration

| Variable | On | Meaning |
|---|---|---|
| `NORA_STORE_ROOT`, `NORA_STORE_INDEX=cell-db`, `NORA_STORE_ENGINE=bm25x` | nora-store | where the cell DBs are; serving mode |
| `NORA_STORE_CORRECTIONS_ROOT` / `NORA_OVERLAY_DB` | nora-store (authority) | Overlay backend: JSON root or SQLite file |
| `NORA_OVERLAY_URL` | peer nora-store | read a shared Overlay from the authority; writes refused |
| `NORA_ENV_DIR`, `NORA_TREES_ROOT`, `NORA_HISTORY_ROOT` | nora-store | Trees (file or SQLite) and History roots |
| `NORA_OVERLAY_URL`, `NORA_TREES_URL`, `NORA_HISTORY_URL` | nora-web | use the store over HTTP (default `http://nora-store:8040`); unset = local file fallback |
| `NORA_STORE_URL(S)` | nora-web | retrieval and Apply targets |

To host a store on its own: `python -m core.src.stores.serve --stores trees,history,overlay --port PORT`
and set that store's URL on every consumer. Cells stays with `POST /query`
(the index lives in that process).

## 9. Versioning and change

- Every DB carries `schema_version` in `meta`; readers refuse an unsupported
  version: TRS-E007 / HIS-E006 / OVL-E005 (`SchemaVersionError`); the Cells
  reader raises `CellDbError` naming the schema (the CNV- codes belong to
  the converter).
- Additive changes (new column with a default, table, meta key, JSON or
  response field) do not bump the version; readers tolerate absence.
- Cells, Trees and History are rebuilt per publish, so a rebuild is the
  migration. Overlay gets in-place migrations when its schema first changes.
- HTTP routes are versioned in the path (`/v1`).
- Error-code families: `CNV-` (converter), `TRS-` (Trees), `HIS-` (History),
  `OVL-` (Overlay).
