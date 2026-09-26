"""Exercise the actual Strands loop with deterministic in-process model streams."""
import json
from copy import deepcopy
from unittest.mock import Mock
import pytest
from strands.models import Model
from saarthi_agents import AgentExecutionError, CareOrchestrator


class ScriptedModel(Model):
    def __init__(self, script):
        self.script = iter(script)
        self.requests = []

    def update_config(self, **kwargs):
        pass

    def get_config(self):
        return {"model_id": "offline", "context_window_limit": 4096}

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.requests.append(deepcopy({"messages": messages, "tools": tool_specs, "system": system_prompt}))
        response = next(self.script)
        if isinstance(response, Exception):
            raise response
        yield {"messageStart": {"role": "assistant"}}
        if isinstance(response, list):
            for index, (name, arguments) in enumerate(response):
                yield {"contentBlockStart": {"contentBlockIndex": index, "start": {
                    "toolUse": {"name": name, "toolUseId": f"call-{len(self.requests)}-{index}"}}}}
                yield {"contentBlockDelta": {"contentBlockIndex": index, "delta": {
                    "toolUse": {"input": json.dumps(arguments)}}}}
                yield {"contentBlockStop": {"contentBlockIndex": index}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": response}}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                            "metrics": {"latencyMs": 0}}}


def runtime(router=None, specialist=None):
    models = {
        "Orchestrator": ScriptedModel(router if router is not None else [[("delegate_request", {})], "Done"]),
        "specialist": ScriptedModel(specialist if specialist is not None else [
            [("execute_request", {})], "Invented medical advice must never escape"]),
    }
    roles = []
    def factory(role):
        roles.append(role)
        return models["Orchestrator" if role == "Orchestrator" else "specialist"]
    return CareOrchestrator("offline", "us-east-1", factory), models, roles


@pytest.mark.parametrize("operation, role", [
    ("get_medication_schedule", "MedGuardian"), ("log_dose", "MedGuardian"),
    ("query_memory", "MedGuardian"), ("book_appointment", "Appointments"),
    ("list_appointments", "Appointments"),
])
def test_real_strands_routes_to_specialist_and_records_exact_result(operation, role):
    care, models, roles = runtime()
    saved = {"text": "A saved fact", "data": {"person": "private-person"}}
    action, record = Mock(return_value=saved), Mock()
    assert care.execute(operation, {"person": "private-person"}, action, record) is saved
    assert roles == ["Orchestrator", role]
    action.assert_called_once_with()
    outcome = record.call_args.args[0]
    assert outcome["specialist"] == role
    assert outcome["status"] == "completed"
    assert outcome["reason"] == "validated_action_completed"
    assert outcome["stages"] == ["Orchestrator", role, "execute_request"]
    record.assert_called_once()
    serialized = json.dumps([model.requests for model in models.values()])
    assert "private-person" not in serialized
    assert "A saved fact" not in serialized


def test_model_cannot_change_person_or_medication_arguments():
    care, _, _ = runtime(specialist=[
        [("execute_request", {"person": "someone-else", "med": "different"})], "Done"])
    arguments = {"person": "bound-person", "med": "bound-med"}
    before = deepcopy(arguments)
    action = Mock(return_value={"saved": True})
    try:
        care.execute("log_dose", arguments, action, Mock())
    except AgentExecutionError as error:
        assert not error.action_attempted
    assert arguments == before
    # SDK may reject unexpected fields or discard them; they never reach action.
    if action.called:
        action.assert_called_once_with()


def test_parallel_duplicate_delegations_and_actions_execute_only_once():
    care, _, roles = runtime(
        router=[[("delegate_request", {}), ("delegate_request", {})], "Done"],
        specialist=[[("execute_request", {}), ("execute_request", {})], [("execute_request", {})], "Done"])
    action, record = Mock(return_value={"event_id": "one"}), Mock()
    assert care.execute("log_dose", {}, action, record) == {"event_id": "one"}
    action.assert_called_once()
    assert roles.count("MedGuardian") == 1
    record.assert_called_once()


@pytest.mark.parametrize("role", ["router", "specialist"])
def test_model_failure_before_action_records_failure(role):
    care, _, _ = runtime(**{role: [RuntimeError("secret-provider-message")]})
    action, record = Mock(), Mock()
    with pytest.raises(AgentExecutionError) as caught:
        care.execute("log_dose", {}, action, record)
    assert caught.value.reason == "model_failed"
    assert not caught.value.action_attempted
    assert "secret" not in str(caught.value)
    action.assert_not_called()
    assert record.call_args.args[0]["status"] == "failed"


@pytest.mark.parametrize("role, tool_name", [("router", "delegate_request"), ("specialist", "execute_request")])
def test_failure_after_completed_write_preserves_result(role, tool_name):
    care, _, _ = runtime(**{role: [[(tool_name, {})], RuntimeError("provider failed")]})
    saved = {"event_id": "already-committed"}
    action, record = Mock(return_value=saved), Mock()
    assert care.execute("log_dose", {}, action, record) is saved
    action.assert_called_once()
    assert record.call_args.args[0]["reason"] == "model_response_failed"
    assert record.call_args.args[0]["status"] == "completed"


def test_uncertain_action_failure_is_not_retried_or_leaked():
    care, models, _ = runtime(specialist=[[("execute_request", {}), ("execute_request", {})], "Done"])
    action = Mock(side_effect=RuntimeError("secret health record may have committed"))
    record = Mock()
    with pytest.raises(AgentExecutionError) as caught:
        care.execute("log_dose", {}, action, record)
    assert caught.value.action_attempted
    assert caught.value.reason == "action_failed_outcome_uncertain"
    action.assert_called_once()
    assert "secret health" not in json.dumps(models["specialist"].requests)
    assert record.call_args.args[0]["status"] == "failed"


def test_no_tool_execution_is_a_failure_not_a_fabricated_success():
    care, _, _ = runtime(router=["I logged the dose without using any tool"])
    action, record = Mock(), Mock()
    with pytest.raises(AgentExecutionError, match="action_not_executed"):
        care.execute("log_dose", {}, action, record)
    action.assert_not_called()
    record.assert_called_once()


def test_repeated_model_loop_has_a_hard_call_budget():
    care, models, _ = runtime(router=[[("unknown_tool", {})]] * 10)
    action, record = Mock(), Mock()
    with pytest.raises(AgentExecutionError):
        care.execute("log_dose", {}, action, record)
    assert len(models["Orchestrator"].requests) == 3
    action.assert_not_called()
    record.assert_called_once()


def test_audit_failure_preserves_completed_result_and_retry_information():
    care, _, _ = runtime()
    saved = {"event_id": "committed"}
    action = Mock(return_value=saved)
    record = Mock(side_effect=RuntimeError("private database error"))
    with pytest.raises(AgentExecutionError) as caught:
        care.execute("log_dose", {}, action, record)
    assert caught.value.reason == "audit_failed"
    assert caught.value.action_attempted
    assert caught.value.completed_result is saved
    action.assert_called_once()
    record.assert_called_once()


def test_unsupported_operation_has_no_model_or_action_authority():
    factory, action, record = Mock(), Mock(), Mock()
    care = CareOrchestrator("offline", "us-east-1", factory)
    with pytest.raises(AgentExecutionError, match="unsupported_operation"):
        care.execute("purchase-private-item", {}, action, record)
    action.assert_not_called()
    factory.assert_not_called()
    assert record.call_args.args[0]["operation"] == "unsupported"


def test_bedrock_configuration_has_timeouts_tokens_and_bounded_retries(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr("saarthi_agents.orchestrator.BedrockModel", constructor)
    CareOrchestrator("configured-claude", "us-east-1")._model("Orchestrator")
    config = constructor.call_args.kwargs
    assert config["model_id"] == "configured-claude"
    assert config["region_name"] == "us-east-1"
    assert config["max_tokens"] == 512
    assert config["boto_client_config"].connect_timeout == 5
    assert config["boto_client_config"].read_timeout == 30
    assert config["boto_client_config"].retries["total_max_attempts"] == 2


def test_usage_counts_both_agents_once_and_resets_between_requests():
    care, _, _ = runtime()
    record = Mock()
    care.execute("list_appointments", {}, Mock(return_value={"appointments": []}), record)
    outcome = record.call_args.args[0]
    assert outcome["model_id"] == "offline"
    assert outcome["elapsed_ms"] >= 0
    assert [u["role"] for u in outcome["model_usage"]] == ["Orchestrator", "Appointments"]
    for item in outcome["model_usage"]:
        assert item["model_calls_attempted"] == 2
        assert item["invocation_completed"] is True
        assert item["reported_tokens"] == {"inputTokens": 2, "outputTokens": 2, "totalTokens": 4}
    # A fresh request must not carry tokens from a previous household/request.
    another, _, _ = runtime()
    another.execute("list_appointments", {}, Mock(return_value={}), record)
    assert record.call_args.args[0]["model_usage"] == outcome["model_usage"]


def test_provider_failure_retains_partial_usage_without_repeating_action():
    care, _, _ = runtime(specialist=[[("execute_request", {})], RuntimeError("provider disconnected")])
    action, record = Mock(return_value={"saved": True}), Mock()
    assert care.execute("book_appointment", {}, action, record) == {"saved": True}
    action.assert_called_once()
    specialist_usage = record.call_args.args[0]["model_usage"][1]
    assert specialist_usage["invocation_completed"] is False
    assert specialist_usage["model_calls_attempted"] == 2
    assert specialist_usage["reported_tokens"] == {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}


def test_unreported_usage_is_unknown_not_zero_cost():
    care, _, _ = runtime(router=[RuntimeError("no metadata")])
    record = Mock()
    with pytest.raises(AgentExecutionError):
        care.execute("list_appointments", {}, Mock(), record)
    usage = record.call_args.args[0]["model_usage"]
    assert len(usage) == 1
    assert usage[0]["model_calls_attempted"] == 1
    assert usage[0]["invocation_completed"] is False
    assert all(value is None for value in usage[0]["reported_tokens"].values())


def test_broken_metrics_do_not_change_saved_result():
    from saarthi_agents.orchestrator import _CallBudget, _usage_snapshot

    class BrokenMetrics:
        @property
        def event_loop_metrics(self):
            raise RuntimeError("metrics unavailable")

    snapshot = _usage_snapshot(BrokenMetrics(), _CallBudget(), "Orchestrator", True)
    assert snapshot["reported_tokens"]["totalTokens"] is None
