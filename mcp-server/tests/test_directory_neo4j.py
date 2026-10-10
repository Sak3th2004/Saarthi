"""Atomic directory writes against an explicitly selected disposable Docker database."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from threading import Barrier
from urllib.parse import urlparse

from neo4j import ManagedTransaction
import pytest

from saarthi_mcp.config import load_settings
from saarthi_mcp.directory import DirectoryConflict, directory_revision
from saarthi_mcp.models import Person, Role
from saarthi_mcp.neo4j_repo import Neo4jRepository, seed_neo4j

pytestmark = pytest.mark.skipif(os.getenv("SAARTHI_RUN_NEO4J_TESTS") != "1", reason="isolated Neo4j opt-in")


@pytest.fixture
def repo():
    settings = load_settings()
    assert settings.backend == "neo4j" and settings.neo4j is not None
    target = urlparse(settings.neo4j.uri)
    assert target.hostname in {"127.0.0.1", "localhost"} and target.port == 17687
    assert settings.neo4j.database == "neo4j"
    repository = Neo4jRepository.from_settings(settings.neo4j)
    repository.wipe()
    try:
        yield repository
    finally:
        repository.wipe()
        repository.close()


def apply(repo, change, **kwargs):
    return repo.apply_household_change(change, directory_revision(repo.household_directory()), **kwargs)


def test_directory_survives_reconnection_and_writes_actual_audit(repo):
    apply(repo, {"kind": "add_person", "id": "parent", "name": "Parent", "role": "elder"}, actor="caregiver-sub")
    apply(repo, {"kind": "add_person", "id": "child", "name": "Child", "role": "family"})
    apply(repo, {"kind": "set_relationship", "from_person": "child", "to_person": "parent", "relation": "child"})
    apply(repo, {"kind": "rename_person", "person_id": "parent", "name": "Renamed parent"})
    reader = Neo4jRepository.from_settings(load_settings().neo4j)
    try:
        result = reader.household_directory()
        assert result["primary_person_id"] == "parent"
        assert result["relationships"] == [{"from_person": "child", "to_person": "parent", "relation": "child"}]
        assert reader.resolve_person("parent").name == "Renamed parent"
        audits = reader._read("MATCH (:Person {id:'parent'})-[:EXPERIENCED]->(e:Event) RETURN e ORDER BY e.at")
        assert len(audits) == 3
        assert audits[0]["e"]["actor"] == "caregiver-sub"
        detail = json.loads(audits[-1]["e"]["detail"])
        assert detail["before"]["name"] == "Parent" and detail["after"]["name"] == "Renamed parent"
        assert reader.medications_for("parent") == reader.dose_logs("parent") == reader.upcoming_appointments("parent") == []
    finally:
        reader.close()


def test_alias_collisions_rejected_and_legacy_metadata_care_records_preserved(repo):
    repo.upsert_person(Person(id="parent", name="Parent", role=Role.elder), aliases=["dad"])
    repo.upsert_person(Person(id="child", name="Child", role=Role.family, email="saved@example.com"))
    for change in [
        {"kind": "add_person", "id": "dad", "name": "Third", "role": "elder"},
        {"kind": "rename_person", "person_id": "child", "name": "DAD"},
    ]:
        with pytest.raises(ValueError, match="another"):
            apply(repo, change)
    apply(repo, {"kind": "rename_person", "person_id": "child", "name": "New child name"})
    assert repo.resolve_person("child").email == "saved@example.com"
    assert repo.resolve_person("dad").id == "parent"
    apply(repo, {"kind": "add_person", "id": "a-new-parent", "name": "Another parent", "role": "elder"})
    assert repo.household_directory()["primary_person_id"] == repo.primary_elder().id == "parent"
    apply(repo, {"kind": "set_primary", "person_id": "a-new-parent"})
    assert repo.primary_elder().id == "a-new-parent"


@pytest.mark.parametrize("new_relation", ["nephew", None])
def test_default_change_recomputes_relationship_without_changing_contacts_or_care(repo, new_relation):
    seed_neo4j(repo)
    apply(repo, {"kind": "add_person", "id": "other", "name": "Other elder", "role": "elder"})
    if new_relation:
        apply(repo, {"kind": "set_relationship", "from_person": "fam-1", "to_person": "other", "relation": new_relation})
    contact = repo.resolve_person("fam-1").model_dump(exclude={"relation"})
    care = repo.medications_for("elder-1"), repo.dose_logs("elder-1"), repo.upcoming_appointments("elder-1")
    links = repo.household_directory()["relationships"]

    apply(repo, {"kind": "set_primary", "person_id": "other"})
    assert repo.resolve_person("fam-1").relation == repo.family_contacts()[0].relation == new_relation
    assert repo.resolve_person("fam-1").model_dump(exclude={"relation"}) == contact
    assert (repo.medications_for("elder-1"), repo.dose_logs("elder-1"), repo.upcoming_appointments("elder-1")) == care
    assert repo.household_directory()["relationships"] == links

    apply(repo, {"kind": "set_primary", "person_id": "elder-1"})
    assert repo.resolve_person("fam-1").relation == "son"


def test_concurrent_updates_have_one_winner_and_one_audit(repo):
    apply(repo, {"kind": "add_person", "id": "parent", "name": "Parent", "role": "elder"})
    revision = directory_revision(repo.household_directory())
    barrier = Barrier(2)

    def attempt(name):
        barrier.wait(timeout=10)
        try:
            return repo.apply_household_change({"kind": "rename_person", "person_id": "parent", "name": name}, revision)
        except DirectoryConflict:
            return None

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(attempt, ["First", "Second"]))
    assert sum(result is not None for result in results) == 1
    assert len(repo.recent_events("parent")) == 2


def test_failure_during_audit_rolls_back_person_and_lock_creation(repo, monkeypatch):
    original = ManagedTransaction.run

    def fail_audit(self, query, *args, **kwargs):
        if "household_setup" in query:
            raise RuntimeError("Injected audit failure")
        return original(self, query, *args, **kwargs)

    monkeypatch.setattr(ManagedTransaction, "run", fail_audit)
    with pytest.raises(RuntimeError, match="Injected"):
        apply(repo, {"kind": "add_person", "id": "parent", "name": "Parent", "role": "elder"})
    assert repo._read("MATCH (n) RETURN count(n) AS count")[0]["count"] == 0


def test_repeated_relationship_update_changes_one_edge_and_rejects_noop(repo):
    for name, role in [("parent", "elder"), ("child", "family")]:
        apply(repo, {"kind": "add_person", "id": name, "name": name.title(), "role": role})
    change = {"kind": "set_relationship", "from_person": "child", "to_person": "parent", "relation": "child"}
    apply(repo, change)
    apply(repo, {**change, "relation": "older child"})
    before = repo.household_directory()
    with pytest.raises(ValueError, match="already saved"):
        apply(repo, {**change, "relation": "older child"})
    assert repo.household_directory() == before
    assert repo.family_contacts()[0].relation == "older child"
    assert len(repo._read("MATCH ()-[r:RELATED_TO]->() RETURN r")) == 1


def test_competing_first_person_writes_cannot_both_apply_empty_review(repo):
    revision = directory_revision(repo.household_directory())
    barrier = Barrier(2)

    def attempt(key):
        barrier.wait(timeout=10)
        try:
            return repo.apply_household_change({"kind": "add_person", "id": key, "name": key, "role": "elder"}, revision)
        except DirectoryConflict:
            return None

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(attempt, ["first", "second"]))
    assert sum(result is not None for result in results) == 1
    assert len(repo.household_directory()["people"]) == 1
