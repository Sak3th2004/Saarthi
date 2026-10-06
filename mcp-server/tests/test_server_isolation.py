"""Separate server instances must never redirect each other's reads, writes or agents."""

import asyncio

from fastmcp import Client

from saarthi_mcp.models import Person, Role
from saarthi_mcp.repository import InMemoryRepository
from saarthi_mcp.server import build_server


def household(name):
    repo = InMemoryRepository()
    # Deliberately overlapping IDs: isolation cannot rely on globally unique test IDs.
    repo.add_person(Person(id="parent", name=name, role=Role.elder))
    return repo


class RecordingOrchestrator:
    def __init__(self, label):
        self.label = label
        self.operations = []

    def execute(self, *, operation, arguments, action, record):
        self.operations.append(operation)
        result = action()
        record({"agent": self.label, "operation": operation, "status": "completed"})
        return result


async def test_two_active_servers_keep_reads_writes_and_agents_separate(monkeypatch):
    monkeypatch.setenv("SAARTHI_AGENTS", "off")
    first, second = household("First parent"), household("Second parent")
    first_agent, second_agent = RecordingOrchestrator("first"), RecordingOrchestrator("second")
    first_server = build_server(first, orchestrator=first_agent)
    async with Client(first_server) as one:
        # Construct a second server while the first client is already connected.
        async with Client(build_server(second, orchestrator=second_agent)) as two:
            results = await asyncio.gather(
                one.call_tool("record_event", {"person": "parent", "type": "note", "detail": "First private fact"}),
                two.call_tool("record_event", {"person": "parent", "type": "note", "detail": "Second private fact"}),
            )
            assert [result.data["person"]["name"] for result in results] == ["First parent", "Second parent"]
            answers = await asyncio.gather(
                one.call_tool("query_memory", {"person": "parent", "question": "private fact"}),
                two.call_tool("query_memory", {"person": "parent", "question": "private fact"}),
            )
            assert "First private fact" in answers[0].data["answer"]
            assert "Second private fact" not in str(answers[0].data)
            assert "Second private fact" in answers[1].data["answer"]
            assert "First private fact" not in str(answers[1].data)
        # Closing the second instance must not affect the first.
        result = await one.call_tool("get_medication_schedule", {"person": "parent"})
        assert result.data["person"]["name"] == "First parent"
    assert first_agent.operations == ["query_memory", "get_medication_schedule"]
    assert second_agent.operations == ["query_memory"]
    for repo, own, other in ((first, "first", "second"), (second, "second", "first")):
        audits = [e.detail for e in repo.recent_events("parent") if e.type == "agent_action"]
        assert audits and all(f'"agent": "{own}"' in e and f'"agent": "{other}"' not in e for e in audits)


async def test_non_agent_instance_does_not_disable_an_existing_agent(monkeypatch):
    monkeypatch.setenv("SAARTHI_AGENTS", "off")
    agent = RecordingOrchestrator("active")
    agent_server = build_server(household("Agent household"), orchestrator=agent)
    plain_server = build_server(household("Plain household"))
    async with Client(agent_server) as active, Client(plain_server) as plain:
        active_result = await active.call_tool("get_medication_schedule", {"person": "parent"})
        plain_result = await plain.call_tool("get_medication_schedule", {"person": "parent"})
    assert active_result.data["person"]["name"] == "Agent household"
    assert plain_result.data["person"]["name"] == "Plain household"
    assert agent.operations == ["get_medication_schedule"]
