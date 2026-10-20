"""Verify a decision model's head actually shipped.

Design §7 makes this an invariant rather than a nicety: a GGUF conversion
drops the decision adapter, the decision head and the calibration
temperatures, leaving a plain backbone that loads and answers **without
erroring**. A silent degradation to base-model generation would invalidate
an accuracy comparison while looking like a successful run, so the check
runs in code before a number is recorded — not by inspection.

The check is a file-list check on the weights directory, which is what
D-DRAFT-3 specifies and what can actually be verified. It therefore runs
wherever the weights live; for a remote server that is the serving host, not
the client. `confidence_is_calibrated` is the complementary behavioural
signal available to a client.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.src.decision.base import DecisionAnswer, DecisionError

logger = logging.getLogger(__name__)

REQUIRED_HEAD_FILES: tuple[str, ...] = ("head.safetensors", "calibration.json")
"""Files a head-bearing distribution must carry.

`head.safetensors` is the decision head itself; `calibration.json` holds the
per-slot temperatures that make `confidence` a probability rather than a
raw logit. Both are separate files from the backbone, which is exactly why
a format conversion drops them.
"""

ADAPTER_MARKERS: tuple[str, ...] = (
    "adapter_config.json",
    "adapter_model.safetensors",
)
"""Any one of these marks the decision adapter's presence, whether it sits
at the top level or inside an adapter folder."""


def inspect_weights(model_dir: Path) -> dict:
    """Describe what a weights directory contains, without judging it.

    Returns:
        A dict with ``path``, ``present`` (the required files found),
        ``missing``, ``has_adapter``, ``gguf_only`` and ``file_count``.
        Names only — never file contents, so the result is safe to log.
    """
    model_dir = Path(model_dir)
    names = {p.name for p in model_dir.rglob("*") if p.is_file()}
    present = sorted(n for n in REQUIRED_HEAD_FILES if n in names)
    return {
        "path": str(model_dir),
        "present": present,
        "missing": sorted(set(REQUIRED_HEAD_FILES) - set(present)),
        "has_adapter": any(m in names for m in ADAPTER_MARKERS),
        "gguf_only": bool(names) and all(n.endswith(".gguf") for n in names),
        "file_count": len(names),
    }


def assert_decision_head_present(model_dir: Path) -> dict:
    """Raise unless ``model_dir`` holds a head-bearing distribution.

    Args:
        model_dir: The weights directory to check.

    Returns:
        The `inspect_weights` report, for stamping onto a run.

    Raises:
        DecisionError: The directory is missing, empty, GGUF-only, or
            missing the head, adapter or calibration files. The message
            names what was missing — a hollow build is indistinguishable
            from a good one at inference time, so the diagnosis has to
            come from here.
    """
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise DecisionError(
            f"decision weights directory not found: {model_dir}"
        )

    report = inspect_weights(model_dir)
    if report["file_count"] == 0:
        raise DecisionError(f"decision weights directory is empty: {model_dir}")
    if report["gguf_only"]:
        raise DecisionError(
            f"{model_dir} contains only .gguf files — this is a hollow "
            "distribution. GGUF conversion drops the decision adapter, the "
            "decision head and the calibration temperatures; the model will "
            "load and answer without erroring, but as a plain backbone. "
            "Use the safetensors distribution (design §7, D-DRAFT-3)."
        )

    faults = []
    if report["missing"]:
        faults.append(f"missing {', '.join(report['missing'])}")
    if not report["has_adapter"]:
        faults.append(
            f"no decision adapter (none of {', '.join(ADAPTER_MARKERS)})"
        )
    if faults:
        raise DecisionError(
            f"{model_dir} is not a head-bearing distribution: "
            f"{'; '.join(faults)}. Recording an accuracy number against it "
            "would measure the backbone, not the decider."
        )

    logger.info(
        "Decision head verified at %s (%d files, adapter present)",
        model_dir, report["file_count"],
    )
    return report


def confidence_is_calibrated(answers: list[DecisionAnswer]) -> bool:
    """Whether the answers look like they came from a calibrated head.

    The complementary signal a *client* can see: a head with its
    calibration temperatures loaded reports a probability per answer, so an
    all-zero confidence vector is the shape a stripped build produces. Weak
    evidence on its own — a legitimate provider may simply not report
    confidence — so this informs a warning, never a hard failure.
    """
    return bool(answers) and any(a.confidence > 0.0 for a in answers)
