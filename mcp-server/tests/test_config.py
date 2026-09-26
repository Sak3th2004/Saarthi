"""Model choice stays explicit and live inference remains opt-in."""

from saarthi_mcp.config import DEFAULT_BEDROCK_MODEL_ID, load_settings
import pytest


def test_default_does_not_select_a_model_or_enable_paid_inference(monkeypatch):
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("SAARTHI_AGENTS", raising=False)
    monkeypatch.setenv("SAARTHI_BACKEND", "memory")
    settings = load_settings()
    assert settings.bedrock_model_id == DEFAULT_BEDROCK_MODEL_ID == ""
    assert settings.agent_mode == "off"


def test_explicit_model_override_is_preserved(monkeypatch):
    monkeypatch.setenv("SAARTHI_BACKEND", "memory")
    monkeypatch.setenv("SAARTHI_AGENTS", "bedrock")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "deepseek.v3.2")
    settings = load_settings()
    assert settings.bedrock_model_id == "deepseek.v3.2"
    assert settings.agent_mode == "bedrock"


@pytest.mark.parametrize("model_id", [None, "", "   "])
def test_live_inference_requires_explicit_model(monkeypatch, model_id):
    monkeypatch.setenv("SAARTHI_AGENTS", "bedrock")
    if model_id is None:
        monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    else:
        monkeypatch.setenv("BEDROCK_MODEL_ID", model_id)
    with pytest.raises(ValueError, match="Set BEDROCK_MODEL_ID"):
        load_settings()
