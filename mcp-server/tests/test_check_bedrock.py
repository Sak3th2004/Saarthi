import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

spec = importlib.util.spec_from_file_location("check_bedrock", Path(__file__).resolve().parents[2] / "scripts/check_bedrock.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_inspection_never_invokes_or_accepts_agreements():
    client = Mock()
    client.get_foundation_model_availability.return_value = {"authorizationStatus": "AUTHORIZED", "ResponseMetadata": {"private": "not-output"}}
    assert module.inspect_models(client, ["entered-model"])[0]["authorizationStatus"] == "AUTHORIZED"
    assert [call[0] for call in client.mock_calls] == ["get_foundation_model_availability"]


def test_inspection_errors_do_not_echo_sensitive_response():
    client = Mock()
    client.get_foundation_model_availability.side_effect = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "private response"}}, "GetFoundationModelAvailability")
    assert module.inspect_models(client, ["m"]) == [{"model_id": "m", "error_code": "AccessDeniedException"}]


def test_smoke_is_bounded_and_does_not_print_model_output():
    client = Mock()
    client.converse.return_value = {"output": {"message": {"content": [{"text": "private generated text"}]}}, "usage": {"totalTokens": 10}, "stopReason": "end_turn"}
    result = module.smoke_invoke(client, "selected-profile", 256)
    assert result["status"] == "responded"
    assert "private" not in str(result)
    assert client.converse.call_args.kwargs["inferenceConfig"] == {"maxTokens": 256}
    with pytest.raises(ValueError):
        module.smoke_invoke(client, "selected-profile", 100000)
    assert client.converse.call_count == 1
