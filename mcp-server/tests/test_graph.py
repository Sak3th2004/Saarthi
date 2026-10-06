"""Graph views must follow arbitrary saved data, never demo fixtures or inferred status."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from saarthi_mcp.graph import memory_graph
from saarthi_mcp.household import HouseholdDefinition
from saarthi_mcp.models import Person, Role
from saarthi_mcp.repository import InMemoryRepository
from saarthi_mcp.server import build_server


def imported_household():
    marker = uuid4().hex
    data = HouseholdDefinition.model_validate({
        "primary_person_id": marker,
        "people": [{"id": marker, "name": "Parent " + marker, "role": "elder",
                    "email": "private@example.com", "phone": "+10000000000",
                    "medications": [{"name": "Saved " + marker, "dose": "as entered", "schedule": ["09:00"]}]}],
    })
    repo = InMemoryRepository()
    repo.import_household(data)
    return repo, repo.primary_elder()


def test_view_contains_only_saved_records_and_no_contact_fields():
    repo, person = imported_household()
    before = repo.recent_events(person.id)
    graph = memory_graph(repo, person)
    assert {n.kind for n in graph.nodes} == {"person", "medication", "event"}
    assert len(graph.nodes) == 3  # person, supplied medication and actual setup audit
    assert graph.nodes[0].record == {"id": person.id, "name": person.name, "role": "elder"}
    assert "private@example.com" not in graph.model_dump_json()
    assert "+10000000000" not in graph.model_dump_json()
    assert "adherence" not in graph.model_dump_json()
    assert repo.recent_events(person.id) == before  # a read adds no invented activity
    ids = {node.id for node in graph.nodes}
    assert all(edge.source in ids and edge.target in ids for edge in graph.edges)


def test_refresh_keeps_old_node_ids_and_includes_new_record_only_for_its_owner():
    repo, person = imported_household()
    before = memory_graph(repo, person)
    repo.add_person(Person(id="other", name="Other", role=Role.elder))
    now = datetime.now(timezone.utc)
    repo.add_event("other", "visit", "Other person's private visit", now)
    repo.add_event(person.id, "visit", "User entered a new visit", now)
    repo.add_appointment(person.id, "Saved appointment", now + timedelta(days=1))
    after = memory_graph(repo, person)
    assert {n.id for n in before.nodes} < {n.id for n in after.nodes}
    assert "User entered a new visit" in after.model_dump_json()
    assert "Other person's private" not in after.model_dump_json()
    assert "HAS_APPOINTMENT" in {e.relation for e in after.edges}
    assert memory_graph(repo, repo.resolve_person("other")).nodes[0].id != after.nodes[0].id


def test_limits_are_explicit_and_identical_events_have_one_visual_identity():
    repo, person = imported_household()
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    for _ in range(3):
        repo.add_event(person.id, "visit", "Same saved fact", now)
    graph = memory_graph(repo, person, limit=2)
    assert graph.truncated == {"medication": False, "appointment": False, "event": True}
    assert len([n for n in graph.nodes if n.kind == "event"]) == 1
    assert "more entries" in graph.speech


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5])
def test_invalid_graph_limits_are_rejected(limit):
    repo, person = imported_household()
    with pytest.raises(ValueError, match="Graph limit"):
        memory_graph(repo, person, limit=limit)


async def test_mcp_graph_reflects_live_write_without_model_calls(monkeypatch):
    monkeypatch.setenv("SAARTHI_AGENTS", "off")
    repo, person = imported_household()

    class MustNotRun:
        def execute(self, **kwargs):
            raise AssertionError("Graph reads must not invoke a paid model")

    async with Client(build_server(repo, orchestrator=MustNotRun())) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools["get_memory_graph"].annotations.read_only_hint is True
        before = await client.call_tool("get_memory_graph", {"person": person.id})
        marker = "callback" + uuid4().hex
        await client.call_tool("record_event", {"person": person.id, "type": "call", "detail": marker})
        after = await client.call_tool("get_memory_graph", {"person": person.id})
        assert len(after.structured_content["nodes"]) == len(before.structured_content["nodes"]) + 1
        assert marker in str(after.structured_content)
        assert after.content[0].text == after.structured_content["speech"]
        with pytest.raises(ToolError):
            await client.call_tool("get_memory_graph", {"person": person.id, "limit": 101})
        with pytest.raises(ToolError, match="Unknown person"):
            await client.call_tool("get_memory_graph", {"person": "not-saved"})


async def test_empty_server_does_not_generate_graph_nodes(monkeypatch):
    monkeypatch.setenv("SAARTHI_AGENTS", "off")
    async with Client(build_server(InMemoryRepository())) as client:
        with pytest.raises(ToolError, match="Unknown person"):
            await client.call_tool("get_memory_graph", {"person": "parent"})
