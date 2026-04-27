#!/usr/bin/env bash
# Setup script for NORA (Network Operator Requirements Analyzer).
# Run this once on a new machine to install dependencies and configure the environment.
#
# Usage:
#   chmod +x setup_env.sh
#   ./setup_env.sh                             # Full setup (Python deps + Ollama + model)
#   ./setup_env.sh --deps-only                 # Python dependencies only
#   ./setup_env.sh --check                     # Verify environment without installing
#   ./setup_env.sh --download-dir ./downloads  # Use pre-downloaded files (see download_urls.txt)

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[!!]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }

DEPS_ONLY=false
CHECK_ONLY=false
DOWNLOAD_DIR=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --deps-only) DEPS_ONLY=true; shift ;;
        --check)     CHECK_ONLY=true; shift ;;
        --download-dir)
            if [ -z "${2:-}" ]; then fail "--download-dir requires a path argument"; exit 1; fi
            DOWNLOAD_DIR="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --deps-only              Install Python deps only (no Ollama/model)"
            echo "  --check                  Verify environment without installing"
            echo "  --download-dir <path>    Use pre-downloaded files from <path> instead of curl"
            echo ""
            echo "Offline workflow (for environments where curl HTTPS fails):"
            echo "  1. Check download_urls.txt for the list of URLs"
            echo "  2. Download each file manually into a directory (keep the filenames)"
            echo "  3. Run: $0 --download-dir /path/to/downloaded/files"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "========================================"
echo "Telecom Requirements AI — Environment Setup"
echo "========================================"
echo ""

# ── 1. Hardware Detection ─────────────────────────────────────────────
echo "── Hardware ──"

# CPU
CPU_MODEL=$(lscpu 2>/dev/null | grep "Model name" | sed 's/Model name:\s*//' | head -1) || CPU_MODEL="unknown"
CPU_CORES=$(nproc 2>/dev/null) || CPU_CORES="?"
echo "  CPU: $CPU_MODEL ($CPU_CORES cores)"

# RAM
RAM_TOTAL=$(free -g 2>/dev/null | awk '/Mem:/ {print $2}') || RAM_TOTAL="?"
RAM_AVAIL=$(free -g 2>/dev/null | awk '/Mem:/ {print $7}') || RAM_AVAIL="?"
echo "  RAM: ${RAM_TOTAL}GB total, ${RAM_AVAIL}GB available"

# GPU
if command -v nvidia-smi &>/dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null) || GPU_INFO=""
    if [ -n "$GPU_INFO" ]; then
        GPU_NAME=$(echo "$GPU_INFO" | cut -d',' -f1 | xargs)
        GPU_VRAM=$(echo "$GPU_INFO" | cut -d',' -f2 | xargs)
        GPU_VRAM_GB=$((GPU_VRAM / 1024))
        ok "GPU: $GPU_NAME (${GPU_VRAM_GB}GB VRAM)"
        HAS_GPU=true
    else
        warn "nvidia-smi found but no GPU detected"
        HAS_GPU=false
    fi
else
    warn "No NVIDIA GPU detected (nvidia-smi not found)"
    HAS_GPU=false
fi

echo ""

# ── 2. Python ─────────────────────────────────────────────────────────
echo "── Python ──"

if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 --version 2>&1)
    ok "Python: $PY_VERSION"
else
    fail "Python 3 not found. Install Python 3.10+."
    exit 1
fi

# Check pip
if python3 -m pip --version &>/dev/null; then
    ok "pip available"
else
    fail "pip not found. Install: python3 -m ensurepip"
    exit 1
fi

echo ""

# ── 3. Python Dependencies ────────────────────────────────────────────
echo "── Python Dependencies ──"

if [ "$CHECK_ONLY" = true ]; then
    echo "  Checking installed packages..."
    MISSING=0
    while IFS= read -r line; do
        pkg=$(echo "$line" | cut -d'>' -f1 | cut -d'=' -f1)
        if ! python3 -c "import importlib; importlib.import_module('${pkg//-/_}')" 2>/dev/null; then
            warn "Missing: $pkg"
            MISSING=$((MISSING + 1))
        fi
    done < requirements.txt
    if [ $MISSING -eq 0 ]; then
        ok "All Python dependencies installed"
    else
        warn "$MISSING packages missing. Run: pip install -r requirements.txt"
    fi
else
    echo "  Installing from requirements.txt..."
    python3 -m pip install -r requirements.txt --quiet
    ok "Python dependencies installed"
fi

echo ""

if [ "$DEPS_ONLY" = true ]; then
    echo "Done (--deps-only). Skipping Ollama/model setup."
    exit 0
fi

# ── 4. LibreOffice (for DOC→DOCX conversion) ─────────────────────────
echo "── LibreOffice ──"

if command -v libreoffice &>/dev/null; then
    LO_VERSION=$(libreoffice --version 2>/dev/null | head -1) || LO_VERSION="unknown version"
    ok "LibreOffice: $LO_VERSION"
else
    warn "LibreOffice not found. Needed for DOC→DOCX conversion."
    warn "Install: sudo apt install libreoffice-writer"
fi

echo ""

# ── 5. Ollama ─────────────────────────────────────────────────────────
echo "── Ollama ──"

