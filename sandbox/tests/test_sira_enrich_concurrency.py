"""The SIRA enrich config takes its concurrency from NORA_SIRA_ENRICH_CONCURRENCY
(an integer; 4 when unset)."""

from pathlib import Path

from omegaconf import OmegaConf

CONFIG = Path(__file__).resolve().parents[1] / "sira_configs" / "enrich" / "nora.yaml"


def test_concurrency_from_env(monkeypatch):
    monkeypatch.delenv("NORA_SIRA_ENRICH_CONCURRENCY", raising=False)
    assert OmegaConf.load(CONFIG).concurrency == 4
    monkeypatch.setenv("NORA_SIRA_ENRICH_CONCURRENCY", "8")
    value = OmegaConf.load(CONFIG).concurrency
    assert value == 8 and isinstance(value, int)
