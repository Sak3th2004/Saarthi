"""Atomic real-household setup, against the disposable database only."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import urlparse
from uuid import uuid4
from datetime import datetime, timedelta, timezone

import pytest
from neo4j import ManagedTransaction

from saarthi_mcp.config import load_settings
from saarthi_mcp.household import HouseholdDefinition
from saarthi_mcp.neo4j_repo import Neo4jRepository

pytestmark = pytest.mark.skipif(os.getenv("SAARTHI_RUN_NEO4J_TESTS") != "1", reason="isolated Neo4j opt-in")


def definition():
    key = uuid4().hex
    return HouseholdDefinition.model_validate({
        "primary_person_id": "parent_" + key,
        "people": [
            {"id": "parent_" + key, "name": "Parent " + key, "role": "elder", "aliases": ["parent"],
             "medications": [{"name": "Saved record " + key, "dose": "as entered", "schedule": ["09:00"]}]},
            {"id": "family_" + key, "name": "Family " + key, "role": "family"},
        ],
        "relationships": [{"from_person": "family_" + key, "to_person": "parent_" + key, "relation": "child"}],
    })


@pytest.fixture
def empty_graph():
    settings = load_settings()
    assert settings.backend == "neo4j" and settings.neo4j is not None
    target = urlparse(settings.neo4j.uri)
    assert target.hostname in {"127.0.0.1", "localhost"} and target.port == 17687
    assert settings.neo4j.database == "neo4j"
    repo = Neo4jRepository.from_settings(settings.neo4j)
    repo.wipe()
    try:
        yield repo
    finally:
        repo.wipe()
        repo.close()


def test_real_import_survives_reconnection_without_fabricated_history(empty_graph):
    data = definition()
    empty_graph.import_household(data)
    reader = Neo4jRepository.from_settings(load_settings().neo4j)
    try:
        assert reader.primary_elder().id == data.primary_person_id
        assert reader.resolve_person("parent").name == data.people[0].name
        assert [m.model_dump() for m in reader.medications_for(data.primary_person_id)] == [
            m.model_dump() for m in data.people[0].medications
        ]
        assert reader.dose_logs(data.primary_person_id) == []
        assert reader.upcoming_appointments(data.primary_person_id) == []
        assert reader.recent_events(data.primary_person_id)[0].type == "household_setup"
        assert reader._read("MATCH ()-[r:RELATED_TO]->() RETURN r.relation AS relation") == [{"relation": "child"}]
    finally:
        reader.close()


def test_reviewed_setup_is_atomic_persistent_and_cannot_replace_household(empty_graph):
    from saarthi_mcp.household_setup import HouseholdSetup, SetupError

    service = HouseholdSetup(empty_graph, persistent=True)
    data = definition()
    review = service.review(data.model_dump(mode="json"))
    assert not service.status({})["configured"]
    confirm = {"review_token": review["review_token"], "confirmed": True}
    result = service.confirm(confirm)
    assert result == service.confirm(confirm)
    reader = Neo4jRepository.from_settings(load_settings().neo4j)
    try:
        fresh = HouseholdSetup(reader, persistent=True)
        assert fresh.status({})["primary_person"]["id"] == data.primary_person_id
        assert fresh.status({})["persistence"] == "persistent"
        assert len(reader.recent_events(data.primary_person_id)) == 1
        assert len(reader._read("MATCH ()-[r:RELATED_TO]->() RETURN r")) == 1
        from saarthi_mcp.graph import memory_graph
        graph = memory_graph(reader, reader.primary_elder())
        family_edges = [e for e in graph.edges if e.relation == "RELATED_TO"]
        assert len(family_edges) == 1 and family_edges[0].detail == "child"
        members = {node.id: node.record["id"] for node in graph.nodes if node.kind == "person"}
        assert members[family_edges[0].source] == data.people[1].id
        assert members[family_edges[0].target] == data.primary_person_id
        with pytest.raises(SetupError, match="already saved"):
            fresh.review(definition().model_dump(mode="json"))
    finally:
        reader.close()


def test_existing_graph_is_never_overwritten_or_partially_imported(empty_graph):
    first = definition()
    empty_graph.import_household(first)
    before = empty_graph._read("MATCH (n) RETURN count(n) AS count")[0]["count"]
    for data in (first, definition()):
        with pytest.raises(ValueError, match="existing records"):
            empty_graph.import_household(data)
        assert empty_graph._read("MATCH (n) RETURN count(n) AS count")[0]["count"] == before
        assert empty_graph.primary_elder().id == first.primary_person_id


def test_even_unrecognized_existing_nodes_prevent_import(empty_graph):
    empty_graph._write("CREATE (:Event {detail:'preexisting data without an id'})")
    with pytest.raises(ValueError, match="empty graph"):
        empty_graph.import_household(definition())
    assert empty_graph._read("MATCH (n) RETURN count(n) AS count")[0]["count"] == 1
    assert empty_graph._read("MATCH (p:Person) RETURN p") == []


def test_failure_after_person_and_medication_write_rolls_back_everything(empty_graph, monkeypatch):
    original = ManagedTransaction.run

    def fail_during_history(self, query, *args, **kwargs):
        if "household_setup" in query:
            raise RuntimeError("Injected failure after person and medication creation")
        return original(self, query, *args, **kwargs)

    monkeypatch.setattr(ManagedTransaction, "run", fail_during_history)
    with pytest.raises(RuntimeError, match="Injected failure"):
        empty_graph.import_household(definition())
    assert empty_graph._read("MATCH (n) RETURN count(n) AS count")[0]["count"] == 0


def test_competing_imports_cannot_mix_two_households(empty_graph):
    barrier = Barrier(2)
    candidates = [definition(), definition()]

    def attempt(data):
        barrier.wait(timeout=10)
        try:
            empty_graph.import_household(data)
            return data.primary_person_id
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, candidates))
    winners = [result for result in results if result]
    assert len(winners) == 1
    assert empty_graph.primary_elder().id == winners[0]
    assert len(empty_graph._read("MATCH (p:Person) RETURN p")) == 2
    assert len(empty_graph._read("MATCH (m:Medication) RETURN m")) == 1


def test_legacy_ambiguous_people_require_unique_id(empty_graph):
    from saarthi_mcp.models import Person, Role
    from saarthi_mcp.repository import AmbiguousPersonError

    for key in ("first", "second"):
        empty_graph.upsert_person(Person(id=key, name="Shared name", role=Role.elder), aliases=[" Parent "])
    for label in ("Shared name", " parent "):
        with pytest.raises(AmbiguousPersonError, match="unique person ID"):
            empty_graph.resolve_person(label)
    assert empty_graph.resolve_person("first").id == "first"
    assert empty_graph.resolve_person("second").id == "second"


def test_search_finds_old_facts_without_other_person_or_execution_metadata(empty_graph):
    data = definition()
    empty_graph.import_household(data)
    at = datetime.now(timezone.utc)
    marker = "callback" + uuid4().hex
    original = empty_graph.add_event(data.primary_person_id, "call", marker + " saved fact", at - timedelta(days=400))
    empty_graph.add_event(data.people[1].id, "call", marker + " other-person private record", at)
    empty_graph.add_event(data.primary_person_id, "agent_action", marker + " internal metadata", at)
    empty_graph._write(
        "UNWIND $records AS props MATCH (p:Person {id:$id}) "
        "CREATE (p)-[:EXPERIENCED]->(e:Event) SET e = props",
        id=data.primary_person_id,
        records=[{"type": "note", "detail": f"Unrelated newer entry {i}", "at": at - timedelta(minutes=i)} for i in range(100)],
    )
    assert original not in empty_graph.recent_events(data.primary_person_id, limit=50)
    reader = Neo4jRepository.from_settings(load_settings().neo4j)
    try:
        answer, evidence = reader.query_memory(data.primary_person_id, marker)
        assert "saved fact" in answer
        assert "other-person" not in answer
        assert "internal metadata" not in answer
        assert evidence == [original]
    finally:
        reader.close()


def test_database_search_ranking_matches_memory_and_treats_query_as_data(empty_graph):
    from saarthi_mcp.repository import InMemoryRepository

    data = definition()
    empty_graph.import_household(data)
    memory = InMemoryRepository()
    memory.import_household(data)
    at = datetime.now(timezone.utc)
    records = [
        ("call", "José confirmed the physio callback", at - timedelta(days=30)),
        ("call", "Physio callback was rescheduled", at),
        ("visit", "Family visit, unrelated detail", at),
    ]
    for kind, detail, when in records:
        for backend in (empty_graph, memory):
            backend.add_event(data.primary_person_id, kind, detail, when)
    for question in ("physio callback", "José", "family visit", ""):
        assert empty_graph.search_events(data.primary_person_id, question) == memory.search_events(data.primary_person_id, question)
    before = empty_graph._read("MATCH (n) RETURN count(n) AS count")[0]["count"]
    assert empty_graph.search_events(data.primary_person_id, "x') MATCH (n) DETACH DELETE n //") == []
    assert empty_graph._read("MATCH (n) RETURN count(n) AS count")[0]["count"] == before
    with pytest.raises(ValueError, match="Search limit"):
        empty_graph.search_events(data.primary_person_id, "callback", limit=101)


async def test_graph_tool_reads_persisted_records_after_reconnection(empty_graph):
    from fastmcp import Client
    from saarthi_mcp.server import build_server

    data = definition()
    empty_graph.import_household(data)
    marker = "new-event-" + uuid4().hex
    async with Client(build_server(empty_graph)) as writer:
        first = await writer.call_tool("get_memory_graph", {"person": data.primary_person_id})
        await writer.call_tool("record_event", {
            "person": data.primary_person_id, "type": "call", "detail": marker,
        })
    reader = Neo4jRepository.from_settings(load_settings().neo4j)
    try:
        async with Client(build_server(reader)) as client:
            graph = await client.call_tool("get_memory_graph", {"person": data.primary_person_id})
        assert marker in str(graph.structured_content)
        assert len(graph.structured_content["nodes"]) == len(first.structured_content["nodes"]) + 1
        ids = {node["id"] for node in graph.structured_content["nodes"]}
        assert {node["id"] for node in first.structured_content["nodes"]} < ids
        assert all(edge["source"] in ids and edge["target"] in ids for edge in graph.structured_content["edges"])
        assert not any(graph.structured_content["truncated"].values())
    finally:
        reader.close()
