"""Verify a decision model's head actually shipped.

Design §7 makes this an invariant rather than a nicety: a GGUF conversion
drops the decision adapter, the decision head and the calibration
temperatures, leaving a plain backbone that loads and answers **without
erroring**. A silent degradation to base-model generation would invalidate
an accuracy comparison while looking like a successful run, so the check
runs in code before a number is recorded — not by inspection.

Two checks, because the file check alone was not enough. A file-list check
on the weights directory runs wherever the weights live — for a remote
server that is the serving host, not the eval client — which left the gap
D-DRAFT-20 found: a run with a verified local head against an *unreachable*
server completed and reported the rule-based baseline. `assert_remote_head`
closes it by asking the server what it actually loaded, over the same
connection the decisions will travel. `confidence_is_calibrated` is the
weaker behavioural signal on top of both.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.src.decision.base import DecisionAnswer, DecisionError

logger = logging.getLogger(__name__)

REQUIRED_HEAD_FILES: tuple[str, ...] = ("head.pt",)
"""Files a head-bearing distribution must carry.

`head.pt` is the decision head, and it carries the calibration temperature
inside it rather than alongside it — verified against jaredpalmer/kev-4b,
whose card states "One temperature, T = 2.41, stored in `head.pt`".

This list was first written as `("head.safetensors", "calibration.json")`
from the design doc, and both entries were wrong: the real distribution
ships neither name, so the gate rejected the genuine article. A guess about
a file name reads as a verified invariant once it is in code, which is the
same failure as D-DRAFT-20 one level down — the gate was never run against
a real distribution before being trusted.
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
            missing the head or adapter files. The message
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


REMOTE_PROBE_PATH = "/v1/models"
"""The route that reports what a server loaded. Part of the wire format, not
a Kev extension, and it is the only verification that runs on the connection
the decisions themselves will use."""


def assert_remote_head(
    base_url: str, timeout: int = 10, api_key: str = "", model: str = ""
) -> dict:
    """Raise unless the server at ``base_url`` is serving a calibrated head.

    The remote half of the gate. A file check on this client proves nothing
    about the process answering the questions — it may be a different build,
    a different checkpoint, or not running at all (D-DRAFT-20). This asks the
    server, and it fails on an unreachable host rather than letting a caller
    degrade quietly into the rule-based baseline.

    Args:
        base_url: Server origin, with or without a trailing ``/v1``.
        timeout: Seconds to wait. Short by design — this is a liveness gate
            run before a measurement, not a request that should be retried.
        api_key: Bearer token, when the server requires one. Empty sends no
            `Authorization` header at all rather than a literal `Bearer `,
            because an open in-corp server is the normal case. The probe
            must carry the same credentials the decisions do, or the gate
            fails on exactly the servers that are configured correctly
            (DRIFT-3).
        model: The model the decisions will name. When set, the gate checks
            THAT card and fails if the server does not list it: a server can
            list several models, and verifying whichever comes first proves
            nothing about the one being measured. Empty keeps the first card,
            for servers that list exactly one.

    Returns:
        The checked model card, which carries ``temperature``, ``lora``,
        ``base``, ``device``, ``backend`` and ``dtype``. Worth stamping onto
        a run: it records what was actually measured.

    Raises:
        DecisionError: Unreachable, rejected credentials, non-JSON, no
            model cards, or a card reporting no adapter or no calibration
            temperature — a plain backbone being served where a decider was
            expected. Auth failure is diagnosed separately from
            unreachability: a 401 arrives as an `HTTPError`, which is a
            `URLError` subclass, so folding the two would report a healthy
            server as down and send an administrator after a network fault.
    """
    import json as _json
    import urllib.error
    import urllib.request

    origin = base_url.rstrip("/")
    if origin.endswith("/v1"):
        origin = origin[: -len("/v1")]
    url = f"{origin}{REMOTE_PROBE_PATH}"

    request = urllib.request.Request(
        url,
        headers=(
            {"Authorization": f"Bearer {api_key}"} if api_key else {}
        ),
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = _json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise DecisionError(
                f"decision server at {url} rejected the credentials "
                f"(HTTP {e.code}). The server is up; it refused the probe. "
                "Set NORA_DECISION_API_KEY (or --decision-api-key) to the "
                "token the server was started with — the reference server "
                "is open unless KEV_API_KEY is set, and a key on the server "
                "with none configured here is this error."
            ) from e
        raise DecisionError(
            f"decision server at {url} is unreachable: {e}. Nothing was "
            "measured — a reachable head check on local weights does not "
            "mean the server is up (D-DRAFT-20)."
        ) from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise DecisionError(
            f"decision server at {url} is unreachable: {e}. Nothing was "
            "measured — a reachable head check on local weights does not "
            "mean the server is up (D-DRAFT-20)."
        ) from e
    except ValueError as e:
        raise DecisionError(
            f"decision server at {url} did not return JSON: {e}"
        ) from e

    cards = data.get("models") if isinstance(data, dict) else None
    if not cards:
        raise DecisionError(
            f"decision server at {url} reported no models; sent keys: "
            f"{sorted(data) if isinstance(data, dict) else type(data).__name__}"
        )

    if model:
        named = [c for c in cards if c.get("name") == model]
        if not named:
            raise DecisionError(
                f"decision server at {url} does not list model {model!r}; "
                f"it lists {[c.get('name') for c in cards]}"
            )
        card = named[0]
    else:
        card = cards[0]
    faults = []
    if not card.get("lora"):
        faults.append("no adapter reported")
    temperature = card.get("temperature")
    if not temperature:
        faults.append("no calibration temperature reported")
    if faults:
        raise DecisionError(
            f"the server at {url} is not serving a head-bearing model: "
            f"{'; '.join(faults)}. It will answer without erroring, but as "
            "a plain backbone. Card: "
            f"{ {k: card.get(k) for k in ('name', 'base', 'lora', 'temperature', 'backend', 'dtype')} }"
        )

    logger.info(
        "Decision server verified at %s (model=%s base=%s backend=%s "
        "dtype=%s T=%.2f)",
        url, card.get("name"), card.get("base"), card.get("backend"),
        card.get("dtype"), float(temperature),
    )
    return card
