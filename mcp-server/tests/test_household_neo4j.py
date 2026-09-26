"""Atomic real-household setup, against the disposable database only."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import urlparse
from uuid import uuid4

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
