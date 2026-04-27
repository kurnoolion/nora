"""Hardware detection and LLM model selection.

Detects CPU, GPU, and RAM via Linux commands, then ranks Ollama models
from best to worst based on what fits in available memory.

Usage:
    from src.llm.model_picker import detect_hardware, pick_model

    hw = detect_hardware()
    choice = pick_model(hw)
    print(choice)  # ModelChoice(model="gemma4:e4b", reason="...")
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardware info
# ---------------------------------------------------------------------------

@dataclass
class HardwareInfo:
    cpu_model: str = "unknown"
    cpu_cores: int = 0
    ram_total_gb: float = 0.0
    ram_available_gb: float = 0.0
    gpu_name: str = ""
    gpu_vram_gb: float = 0.0
    has_gpu: bool = False

    def compact(self) -> str:
        """One-line summary for reports."""
        parts = [f"CPU={self.cpu_model}({self.cpu_cores}c)"]
        parts.append(f"RAM={self.ram_total_gb:.0f}G")
        if self.has_gpu:
            parts.append(f"GPU={self.gpu_name}({self.gpu_vram_gb:.0f}G)")
        else:
            parts.append("GPU=none")
        return " ".join(parts)


def detect_hardware() -> HardwareInfo:
    """Detect CPU, GPU, and RAM using Linux commands."""
    hw = HardwareInfo()

    # CPU
    try:
        out = subprocess.check_output(
            ["lscpu"], text=True, stderr=subprocess.DEVNULL, timeout=5
        )
        for line in out.splitlines():
            if line.startswith("Model name:"):
                hw.cpu_model = line.split(":", 1)[1].strip()
                # Shorten common names
                for remove in ["(R)", "(TM)", "CPU ", "Processor"]:
                    hw.cpu_model = hw.cpu_model.replace(remove, "")
                hw.cpu_model = " ".join(hw.cpu_model.split())  # collapse spaces
            elif line.startswith("CPU(s):"):
                try:
                    hw.cpu_cores = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.debug("lscpu not available")

    # RAM
    try:
        out = subprocess.check_output(
            ["free", "-b"], text=True, stderr=subprocess.DEVNULL, timeout=5
        )
        for line in out.splitlines():
            if line.startswith("Mem:"):
                parts = line.split()
                hw.ram_total_gb = int(parts[1]) / (1024**3)
                hw.ram_available_gb = int(parts[6]) / (1024**3)
                break
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.debug("free not available")

    # GPU (NVIDIA)
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        line = out.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        hw.gpu_name = parts[0].replace("NVIDIA ", "").replace("GeForce ", "")
        hw.gpu_vram_gb = float(parts[1]) / 1024  # MiB -> GiB
        hw.has_gpu = True
    except (subprocess.SubprocessError, FileNotFoundError, IndexError, ValueError,
            PermissionError):
        logger.debug("nvidia-smi not available or no NVIDIA GPU")

    return hw


# ---------------------------------------------------------------------------
# Model catalog — ordered best to worst within each tier
# ---------------------------------------------------------------------------

@dataclass
class ModelSpec:
    name: str  # Ollama model tag
    ram_gb: float  # Approximate RAM/VRAM needed (quantized)
    effective_params: str  # Human-readable
    description: str
    gpu_only: bool = False  # Too slow for CPU inference
    quantization: str = "Q4_K_M"

    def fits(self, hw: HardwareInfo) -> bool:
        """Check if model fits available memory."""
        if self.gpu_only and not hw.has_gpu:
            return False
        if hw.has_gpu:
            return self.ram_gb <= hw.gpu_vram_gb * 0.9  # 10% headroom
        return self.ram_gb <= hw.ram_available_gb * 0.85  # 15% headroom for CPU


# Models ranked best-to-worst for quality. Each entry should have
# an Ollama pull tag that resolves to the desired quantization.
MODEL_CATALOG: list[ModelSpec] = [
    ModelSpec(
        "gemma4:27b-it-qat",
        ram_gb=18.0,
        effective_params="26B (MoE 4B active)",
        description="Gemma 4 26B — best quality, needs >=20GB VRAM",
        gpu_only=True,
    ),
    ModelSpec(
        "gemma3:12b",
        ram_gb=8.0,
        effective_params="12B",
        description="Gemma 3 12B — strong quality, fits 12GB+ VRAM or 16GB RAM",
    ),
    ModelSpec(
        "gemma4:e4b",
        ram_gb=9.6,
        effective_params="4B effective (8B total, PLE)",
        description="Gemma 4 E4B — good quality, 128K context, CPU-viable at ~12 tok/s",
    ),
    ModelSpec(
        "gemma3:4b",
        ram_gb=3.0,
        effective_params="4B",
        description="Gemma 3 4B — lighter fallback, fast on CPU",
    ),
    ModelSpec(
        "gemma3:1b",
        ram_gb=1.5,
        effective_params="1B",
        description="Gemma 3 1B — minimal, low quality but runs anywhere",
    ),
]


@dataclass
class ModelChoice:
    model: str
    reason: str
    spec: ModelSpec | None = None
    is_auto: bool = True


def pick_model(hw: HardwareInfo, prefer: str | None = None) -> ModelChoice:
    """Select the best model for the detected hardware.

    Args:
        hw: Detected hardware info.
        prefer: User-preferred model name. If set and valid, used as-is.

    Returns:
        ModelChoice with model name and selection reason.
    """
    # User override — trust their choice
    if prefer and prefer != "auto":
        spec = next((m for m in MODEL_CATALOG if m.name == prefer), None)
        if spec:
            if not spec.fits(hw):
                logger.warning(
                    f"User-selected model '{prefer}' may not fit available memory "
                    f"(needs ~{spec.ram_gb:.1f}GB, "
                    f"{'VRAM=' + f'{hw.gpu_vram_gb:.0f}GB' if hw.has_gpu else 'RAM=' + f'{hw.ram_available_gb:.0f}GB'})"
                )
            return ModelChoice(
                model=prefer,
                reason=f"User-selected: {spec.description}",
                spec=spec,
                is_auto=False,
            )
        return ModelChoice(
            model=prefer,
            reason=f"User-selected (not in catalog)",
            is_auto=False,
        )

    # Auto-select: try each model best-to-worst
    # First, check what's available on Ollama
    available = set(list_available_ollama_models())

    # Prefer a model that both fits AND is already pulled
    for spec in MODEL_CATALOG:
        if spec.fits(hw) and spec.name in available:
            return ModelChoice(
                model=spec.name,
                reason=f"Auto: {spec.description} (fits {'VRAM' if hw.has_gpu else 'RAM'}, already pulled)",
                spec=spec,
            )

    # If nothing pulled fits, recommend the best that fits (user will need to pull)
    for spec in MODEL_CATALOG:
        if spec.fits(hw):
            return ModelChoice(
                model=spec.name,
                reason=f"Auto: {spec.description} (fits {'VRAM' if hw.has_gpu else 'RAM'}, needs: ollama pull {spec.name})",
                spec=spec,
            )

    # Nothing fits — fall back to smallest
    fallback = MODEL_CATALOG[-1]
    return ModelChoice(
        model=fallback.name,
        reason=f"Fallback (nothing larger fits): {fallback.description}",
        spec=fallback,
    )


def list_available_ollama_models(base_url: str = "http://localhost:11434") -> list[str]:
    """Query local Ollama server for pulled models."""
    try:
        req = urllib.request.Request(f"{base_url}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
    except (urllib.error.URLError, Exception):
        return []


def check_model_available(
    model: str, base_url: str = "http://localhost:11434"
) -> tuple[bool, list[str]]:
    """Check if a model is available on the local Ollama server.

    Returns (is_available, all_available_models).
    """
    available = list_available_ollama_models(base_url)
    return (model in available, available)
