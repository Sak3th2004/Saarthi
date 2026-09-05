"""Live Neo4j tests — real cross-session recall against a real graph (AGENTS.md §6).

Destructive (wipes + seeds the target DB) and needs a reachable Neo4j, so it is skipped unless
``SAARTHI_RUN_NEO4J_TESTS=1`` and the ``neo4j`` backend is configured. Run against the dev/demo
database only:

    SAARTHI_RUN_NEO4J_TESTS=1 pytest tests/test_neo4j.py -q
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from saarthi_mcp.config import load_settings
from saarthi_mcp.models import DoseStatus
from saarthi_mcp.repository import PersonNotFoundError

pytestmark = pytest.mark.skipif(
    os.getenv("SAARTHI_RUN_NEO4J_TESTS") != "1",
    reason="set SAARTHI_RUN_NEO4J_TESTS=1 to run (destructive; needs a reachable Neo4j)",
)


@pytest.fixture(scope="module")
def repo():
    from saarthi_mcp.neo4j_repo import Neo4jRepository, seed_neo4j

    s = load_settings()
    assert s.backend == "neo4j" and s.neo4j is not None, "configure SAARTHI_BACKEND=neo4j + NEO4J_*"
    r = Neo4jRepository.from_settings(s.neo4j)
    seed_neo4j(r, wipe=True)
    yield r
    r.close()


def test_resolve_by_alias_and_primary_elder(repo):
    assert repo.resolve_person("dad").name == "Ramesh"
    assert repo.primary_elder().name == "Ramesh"


def test_seeded_medications(repo):
    names = {m.name for m in repo.medications_for("elder-1")}
    assert {"Metformin", "Amlodipine", "Atorvastatin"} <= names


def test_adherence_reflects_missed_dose(repo):
    a = repo.adherence("elder-1")
    assert 0.0 <= a < 1.0  # one seeded evening dose was missed


def test_write_then_recall_from_graph(repo):
    at = datetime.now(timezone.utc)
    _, already = repo.add_dose("elder-1", "Atorvastatin", DoseStatus.taken, at)
    assert already is False
    answer, _ = repo.query_memory("elder-1", "did dad take his Atorvastatin?")
    assert "Atorvastatin" in answer and "took" in answer.lower()


def test_cross_session_recall_of_physio_call(repo):
    answer, events = repo.query_memory("elder-1", "did the physio call back?")
    assert "Meera" in answer or "call back" in answer.lower()
    assert events


def test_duplicate_dose_guard(repo):
    at = datetime.now(timezone.utc) - timedelta(hours=1)
    _, first = repo.add_dose("elder-1", "Amlodipine", DoseStatus.taken, at)
    _, second = repo.add_dose("elder-1", "Amlodipine", DoseStatus.taken, at + timedelta(minutes=5))
    assert first is False
    assert second is True


def test_upcoming_appointments_sorted(repo):
    appts = repo.upcoming_appointments("elder-1", limit=10)
    assert len(appts) >= 2
    whens = [a.when for a in appts]
    assert whens == sorted(whens)


def test_unknown_person_raises(repo):
    with pytest.raises(PersonNotFoundError):
        repo.resolve_person("stranger")
