"""Calendar persistence: exact retries, ownership, rollback, and durable graph reads.

Neo4j cases require SAARTHI_RUN_NEO4J_TESTS=1 and only use the dedicated local
test container on port 17687. They never load an environment connection URI.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest

from saarthi_mcp.models import Person, Role
from saarthi_mcp.repository import InMemoryRepository, PersonNotFoundError


@pytest.fixture(params=[
    "memory",
    pytest.param("neo4j", marks=pytest.mark.skipif(
        os.getenv("SAARTHI_RUN_NEO4J_TESTS") != "1",
        reason="Opt in to the dedicated local Neo4j test container.",
    )),
])
def household(request):
    people = [Person(id="calendar-test-" + uuid4().hex, name="Saved member " + str(i), role=Role.elder)
              for i in range(2)]
    if request.param == "memory":
        repo = InMemoryRepository()
        for person in people:
            repo.add_person(person)
        yield repo, people
        return
    from neo4j import GraphDatabase
    from saarthi_mcp.neo4j_repo import Neo4jRepository

    driver = GraphDatabase.driver("bolt://127.0.0.1:17687", auth=("neo4j", "saarthi-test-only"))
    repo = Neo4jRepository(driver, "neo4j")
    try:
        for person in people:
            repo.upsert_person(person)
        yield repo, people
    finally:
        # Only fixture-owned records, never a whole-graph wipe or Aura connection.
        repo._write(
            "MATCH (p:Person) WHERE p.id IN $ids "
            "OPTIONAL MATCH (p)-[:HAS_APPOINTMENT|EXPERIENCED]->(n) "
            "DETACH DELETE n, p",
            ids=[person.id for person in people],
        )
        repo.close()


@pytest.fixture
def entry():
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=4)
    return dict(event_id=uuid4().hex, kind="User entered meeting", start=start,
                end=start + timedelta(minutes=45), timezone_name="UTC")


def test_retry_saves_one_appointment_and_one_audit(household, entry):
    repo, people = household
    owner = people[0].id
    first = repo.save_calendar_appointment(owner, **entry)
    assert first.id == "google-" + entry["event_id"]
    assert first.end == entry["end"]
    assert first.time_zone == "UTC"
    assert first.calendar_event_id == entry["event_id"]
    assert repo.save_calendar_appointment(owner, **entry) == first
    assert repo.upcoming_appointments(owner) == [first]
    events = repo.recent_events(owner)
    assert len(events) == 1 and events[0].type == "calendar_appointment_saved"
    assert entry["kind"] in events[0].detail
    assert "not provider confirmation" in events[0].detail


def test_replay_cannot_reassign_another_person(household, entry):
    repo, people = household
    first = repo.save_calendar_appointment(people[0].id, **entry)
    with pytest.raises(ValueError, match="conflicts"):
        repo.save_calendar_appointment(people[1].id, **entry)
    assert repo.upcoming_appointments(people[0].id) == [first]
    assert not repo.upcoming_appointments(people[1].id)
    assert not repo.recent_events(people[1].id)


@pytest.mark.parametrize("changed", ["kind", "start", "end", "timezone_name"])
def test_replay_cannot_overwrite_changed_attributes(household, entry, changed):
    repo, people = household
    first = repo.save_calendar_appointment(people[0].id, **entry)
    updated = dict(entry)
    if changed in {"start", "end"}:
        updated[changed] += timedelta(minutes=5)
    else:
        updated[changed] = "Etc/UTC" if changed == "timezone_name" else "Changed title"
    with pytest.raises(ValueError, match="conflicts"):
        repo.save_calendar_appointment(people[0].id, **updated)
    assert repo.upcoming_appointments(people[0].id) == [first]
    assert len(repo.recent_events(people[0].id)) == 1


def test_unknown_owner_creates_no_appointment(household, entry):
    repo, people = household
    with pytest.raises(PersonNotFoundError):
        repo.save_calendar_appointment("missing-" + uuid4().hex, **entry)
    # A failed attempt must not reserve the event or leave an orphan appointment.
    saved = repo.save_calendar_appointment(people[0].id, **entry)
    assert repo.upcoming_appointments(people[0].id) == [saved]


@pytest.mark.parametrize("invalid", ["event_id", "naive_start", "naive_end", "end", "timezone_name", "kind"])
def test_invalid_calendar_record_does_not_write(household, entry, invalid):
    repo, people = household
    values = dict(entry)
    if invalid.startswith("naive_"):
        key = invalid.removeprefix("naive_")
        values[key] = values[key].replace(tzinfo=None)
    elif invalid == "end":
        values[invalid] = values["start"]
    else:
        values[invalid] = "" if invalid == "kind" else "invalid"
    with pytest.raises(ValueError):
        repo.save_calendar_appointment(people[0].id, **values)
    assert not repo.upcoming_appointments(people[0].id)
    assert not repo.recent_events(people[0].id)


def test_concurrent_replays_do_not_duplicate(household, entry):
    repo, people = household
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repo.save_calendar_appointment(people[0].id, **entry), range(8)))
    assert all(result == results[0] for result in results)
    assert repo.upcoming_appointments(people[0].id) == [results[0]]
    assert len(repo.recent_events(people[0].id)) == 1


def test_failed_audit_rolls_back_appointment(household, entry, monkeypatch):
    repo, people = household
    module = "repository" if isinstance(repo, InMemoryRepository) else "neo4j_repo"

    def fail(_):
        raise RuntimeError("Deliberate audit failure")

    with monkeypatch.context() as scoped:
        scoped.setattr("saarthi_mcp." + module + ".calendar_appointment_detail", fail)
        with pytest.raises(RuntimeError, match="Deliberate"):
            repo.save_calendar_appointment(people[0].id, **entry)
    assert not repo.upcoming_appointments(people[0].id)
    assert not repo.recent_events(people[0].id)
    # A fresh attempt proves rollback also removed the unique ID reservation.
    repo.save_calendar_appointment(people[0].id, **entry)
    assert len(repo.recent_events(people[0].id)) == 1


def test_concurrent_owners_cannot_share_event(household, entry):
    repo, people = household

    def save(person):
        try:
            return repo.save_calendar_appointment(person.id, **entry)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, people))
    assert sum(result is not None for result in results) == 1
    assert sum(len(repo.upcoming_appointments(person.id)) for person in people) == 1
    assert sum(len(repo.recent_events(person.id)) for person in people) == 1


def test_neo4j_read_after_new_connection(household, entry):
    repo, people = household
    if isinstance(repo, InMemoryRepository):
        pytest.skip("Process-local backend has no durable connection.")
    from neo4j import GraphDatabase
    from saarthi_mcp.neo4j_repo import Neo4jRepository

    saved = repo.save_calendar_appointment(people[0].id, **entry)
    reader = Neo4jRepository(
        GraphDatabase.driver("bolt://127.0.0.1:17687", auth=("neo4j", "saarthi-test-only")), "neo4j",
    )
    try:
        assert reader.upcoming_appointments(people[0].id) == [saved]
        assert reader.save_calendar_appointment(people[0].id, **entry) == saved
        assert len(reader.recent_events(people[0].id)) == 1
    finally:
        reader.close()
