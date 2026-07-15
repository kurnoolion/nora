# SIRA images — build context is the REPO ROOT, plus a named `vendor` context
# (docker/vendor — see docker-compose.yml additional_contexts):
#   docker build -f docker/sira.Dockerfile --build-context vendor=docker/vendor \
#       --target sira-query -t sira-query .
#
# Source selection (ARG OFFLINE selects the stage):
#   OFFLINE=0  sira-src-0: clone upstream @ SIRA_REF, rustup, build the bm25x
#              wheel, apply install_configs in-container (network needed).
#   OFFLINE=1  sira-src-1: take the pre-patched clone + pre-built bm25x wheel
#              from the vendor context (docker/prep-offline.sh) — zero network,
#              no apt/rustup/git in any stage (hosts without container egress,
#              or deterministic fully-vendored builds).
#
# sira-base    trimmed runtime (SETUP.md §2a): no torch/sglang; BM25 + HTTP
#              LLM routing only. NORA repo at /app, SIRA clone at
#              /app/sandbox/sira (pinned via SIRA_REF).
# sira-query   the per-query FastAPI service (:8040)
# sira-batch   sira_multi / run_pipeline.py / sira_incremental jobs
#
# No endpoints or corpus data baked in — env vars + /data/* volumes only.

ARG OFFLINE=0
ARG SIRA_REPO=https://github.com/facebookresearch/sira.git
# Pinned upstream commit — the one our patches + configs are verified against
# (upstream main drifts; per-stage-routing.patch no longer applies there).
# Bump deliberately: new ref -> re-verify install_configs.sh patches apply.
ARG SIRA_REF=62ec59cfb0d76f28ceb3c3d80023ac58a98e4b7a

# ------------------------------------------------------ shared python base --
# Some corporate DPI/firewall devices reset the large post-quantum ClientHello
# that this image's OpenSSL 3.5+ sends by default (host tools on older OpenSSL
# 3.0 pass, so only container TLS appears "blocked"). Pin classical key-exchange
# groups — universally supported — so pip/git/https work behind such middleboxes.
FROM python:3.12-slim AS py-classical
RUN printf '%s\n' 'openssl_conf = openssl_init' '[openssl_init]' \
    'ssl_conf = ssl_sect' '[ssl_sect]' 'system_default = system_default_sect' \
    '[system_default_sect]' 'Groups = X25519:P-256:P-384' > /etc/openssl-classical.cnf
ENV OPENSSL_CONF=/etc/openssl-classical.cnf

# ---------------------------------------------- source: ONLINE (OFFLINE=0) --
# Based on the SAME python as the runtime stage so the wheel tag matches.
FROM py-classical AS sira-src-0
ARG SIRA_REPO
ARG SIRA_REF
RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl build-essential pkg-config && rm -rf /var/lib/apt/lists/*
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH=/root/.cargo/bin:$PATH
RUN pip install --no-cache-dir maturin
RUN git clone "$SIRA_REPO" /sira \
 && git -C /sira fetch origin "$SIRA_REF" \
 && git -C /sira checkout FETCH_HEAD
WORKDIR /sira/src/sira/bm25x/python
RUN maturin build --release -o /wheels

# --------------------------------------------- source: OFFLINE (OFFLINE=1) --
FROM python:3.12-slim AS sira-src-1
RUN --mount=type=bind,from=vendor,target=/vendor \
    test -d /vendor/sira || { echo "OFFLINE=1 but docker/vendor/sira missing — run docker/prep-offline.sh first" >&2; exit 1; } \
 && test -n "$(ls /vendor/wheels/bm25x-*.whl 2>/dev/null)" || { echo "OFFLINE=1 but no bm25x wheel in docker/vendor/wheels" >&2; exit 1; } \
 && mkdir -p /wheels /sira \
 && cp /vendor/wheels/bm25x-*.whl /wheels/ \
 && cp -r /vendor/sira/. /sira/

# resolved source stage (ARG-selected)
FROM sira-src-${OFFLINE} AS sira-src

# ---------------------------------------------------------------- sira-base --
FROM py-classical AS sira-base
ARG OFFLINE=0
# PYTHONPATH stands in for bare-metal's `pip install --no-deps -e .` on the
# clone (SETUP.md §2): SIRA's run_pipeline imports the `sira` package from
# the clone's src/ layout. An editable install would need a PEP-660 build
# backend fetch — not OFFLINE-safe; PYTHONPATH is equivalent and free.
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/sandbox/sira/src
# Trimmed dependency set (sandbox/SETUP.md §2a) — deliberately NO torch/sglang.
# ONLINE: git stays in the image (install_configs.sh needs `git apply`); gcc/g++
# only for pytrec_eval's C extension, purged in-layer.
# OFFLINE: no apt at all — pytrec_eval arrives as a vendored wheel and the
# clone is pre-patched, so neither compiler nor git is needed.
RUN --mount=type=bind,from=vendor,target=/vendor \
    if [ "$OFFLINE" = "1" ]; then \
        pip install --no-index --find-links=/vendor/wheels \
            aiohttp hydra-core omegaconf 'polars[rtcompat]' \
            huggingface_hub fastapi uvicorn httpx pydantic \
            pytrec_eval numpy requests \
     && pip install --no-index --find-links=/vendor/wheels --no-deps beir; \
    else \
        apt-get update && apt-get install -y --no-install-recommends git gcc g++ \
     && pip install \
            aiohttp hydra-core omegaconf 'polars[rtcompat]' \
            huggingface_hub fastapi uvicorn httpx pydantic \
            pytrec_eval numpy requests \
     && pip install --no-deps beir \
     && apt-get purge -y gcc g++ && apt-get autoremove -y \
     && rm -rf /var/lib/apt/lists/*; \
    fi
COPY --from=sira-src /wheels/ /tmp/wheels/
RUN pip install /tmp/wheels/*.whl && rm -rf /tmp/wheels
WORKDIR /app
COPY core/ core/
COPY customizations/ customizations/
COPY config/ config/
COPY sandbox/ sandbox/
# --chmod=0777: ingest jobs run as the host user (JOB_UID, no passwd entry);
# the sira lane writes per-cell data configs into the clone
# (scripts/configs/data/<cell>.yaml) and SIRA's run_pipeline runs with
# cwd=clone — the baked clone must be writable for non-root. Container-
# ephemeral content, so wide perms carry no risk.
COPY --from=sira-src --chmod=0777 /sira/ sandbox/sira/
# ONLINE: apply configs+prompts+patches now. OFFLINE: prep-offline.sh already
# did this on the host against the vendored clone. The trailing chmod keeps
# files CREATED by install_configs (root umask) writable for JOB_UID too.
RUN if [ "$OFFLINE" = "1" ]; then echo "offline: clone pre-patched by prep-offline.sh"; \
    else bash sandbox/install_configs.sh && chmod -R go+w sandbox/sira/scripts/configs; fi

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
#   python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db ...
#   python -m sandbox.sira_incremental retry-failed --dataset /data/db/<cell> ...
CMD ["bash"]
