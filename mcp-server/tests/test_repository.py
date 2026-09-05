"""Unit tests for the in-memory repository (the interface Neo4j will implement in Week 2)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from saarthi_mcp.models import DoseStatus
from saarthi_mcp.repository import (
    InMemoryRepository,
    PersonNotFoundError,
    now_utc,
    seeded_repository,
)


def test_resolve_person_by_alias():
    repo = seeded_repository()
    assert repo.resolve_person("dad").name == "Ramesh"
    assert repo.resolve_person("RAMESH").id == "elder-1"


def test_resolve_unknown_person_raises():
    repo = seeded_repository()
    with pytest.raises(PersonNotFoundError):
        repo.resolve_person("stranger")


def test_adherence_is_a_fraction():
    repo = seeded_repository()
    a = repo.adherence("elder-1")
    assert 0.0 <= a <= 1.0
    # One evening dose was seeded as missed, so adherence should be below perfect.
    assert a < 1.0


def test_adherence_empty_defaults_to_one():
    repo = InMemoryRepository()
    from saarthi_mcp.models import Person, Role

    repo.add_person(Person(id="e1", name="Test", role=Role.elder))
    assert repo.adherence("e1") == 1.0


def test_add_dose_duplicate_guard():
    repo = seeded_repository()
    at = now_utc()
    _, first = repo.add_dose("elder-1", "Metformin", DoseStatus.taken, at)
    _, second = repo.add_dose("elder-1", "Metformin", DoseStatus.taken, at + timedelta(minutes=5))
    assert first is False
    assert second is True


def test_query_memory_finds_seeded_call_event():
    repo = seeded_repository()
    answer, events = repo.query_memory("elder-1", "did the physio call back?")
    assert "Meera" in answer or "call back" in answer.lower()
    assert events


def test_query_memory_unknown_topic_is_graceful():
    repo = seeded_repository()
    answer, events = repo.query_memory("elder-1", "what about the trip to Mars?")
    assert "don't have" in answer.lower()
    assert events == []
