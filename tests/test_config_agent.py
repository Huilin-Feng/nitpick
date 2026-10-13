import pytest

from nitpick.config import Settings


def test_defaults_to_pipeline_mode(monkeypatch):
    for name in ("REVIEW_MODE", "AGENT_MAX_STEPS", "AGENT_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    s = Settings.from_env()
    assert (s.review_mode, s.agent_max_steps, s.agent_max_tokens) == ("pipeline", 8, 60_000)


def test_reads_agent_settings(monkeypatch):
    monkeypatch.setenv("REVIEW_MODE", " Agent ")
    monkeypatch.setenv("AGENT_MAX_STEPS", "4")
    monkeypatch.setenv("AGENT_MAX_TOKENS", "20000")
    s = Settings.from_env()
    assert (s.review_mode, s.agent_max_steps, s.agent_max_tokens) == ("agent", 4, 20_000)


def test_rejects_unknown_mode(monkeypatch):
    monkeypatch.setenv("REVIEW_MODE", "agnet")
    with pytest.raises(ValueError, match="REVIEW_MODE"):
        Settings.from_env()
