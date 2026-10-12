"""Bedrock entitlement probe helpers stay read-only unless explicitly invoked."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_bedrock", _ROOT / "scripts/check_bedrock.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_inspection_never_invokes_models():
    client = Mock()
    client.get_foundation_model_availability.return_value = {"authorizationStatus": "AUTHORIZED"}
    assert module.inspect_models(client, ["probe-model"])[0]["authorizationStatus"] == "AUTHORIZED"
    client.converse.assert_not_called()


def test_inspection_errors_are_codes_only():
    client = Mock()
    client.get_foundation_model_availability.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "private"}}, "GetFoundationModelAvailability",
    )
    assert module.inspect_models(client, ["m"]) == [{"model_id": "m", "error_code": "AccessDeniedException"}]


def test_smoke_invoke_is_token_bounded():
    client = Mock()
    client.converse.return_value = {
        "output": {"message": {"content": [{"text": "connected"}]}},
        "usage": {"totalTokens": 12},
        "stopReason": "end_turn",
    }
    result = module.smoke_invoke(client, "profile-id", 16)
    assert result["status"] == "responded"
    assert client.converse.call_args.kwargs["inferenceConfig"] == {"maxTokens": 16}
    with pytest.raises(ValueError):
        module.smoke_invoke(client, "profile-id", 512)
