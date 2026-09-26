"""Recall must find stored evidence beyond a small recent-event demo window."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastmcp import Client

from saarthi_mcp.memory_query import answer_question, event_search_terms, rank_events
from saarthi_mcp.models import DoseStatus, Event, Person, Role
from saarthi_mcp.repository import InMemoryRepository
from saarthi_mcp.server import build_server


NOW = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)


def test_repeated_query_words_do_not_artificially_boost_relevance():
    assert event_search_terms("Please tell me about callback, CALLBACK, callback!") == ["callback"]


def test_query_budget_and_literal_terms():
    terms = event_search_terms(" ".join(f"term{i}" for i in range(100)))
    assert len(terms) == 24
    assert not event_search_terms("x" * 81)
    assert event_search_terms("José's café?") == ["josé", "café"]
    assert all("'" not in term for term in event_search_terms("x') MATCH (n) DETACH DELETE n //"))


def test_internal_execution_records_are_not_used_as_household_evidence():
    secret = "callback location"
    events = [Event(type=kind, detail=secret, at=NOW) for kind in ("agent_action", "household_setup")]
    answer, evidence = answer_question("Parent", secret, [], events)
    assert "don't have anything on record" in answer
    assert evidence == []


def test_rank_is_relevance_then_recency_and_deduplicates_evidence():
    earlier = Event(type="call", detail="physio callback confirmed", at=NOW - timedelta(days=90))
    later = Event(type="call", detail="physio callback updated", at=NOW)
    unrelated = Event(type="call", detail="different visitor called", at=NOW)
    assert rank_events("physio callback", [earlier, unrelated, earlier, later]) == [later, earlier]


async def test_mcp_finds_old_arbitrary_fact_among_newer_events_and_other_people():
    repo = InMemoryRepository()
    repo.add_person(Person(id="person-a", name="Person A", role=Role.elder))
    repo.add_person(Person(id="person-b", name="Person B", role=Role.elder))
    marker = "callback" + uuid4().hex
    old = repo.add_event("person-a", "call", marker + " original saved detail", NOW - timedelta(days=400))
    for i in range(100):
        repo.add_event("person-a", "note", f"Unrelated newer activity {i}", NOW - timedelta(minutes=i))
    repo.add_event("person-b", "call", marker + " private other-person detail", NOW + timedelta(hours=1))
    repo.add_event("person-a", "agent_action", marker + " execution metadata", NOW)
    assert old not in repo.recent_events("person-a", limit=50)
    async with Client(build_server(repo)) as client:
        result = await client.call_tool("query_memory", {"person": "person-a", "question": marker})
    assert "original saved detail" in result.data["answer"]
    assert "private other-person" not in str(result.data)
    assert "execution metadata" not in str(result.data)
    assert len(result.data["supporting_events"]) == 1
    assert str(old.at.year) in result.data["answer"]


def test_recent_dose_evidence_is_not_duplicated_when_candidates_overlap():
    repo = InMemoryRepository()
    repo.add_person(Person(id="person-a", name="Person A", role=Role.elder))
    repo.add_dose("person-a", "SavedMed", DoseStatus.taken, datetime.now(timezone.utc))
    _, events = repo.query_memory("person-a", "Latest SavedMed dose?")
    assert len(events) == 1


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_retrieval_limit_is_bounded(limit):
    with pytest.raises(ValueError, match="Search limit"):
        InMemoryRepository().search_events("person", "callback", limit=limit)


def test_search_empty_question_does_not_return_recent_facts():
    repo = InMemoryRepository()
    repo.add_person(Person(id="person-a", name="Person A", role=Role.elder))
    repo.add_event("person-a", "call", "Private callback detail", NOW)
    assert repo.search_events("person-a", "") == []
