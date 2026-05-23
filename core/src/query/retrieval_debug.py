"""Retrieval debug — pinpoint why retrieval behaves differently across
machines or runs, or inspect one chunk's vectorstore + BM25 view.

Modes (mutually exclusive):

  python -m core.src.query.retrieval_debug --compare-envs \\
      --env-dir <ENV_DIR> [--query "..."]

      Print a 4-section fingerprint of the current machine's retrieval
      stack — code version, embedding model, vectorstore, retrieval
      output for a probe query. Run on each machine and diff the
      outputs side-by-side; the first diverging row pinpoints the
      cause (different commit, different model bytes, different chunk
      set, different embedding behavior, etc.). The default probe
      query is the first in-domain question shipped with the eval
      set; override with --query if you want to reproduce a specific
      user-reported issue.

  python -m core.src.query.retrieval_debug --inspect-req REQ_ID \\
      --env-dir <ENV_DIR> [--query "..."]

      Show what the vectorstore + BM25 index actually hold for a
      single requirement. Surfaces the full chunk text (as indexed),
      the body-only view (after _strip_chunk_headers — what the
      synthesizer sees), all metadata, BM25 tokenization, and
      (when --query is passed) the chunk's dense distance + BM25
      rank against that query. Use this when retrieval results
      look surprising — e.g. heading-only parents displacing leaves,
      or a known-relevant req missing from top-K.

      REQ_ID can be passed bare (VZ_REQ_PLAN_NNN) or with the
      chunk_id prefix (req:VZ_REQ_PLAN_NNN). Fuzzy match suggestions
      are surfaced if the exact id isn't found.

  (more modes can be added here later — e.g. --query-trace, --rerank-trace)

When pasting output back into chat, the chunk previews under
RETRIEVAL FINGERPRINT show only req_id and plan_id (no source text).
The --inspect-req mode DOES print chunk text — safe-paste only if
your corpus permits it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# Keys we surface from the env-resolution chain. CLI flag > env var >
# config/llm.json — same as resolve_embedding_*.
_TRACKED_ENV_VARS = (
    "NORA_EMBEDDING_PROVIDER",
    "NORA_EMBEDDING_MODEL",
    "NORA_MAX_DISTANCE_THRESHOLD",
    "NORA_LLM_PROVIDER",
    "NORA_LLM_MODEL",
    "NORA_OLLAMA_TIMEOUT_S",
)


def _hr(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _section_machine(repo_root: Path, env_dir: Path) -> dict | None:
    """Print machine fingerprint; return parsed vectorstore config (or None)."""
    _hr("MACHINE FINGERPRINT")

    # Code version
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True,
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"], text=True,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", str(repo_root), "status", "--porcelain"], text=True,
        ).strip()
        print(f"git: {branch} @ {sha[:12]}{' (DIRTY)' if dirty else ''}")
    except Exception as e:
        print(f"git: error — {e}")

    # config/llm.json embedding fields
    print("\nconfig/llm.json embedding fields:")
    cfg_path = repo_root / "config" / "llm.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
            print(f"  embedding_provider = {cfg.get('embedding_provider')!r}")
            print(f"  embedding_model    = {cfg.get('embedding_model')!r}")
        except Exception as e:
            print(f"  parse error: {e}")
    else:
        print("  (file missing)")

    # Env-var overrides
    print("\nNORA_* env vars:")
    for k in _TRACKED_ENV_VARS:
        print(f"  {k} = {os.environ.get(k, '(unset)')}")

    # Vectorstore config
    print("\nvectorstore config.json:")
    vs_cfg_path = env_dir / "out" / "vectorstore" / "config.json"
    if not vs_cfg_path.exists():
        print(f"  (file missing at {vs_cfg_path})")
        return None
    try:
        vs_cfg = json.loads(vs_cfg_path.read_text())
    except Exception as e:
        print(f"  parse error: {e}")
        return None
    for key in (
        "embedding_provider", "embedding_model",
        "distance_metric", "collection_name",
        "normalize_embeddings", "embedding_batch_size",
    ):
        print(f"  {key:<22s} = {vs_cfg.get(key)!r}")
    return vs_cfg


def _ollama_embed(model: str, prompt: str, base_url: str) -> tuple[list[float], None] | tuple[None, str]:
    """Call /api/embeddings directly. Returns (vec, None) or (None, error_msg)."""
    body = json.dumps({"model": model, "prompt": prompt}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/embeddings",
        data=body, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        return None, str(e)
    emb = data.get("embedding")
    if not emb:
        return None, f"unexpected response shape: {data}"
    return emb, None


def _section_embedding_model(vs_cfg: dict, repo_cfg_path: Path) -> None:
    """Direct API probe of the embedding model — proves identical bytes."""
    _hr("EMBEDDING MODEL FINGERPRINT")

    provider = vs_cfg.get("embedding_provider")
    model = vs_cfg.get("embedding_model")
    print(f"\nProvider: {provider}")
    print(f"Model:    {model}")

    if provider != "ollama":
        print("\n(Direct API probe only implemented for ollama — skipping.)")
        return

    base_url = vs_cfg.get("extra", {}).get("ollama_url") or "http://localhost:11434"
    test_text = "explain ADD requirements"
    print(f"\nDirect /api/embeddings call ({base_url}) with prompt={test_text!r}:")
    emb, err = _ollama_embed(model, test_text, base_url)
    if err:
        print(f"  ERROR: {err}")
        return
    norm = sum(v * v for v in emb) ** 0.5
    print(f"  dimension       = {len(emb)}")
    print(f"  L2 norm         = {round(norm, 6)}")
    print(f"  first 5 values  = {[round(v, 6) for v in emb[:5]]}")
    print(f"  last 5 values   = {[round(v, 6) for v in emb[-5:]]}")
    print("\nIDENTICAL first/last/norm across machines == identical model bytes.")


def _section_vectorstore(env_dir: Path, vs_cfg: dict) -> "ChromaDBStore | None":
    """Vectorstore content fingerprint — chunk count, plan distribution, hashes."""
    _hr("VECTORSTORE FINGERPRINT")

    from core.src.vectorstore.store_chroma import ChromaDBStore

    vs_dir = env_dir / "out" / "vectorstore"
    collection = vs_cfg.get("collection_name", "requirements")
    metric = vs_cfg.get("distance_metric", "cosine")
    store = ChromaDBStore(
        persist_directory=str(vs_dir),
        collection_name=collection,
        distance_metric=metric,
    )
    all_docs = store.get_all()
    n = len(all_docs.ids)
    print(f"\nTotal chunks: {n}")
    if n == 0:
        print("(empty vectorstore — nothing more to report)")
        return store

    # Plan + doc_type distributions
    plan_counts: dict[str, int] = {}
    doc_type_counts: dict[str, int] = {}
    for m in all_docs.metadatas:
        pid = m.get("plan_id", "(unknown)")
        plan_counts[pid] = plan_counts.get(pid, 0) + 1
        dt = m.get("doc_type", "(unknown)")
        doc_type_counts[dt] = doc_type_counts.get(dt, 0) + 1

    print("\nChunks per plan:")
    for pid, cnt in sorted(plan_counts.items()):
        print(f"  {pid:<25s} {cnt:>5}")
    print("\nChunks per doc_type:")
    for dt, cnt in sorted(doc_type_counts.items()):
        print(f"  {dt:<25s} {cnt:>5}")

    # Stable hash of all chunk IDs (sorted) — identical hashes mean
    # identical chunk sets. Differs across machines if the parser ran
    # on different docs / different code / different profile.
    ids_hash = hashlib.sha256(
        "\n".join(sorted(all_docs.ids)).encode()
    ).hexdigest()[:16]
    print(f"\nSorted chunk-IDs SHA256 (first 16 hex): {ids_hash}")

    # Hash first chunk text for quick content check (no preview to keep
    # output safe for chat-paste in proprietary-corpus settings).
    first_text = all_docs.documents[0]
    text_hash = hashlib.sha256(first_text.encode()).hexdigest()[:16]
    print(f"First-chunk text SHA256 (first 16 hex):  {text_hash}")
    print(f"First chunk id: {all_docs.ids[0]}")
    print(f"First chunk hierarchy_path: "
          f"{all_docs.metadatas[0].get('hierarchy_path')}")
    return store


def _section_retrieval(store: "ChromaDBStore", vs_cfg: dict, query: str) -> None:
    """Pure-dense top-10 — rawest retrieval signal."""
    _hr("RETRIEVAL FINGERPRINT — query distances")

    from core.src.vectorstore import make_embedder
    from core.src.vectorstore.config import VectorStoreConfig

    # Reconstruct the embedder the way the pipeline would.
    cfg = VectorStoreConfig(**{
        k: v for k, v in vs_cfg.items()
        if k in VectorStoreConfig.__dataclass_fields__
    })
    embedder = make_embedder(cfg)
    qvec = embedder.embed_query(query)

    result = store.query(query_embedding=qvec, n_results=10)
    print(f"\nQuery: {query!r}")
    print("Pure-dense top-10 (no BM25, no rerank, no threshold):")
    print(f"{'rank':<5} {'distance':>10}  {'req_id':<35} {'plan':<15}")
    for i, (cid, dist, meta) in enumerate(zip(
        result.ids, result.distances, result.metadatas,
    )):
        req_id = meta.get("req_id", "")
        plan_id = meta.get("plan_id", "")
        print(f"{i+1:<5} {dist:>10.4f}  {req_id:<35} {plan_id:<15}")

    print("\nIDENTICAL distances across machines == identical retrieval. Any "
          "divergence after model + vectorstore matched means the embedder is "
          "configured differently (normalize_embeddings, distance_metric, batch).")


def _section_inspect_req(
    store: "ChromaDBStore",
    vs_cfg: dict,
    req_id: str,
    query: str | None,
) -> None:
    """Vectorstore + BM25 view of one chunk; optionally score vs query."""
    chunk_id = req_id if req_id.startswith("req:") else f"req:{req_id}"
    _hr(f"INSPECT REQ: {chunk_id}")

    all_docs = store.get_all()
    by_id = {
        cid: (doc, meta) for cid, doc, meta in zip(
            all_docs.ids, all_docs.documents, all_docs.metadatas,
        )
    }

    if chunk_id not in by_id:
        print(f"\n'{chunk_id}' not found in vectorstore "
              f"({len(all_docs.ids)} chunks).")
        sub = req_id.replace("req:", "").upper()
        near = [c for c in all_docs.ids if sub in c.upper()][:10]
        if near:
            print("Near-matches:")
            for n in near:
                print(f"  {n}")
        return

    doc, meta = by_id[chunk_id]

    print(f"\ntext length: {len(doc)} chars")
    print("\n--- metadata ---")
    for k in sorted(meta.keys()):
        v = meta[k]
        print(f"  {k}: {v!r}" if not isinstance(v, (list, dict)) else f"  {k}: {v}")

    print("\n--- chunk text (as stored — what both dense + BM25 index) ---")
    print(doc)

    # Body-only view: strip the synthetic [MNO:]/[Path:]/[Req ID:] headers
    # the same way ContextBuilder does at synthesis time. This is what the
    # LLM actually sees in the user-message context.
    from core.src.query.context_builder import ContextBuilder
    body_only = ContextBuilder._strip_chunk_headers(doc)
    print("\n--- body-only (headers stripped — what synthesizer sees) ---")
    print(f"length: {len(body_only)} chars")
    if body_only:
        print(body_only[:2000])
        if len(body_only) > 2000:
            print(f"\n... (+{len(body_only) - 2000} more chars)")
    else:
        print("(EMPTY — chunk is headers-only, no body content)")

    # BM25 tokenization of this chunk
    from core.src.query.bm25_index import BM25Index, tokenize
    tokens = tokenize(doc)
    print("\n--- BM25 tokenization (what BM25 indexes for this chunk) ---")
    print(f"token count:    {len(tokens)}")
    print(f"unique tokens:  {len(set(tokens))}")
    sample = tokens[:60]
    print(f"first 60 tokens: {sample}")

    if not query:
        return

    # Score this chunk against the query — both lanes, with rank context.
    _hr(f"SCORING vs query: {query!r}")
    bm25 = BM25Index.from_store(store)
    if bm25 is None:
        print("(BM25 index unavailable — skipping query scoring)")
        return

    # Dense: query the whole store (n_results = corpus size) so we can find
    # this chunk's rank even if it's far outside the usual top-K.
    from core.src.vectorstore import make_embedder
    from core.src.vectorstore.config import VectorStoreConfig
    cfg = VectorStoreConfig(**{
        k: v for k, v in vs_cfg.items()
        if k in VectorStoreConfig.__dataclass_fields__
    })
    embedder = make_embedder(cfg)
    qvec = embedder.embed_query(query)
    n_total = len(all_docs.ids)
    dense_result = store.query(query_embedding=qvec, n_results=n_total)

    dense_rank: int | None = None
    dense_dist: float | None = None
    for i, (cid, dist) in enumerate(zip(dense_result.ids, dense_result.distances)):
        if cid == chunk_id:
            dense_rank = i + 1
            dense_dist = dist
            break

    print("\nDense (top-10 of full corpus):")
    for i, (cid, dist) in enumerate(zip(
        dense_result.ids[:10], dense_result.distances[:10],
    )):
        mark = "  ←this req" if cid == chunk_id else ""
        print(f"  #{i+1:<3} dist={dist:.4f}  {cid}{mark}")
    if dense_rank is not None and dense_rank > 10:
        print(f"  ... #{dense_rank}  dist={dense_dist:.4f}  {chunk_id}  ←this req")
    elif dense_rank is None:
        print(f"  (this req not in dense results — unexpected)")

    # BM25 over the whole corpus
    bm25_pairs = bm25.search(query, top_k=n_total)
    bm25_rank: int | None = None
    bm25_score: float | None = None
    for i, (cid, score) in enumerate(bm25_pairs):
        if cid == chunk_id:
            bm25_rank = i + 1
            bm25_score = score
            break

    print("\nBM25 (top-10 of full corpus):")
    for i, (cid, score) in enumerate(bm25_pairs[:10]):
        mark = "  ←this req" if cid == chunk_id else ""
        print(f"  #{i+1:<3} score={score:.4f}  {cid}{mark}")
    if bm25_rank is not None and bm25_rank > 10:
        print(f"  ... #{bm25_rank}  score={bm25_score:.4f}  {chunk_id}  ←this req")
    elif bm25_rank is None:
        print("  (this req not in BM25 results — likely zero token overlap)")


def cmd_inspect_req(args: argparse.Namespace) -> int:
    """Load store + BM25, dump one chunk's view."""
    env_dir = Path(args.env_dir).resolve()
    if not env_dir.exists():
        print(f"ERROR: env_dir {env_dir} does not exist")
        return 1

    vs_cfg_path = env_dir / "out" / "vectorstore" / "config.json"
    if not vs_cfg_path.exists():
        print(f"ERROR: vectorstore config not found at {vs_cfg_path}")
        return 1
    try:
        vs_cfg = json.loads(vs_cfg_path.read_text())
    except Exception as e:
        print(f"ERROR: parse error on {vs_cfg_path}: {e}")
        return 1

    from core.src.vectorstore.store_chroma import ChromaDBStore
    vs_dir = env_dir / "out" / "vectorstore"
    store = ChromaDBStore(
        persist_directory=str(vs_dir),
        collection_name=vs_cfg.get("collection_name", "requirements"),
        distance_metric=vs_cfg.get("distance_metric", "cosine"),
    )
    _section_inspect_req(store, vs_cfg, args.inspect_req, args.query if args.query else None)
    return 0


