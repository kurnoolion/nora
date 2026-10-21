# stores

**Purpose**
Data stores behind service interfaces (strand corp-db-layer, `store-split-design.md`). Each store is one Python protocol with local backends; consumers compose stores, and no store reads another store's backend. Step S3 ships the **Overlay** store: the review edits to SIRA enrichments (word-level remove/add records, entry-level `suppress_all`, label merge log, disabled labels, reason categories), the only mutable store. Implements D-DRAFT-22 (four orthogonal stores) and D-DRAFT-24 (Overlay is a live store keyed by requirement and fingerprint); Cells, Trees and History land in later steps.

**Public surface**
- `OverlayStore` — `runtime_checkable` Protocol at entry-dict granularity. Reads: `get_overlay(mno) -> {req_id: entry}`, `get_entry(mno, req_id)`, `list_mnos()`, `version(mno) -> float` (monotonic change marker; 0.0 = no overlay), `accepted_labels()`, `disabled_labels()`, `reason_categories() -> list | None` (None = never set; the caller applies defaults). Writes: `transaction()` (context manager; exclusive across processes on one host; nested use joins; reads inside see the transaction's writes), `put_entry(mno, req_id, entry | None)` (None/{} deletes; always registers the MNO and bumps its version), `set_accepted_labels(set)`, `set_disabled_labels(set)`, `set_reason_categories(list)`. Attribute `read_only`.
- `JsonOverlayStore(corrections_root, read_only=False)` — the original layout under `<root>/sira-enrich/`: `<MNO>.json`, `accepted-labels.json` (`{"accepted": [...]}`), `labels.json` (`{"disabled": [...]}`), `reason-categories.json`. Transaction = flock on `sira-enrich/.lock`; writes buffered per transaction and flushed per file with tmp + rename. `version` = file mtime.
- `SqliteOverlayStore(db_path, read_only=False, busy_timeout_ms=5000)` — one file per deployment. WAL, `busy_timeout`, `BEGIN IMMEDIATE` transactions, per-call connections. Tables: `record(mno, req_id, direction CHECK IN ('remove','add','suppress_all'), word ('' for suppress_all), seq, label, reason_json, by_user, at, origin_release, fingerprint, raw_json, PK(mno, req_id, direction, word))`, `label(name PK, accepted, disabled)`, `reason_category(pos, name)`, `mno_version(mno PK, version)` (wall-clock epoch forced strictly increasing), `meta(key, value)`. `read_only=True` opens with a `mode=ro` URI; a missing file reads as empty and is never created. `is_empty()`, `clear()` for the import CLI.
- `open_overlay_store(corrections_root="", db_path="", read_only=False) -> OverlayStore | None` — `db_path`, else `$NORA_OVERLAY_DB` → SQLite; else `corrections_root` → JSON; else None (overlay disabled).
- `OverlayReadOnlyError` — a write on a read-only store.
- CLI `python -m core.src.stores.overlay_import --corrections-root <dir> --db <path> [--force]` (`overlay_import.py`, `import_overlay()`) — one-time copy of the JSON Overlay into SQLite; verifies `get_overlay(mno)` per MNO, the MNO list, label sets and categories are equal across backends inside the same transaction (mismatch → rollback); prints counts only. Error codes `OVL-E001` (no `sira-enrich/` dir), `OVL-E002` (non-empty target without `--force`), `OVL-E003` (verify mismatch), `OVL-E004` (SQLite failure).

**Invariants**
- Both backends return the same entry shape for the same writes, byte-identical under `json.dumps` (record keys sorted; entry keys `add`/`remove`/`suppress_all`): `{"word", "label", "reason": {...}, "by", "at", "origin": {"release"}}`, plus `fingerprint` only when non-empty, plus `"value": true` on `suppress_all`. So the label-view digest (`EnrichOverlayStore.overlay_digest` = nora-store `_overlay_digest`) is backend-independent.
- Record order within (MNO, req, direction) is insertion order (`seq`); a re-edited word moves to the end, as in the JSON lists.
- A record the columns cannot reproduce exactly (legacy or hand-edited shape) is kept verbatim in `raw_json`; nothing is silently normalized.
- The SQLite schema uses TEXT / INTEGER / REAL and `ON CONFLICT` upserts only (Postgres-portable, D-DRAFT-26).
- One writer: nora-web. Readers (nora-store, the converter's identity gate) open read-only. A read-only open never creates or migrates the file.
- Stdlib only (the stdlib fold in `sandbox/sira_query/enrich_overlay.py` imports `JsonOverlayStore`).
- No requirement text, words or reasons in error messages or CLI output.

**Key choices**
- Entry-dict granularity, not op granularity: edit semantics (ops, labels as branches, reaffirm/discard, remove-wins) stay in `web.EnrichOverlayStore` and the fold, so a backend swap cannot change them (D-DRAFT-24).
- Fingerprint is a column on each record, stamped by the caller at edit time; the fold, not the store, decides what a match means (store-split-design §3/§4). Records without one stay Jaccard-guarded.
- SQLite in dev/test; corp picks SQLite or Postgres by deployment facts (design §9.4, D-DRAFT-26). Same-host only: SQLite locks do not work across hosts or on network shares.
- Local backends now; an HTTP client with the same protocol (`NORA_OVERLAY_URL`, routes under `/stores/overlay/v1`) arrives with S6 (D-DRAFT-25).
- Composition (Cells + Overlay → BM25) happens in the consumer, never in the store (D-DRAFT-23).

**Non-goals**
- No edit semantics, label views, digests or fold logic — those live in `web.enrich_overlay_store` and `sandbox.sira_query.enrich_overlay`.
- No retention or purge of held records (design §9 question 2).
- No multi-host writers; no schema migrations beyond create-if-missing.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


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
<!-- END:STRUCTURE -->

**Depends on**
[pipeline](../pipeline/MODULE.md) (`error_codes.PipelineError` / `CODES` — import CLI only).

**Depended on by**
[web](../web/MODULE.md) (`EnrichOverlayStore` backend). **nora-store service** (`sandbox/sira_query/service.py` reads read-only via `open_overlay_store`; `enrich_overlay.load_overlay` / `load_accepted_labels` wrap `JsonOverlayStore`) and the cell-DB converter (`sandbox/cell_db_convert.py`, identity-gate fold).
