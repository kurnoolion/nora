# Phase 4 plan — store split, then publish

Strand `corp-db-layer` · v1 · 2026-10-21 · design: `store-split-design.md`.
Scope: 4a + 4b now; 4c (corp deployment) waits on storage-design §8 points 2 and 3.

## 4a — store split (local)

| # | Change |
|---|---|
| S1 | `text_fp` (was `req_fingerprint`) in one shared module, canonical form v2, versioned values; computed at build; tests pin the normalization. |
| S2 | Cells v2: converter writes the vanilla index and `sira_phrases` (raw, per run), no effective set; `schema_version` 2. nora-store composes at load (§4); no cache (measured, D-DRAFT-23). Identity gate: v2 composed index == today's baked index on the same inputs. |
| S3 | `OverlayStore`: SQLite backend, one-time import of the per-MNO JSON corrections (provenance kept), fingerprint recorded per edit. nora-web writes through it; nora-store reads through it. Apply works in cell-db mode. |
| S4 | `TreeStore`: protocol; file backend over `out/parse` (today's `req_tree` logic moves behind it); SQLite backend and builder. |
| S5 | `HistoryStore` (design §6a): builder from `TreeStore` across an MNO's releases computing `node_fp` / `subtree_fp`; version intervals + content-addressed text in SQLite; the six query kinds. |
| S6 | nora-store mounts `/stores/{cells,trees,history,overlay}/v1` (Overlay with writes); HTTP clients for all four chosen by `NORA_<STORE>_URL`; nora-web uses the clients (file backends = dev fallback); peer-store Overlay read; schema-tolerance tests; parity tests local vs HTTP. Checkpoint: trial's nora-web without corrections/parse mounts, review edits and Apply through the store. |

Checkpoint after S2–S3: rebuild, trial stack on Cells v2 + Overlay
store; golden Stage-1 equal to the current trial; review Apply works.

## 4b — publish (against a stand-in corp volume) — design: `publish-design.md`

| # | Change |
|---|---|
| B1 | `corpus_publish`: publishes Cells and Trees per cell, rebuilds History per MNO, deterministic fingerprints, skips unchanged units. |
| B2 | `registry.db`: `store_versions`, `active`, publish log. |
| B3 | Stage with checksum + temp-name rename; transfer pluggable (local copy now). |
| B4 | Flip refuses without a recorded passing golden run (`--force --reason` overrides). |
| B5 | Flip and rollback, one transaction per release. |
| B6 | Services load the active versions from `registry.db` (`NORA_STORE_REGISTRY`). |
| B7 | GC: keep active + N previous. |
| B8 | Local end-to-end on trial: publish → stage → gate → flip → rollback. |

## Out of scope

Corp transfer, staging and first publish (4c); corp metrics/config DBs;
retiring flat build files.