def cmd_compare_envs(args: argparse.Namespace) -> int:
    """Run all four fingerprint sections."""
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[3]
    env_dir = Path(args.env_dir).resolve()

    if not env_dir.exists():
        print(f"ERROR: env_dir {env_dir} does not exist")
        return 1
    if not (repo_root / "core").is_dir():
        print(f"ERROR: repo_root {repo_root} doesn't look like the nora repo "
              f"(no core/ directory)")
        return 1

    print(f"repo_root = {repo_root}")
    print(f"env_dir   = {env_dir}")
    print(f"query     = {args.query!r}")

    vs_cfg = _section_machine(repo_root, env_dir)
    if not vs_cfg:
        print("\nCannot continue without vectorstore config.json.")
        return 1
    _section_embedding_model(vs_cfg, repo_root / "config" / "llm.json")
    store = _section_vectorstore(env_dir, vs_cfg)
    if store and len(store.get_all().ids) > 0:
        _section_retrieval(store, vs_cfg, args.query)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retrieval debug — pinpoint cross-machine divergence.",
    )
    sub = parser.add_subparsers(dest="mode")

    # Modes are flags rather than subcommands so the CLI can grow naturally.
    parser.add_argument(
        "--compare-envs", action="store_true",
        help="Print 4-section fingerprint (machine / model / vectorstore / "
             "retrieval) for side-by-side cross-machine comparison.",
    )
    parser.add_argument(
        "--inspect-req", metavar="REQ_ID", default=None,
        help="Dump vectorstore + BM25 view of one chunk by req_id (bare or "
             "with 'req:' prefix). Pair with --query to also score the chunk "
             "against a query.",
    )
    parser.add_argument(
        "--env-dir", required=False,
        help="Per-env runtime dir (containing out/vectorstore/, out/parse/, ...)",
    )
    parser.add_argument(
        "--repo-root", default=None,
        help="Repo root (default: inferred from this file's location)",
    )
    parser.add_argument(
        "--query", default="Explain ADD requirements",
        help="Probe query — used by --compare-envs's retrieval fingerprint "
             "AND by --inspect-req's optional scoring section. Default: "
             "'Explain ADD requirements'. Pass empty string to disable "
             "scoring in --inspect-req.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose logging from underlying providers",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.compare_envs and args.inspect_req:
        parser.error("--compare-envs and --inspect-req are mutually exclusive")
    if args.compare_envs:
        if not args.env_dir:
            parser.error("--compare-envs requires --env-dir")
        sys.exit(cmd_compare_envs(args))
    if args.inspect_req:
        if not args.env_dir:
            parser.error("--inspect-req requires --env-dir")
        sys.exit(cmd_inspect_req(args))

    parser.print_help()
    print("\nNo mode selected. Try one of:")
    print("  --compare-envs --env-dir <path>")
    print("  --inspect-req <REQ_ID> --env-dir <path> [--query \"...\"]")


if __name__ == "__main__":
    main()
