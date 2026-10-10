"""Household edits preserve care records and require a reviewed metadata revision."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

import pytest

from saarthi_mcp.directory import DirectoryConflict, directory_revision, preview_change, validate_change
from saarthi_mcp.models import Person, Role
from saarthi_mcp.repository import InMemoryRepository, seeded_repository


def apply(repo, change, **kwargs):
    return repo.apply_household_change(change, directory_revision(repo.household_directory()), **kwargs)


def test_empty_household_can_be_created_incrementally_without_care_facts():
    repo = InMemoryRepository()
    assert repo.household_directory() == {"primary_person_id": None, "people": [], "relationships": []}
    apply(repo, {"kind": "add_person", "id": "child", "name": " Child ", "role": "family"})
    assert repo.household_directory()["primary_person_id"] is None
    result = apply(repo, {"kind": "add_person", "id": "parent", "name": "Parent", "role": "elder"}, actor="subject")
    assert result["primary_person_id"] == "parent"
    assert [p["id"] for p in result["people"]] == ["child", "parent"]
    assert repo.medications_for("parent") == repo.dose_logs("parent") == repo.upcoming_appointments("parent") == []
    audit = json.loads(repo.recent_events("parent")[0].detail)
    assert audit["before"] is None and audit["after"]["name"] == "Parent"
    assert repo._directory_audits[-1]["actor"] == "subject"
    assert "subject" not in str(result)


def test_edit_keeps_sample_records_aliases_and_existing_care_history():
    repo = seeded_repository()
    before = repo.medications_for("elder-1"), repo.dose_logs("elder-1"), repo.upcoming_appointments("elder-1")
    apply(repo, {"kind": "rename_person", "person_id": "elder-1", "name": "New parent name"})
    assert repo.resolve_person("dad").name == "New parent name"
    assert before == (repo.medications_for("elder-1"), repo.dose_logs("elder-1"), repo.upcoming_appointments("elder-1"))
    apply(repo, {"kind": "set_relationship", "from_person": "fam-1", "to_person": "elder-1", "relation": "older child"})
    apply(repo, {"kind": "set_relationship", "from_person": "fam-1", "to_person": "elder-1", "relation": "son"})
    assert len(repo.household_directory()["relationships"]) == 1
    assert repo.family_contacts()[0].relation == "son"
    assert repo.resolve_person("fam-1").email == "family@example.com"
    snapshot = repo.household_directory()
    snapshot["people"][0]["name"] = "tamper"
    assert "tamper" not in str(repo.household_directory())


@pytest.mark.parametrize("change", [
    {"kind": "add_person", "id": "third", "name": "DAD", "role": "elder"},
    {"kind": "add_person", "id": "arjun", "name": "Third", "role": "elder"},
    {"kind": "rename_person", "person_id": "fam-1", "name": "elder-1"},
    {"kind": "rename_person", "person_id": "fam-1", "name": "Father"},
    {"kind": "set_primary", "person_id": "fam-1"},
    {"kind": "set_primary", "person_id": "elder-1"},
    {"kind": "rename_person", "person_id": "missing", "name": "Third"},
    {"kind": "set_relationship", "from_person": "elder-1", "to_person": "elder-1", "relation": "child"},
    {"kind": "set_relationship", "from_person": "missing", "to_person": "elder-1", "relation": "child"},
])
def test_invalid_changes_leave_directory_and_audit_untouched(change):
    repo = seeded_repository()
    before = repo.household_directory()
    events = repo.recent_events("elder-1", 100)
    with pytest.raises(ValueError):
        apply(repo, change)
    assert repo.household_directory() == before
    assert repo.recent_events("elder-1", 100) == events


@pytest.mark.parametrize("change", [None, [], {}, {"kind": []},
    {"kind": "add_person", "id": "x", "name": "name", "role": "elder", "medications": []},
    {"kind": "rename_person", "person_id": "x", "name": "bad\nname"},
    {"kind": "rename_person", "person_id": "x", "name": True},
    {"kind": "add_person", "id": "x y", "name": "Name", "role": "elder"},
    {"kind": "add_person", "id": "x", "name": "Name", "role": "doctor"},
])
def test_change_shape_is_strict(change):
    with pytest.raises(ValueError):
        validate_change(change)


def test_revision_is_stable_under_order_changes_but_protects_concurrent_edits():
    repo = seeded_repository()
    snapshot = repo.household_directory()
    reversed_snapshot = {**snapshot, "people": list(reversed(snapshot["people"]))}
    revision = directory_revision(snapshot)
    assert revision == directory_revision(reversed_snapshot)
    barrier = Barrier(2)

    def attempt(name):
        barrier.wait(timeout=5)
        try:
            return repo.apply_household_change({"kind": "rename_person", "person_id": "elder-1", "name": name}, revision)
        except DirectoryConflict:
            return None

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(attempt, ["First name", "Second name"]))
    assert sum(result is not None for result in results) == 1
    assert len(repo._directory_audits) == 1


def test_new_default_must_be_elder_and_preview_has_no_side_effect():
    repo = seeded_repository()
    apply(repo, {"kind": "add_person", "id": "second", "name": "Another parent", "role": "elder"})
    before = repo.household_directory()
    change = {"kind": "set_primary", "person_id": "second"}
    assert preview_change(before, change)["primary_person_id"] == "second"
    assert repo.household_directory() == before
    apply(repo, change)
    assert repo.primary_elder().id == "second"
    assert repo.resolve_person("elder-1").name == "Ramesh"


@pytest.mark.parametrize("new_relation", ["nephew", None])
def test_default_change_recomputes_relationship_without_changing_contacts_or_care(new_relation):
    repo = seeded_repository()
    apply(repo, {"kind": "set_relationship", "from_person": "fam-1", "to_person": "elder-1", "relation": "son"})
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


def test_people_limit_fails_without_partial_write():
    repo = InMemoryRepository()
    for i in range(100):
        repo.add_person(Person(id=f"p{i}", name=f"Person {i}", role=Role.family))
    with pytest.raises(ValueError, match="size"):
        apply(repo, {"kind": "add_person", "id": "extra", "name": "Extra", "role": "elder"})
    assert len(repo.household_directory()["people"]) == 100
    assert repo._directory_audits == []


def test_relationship_limit_does_not_block_editing_existing_link():
    directory = {"primary_person_id": "p0", "people": [
        {"id": f"p{i}", "name": f"Person {i}", "role": "elder" if i == 0 else "family"} for i in range(25)
    ], "relationships": [
        {"from_person": f"p{i}", "to_person": f"p{j}", "relation": "relative"}
        for i in range(25) for j in range(25) if i != j
    ][:500]}
    with pytest.raises(ValueError, match="size"):
        preview_change(directory, {"kind": "set_relationship", "from_person": "p24", "to_person": "p23", "relation": "sibling"})
    first = directory["relationships"][0]
    after = preview_change(directory, {"kind": "set_relationship", **first, "relation": "sibling"})
    assert len(after["relationships"]) == 500
