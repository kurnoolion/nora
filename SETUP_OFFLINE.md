# NORA Offline Setup — Work PC (no internet)

Everything NORA needs to run on a host with no internet access: Ollama runtime, Gemma LLM weights, and the HuggingFace sentence-embedding model. Perform the **"on an internet-connected machine"** steps on any PC with open egress (home laptop, dev workstation), then transfer the bundled artifacts to the Work PC.

---

## 1. Ollama runtime (with CUDA)

As of v0.21.0, CUDA libraries are bundled into the main package (no separate `-cuda` download). The format is `.tar.zst` (not `.tgz`).

### On an internet-connected machine

Download from GitHub releases:

| File | Size | URL |
|------|------|-----|
| Ollama (with CUDA) | **2 GB** | `https://github.com/ollama/ollama/releases/download/v0.21.0/ollama-linux-amd64.tar.zst` |

### On the Work PC

```bash
# Install zstd if not already present
sudo apt install zstd -y

# Extract to /usr/local (contains bin/ollama + lib/ollama/cuda_v12/, cuda_v13/, etc.)
sudo tar --use-compress-program=unzstd -xf ollama-linux-amd64.tar.zst -C /usr/local

# Verify CUDA libs are in place
ls /usr/local/lib/ollama/cuda_v12/

# Start Ollama and verify GPU is detected
ollama serve &
nvidia-smi   # should show ollama process using GPU memory
```

---

## 2. Gemma LLM weights (manual Ollama model install)

Use this when `ollama pull` fails (corporate proxy, self-signed certs). Pick the model your environment is configured for.

### 2a. gemma3:12b

**Download on the internet-connected machine:**

| # | Description    | Size     | URL |
|---|----------------|----------|-----|
| 1 | Model weights  | **8.1 GB** | `https://registry.ollama.ai/v2/library/gemma3/blobs/sha256:e8ad13eff07a78d89926e9e8b882317d082ef5bf9768ad7b50fcdbbcd63748de` |
| 2 | Config         | 490 B    | `https://registry.ollama.ai/v2/library/gemma3/blobs/sha256:6819964c2bcf53f6dd3593f9571e91cbf2bab9665493f870f96eeb29873049b4` |
| 3 | Template       | 358 B    | `https://registry.ollama.ai/v2/library/gemma3/blobs/sha256:e0a42594d802e5d31cdc786deb4823edb8adff66094d49de8fffe976d753e348` |
| 4 | License        | 8 KB     | `https://registry.ollama.ai/v2/library/gemma3/blobs/sha256:dd084c7d92a3c1c14cc09ae77153b903fd2024b64a100a0cc8ec9316063d2dbc` |
| 5 | Params         | 77 B     | `https://registry.ollama.ai/v2/library/gemma3/blobs/sha256:3116c52250752e00dd06b16382e952bd33c34fd79fc4fe3a5d2c77cf7de1b14b` |

Only file #1 (8.1 GB) takes real time; the rest are tiny.

**Install on the Work PC:**

