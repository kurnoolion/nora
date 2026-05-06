"""Retrieval debug — pinpoint why retrieval behaves differently across
machines or runs.

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

  (more modes can be added here later — e.g. --query-trace, --rerank-trace)

When pasting output back into chat, the chunk previews under
RETRIEVAL FINGERPRINT show only req_id and plan_id (no source text).
Safe by default for proprietary corpora.
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

    # --compare-envs is the only mode for now; modeled as a flag rather than
    # a subcommand so the CLI shape can grow naturally to other modes.
    parser.add_argument(
        "--compare-envs", action="store_true",
        help="Print 4-section fingerprint (machine / model / vectorstore / "
             "retrieval) for side-by-side cross-machine comparison.",
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
        help="Probe query for the retrieval fingerprint section "
             "(default: 'Explain ADD requirements')",
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

    if args.compare_envs:
        if not args.env_dir:
            parser.error("--compare-envs requires --env-dir")
        sys.exit(cmd_compare_envs(args))

    parser.print_help()
    print("\nNo mode selected. Try --compare-envs --env-dir <path>.")


if __name__ == "__main__":
    main()
