"""Recall regressions over stored facts, without external services."""

from datetime import datetime, timedelta, timezone

import pytest

from saarthi_mcp.memory_query import answer_question
from saarthi_mcp.models import DoseLog, DoseStatus, Event, Medication, Person, Role
from saarthi_mcp.neo4j_repo import Neo4jRepository
from saarthi_mcp.repository import InMemoryRepository


AT = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
LOGS = [
    DoseLog(med="Amlodipine", status=DoseStatus.taken, at=AT),
    DoseLog(med="Metformin", status=DoseStatus.missed, at=AT - timedelta(hours=1)),
    DoseLog(med="Metformin", status=DoseStatus.taken, at=AT - timedelta(hours=2)),
]


@pytest.mark.parametrize("question", ["Did dad take Metformin?", "Latest METFORMIN record?"])
def test_named_recall_uses_latest_matching_dose(question):
    answer, _ = answer_question("Ramesh", question, LOGS, [])
    assert "missed Metformin" in answer
    assert "Amlodipine" not in answer


def test_scheduled_medication_without_logs_has_no_matching_record():
    answer, events = answer_question(
        "Ramesh", "Did dad take Atorvastatin?", LOGS, [], ["Atorvastatin"]
    )
    assert "don't have a dose record" in answer
    assert events == []


@pytest.mark.parametrize("question", [
    "Did dad take Aspirin?",
    "Did dad take Aspirin pills?",
    "Did dad take MetforminXR?",
    "Did dad take Metformin-XR?",
    "Did dad take Metformin XR?",
    "Did dad take Metformin and Amlodipine?",
])
def test_unknown_or_ambiguous_medication_never_returns_another_dose(question):
    answer, events = answer_question("Ramesh", question, LOGS, [], ["Metformin"])
    assert "Please ask about one medication" in answer
    assert events == []


@pytest.mark.parametrize("question", ["Latest dose?", "Did dad take his pills?"])
def test_generic_dose_recall_is_preserved(question):
    answer, _ = answer_question("Ramesh", question, LOGS, [])
    assert "took Amlodipine" in answer


@pytest.mark.parametrize("question, detail", [
    ("What did the caretaker say?", "The caretaker called to confirm Friday's visit."),
    ("Did he spill the water?", "He spilled the water during dinner."),
    ("What happened with the token?", "The token for the appointment is ready."),
    ("Did the physio call back?", "The physio called back Friday."),
])
def test_medication_keyword_substrings_do_not_hijack_event_recall(question, detail):
    event = Event(type="call", detail=detail, at=AT)
    answer, events = answer_question("Ramesh", question, LOGS, [event])
    assert detail in answer
    assert events == [event]


def test_evidence_matches_medication_and_logged_timestamp():
    latest = LOGS[1]
    matching = Event(type="dose", detail="Metformin missed", at=latest.at)
    events = [
        Event(type="dose", detail="MetforminXR taken", at=latest.at),
        Event(type="dose", detail="Metformin taken", at=AT - timedelta(hours=2)),
        matching,
    ]
    _, supporting = answer_question("Ramesh", "Did dad take Metformin?", LOGS, events)
    assert supporting == [matching]


def test_named_medication_and_time_window_both_filter_records():
    morning = datetime(2026, 9, 10, 8).astimezone()
    evening = morning.replace(hour=20)
    logs = [
        DoseLog(med="Amlodipine", status=DoseStatus.taken, at=evening),
        DoseLog(med="Metformin", status=DoseStatus.missed, at=evening),
        DoseLog(med="Metformin", status=DoseStatus.taken, at=morning),
    ]
    answer, _ = answer_question("Ramesh", "Did dad take Metformin this morning?", logs, [])
    assert "took Metformin" in answer
    generic, _ = answer_question("Ramesh", "Did dad take his evening pills?", logs, [])
    assert "took Amlodipine" in generic


@pytest.mark.parametrize("backend", [InMemoryRepository, Neo4jRepository])
def test_both_repositories_pass_scheduled_medications_to_recall(backend, monkeypatch):
    # The Neo4j adapter is exercised with fetched values, without opening a database.
    repo = object.__new__(backend)
    person = Person(id="elder-1", name="Ramesh", role=Role.elder)
    if backend is InMemoryRepository:
        repo._people = {person.id: person}
    else:
        monkeypatch.setattr(repo, "_person_by_id", lambda person_id: person)
    monkeypatch.setattr(repo, "dose_logs", lambda person_id, since: LOGS)
    monkeypatch.setattr(repo, "recent_events", lambda person_id, limit: [])
    monkeypatch.setattr(repo, "search_events", lambda person_id, question, limit: [])
    monkeypatch.setattr(repo, "medications_for", lambda person_id: [
        Medication(name="Atorvastatin", dose="saved dose", schedule=[])
    ])
    answer, events = repo.query_memory(person.id, "Did dad take Atorvastatin?")
    assert "don't have a dose record" in answer
    assert events == []
