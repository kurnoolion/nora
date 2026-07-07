# SIRA images — build context is the REPO ROOT:
#   docker build -f docker/sira.Dockerfile --target sira-query -t sira-query .
#   docker build -f docker/sira.Dockerfile --target sira-batch -t sira-batch .
#
# bm25x-builder  Rust stage: clones the pinned upstream SIRA repo, builds the
#                bm25x wheel (CPU mode — GPU lives behind the LLM endpoints).
# sira-base      trimmed runtime (SETUP.md §2a): no torch/sglang; BM25 + HTTP
#                LLM routing only. NORA repo at /app, SIRA clone baked at
#                /app/sandbox/sira (pinned via SIRA_REF) with configs+prompts
#                installed by install_configs.sh.
# sira-query     the per-query FastAPI service (:8040)
# sira-batch     sira_multi / run_pipeline.py / sira_incremental jobs
#
# No endpoints or corpus data baked in — env vars + /data/* volumes only.

ARG SIRA_REPO=https://github.com/facebookresearch/sira.git
# Pinned upstream commit — the one our patches + configs are verified against
# (upstream main drifts; per-stage-routing.patch no longer applies there).
# Bump deliberately: new ref -> re-verify install_configs.sh patches apply.
ARG SIRA_REF=62ec59cfb0d76f28ceb3c3d80023ac58a98e4b7a

# -------------------------------------------------------------- bm25x build --
# Based on the SAME python as the runtime stage so the wheel tag matches
# (a rust-image builder ships a different Debian python -> cpXY mismatch).
FROM python:3.12-slim AS bm25x-builder
ARG SIRA_REPO
ARG SIRA_REF
RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl build-essential pkg-config && rm -rf /var/lib/apt/lists/*
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH=/root/.cargo/bin:$PATH
RUN pip install --no-cache-dir maturin
RUN git clone --depth 1 "$SIRA_REPO" /sira \
 && git -C /sira fetch --depth 1 origin "$SIRA_REF" \
 && git -C /sira checkout FETCH_HEAD
WORKDIR /sira/src/sira/bm25x/python
RUN maturin build --release -o /wheels

# ---------------------------------------------------------------- sira-base --
FROM python:3.12-slim AS sira-base
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
# Trimmed dependency set (sandbox/SETUP.md §2a) — deliberately NO torch/sglang.
# git stays in the image (install_configs.sh applies patches via `git apply`;
# also handy for debugging the baked clone). gcc/g++ only for pytrec_eval's
# C extension; purged in the same layer.
RUN apt-get update && apt-get install -y --no-install-recommends git gcc g++ \
 && pip install \
      aiohttp hydra-core omegaconf 'polars[rtcompat]' \
      huggingface_hub fastapi uvicorn httpx pydantic \
      pytrec_eval numpy requests \
 && pip install --no-deps beir \
 && apt-get purge -y gcc g++ && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
COPY --from=bm25x-builder /wheels/ /tmp/wheels/
RUN pip install /tmp/wheels/*.whl && rm -rf /tmp/wheels
WORKDIR /app
COPY core/ core/
COPY customizations/ customizations/
COPY config/ config/
COPY sandbox/ sandbox/
COPY --from=bm25x-builder /sira/ sandbox/sira/
RUN bash sandbox/install_configs.sh

# --------------------------------------------------------------- sira-query --
FROM sira-base AS sira-query
ENV NORA_SIRA_DB_ROOT=/data/db
EXPOSE 8040
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8040/healthz', timeout=4)"
CMD ["uvicorn", "sandbox.sira_query.service:app", "--host", "0.0.0.0", "--port", "8040"]

# --------------------------------------------------------------- sira-batch --
FROM sira-base AS sira-batch
# Jobs pass their own command, e.g.
#   python -m sandbox.sira_multi --db-root /data/db --sira-clone sandbox/sira ...
#   python -m sandbox.sira_incremental retry-failed --dataset /data/db/<cell> ...
CMD ["bash"]