if command -v ollama &>/dev/null; then
    OLLAMA_VERSION=$(ollama --version 2>/dev/null) || OLLAMA_VERSION="unknown"
    ok "Ollama: $OLLAMA_VERSION"
else
    if [ "$CHECK_ONLY" = true ]; then
        warn "Ollama not installed. Install: curl -fsSL https://ollama.com/install.sh | sh"
    elif [ -n "$DOWNLOAD_DIR" ] && [ -f "$DOWNLOAD_DIR/ollama-linux-amd64.tgz" ]; then
        echo "  Installing Ollama from local tarball: $DOWNLOAD_DIR/ollama-linux-amd64.tgz"
        mkdir -p /tmp/ollama-install
        tar -xzf "$DOWNLOAD_DIR/ollama-linux-amd64.tgz" -C /tmp/ollama-install
        sudo cp /tmp/ollama-install/bin/ollama /usr/local/bin/ollama
        rm -rf /tmp/ollama-install
        ok "Ollama installed (from local tarball)"
    elif [ -n "$DOWNLOAD_DIR" ] && [ -f "$DOWNLOAD_DIR/install_ollama.sh" ]; then
        echo "  Installing Ollama from local script: $DOWNLOAD_DIR/install_ollama.sh"
        bash "$DOWNLOAD_DIR/install_ollama.sh"
        ok "Ollama installed (from local script)"
    else
        echo "  Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
        ok "Ollama installed"
    fi
fi

# Check Ollama server
if curl -s http://localhost:11434/api/tags &>/dev/null; then
    ok "Ollama server running"
else
    warn "Ollama server not running. Start: ollama serve"
    if [ "$CHECK_ONLY" = false ]; then
        echo "  Starting Ollama server in background..."
        nohup ollama serve &>/dev/null &
        sleep 3
        if curl -s http://localhost:11434/api/tags &>/dev/null; then
            ok "Ollama server started"
        else
            warn "Failed to start Ollama server. Run manually: ollama serve"
        fi
    fi
fi

echo ""

# ── 6. Model Selection & Pull ─────────────────────────────────────────
echo "── LLM Model ──"

# Use Python model picker for recommendation
echo "  Running model picker..."
RECOMMENDED=$(python3 -c "
from src.llm.model_picker import detect_hardware, pick_model
hw = detect_hardware()
choice = pick_model(hw)
print(choice.model)
" 2>/dev/null) || RECOMMENDED="gemma4:e4b"

ok "Recommended model: $RECOMMENDED"

# Check if model is already pulled
AVAILABLE=$(curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('models', []):
    print(m['name'])
" 2>/dev/null) || AVAILABLE=""

if echo "$AVAILABLE" | grep -q "^${RECOMMENDED}$"; then
    ok "Model '$RECOMMENDED' already available"
else
    if [ "$CHECK_ONLY" = true ]; then
        warn "Model '$RECOMMENDED' not pulled. Run: ollama pull $RECOMMENDED"
    else
        echo "  Pulling model '$RECOMMENDED' (this may take several minutes)..."
        if ollama pull "$RECOMMENDED"; then
            ok "Model '$RECOMMENDED' pulled"
        else
            warn "Normal pull failed. Retrying with OLLAMA_INSECURE=1 (skip TLS verify)..."
            if OLLAMA_INSECURE=1 ollama pull "$RECOMMENDED"; then
                ok "Model '$RECOMMENDED' pulled (insecure mode)"
            else
                fail "Model pull failed. Try manually:"
                echo "    OLLAMA_INSECURE=1 ollama pull $RECOMMENDED"
                echo "  Or pull on another machine and copy ~/.ollama/models/ here."
            fi
        fi
    fi
fi

echo ""

# ── 7. Verification ──────────────────────────────────────────────────
echo "── Verification ──"

echo "  Running model picker check..."
python3 -c "
from src.llm.model_picker import detect_hardware, pick_model, check_model_available
hw = detect_hardware()
print(f'  HW: {hw.compact()}')
choice = pick_model(hw)
print(f'  Model: {choice.model} ({choice.reason})')
avail, models = check_model_available(choice.model)
print(f'  Available: {avail} (models on server: {len(models)})')
"

echo ""
echo "  Running quick import check..."
python3 -c "
modules = [
    'src.extraction.registry',
    'src.profiler.profiler',
    'src.parser.structural_parser',
    'src.resolver.resolver',
    'src.taxonomy.extractor',
    'src.graph.builder',
    'src.vectorstore.builder',
    'src.query.pipeline',
    'src.eval.runner',
    'src.pipeline.runner',
    'src.env.config',
]
ok = 0
for m in modules:
    try:
        __import__(m)
        ok += 1
    except Exception as e:
        print(f'  WARN: {m}: {e}')
print(f'  {ok}/{len(modules)} modules importable')
"

echo ""
echo "========================================"
echo "Setup complete."
echo ""
echo "Next steps:"
echo "  1. Place documents in a directory"
echo "  2. Create an environment:"
echo "     python -m core.src.env.env_cli create --name my-env --member yourname --doc-root /path/to/docs --stages extract:parse"
echo "  3. Initialize the environment:"
echo "     python -m core.src.env.env_cli init my-env"
echo "  4. Run the pipeline:"
echo "     python -m core.src.pipeline.run_cli --env my-env"
echo "========================================"
