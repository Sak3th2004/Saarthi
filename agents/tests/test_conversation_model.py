from unittest.mock import Mock

import pytest
from saarthi_agents.conversation import ConversationModel
from strands.types.exceptions import EventLoopException
from test_orchestrator import ScriptedModel


def test_actual_strands_loop_exposes_only_reads_and_finish():
    model = ScriptedModel([
        [("read_notes", {"query": "folder"})],
        [("finish_answer", {"evidence_ids": ["verified-id"], "intent": "answer"})],
        "Untrusted model prose must not be used as an answer.",
    ])
    read = Mock(return_value=[{"id": "verified-id", "text": "Saved record"}])
    finish = Mock()
    result = ConversationModel("offline", "us-east-1", lambda: model).run(
        {"question": "Where is the folder?"}, read, finish,
    )
    assert result is None
    read.assert_called_once_with("notes", "folder")
    finish.assert_called_once_with(["verified-id"], "answer", "", "", "")
    names = {tool["name"] for tool in model.requests[0]["tools"]}
    assert names == {"read_notes", "read_appointments", "read_medications", "read_relationships", "finish_answer"}


def test_actual_strands_loop_stops_at_model_call_budget():
    model = ScriptedModel([[("read_notes", {})]] * 7)
    with pytest.raises(EventLoopException, match="budget"):
        ConversationModel("offline", "us-east-1", lambda: model).run({}, Mock(return_value=[]), Mock())
    assert len(model.requests) == 5
