# NORA images — build context is the REPO ROOT, plus a named `vendor` context
# (docker/vendor — see docker-compose.yml additional_contexts):
#   docker build -f docker/nora.Dockerfile --build-context vendor=docker/vendor \
#       --target nora-web -t nora-web .
#
# nora-base     shared dependency layer (requirements.txt, torch)
# nora-web      the web app (serving)
# nora-pipeline batch pipeline + SIRA adapter + Docling (ingest jobs / dev toolbox)
#
# OFFLINE=1 (hosts with no container egress at all, or deterministic vendored
# builds): every dependency installs from the vendored wheels prepared by
# docker/prep-offline.sh — zero in-container network. The vendor wheels are
# BIND-MOUNTED during RUN (never COPY'd), so they add nothing to image layers.
#
# No endpoints, models, mappings, or corpus data are baked in — all arrive via
# env vars and /data/* volume mounts (see docker/docker-compose.yml).

FROM python:3.12-slim AS nora-base
# Some corporate DPI/firewall devices reset the large post-quantum ClientHello
# that this image's OpenSSL 3.5+ sends by default (host tools on older OpenSSL
# 3.0 pass, so only container TLS appears "blocked"). Pin classical key-exchange
# groups — universally supported — so pip at build time and outbound HTTPS at
# run time (e.g. the standards stage) work behind such middleboxes.
RUN printf '%s\n' 'openssl_conf = openssl_init' '[openssl_init]' \
    'ssl_conf = ssl_sect' '[ssl_sect]' 'system_default = system_default_sect' \
    '[system_default_sect]' 'Groups = X25519:P-256:P-384' > /etc/openssl-classical.cnf
ENV OPENSSL_CONF=/etc/openssl-classical.cnf
ARG OFFLINE=0
# Default: the CPU-only torch index (small image, unrestricted builders).
# Online proxy-restricted builders: --build-arg TORCH_INDEX_URL=https://pypi.org/simple
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
# torch AND torchvision from the same index in one resolve — docling needs
# torchvision, and a PyPI torchvision on top of index-installed torch loads
# with broken compiled ops ('operator torchvision::nms does not exist').
RUN --mount=type=bind,from=vendor,target=/vendor \
    if [ "$OFFLINE" = "1" ]; then \
        pip install --no-index --find-links=/vendor/wheels torch torchvision; \
    else \
        pip install --index-url "$TORCH_INDEX_URL" torch torchvision; \
    fi
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=bind,from=vendor,target=/vendor \
    if [ "$OFFLINE" = "1" ]; then \
        pip install --no-index --find-links=/vendor/wheels -r /tmp/requirements.txt; \
    else \
        pip install -r /tmp/requirements.txt; \
    fi

# ---------------------------------------------------------------- nora-web --
FROM nora-base AS nora-web
# Serving-code identity (strand golden-eval): baked git sha for eval
# StackStamp attribution. Pass --build-arg CODE_VERSION="$(git rev-parse --short HEAD)".
ARG CODE_VERSION=""
ENV NORA_CODE_VERSION=$CODE_VERSION
WORKDIR /app
COPY core/ core/
COPY customizations/ customizations/
COPY config/ config/
COPY sandbox/ sandbox/
ENV ENV_DIR=/data/env
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/', timeout=4)"
CMD ["uvicorn", "core.src.web.app:app", "--host", "0.0.0.0", "--port", "8000"]

# ----------------------------------------------------------- nora-pipeline --
FROM nora-base AS nora-pipeline
ARG OFFLINE=0
# Install build essentials for torch._inductor (g++ required by docling/torch
# tensor-compile path — without a C++ compiler the layout provider aborts).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential g++ \
    && rm -rf /var/lib/apt/lists/*
# Docling (CPU): table+figure layout provider for opt-in corpora (D-122).
# Models are NOT baked — provision into the models volume and set
# DOCLING_ARTIFACTS=/data/models/docling + HF_HUB_OFFLINE=1 at run time.
# docling's chain pulls the full opencv-python, which links X11 libs absent
# from slim images (libxcb.so.1 etc.) — swap it for the headless build.
RUN --mount=type=bind,from=vendor,target=/vendor \
    if [ "$OFFLINE" = "1" ]; then \
        pip install --no-index --find-links=/vendor/wheels docling \
     && { pip uninstall -y opencv-python || true; } \
     && pip install --no-index --find-links=/vendor/wheels opencv-python-headless; \
    else \
        pip install docling \
     && { pip uninstall -y opencv-python || true; } \
     && pip install opencv-python-headless; \
    fi \
    # build-time canary: a torch/torchvision/transformers/cv2 problem must
    # fail HERE, not degrade the parse at run time (docling needs this chain).
 && python -c "import torch, torchvision, cv2; \
from transformers import AutoProcessor; \
from docling.document_converter import DocumentConverter; \
print('docling dep chain OK:', torch.__version__, torchvision.__version__, cv2.__version__)"
WORKDIR /app
COPY core/ core/
COPY customizations/ customizations/
COPY config/ config/
COPY sandbox/ sandbox/
ENV ENV_DIR=/data/env
# Toolbox by default; ingest jobs pass their own command, e.g.
#   python -m core.src.pipeline.run_cli --env-dir /data/env --lane ingestion ...
#   python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db ...
CMD ["bash"]