```bash
mkdir -p ~/.ollama/models/manifests/registry.ollama.ai/library/gemma3
mkdir -p ~/.ollama/models/blobs

# Write the manifest
cat > ~/.ollama/models/manifests/registry.ollama.ai/library/gemma3/12b << 'EOF'
{"schemaVersion":2,"mediaType":"application/vnd.docker.distribution.manifest.v2+json","config":{"mediaType":"application/vnd.docker.container.image.v1+json","digest":"sha256:6819964c2bcf53f6dd3593f9571e91cbf2bab9665493f870f96eeb29873049b4","size":490},"layers":[{"mediaType":"application/vnd.ollama.image.model","digest":"sha256:e8ad13eff07a78d89926e9e8b882317d082ef5bf9768ad7b50fcdbbcd63748de","size":8149180896},{"mediaType":"application/vnd.ollama.image.template","digest":"sha256:e0a42594d802e5d31cdc786deb4823edb8adff66094d49de8fffe976d753e348","size":358},{"mediaType":"application/vnd.ollama.image.license","digest":"sha256:dd084c7d92a3c1c14cc09ae77153b903fd2024b64a100a0cc8ec9316063d2dbc","size":8432},{"mediaType":"application/vnd.ollama.image.params","digest":"sha256:3116c52250752e00dd06b16382e952bd33c34fd79fc4fe3a5d2c77cf7de1b14b","size":77}]}
EOF

# Move downloaded files to blobs dir (note: use `-` not `:` after sha256)
mv <downloaded-file-1> ~/.ollama/models/blobs/sha256-e8ad13eff07a78d89926e9e8b882317d082ef5bf9768ad7b50fcdbbcd63748de
mv <downloaded-file-2> ~/.ollama/models/blobs/sha256-6819964c2bcf53f6dd3593f9571e91cbf2bab9665493f870f96eeb29873049b4
mv <downloaded-file-3> ~/.ollama/models/blobs/sha256-e0a42594d802e5d31cdc786deb4823edb8adff66094d49de8fffe976d753e348
mv <downloaded-file-4> ~/.ollama/models/blobs/sha256-dd084c7d92a3c1c14cc09ae77153b903fd2024b64a100a0cc8ec9316063d2dbc
mv <downloaded-file-5> ~/.ollama/models/blobs/sha256-3116c52250752e00dd06b16382e952bd33c34fd79fc4fe3a5d2c77cf7de1b14b

# Restart and verify
sudo systemctl restart ollama     # or `ollama serve` if running manually
ollama list                        # should show gemma3:12b
ollama run gemma3:12b "Hello, what model are you?"
```

### 2b. gemma4:e4b

**Download on the internet-connected machine:**

| # | Description    | Size       | URL |
|---|----------------|------------|-----|
| 1 | Model weights  | **9.6 GB** | `https://registry.ollama.ai/v2/library/gemma4/blobs/sha256:4c27e0f5b5adf02ac956c7322bd2ee7636fe3f45a8512c9aba5385242cb6e09a` |
| 2 | Config         | 473 B      | `https://registry.ollama.ai/v2/library/gemma4/blobs/sha256:f0988ff50a2458c598ff6b1b87b94d0f5c44d73061c2795391878b00b2285e11` |
| 3 | License        | 11 KB      | `https://registry.ollama.ai/v2/library/gemma4/blobs/sha256:7339fa418c9ad3e8e12e74ad0fd26a9cc4be8703f9c110728a992b193be85cb2` |
| 4 | Params         | 42 B       | `https://registry.ollama.ai/v2/library/gemma4/blobs/sha256:56380ca2ab89f1f68c283f4d50863c0bcab52ae3f1b9a88e4ab5617b176f71a3` |

**Install on the Work PC:**

```bash
mkdir -p ~/.ollama/models/manifests/registry.ollama.ai/library/gemma4
mkdir -p ~/.ollama/models/blobs

# Write the manifest
cat > ~/.ollama/models/manifests/registry.ollama.ai/library/gemma4/e4b << 'EOF'
{"schemaVersion":2,"mediaType":"application/vnd.docker.distribution.manifest.v2+json","config":{"mediaType":"application/vnd.docker.container.image.v1+json","digest":"sha256:f0988ff50a2458c598ff6b1b87b94d0f5c44d73061c2795391878b00b2285e11","size":473},"layers":[{"mediaType":"application/vnd.ollama.image.model","digest":"sha256:4c27e0f5b5adf02ac956c7322bd2ee7636fe3f45a8512c9aba5385242cb6e09a","size":9608338848},{"mediaType":"application/vnd.ollama.image.license","digest":"sha256:7339fa418c9ad3e8e12e74ad0fd26a9cc4be8703f9c110728a992b193be85cb2","size":11355},{"mediaType":"application/vnd.ollama.image.params","digest":"sha256:56380ca2ab89f1f68c283f4d50863c0bcab52ae3f1b9a88e4ab5617b176f71a3","size":42}]}
EOF

# Move downloaded files to blobs dir (note: use `-` not `:` after sha256)
mv <downloaded-file-1> ~/.ollama/models/blobs/sha256-4c27e0f5b5adf02ac956c7322bd2ee7636fe3f45a8512c9aba5385242cb6e09a
mv <downloaded-file-2> ~/.ollama/models/blobs/sha256-f0988ff50a2458c598ff6b1b87b94d0f5c44d73061c2795391878b00b2285e11
mv <downloaded-file-3> ~/.ollama/models/blobs/sha256-7339fa418c9ad3e8e12e74ad0fd26a9cc4be8703f9c110728a992b193be85cb2
mv <downloaded-file-4> ~/.ollama/models/blobs/sha256-56380ca2ab89f1f68c283f4d50863c0bcab52ae3f1b9a88e4ab5617b176f71a3

# Restart and verify
sudo systemctl restart ollama     # or `ollama serve` if running manually
ollama list                        # should show gemma4:e4b
ollama run gemma4:e4b "Hello, what model are you?"
```

