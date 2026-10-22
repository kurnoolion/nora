# stores

**Purpose**
Data stores behind service interfaces (strand corp-db-layer, `store-split-design.md`). Each store is one Python protocol with local backends; consumers compose stores, and no store reads another store's backend. Step S3 ships the **Overlay** store: the review edits to SIRA enrichments (word-level remove/add records, entry-level `suppress_all`, label merge log, disabled labels, reason categories), the only mutable store. Step S4 ships the **Trees** store: parsed requirement trees per (MNO, release) cell, with traversal by `parent_req_id`. Step S5 ships the **History** store: release lineage per MNO built from Trees (version intervals, content-addressed text, change queries). Implements D-DRAFT-22 (four orthogonal stores) and D-DRAFT-24 (Overlay is a live store keyed by requirement and fingerprint); Cells lands in a later step.

**Public surface**
- `OverlayStore` — `runtime_checkable` Protocol at entry-dict granularity. Reads: `get_overlay(mno) -> {req_id: entry}`, `get_entry(mno, req_id)`, `list_mnos()`, `version(mno) -> float` (monotonic change marker; 0.0 = no overlay), `accepted_labels()`, `disabled_labels()`, `reason_categories() -> list | None` (None = never set; the caller applies defaults). Writes: `transaction()` (context manager; exclusive across processes on one host; nested use joins; reads inside see the transaction's writes), `put_entry(mno, req_id, entry | None)` (None/{} deletes; always registers the MNO and bumps its version), `set_accepted_labels(set)`, `set_disabled_labels(set)`, `set_reason_categories(list)`. Attribute `read_only`.
- `JsonOverlayStore(corrections_root, read_only=False)` — the original layout under `<root>/sira-enrich/`: `<MNO>.json`, `accepted-labels.json` (`{"accepted": [...]}`), `labels.json` (`{"disabled": [...]}`), `reason-categories.json`. Transaction = flock on `sira-enrich/.lock`; writes buffered per transaction and flushed per file with tmp + rename. `version` = file mtime.
- `SqliteOverlayStore(db_path, read_only=False, busy_timeout_ms=5000)` — one file per deployment. WAL, `busy_timeout`, `BEGIN IMMEDIATE` transactions, per-call connections. Tables: `record(mno, req_id, direction CHECK IN ('remove','add','suppress_all'), word ('' for suppress_all), seq, label, reason_json, by_user, at, origin_release, fingerprint, raw_json, PK(mno, req_id, direction, word))`, `label(name PK, accepted, disabled)`, `reason_category(pos, name)`, `mno_version(mno PK, version)` (wall-clock epoch forced strictly increasing), `meta(key, value)`. `read_only=True` opens with a `mode=ro` URI; a missing file reads as empty and is never created. `is_empty()`, `clear()` for the import CLI.
- `open_overlay_store(corrections_root="", db_path="", read_only=False) -> OverlayStore | None` — `db_path`, else `$NORA_OVERLAY_DB` → SQLite; else `corrections_root` → JSON; else None (overlay disabled).
- `OverlayReadOnlyError` — a write on a read-only store.
- CLI `python -m core.src.stores.overlay_import --corrections-root <dir> --db <path> [--force]` (`overlay_import.py`, `import_overlay()`) — one-time copy of the JSON Overlay into SQLite; verifies `get_overlay(mno)` per MNO, the MNO list, label sets and categories are equal across backends inside the same transaction (mismatch → rollback); prints counts only. Error codes `OVL-E001` (no `sira-enrich/` dir), `OVL-E002` (non-empty target without `--force`), `OVL-E003` (verify mismatch), `OVL-E004` (SQLite failure).
- `TreeStore` — `runtime_checkable` Protocol (read-only; cells are write-once). `list_cells() -> [{mno, release}]` (by MNO, then chronological release), `list_docs(mno="", release="")` (one cell with both qualifiers, else every cell; sorted, a doc listed once per cell), `load_tree(doc_id, mno="", release="") -> dict` ({} when missing; shared, read-only), `plans_for_mno(mno) -> {plan: [release, ...]}`, `find_req(req_id, mno="", release="", ignore_case=False)` (rows `{req_id, mno, release, plan, plan_name, doc_id, title, section_number, parent_section, text}`; `plan_name` only for the tree's primary plan), `reqs_for_plan(mno, release, plan)` (requirement dict + `doc_id`, by doc then document order). Traversal within one cell (rows = requirement dict + `doc_id`): `get_node`, `children`, `subtree` (node first, document order), `ancestors` (root first, node excluded), each `(mno, release, req_id)`; `find_by_fingerprint(fingerprint, mno="")` (find_req-shaped rows across cells; blank ids excluded).
- `FileTreeStore(env_dir)` — the JSON trees under `<env_dir>/out/parse/`: per-cell `<mno>/<release>/<doc>_tree.json`, plus the legacy flat layout (unqualified lookups: flat file first, then recursive search; `find_req` and `find_by_fingerprint` fall back to the flat corpus as cell `("", "")` when no per-cell trees exist). Per-instance mtime+size tree cache; traversal indexes and fingerprints derived lazily per cached tree. Module helpers `parse_dir(env_dir)`, `find_tree(env_dir, doc_id, mno, release)`.
- `SqliteTreeStore(root_dir="", db_paths=None)` — every `trees-<mno>-<release>.db` under `root_dir` (or the explicit `db_paths`), one per cell; the cell is read from each file's `meta`, not its name; a second file for the same cell is ignored with a warning; a missing root reads as empty. Per-call connections opened `mode=ro&immutable=1`; a rebuilt file is picked up by its changed mtime/size. Per-cell only: no flat layout. `cell_meta(mno, release)` returns a cell DB's `meta` ({} when absent; not on the protocol). Schema (Postgres-portable, TEXT / INTEGER): `meta(key, value)` (`schema_version`=1, `mno`, `release`, `cell`=`MNO__REL`, `n_docs`, `n_nodes`, `source_fingerprint`, `fingerprint_version`, `created_at`); `doc(doc_id PK, plan_id, plan_name, req_key_pos, tree_json)` — the tree without its `requirements` list, `req_key_pos` = that key's position in the original key order (-1 = absent); `node(id PK, doc_id, seq, parent_id → node.id, path, depth, req_id, req_id_cf, section_number, title, plan_id (effective), is_requirement, fingerprint (`text_fp`), req_json)`, `UNIQUE(doc_id, seq)`, indexes on `req_id`, `req_id_cf` (casefolded, for `ignore_case`), `fingerprint`, `plan_id`, `(doc_id, path)`, `parent_id`. `path` is a 6-digit zero-padded ordinal path (`000003.000001.000002`): subtree = range scan `[path + '.', path + '/')`, ancestors = path prefixes.
- `open_tree_store(env_dir, root_dir="") -> TreeStore` — `root_dir`, else `$NORA_TREES_ROOT` → SQLite; else the file backend.
- Pure helpers: `tree_layout(reqs) -> [(parent_index, path, depth)]` (the one hierarchy rule both backends use), `build_tree_hierarchy`, `latest_match`, `req_plan`, `release_sort_key`, `req_text_fp(req)` (= `models.fingerprint.text_fp(title, text)`), `write_tree_db`, `read_meta`, `db_name`, `source_fingerprint`.
- CLI `python -m core.src.stores.trees_build --env-dir <dir> --out <dir> [--cells MNO__REL,...] [--force]` (`trees_build.py`, `build_trees()`) — one DB per cell from `out/parse/<mno>/<release>/*_tree.json`, written under a temp name, verified, then renamed. Verify: `SqliteTreeStore` over the new file and `FileTreeStore` must agree on `list_docs`, `load_tree` (equality, every doc), `reqs_for_plan` (every plan) and `find_req` for up to ~50 sampled ids (exact and case-swapped with `ignore_case`); a mismatch removes the temp file. Existing targets are checked before anything is written. `source_fingerprint` = sha256 over the sorted tree files' names and bytes. Prints counts only. Error codes `TRS-E001` (no per-cell parse trees), `TRS-E002` (target exists without `--force`), `TRS-E003` (requested cell has no trees), `TRS-E004` (unreadable or malformed tree), `TRS-E005` (verify mismatch), `TRS-E006` (SQLite write failure).

- `HistoryStore` — `runtime_checkable` Protocol (read-only; one write-once DB per MNO). Release bounds default to oldest → latest; an unknown label raises `ValueError`. `list_mnos()`; `releases(mno)` (oldest first; [] when unknown); `req_history(mno, plan_id, req_id, from_rel="", to_rel="")` → versions overlapping the bounds `{valid_from, valid_to, title, text, priority, applicability, location, node_fp, subtree_fp, change, diff}` (`change` vs the previous version: `added` | `readded` | `changed` | `moved` | `reordered` | `subtree`; `diff` = `{title, text}` word diffs when `text_fp` changed, else None); `plan_history(mno, plan_id, from_rel="", to_rel="")` → `[{from, to, changes}]` per release step whose plan subtree changed (change rows with diffs); `last_changed(mno, plan_id, req_id)` (latest `valid_from` bringing a new `node_fp`; None when unknown); `plan_last_changed(mno, plan_id)` (same on the plan node's `subtree_fp`); `diff_releases(mno, rel_a, rel_b, diffs=False)` → `{mno, from, to, counts, plans: {plan: {counts, sections: {section: counts}}}, most_changed_sections (top 10), changes}`; change rows `{key, node, plan_id, local_id, section, change: [kinds], renamed_from?, diff?}`; `latest_changes(mno, diffs=False)` (last two releases; None with fewer than two).
- Change kinds: `added`, `removed`, `changed` (`node_fp`), `moved` (location), `reordered` (`node_fp` and `child_set_fp` equal, `subtree_fp` different), `likely_renamed` (an added key whose non-empty `text_fp` equals a removed key's in the same step, same node kind; paired one-to-one in key order, the pair still counts as added + removed). Counts per kind, per plan and per section (a requirement's section location; a section's own number).
- `SqliteHistoryStore(root_dir="", db_paths=None)` — every `history-<mno>.db` under `root_dir` (or explicit `db_paths`); the MNO is read from `meta`; a second file for one MNO is ignored with a warning; per-call connections `mode=ro&immutable=1`. Schema (Postgres-portable, TEXT / INTEGER): `meta(key, value)` (`schema_version`=1, `fingerprint_version`, `mno`, `releases` (JSON), `source` (JSON release → Trees source fingerprint), `created_at`, counts `n_releases`, `n_nodes`, `n_plans`, `n_sections`, `n_requirements`, `n_versions`, `n_content`); `release(mno, release, ord, PK(mno, release))`; `node(key PK, kind, plan_id, local_id, parent_key)` (latest parent; UNIQUE `(kind, plan_id, local_id)`, index `plan_id`); `version(node_key, valid_from, valid_to, from_ord, to_ord, node_fp, subtree_fp, child_set_fp, text_fp, attrs_json, location_json, PK(node_key, from_ord))` (index `(to_ord, from_ord)`); `content(text_fp PK, title, text)`.
- `open_history_store(root_dir="") -> HistoryStore | None` — `root_dir`, else `$NORA_HISTORY_ROOT` → SQLite; else None (history disabled; no file backend).
- History helpers: `release_nodes(tree_store, mno, release)` (one release's keyed nodes with hashes), `write_history_db`, `read_meta`, `db_name`, `node_key(kind, plan_id, local_id)` (compact JSON list; consumers look nodes up by the triple, never parse keys), `word_diff(a, b) -> [{op: eq|del|add, text}]`.
- CLI `python -m core.src.stores.history_build --env-dir <dir> [--trees-root <dir>] --out <dir> [--mno MNO,...] [--force]` (`history_build.py`, `build_history()`) — reads Trees through `open_tree_store` (`--trees-root`, else `$NORA_TREES_ROOT`, else the parse trees under `--env-dir`); one DB per MNO written under a temp name, verified, renamed. Verify: release list equals the Trees store's; a self-diff of the latest release is empty; the current requirement count equals the latest release's; for up to ~50 sampled requirements the latest version is current and has the tree's `text_fp`. Existing targets are checked before anything is written. Prints counts only. Error codes `HIS-E001` (Trees store has no cells), `HIS-E002` (target exists without `--force`), `HIS-E003` (requested MNO has no cells), `HIS-E004` (SQLite write failure), `HIS-E005` (verify mismatch).

**Invariants**
- Both backends return the same entry shape for the same writes, byte-identical under `json.dumps` (record keys sorted; entry keys `add`/`remove`/`suppress_all`): `{"word", "label", "reason": {...}, "by", "at", "origin": {"release"}}`, plus `fingerprint` only when non-empty, plus `"value": true` on `suppress_all`. So the label-view digest (`EnrichOverlayStore.overlay_digest` = nora-store `_overlay_digest`) is backend-independent.
- Record order within (MNO, req, direction) is insertion order (`seq`); a re-edited word moves to the end, as in the JSON lists.
- A record the columns cannot reproduce exactly (legacy or hand-edited shape) is kept verbatim in `raw_json`; nothing is silently normalized.
- The SQLite schema uses TEXT / INTEGER / REAL and `ON CONFLICT` upserts only (Postgres-portable, D-DRAFT-26).
- One writer: nora-web. Readers (nora-store, the converter's identity gate) open read-only. A read-only open never creates or migrates the file.
- Trees: both backends return equal results for every `TreeStore` method on per-cell data; `SqliteTreeStore.load_tree` reconstructs a dict equal to the JSON tree, key order and requirement order included (`req_json` is the exact original requirement dict).
- Trees hierarchy is per document, by `parent_req_id` (`build_tree_hierarchy`'s rule): a node whose parent id is not in the document is a root; for an id repeated in one document the LAST occurrence is the addressable node (earlier copies and blank-id nodes are isolated roots); a parent cycle is cut at its first node in document order. `get_node` picks the first doc (by doc_id) holding the id.
- Trees cell DBs are write-once: the builder never edits a published file; `--force` replaces it by rename.
- History identity: plan = `plan_id`; section = `(plan_id, section_number)` for a non-requirement node with a section number (a renumbering is removed + added); requirement = `(plan_id, req_id)` with the effective plan (`req_plan`), section kept as location. `is_requirement` absent → a node with a req_id is a requirement. Nodes with neither identity, and repeated keys (first in doc order wins; within one doc the last occurrence of a req_id, as in Trees), are skipped; keyed descendants climb to the next keyed ancestor. A node whose nearest keyed ancestor is absent or in another plan hangs under its plan node. Location = `{parent}` for sections, `{parent, section}` for requirements, `{}` for plans.
- History hashes (`models.fingerprint`): plans hash `plan_name` as title (the primary tree's), sections their title + text, requirements title + text plus priority and applicability in `node_fp`. Each child enters its parent's `subtree_fp` / `child_set_fp` as a placed hash of (key, location, `subtree_fp`), so equal `subtree_fp` means nothing at or under the node changed except its own location — the diff descends only into nodes whose `subtree_fp` differs, plan by plan.
- History versions: a row only when `node_fp`, `subtree_fp` or location changes; `valid_to` = the last release held, '' while held in the latest release; absence closes the interval, reappearance opens a new one. Content is stored once per `text_fp`, first-seen display text: a whitespace-only edit keeps the earlier display text.
- History DBs are write-once; `--force` replaces by rename.
- Stdlib only (the stdlib fold in `nora_store/enrich_overlay.py` imports `JsonOverlayStore`).
- No requirement text, words or reasons in error messages or CLI output (Trees and History builders: cell / MNO names and counts only).

**Key choices**
- Entry-dict granularity, not op granularity: edit semantics (ops, labels as branches, reaffirm/discard, remove-wins) stay in `web.EnrichOverlayStore` and the fold, so a backend swap cannot change them (D-DRAFT-24).
- Fingerprint is a column on each record, stamped by the caller at edit time; the fold, not the store, decides what a match means (store-split-design §3/§4). Records without one stay Jaccard-guarded.
- SQLite in dev/test; corp picks SQLite or Postgres by deployment facts (design §9.4, D-DRAFT-26). Same-host only: SQLite locks do not work across hosts or on network shares.
- Local backends now; an HTTP client with the same protocol (`NORA_OVERLAY_URL`, routes under `/stores/overlay/v1`) arrives with S6 (D-DRAFT-25).
- Composition (Cells + Overlay → BM25) happens in the consumer, never in the store (D-DRAFT-23).
- Trees schema keeps the exact requirement dict (`req_json`) beside derived query columns rather than the design's `node` + `req_body` split, so any parser field reaches consumers unchanged and `load_tree` stays exact; `req_id_cf` and `doc.req_key_pos` were added for exact `ignore_case` matching and key-order round-trip.
- `NORA_TREES_ROOT` unset → the file backend, so existing deployments are unchanged; an HTTP client (`NORA_TREES_URL`, `/stores/trees/v1`) arrives with S6.
- History `version` adds `from_ord` / `to_ord` (range queries), `text_fp` (join to `content`) and `attrs_json` (priority, applicability) beyond the design's columns; `content` has no `fields_json` because those fields are not part of `text_fp` and would be ambiguous on a text-keyed row.
- History child entries fold in key and location (not only the children's `subtree_fp` as design §6a sketches): otherwise a section renumbering, a rename with identical content or a section-number move under the same parent leaves every ancestor's `subtree_fp` equal and the descent would miss it.
- `likely_renamed` is an annotation on an added + removed pair, not a replacement for them, so a renumbered section reads "removed + added (likely renamed)".
- `NORA_HISTORY_ROOT` unset → None: History is optional; an HTTP client (`NORA_HISTORY_URL`, `/stores/history/v1`) arrives with S6.

**Non-goals**
- No edit semantics, label views, digests or fold logic — those live in `web.enrich_overlay_store` and `nora_store.enrich_overlay`.
- No retention or purge of held records (design §9 question 2).
- No multi-host writers; no schema migrations beyond create-if-missing.
- Trees: no SQLite build of the legacy flat layout; no release lineage (History store).
- History: no LLM summaries (a paragraph may be written over the deterministic summary by a caller); no mapping of display names to `plan_id` (query layer); no HTTP client yet (S6).

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`history.py`
- `CHANGE_KINDS` — constant — pub
- `DB_PREFIX` — constant — pub
- `DB_SUFFIX` — constant — pub
- `HISTORY_ROOT_ENV` — constant — pub
- `HistoryStore` — class — pub — Read interface over release lineage (write-once per MNO).
  - `diff_releases` — method — pub
  - `last_changed` — method — pub
  - `latest_changes` — method — pub — ``diff_releases`` of the last two releases; None with fewer.
  - `list_mnos` — method — pub
  - `plan_history` — method — pub — Per release step whose plan subtree changed: ``{from, to,
  - `plan_last_changed` — method — pub
  - `releases` — method — pub — The MNO's releases, oldest first; [] when unknown.
  - `req_history` — method — pub — Versions overlapping the bounds, oldest first: ``{valid_from,
- `SQLITE_SCHEMA_VERSION` — constant — pub
- `SqliteHistoryStore` — class — pub — Every ``history-<mno>.db`` under ``root_dir`` (or the explicit
  - `__init__` — constructor — internal
  - `_bounds` — staticmethod — internal
  - `_con` — method — internal
  - `_content` — staticmethod — internal
  - `_dbs` — method — internal
  - `_diff` — method — internal — Change rows between ord ``a`` and ord ``b``: plans first, then
  - `_files` — method — internal
  - `_last_new` — method — internal — The latest ``valid_from`` that brought a new ``col`` value.
  - `_node_history` — method — internal
  - `_releases` — staticmethod — internal
  - `_state` — staticmethod — internal
  - `_versions` — staticmethod — internal
  - `diff_releases` — method — pub
  - `last_changed` — method — pub
  - `latest_changes` — method — pub
  - `list_mnos` — method — pub
  - `plan_history` — method — pub
  - `plan_last_changed` — method — pub
  - `releases` — method — pub
  - `req_history` — method — pub
- `_EMPTY_TEXT_FP` — constant — internal
- `_SQLITE_DDL` — constant — internal
- `_STATE_SQL` — constant — internal
- `_VERSION_KEYS` — constant — internal
- `_change_diff` — function — internal
- `_connect_ro` — function — internal
- `_descend` — function — internal
- `_is_requirement` — function — internal
- `_pair_renames` — function — internal — Mark an added key whose text_fp equals a removed key's (same node
- `_placed` — function — internal — A child entry for its parent's subtree/child-set hash.
- `_s` — function — internal
- `_summary` — function — internal — Deterministic counts per kind, per plan and per section.
- `_version_change` — function — internal
- `db_name` — function — pub
- `logger` — constant — pub
- `node_key` — function — pub — Unambiguous node key (a compact JSON list). Consumers look nodes up
- `open_history_store` — function — pub — ``root_dir`` (else ``$NORA_HISTORY_ROOT``) → SQLite; else None
- `read_meta` — function — pub
- `release_nodes` — function — pub — Every keyed node of one cell with its hashes, by key; dict order is
- `word_diff` — function — pub — Word-level diff of display text: ``[{"op": "eq"|"del"|"add", "text"}]``.
- `write_history_db` — function — pub — Write one MNO's DB (the file must not exist). ``states[i]`` is

`history_build.py`
- `_SAMPLE` — constant — internal
- `_build_mno` — function — internal
- `_source` — function — internal — The cell's Trees source fingerprint, '' when not known.
- `_verify` — function — internal
- `build_history` — function — pub — Build + verify every selected MNO; returns the counts report.
- `main` — function — pub

`overlay.py`
- `DIRECTIONS` — constant — pub
- `JsonOverlayStore` — class — pub — Today's per-MNO JSON files. Writes inside a transaction are
  - `__init__` — constructor — internal
  - `_locked` — method — internal
  - `_mno_path` — method — internal
  - `_pending` — method — internal
  - `_read` — method — internal — Read through the open transaction's buffer (a deep copy, so
  - `_read_json` — staticmethod — internal
  - `_stage` — method — internal
  - `_write_json` — staticmethod — internal
  - `accepted_labels` — method — pub
  - `disabled_labels` — method — pub
  - `get_entry` — method — pub
  - `get_overlay` — method — pub
  - `list_mnos` — method — pub — MNOs with an overlay file (sorted).
  - `put_entry` — method — pub
  - `reason_categories` — method — pub
  - `set_accepted_labels` — method — pub
  - `set_disabled_labels` — method — pub
  - `set_reason_categories` — method — pub
  - `transaction` — method — pub
  - `version` — method — pub — File mtime; 0.0 when absent.
- `OVERLAY_DB_ENV` — constant — pub
- `OverlayReadOnlyError` — class — pub — A write was attempted on a store opened read-only.
- `OverlayStore` — class — pub — Storage interface for the Overlay (one writer: nora-web).
  - `accepted_labels` — method — pub
  - `disabled_labels` — method — pub
  - `get_entry` — method — pub
  - `get_overlay` — method — pub
  - `list_mnos` — method — pub
  - `put_entry` — method — pub — Replace one req's entry; None or {} deletes it. Always marks
  - `reason_categories` — method — pub — The stored list; None when never set (caller applies defaults).
  - `set_accepted_labels` — method — pub
  - `set_disabled_labels` — method — pub
  - `set_reason_categories` — method — pub
  - `transaction` — method — pub — Context manager: exclusive across processes on one host. Reads
  - `version` — method — pub — Monotonic change marker; 0.0 when the MNO has no overlay.
- `SQLITE_SCHEMA_VERSION` — constant — pub
- `SUPPRESS` — constant — pub
- `SqliteOverlayStore` — class — pub — One SQLite file per deployment. Single writer (nora-web); readers
  - `__init__` — constructor — internal
  - `_bump` — method — internal
  - `_connect` — method — internal
  - `_ensure_schema` — method — internal — Create the schema once per file. Checks first, so an instance
  - `_label_set` — method — internal
  - `_reader` — method — internal — The open transaction's connection, else a fresh one. Yields
  - `_set_label_flag` — method — internal
  - `_tx_con` — method — internal
  - `accepted_labels` — method — pub
  - `clear` — method — pub
  - `disabled_labels` — method — pub
  - `get_entry` — method — pub
  - `get_overlay` — method — pub
  - `is_empty` — method — pub
  - `list_mnos` — method — pub
  - `put_entry` — method — pub
  - `reason_categories` — method — pub
  - `set_accepted_labels` — method — pub
  - `set_disabled_labels` — method — pub
  - `set_reason_categories` — method — pub
  - `transaction` — method — pub
  - `version` — method — pub
- `_ACCEPTED_FILE` — constant — internal
- `_DISABLED_FILE` — constant — internal
- `_NON_MNO_FILES` — constant — internal
- `_REASONS_FILE` — constant — internal
- `_RECORD_COLS` — constant — internal
- `_SQLITE_DDL` — constant — internal
- `_record_to_row` — function — internal — Record → column values; falls back to `raw_json` whenever the
- `_row_to_record` — function — internal — Columns → the record dict the JSON backend returns (sorted keys).
- `_str` — function — internal
- `open_overlay_store` — function — pub — `db_path` (else `$NORA_OVERLAY_DB`) → SQLite; else

`overlay_import.py`
- `_verify` — function — internal — Both backends must return the same content for every read.
- `import_overlay` — function — pub — Copy + verify; returns the counts report. Raises PipelineError.
- `main` — function — pub

`trees.py`
- `DB_PREFIX` — constant — pub
- `DB_SUFFIX` — constant — pub
- `FileTreeStore` — class — pub — Today's JSON trees under ``<env_dir>/out/parse/``.
  - `__init__` — constructor — internal
  - `_entry` — method — internal
  - `_index` — method — internal
  - `_locate` — method — internal — (doc_id, index, i) of the addressable node: first doc that has
  - `_scan_cells` — method — internal — Both qualifiers → that cell; else every cell, or the legacy flat
  - `ancestors` — method — pub
  - `children` — method — pub
  - `find_by_fingerprint` — method — pub
  - `find_req` — method — pub
  - `find_tree` — method — pub
  - `get_node` — method — pub
  - `list_cells` — method — pub
  - `list_docs` — method — pub
  - `load_tree` — method — pub
  - `plans_for_mno` — method — pub
  - `reqs_for_plan` — method — pub
  - `subtree` — method — pub
- `SQLITE_SCHEMA_VERSION` — constant — pub
- `SqliteTreeStore` — class — pub — Every ``trees-<mno>-<release>.db`` under ``root_dir`` (one per cell,
  - `__init__` — constructor — internal
  - `_as_rows` — staticmethod — internal
  - `_cells` — method — internal — (mno, release) → DB file, in list_cells order.
  - `_docs` — method — internal — doc_id → (tree head, req_key_pos), cached per file version.
  - `_files` — method — internal
  - `_locate` — method — internal — (path to DB, node id, doc_id, node path) of the addressable node.
  - `_query` — method — internal
  - `_rows` — method — internal
  - `_scope` — method — internal
  - `ancestors` — method — pub
  - `cell_meta` — method — pub — The cell DB's ``meta`` table; {} when the cell is absent.
  - `children` — method — pub
  - `find_by_fingerprint` — method — pub
  - `find_req` — method — pub
  - `get_node` — method — pub
  - `list_cells` — method — pub
  - `list_docs` — method — pub
  - `load_tree` — method — pub
  - `plans_for_mno` — method — pub
  - `reqs_for_plan` — method — pub
  - `subtree` — method — pub
- `TREES_ROOT_ENV` — constant — pub
- `TreeStore` — class — pub — Read interface over parsed trees (write-once per cell).
  - `ancestors` — method — pub — Root first; excludes the node itself.
  - `children` — method — pub
  - `find_by_fingerprint` — method — pub
  - `find_req` — method — pub
  - `get_node` — method — pub
  - `list_cells` — method — pub — ``[{mno, release}]`` with at least one tree, by MNO then
  - `list_docs` — method — pub — Doc ids of one cell (both qualifiers) or of every cell (sorted,
  - `load_tree` — method — pub — The full tree dict; {} when missing. Read-only for callers.
  - `plans_for_mno` — method — pub
  - `reqs_for_plan` — method — pub
  - `subtree` — method — pub — The node and all its descendants, in document order.
- `_DocIndex` — class — internal — Derived per-tree data for traversal and fingerprint lookups.
  - `__init__` — constructor — internal
  - `fingerprints` — method — pub
- `_PATH_WIDTH` — constant — internal
- `_REQS_KEY` — constant — internal
- `_SQLITE_DDL` — constant — internal
- `_connect_ro` — function — internal
- `_match_row` — function — internal — One ``find_req`` row (primary-plan-only ``plan_name``).
- `_s` — function — internal
- `_sort_cells` — function — internal
- `build_tree_hierarchy` — function — pub — Convert flat requirement list into nested tree (child_nodes
- `db_name` — function — pub
- `find_tree` — function — pub — Resolve a doc's tree file. Cell-qualified path first, flat legacy
- `latest_match` — function — pub — Pick the newest cell from ``find_req`` results — the match with the
- `logger` — constant — pub
- `open_tree_store` — function — pub — ``root_dir`` (else ``$NORA_TREES_ROOT``) → SQLite cell DBs; else the
- `parse_dir` — function — pub
- `read_meta` — function — pub
- `release_sort_key` — function — pub — Chronological MMMYYYY order; non-conforming labels sort first.
- `req_plan` — function — pub — A requirement's effective plan: per-req plan_id (multi-plan docs)
- `req_text_fp` — function — pub — ``text_fp`` over the requirement's title and body.
- `source_fingerprint` — function — pub — sha256 over the tree files (name + bytes) in sorted name order.
- `tree_layout` — function — pub — Per requirement (by list index): ``(parent_index, path, depth)``.
- `write_tree_db` — function — pub — Write one cell DB (the file must not exist). ``docs`` is

`trees_build.py`
- `_SAMPLE` — constant — internal
- `_build_cell` — function — internal
- `_read_cell` — function — internal
- `_verify` — function — internal — Both backends must return the same content for the cell.
- `build_trees` — function — pub — Build + verify every selected cell; returns the counts report.
- `main` — function — pub
<!-- END:STRUCTURE -->

**Depends on**
[pipeline](../pipeline/MODULE.md) (`error_codes.PipelineError` / `CODES` — import and build CLIs only), [models](../models/MODULE.md) (`fingerprint.text_fp`, `node_fp`, `subtree_fp`, `child_set_fp`, `FINGERPRINT_VERSION`), [extraction](../extraction/MODULE.md) (`release_key.release_order_key`, lazy).

**Depended on by**
[web](../web/MODULE.md) (`EnrichOverlayStore` backend; `req_tree` delegates to `open_tree_store`). **nora-store service** (`nora_store/service.py` reads read-only via `open_overlay_store`; `enrich_overlay.load_overlay` / `load_accepted_labels` wrap `JsonOverlayStore`) and the cell-DB converter (`sandbox/cell_db_convert.py`, identity-gate fold).
