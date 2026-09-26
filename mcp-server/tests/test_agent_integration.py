"""MCP contracts and persisted action feed at the orchestration boundary."""

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from saarthi_mcp.repository import seeded_repository
from saarthi_mcp.server import build_server


class RecordingOrchestrator:
    def __init__(self):
        self.calls = []

    def execute(self, operation, arguments, action, record):
        self.calls.append((operation, arguments))
        result = action()
        record({"operation": operation, "specialist": "MedGuardian", "status": "completed"})
        return result


async def test_mcp_log_and_recall_delegate_and_write_action_feed():
    repo = seeded_repository()
    orchestrator = RecordingOrchestrator()
    async with Client(build_server(repo, orchestrator=orchestrator)) as client:
        logged = await client.call_tool("log_dose", {
            "person": "dad", "med": "Metformin", "taken": True,
        })
        assert logged.data["dose"]["status"] == "taken"
        assert logged.content[0].text == logged.data["speech"]
        recalled = await client.call_tool("query_memory", {
            "person": "dad", "question": "did dad take Metformin?",
        })
        assert "took Metformin" in recalled.data["answer"]
    assert [operation for operation, _ in orchestrator.calls] == ["log_dose", "query_memory"]
    assert orchestrator.calls[0][1]["taken"] is True
    events = repo.recent_events("elder-1", limit=10)
    assert len([e for e in events if e.type == "agent_action"]) == 2
    assert any(e.type == "dose" for e in events)


async def test_unknown_person_rejected_before_agent_is_invoked():
    orchestrator = RecordingOrchestrator()
    async with Client(build_server(seeded_repository(), orchestrator=orchestrator)) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_medication_schedule", {"person": "unknown"})
    assert orchestrator.calls == []


async def test_agent_failure_is_not_reported_as_a_successful_dose():
    class FailedOrchestrator:
        def execute(self, **kwargs):
            raise RuntimeError("Private provider error must not be spoken")

    repo = seeded_repository()
    before = len(repo.dose_logs("elder-1"))
    async with Client(build_server(repo, orchestrator=FailedOrchestrator())) as client:
        with pytest.raises(ToolError, match="Check the recorded action status") as error:
            await client.call_tool("log_dose", {"person": "dad", "med": "Metformin", "taken": True})
        assert "Private provider error" not in str(error.value)
    assert len(repo.dose_logs("elder-1")) == before


async def test_completed_write_survives_failure_to_record_agent_history():
    class AuditFailure(RuntimeError):
        def __init__(self, completed_result):
            self.completed_result = completed_result

    class AuditFailedOrchestrator:
        def execute(self, action, **kwargs):
            raise AuditFailure(action())

    repo = seeded_repository()
    before = len(repo.dose_logs("elder-1"))
    async with Client(build_server(repo, orchestrator=AuditFailedOrchestrator())) as client:
        result = await client.call_tool("log_dose", {
            "person": "dad", "med": "Metformin", "taken": True,
        })
        assert result.data["dose"]["status"] == "taken"
        assert "record was saved" in result.data["speech"]
        assert result.data["warnings"]
    assert len(repo.dose_logs("elder-1")) == before + 1
