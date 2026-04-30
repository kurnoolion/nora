"""Embed debug — verify the embedding provider standalone, find failing chunks.

Four modes (mutually exclusive):

  python -m core.src.vectorstore.embed_debug --check
      Probe connection, model availability, embed a short string. Compact
      one-line "EMB" output safe to paste back in chat.

  python -m core.src.vectorstore.embed_debug --text "..."
      Embed an ad-hoc string. Prints dimension, L2 norm, first 5 components,
      elapsed. Useful for "does this exact input embed?" reproductions.

  python -m core.src.vectorstore.embed_debug --length-sweep
      Embed synthetic Lorem-ipsum strings of increasing length (100, 1k, 4k,
      8k, 16k, 32k, 64k, 128k chars) to find the model's effective max input
      length before Ollama returns 500.

  python -m core.src.vectorstore.embed_debug --chunks <ENV_DIR>
      Reproduce the actual pipeline chunks from <ENV_DIR>/out/parse +
      <ENV_DIR>/out/taxonomy/taxonomy.json (same loader path the pipeline
      uses), embed each in order, stop at the first failure and report its
      index, length, chunk_id, and a short preview. This reproduces the
      pipeline's vectorstore stage in isolation — no need to re-run the
      whole pipeline to debug embedding-side failures.

Provider/model selection mirrors the pipeline runner:
  --embedding-provider {sentence-transformers,huggingface,ollama} (default: ollama)
  --embedding-model <name>                                        (default: nomic-embed-text)
  --ollama-url <url>                                              (default: http://localhost:11434)

Use --no-preview on --chunks if you plan to paste the output in a chat where
the source corpus content cannot appear.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from core.src.vectorstore import make_embedder
from core.src.vectorstore.builder import VectorStoreBuilder
from core.src.vectorstore.chunk_builder import ChunkBuilder
from core.src.vectorstore.config import VectorStoreConfig

logger = logging.getLogger(__name__)


def _build_embedder(args: argparse.Namespace) -> tuple[VectorStoreConfig, object]:
    config = VectorStoreConfig(
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
    )
    if args.ollama_url:
        config.extra["ollama_url"] = args.ollama_url
    return config, make_embedder(config)


def _preview(text: str, n: int = 80) -> str:
    s = text[:n].replace("\n", " ").replace("\r", " ")
    return s + ("…" if len(text) > n else "")


def _err_summary(e: BaseException, max_len: int = 200) -> str:
    msg = str(e).splitlines()[0] if str(e) else type(e).__name__
    return msg[:max_len] + ("…" if len(msg) > max_len else "")


def cmd_check(args: argparse.Namespace) -> int:
    config, embedder = _build_embedder(args)
    print(
        f"EMB connect provider={config.embedding_provider} "
        f"model={config.embedding_model}"
    )
    t0 = time.time()
    try:
        vec = embedder.embed_query("ping")
    except Exception as e:
        print(f"EMB probe FAIL {type(e).__name__}: {_err_summary(e)}")
        return 1
    dt = time.time() - t0
    norm = sum(x * x for x in vec) ** 0.5
    print(f"EMB probe ok dim={len(vec)} norm={norm:.4f} elapsed={dt:.2f}s")
    return 0


def cmd_text(args: argparse.Namespace) -> int:
    config, embedder = _build_embedder(args)
    print(
        f"EMB text length={len(args.text)} chars "
        f"provider={config.embedding_provider} model={config.embedding_model}"
    )
    t0 = time.time()
    try:
        vec = embedder.embed_query(args.text)
    except Exception as e:
        print(f"EMB text FAIL {type(e).__name__}: {_err_summary(e)}")
        return 1
    dt = time.time() - t0
    norm = sum(x * x for x in vec) ** 0.5
    head = ", ".join(f"{x:.4f}" for x in vec[:5])
    print(f"EMB text ok dim={len(vec)} norm={norm:.4f} first5=[{head}] elapsed={dt:.2f}s")
    return 0


_SWEEP_LENGTHS = [100, 1_000, 4_000, 8_000, 16_000, 32_000, 64_000, 128_000]
_LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. "
)


def cmd_sweep(args: argparse.Namespace) -> int:
    config, embedder = _build_embedder(args)
    print(
        f"EMB sweep provider={config.embedding_provider} "
        f"model={config.embedding_model}"
    )
    last_ok = 0
    for length in _SWEEP_LENGTHS:
        repeats = (length // len(_LOREM)) + 1
        text = (_LOREM * repeats)[:length]
        t0 = time.time()
        try:
            vec = embedder.embed_query(text)
            dt = time.time() - t0
            print(f"  {length:>7} chars  -> ok    dim={len(vec)} ({dt:.2f}s)")
            last_ok = length
        except Exception as e:
            dt = time.time() - t0
            print(
                f"  {length:>7} chars  -> FAIL  "
                f"{type(e).__name__}: {_err_summary(e, max_len=120)} ({dt:.2f}s)"
            )
            break
    print(f"EMB max-ok-length={last_ok} chars")
    return 0


def cmd_chunks(args: argparse.Namespace) -> int:
    env_dir = Path(args.chunks).expanduser().resolve()
    trees_dir = env_dir / "out" / "parse"
    taxonomy_path = env_dir / "out" / "taxonomy" / "taxonomy.json"

    if not trees_dir.exists():
        print(f"EMB chunks FAIL trees_dir not found: {trees_dir}")
        return 1

    config, embedder = _build_embedder(args)
    cb = ChunkBuilder(config)
    trees = VectorStoreBuilder._load_trees(trees_dir)
    taxonomy = VectorStoreBuilder._load_taxonomy(
        taxonomy_path if taxonomy_path.exists() else None
    )
    raw_chunks = cb.build_chunks(trees, taxonomy)
    chunks = VectorStoreBuilder._deduplicate_chunks(raw_chunks)

    print(
        f"EMB chunks env_dir={env_dir} trees={len(trees)} chunks={len(chunks)} "
        f"provider={config.embedding_provider} model={config.embedding_model}"
    )

    if not chunks:
        print("EMB chunks SKIP no chunks built — run pipeline through `parse` first")
        return 1

    longest_seen = 0
    for i, chunk in enumerate(chunks):
        longest_seen = max(longest_seen, len(chunk.text))
        try:
            embedder.embed_query(chunk.text)
        except Exception as e:
            print(
                f"EMB chunks FAIL #{i}/{len(chunks)} "
                f"length={len(chunk.text)} chars chunk_id={chunk.chunk_id}"
            )
            if not args.no_preview:
                print(f"  preview={_preview(chunk.text)!r}")
            print(f"  error={type(e).__name__}: {_err_summary(e)}")
            print(f"EMB stats longest_chunk_seen_so_far={longest_seen} chars")
            return 1
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(chunks)} ok (longest so far: {longest_seen} chars)")

    print(f"EMB chunks ok all={len(chunks)} longest_chunk={longest_seen} chars")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NORA — Embedding-provider debug tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --check\n"
            "  %(prog)s --text 'attach reject cause codes'\n"
            "  %(prog)s --length-sweep --embedding-model qwen3-embedding-q8-0:4b\n"
            "  %(prog)s --chunks ~/env-vzw --embedding-model qwen3-embedding-q8-0:4b\n"
        ),
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Connection + model + short embed")
    mode.add_argument("--text", metavar="TEXT", help="Embed an ad-hoc string")
    mode.add_argument(
        "--length-sweep", dest="length_sweep", action="store_true",
        help="Find the model's max input length",
    )
    mode.add_argument(
        "--chunks", metavar="ENV_DIR",
        help="Reproduce pipeline chunks from <ENV_DIR>/out/parse and embed in order",
    )

    parser.add_argument(
        "--embedding-provider", default="ollama",
        choices=["sentence-transformers", "huggingface", "ollama"],
        help="(default: ollama)",
    )
    parser.add_argument(
        "--embedding-model", default="nomic-embed-text",
        help="(default: nomic-embed-text)",
    )
    parser.add_argument(
        "--ollama-url", default=None,
        help="Override Ollama server URL (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--no-preview", action="store_true",
        help="In --chunks mode, suppress text preview on failure (paste-safe)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    if args.check:
        sys.exit(cmd_check(args))
    if args.text is not None:
        sys.exit(cmd_text(args))
    if args.length_sweep:
        sys.exit(cmd_sweep(args))
    if args.chunks:
        sys.exit(cmd_chunks(args))


if __name__ == "__main__":
    main()
