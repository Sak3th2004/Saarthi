"""Dose retries must not double-count supply, including across database clients."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier
import time
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from saarthi_mcp.models import DoseStatus, Medication, Person, Role
from saarthi_mcp.repository import InMemoryRepository, PersonNotFoundError


@pytest.fixture(params=["memory", "neo4j"])
def dose_repositories(request):
    person_id = "atomic-dose-" + uuid4().hex
    if request.param == "memory":
        repo = InMemoryRepository()
        repo.add_person(Person(id=person_id, name="Concurrency test", role=Role.elder))
        repo.add_medication(person_id, Medication(
            name="Saved medication", dose="Saved label", schedule=["08:00"], supply_count=20,
        ))
        yield (repo, repo), person_id
        return

    if os.getenv("SAARTHI_RUN_NEO4J_TESTS") != "1":
        pytest.skip("requires the disposable localhost:17687 Neo4j test database")
    from saarthi_mcp.config import load_settings
    from saarthi_mcp.neo4j_repo import Neo4jRepository

    settings = load_settings()
    assert settings.backend == "neo4j" and settings.neo4j is not None
    target = urlparse(settings.neo4j.uri)
    assert target.hostname in {"localhost", "127.0.0.1"} and target.port == 17687, (
        "Dose integration tests must only write to the disposable local database; never Aura."
    )
    first = Neo4jRepository.from_settings(settings.neo4j)
    second = Neo4jRepository.from_settings(settings.neo4j)
    try:
        first._write(
            "CREATE (p:Person {id:$id, name:'Concurrency test', role:'elder'}) "
            "CREATE (p)-[:TAKES]->(:Medication {name:'Saved medication', dose:'Saved label', "
            "schedule:['08:00'], supply_count:20})",
            id=person_id,
        )
        yield (first, second), person_id
    finally:
        first.close()
        second.close()


def test_concurrent_duplicate_doses_write_once(dose_repositories, monkeypatch):
    repositories, person_id = dose_repositories
    if isinstance(repositories[0], InMemoryRepository):
        # Yield while constructing a log so the race is exercised even under the GIL.
        # The lock must cover both duplicate detection and the eventual insertion.
        import saarthi_mcp.repository as repository_module

        original_log = repository_module.DoseLog

        def slow_log(**kwargs):
            time.sleep(0.03)
            return original_log(**kwargs)

        monkeypatch.setattr(repository_module, "DoseLog", slow_log)

    at = datetime.now(timezone.utc)
    workers = 8
    ready = Barrier(workers)

    def submit(index):
        ready.wait(timeout=10)
        return repositories[index % 2].add_dose(
            person_id, "Saved medication" if index % 2 else "SAVED MEDICATION",
            DoseStatus.taken, at + timedelta(seconds=index),
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(submit, range(workers)))

    assert sum(not duplicate for _, duplicate in outcomes) == 1
    repo = repositories[0]
    assert len(repo.dose_logs(person_id)) == 1
    assert repo.medications_for(person_id)[0].supply_count == 19
    assert len([event for event in repo.recent_events(person_id) if event.type == "dose"]) == 1


def test_duplicate_window_includes_thirty_minutes(dose_repositories):
    repositories, person_id = dose_repositories
    at = datetime.now(timezone.utc)
    repo = repositories[0]
    assert repo.add_dose(person_id, "Saved medication", DoseStatus.taken, at)[1] is False
    assert repositories[1].add_dose(
        person_id, "saved medication", DoseStatus.taken, at + timedelta(minutes=30),
    )[1] is True
    assert repositories[1].add_dose(
        person_id, "Saved medication", DoseStatus.taken, at + timedelta(seconds=1801),
    )[1] is False
    assert len(repo.dose_logs(person_id)) == 2
    assert repo.medications_for(person_id)[0].supply_count == 18


@pytest.mark.parametrize("status", [DoseStatus.missed, DoseStatus.skipped])
def test_other_statuses_preserve_supply_and_deduplicate(dose_repositories, status):
    repositories, person_id = dose_repositories
    at = datetime.now(timezone.utc)
    assert repositories[0].add_dose(person_id, "Saved medication", status, at)[1] is False
    assert repositories[1].add_dose(person_id, "Saved medication", status, at)[1] is True
    assert repositories[0].medications_for(person_id)[0].supply_count == 20
    assert len(repositories[0].dose_logs(person_id)) == 1


def test_missing_person_cannot_report_a_saved_dose(dose_repositories):
    repositories, person_id = dose_repositories
    missing_id = person_id + "-missing"
    with pytest.raises(PersonNotFoundError):
        repositories[0].add_dose(
            missing_id, "Saved medication", DoseStatus.taken, datetime.now(timezone.utc),
        )
    assert repositories[0].dose_logs(missing_id) == []
    assert repositories[0].recent_events(missing_id) == []
