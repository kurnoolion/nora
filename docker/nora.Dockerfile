# NORA images — build context is the REPO ROOT:
#   docker build -f docker/nora.Dockerfile --target nora-web      -t nora-web      .
#   docker build -f docker/nora.Dockerfile --target nora-pipeline -t nora-pipeline .
#
# nora-base     shared dependency layer (requirements.txt, CPU torch)
# nora-web      the web app (serving)
# nora-pipeline batch pipeline + SIRA adapter + Docling (ingest jobs / dev toolbox)
#
# No endpoints, models, mappings, or corpus data are baked in — all arrive via
# env vars and /data/* volume mounts (see docker/docker-compose.yml).

FROM python:3.12-slim AS nora-base
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
# CPU-only torch FIRST so sentence-transformers doesn't drag the CUDA build in.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# ---------------------------------------------------------------- nora-web --
FROM nora-base AS nora-web
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
# Docling (CPU): table+figure layout provider for opt-in corpora (D-122).
# Models are NOT baked — provision into the models volume and set
# DOCLING_ARTIFACTS=/data/models/docling + HF_HUB_OFFLINE=1 at run time.
RUN pip install docling
WORKDIR /app
COPY core/ core/
COPY customizations/ customizations/
COPY config/ config/
COPY sandbox/ sandbox/
ENV ENV_DIR=/data/env
# Toolbox by default; ingest jobs pass their own command, e.g.
#   python -m core.src.pipeline.run_cli --env-dir /data/env --start extract --end parse ...
#   python -m sandbox.adapter.nora_to_beir --env-dir /data/env --output /data/db --multi-cell ...
CMD ["bash"]