---

## 3. HuggingFace sentence-embedding model

The vectorstore and query stages use a `sentence-transformers` model (default `all-MiniLM-L6-v2`; configurable via `VectorStoreConfig.embedding_model`). On first use, `sentence-transformers` calls `huggingface_hub` to resolve the model revision. On a restricted network, that call fails with `httpx.ConnectError`, which triggers a known bug in `huggingface_hub._http_backoff_base`: it closes the shared httpx client and retries with the same (now-closed) reference, raising

```
RuntimeError: Cannot send a request, as the client has been closed.
```

NORA surfaces this as `ERR PIP-E001: Unhandled error: ...`.

### Fix: pre-populate the HF cache

NORA auto-detects a cached model and enables `HF_HUB_OFFLINE=1` automatically (see `src/vectorstore/hf_offline.py`), so the only action required is getting the cache onto the Work PC.

**Option A — use the tarball vendored in this repo (default model only).**

`assets/hf_cache/all-MiniLM-L6-v2.tgz` (80 MB) is checked into the repo and pulled with `git pull`. On the Work PC:

```bash
mkdir -p ~/.cache/huggingface
cd ~/.cache/huggingface
tar xzf /path/to/nora/assets/hf_cache/all-MiniLM-L6-v2.tgz

# Verify a snapshot with config.json exists
ls hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/*/config.json
```

No env vars to export. Next pipeline run, `enable_offline_if_cached` finds the snapshot, flips `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`, patches `huggingface_hub.constants.HF_HUB_OFFLINE = True`, and the revision HEAD call is skipped entirely.

**Option B — build a tarball for a different model.**

If your env uses something other than `all-MiniLM-L6-v2` (e.g., `all-mpnet-base-v2`, `BAAI/bge-small-en-v1.5`), build the tarball yourself on an internet-connected machine:

```bash
# One-time warm-up if the cache is empty
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"

# Bundle the cache — substitute the model name
cd ~/.cache/huggingface
tar czf /tmp/hf_cache.tgz hub/models--sentence-transformers--all-mpnet-base-v2
```

Then transfer `/tmp/hf_cache.tgz` to the Work PC and extract under `~/.cache/huggingface/` as in Option A.

### Manual override

If you need to force offline mode before NORA runs (e.g., other tooling on the same host), export:

```bash
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
```

---

## 4. End-to-end verification

```bash
# Ollama model reachable
ollama list                                    # gemma3:12b or gemma4:e4b listed
ollama run gemma4:e4b "2+2="                   # returns quickly

# HF cache present
ls ~/.cache/huggingface/hub/models--sentence-transformers--*/snapshots/*/config.json

# Run a short NORA pipeline stage that touches both
python -m core.src.cli.run_pipeline --stage-start vectorstore --stage-end vectorstore \
    --document-dir /path/to/docs
# Expect: no PIP-E001, "HF model '...' found in cache — HF_HUB_OFFLINE=1 enabled" in logs
```

If `run_vectorstore` or `run_eval` still raises `PIP-E001: Cannot send a request, as the client has been closed`, the cache path or snapshot is not where the auto-detector expects — check `_hf_cache_root()` in `src/vectorstore/hf_offline.py` and confirm `HF_HOME` / `HF_HUB_CACHE` env vars match the directory you populated.
