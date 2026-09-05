"""The household data layer.

``HouseholdRepository`` is the interface the MCP tools depend on. Week 1 ships
``InMemoryRepository`` (seeded sample data). Week 2 adds a ``Neo4jRepository`` implementing the
same interface — the MCP tool contract in ``server.py`` does not change.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from saarthi_mcp.memory_query import answer_question
from saarthi_mcp.models import (
    Appointment,
    DoseLog,
    DoseStatus,
    Event,
    Medication,
    Person,
    Role,
)
from saarthi_mcp.timeutil import ensure_aware, now_utc  # re-exported for callers

__all__ = [
    "HouseholdRepository",
    "InMemoryRepository",
    "PersonNotFoundError",
    "ensure_aware",
    "now_utc",
    "seeded_repository",
]


class PersonNotFoundError(ValueError):
    """Raised when a person string cannot be resolved to a known household member."""


# --------------------------------------------------------------------------- interface


@runtime_checkable
class HouseholdRepository(Protocol):
    def resolve_person(self, person: str) -> Person: ...
    def primary_elder(self) -> Person: ...
    def family_contacts(self) -> list[Person]: ...
    def medications_for(self, person_id: str) -> list[Medication]: ...
    def upcoming_appointments(self, person_id: str, limit: int = 5) -> list[Appointment]: ...
    def recent_events(self, person_id: str, limit: int = 10) -> list[Event]: ...
    def dose_logs(self, person_id: str, since: datetime | None = None) -> list[DoseLog]: ...
    def adherence(self, person_id: str, days: int = 7) -> float: ...
    def add_dose(
        self, person_id: str, med: str, status: DoseStatus, at: datetime
    ) -> tuple[DoseLog, bool]: ...
    def add_appointment(self, person_id: str, kind: str, when: datetime) -> Appointment: ...
    def add_event(self, person_id: str, type: str, detail: str, at: datetime) -> Event: ...
    def query_memory(self, person_id: str, question: str) -> tuple[str, list[Event]]: ...


# --------------------------------------------------------------------------- in-memory impl


class InMemoryRepository:
    """A process-local store. Swappable for Neo4j via the ``HouseholdRepository`` interface."""

    def __init__(self) -> None:
        self._people: dict[str, Person] = {}
        self._aliases: dict[str, str] = {}  # lowercased alias -> person id
        self._meds: dict[str, list[Medication]] = {}
        self._doses: dict[str, list[DoseLog]] = {}
        self._appts: dict[str, list[Appointment]] = {}
        self._events: dict[str, list[Event]] = {}
        self._primary_elder_id: str | None = None
        self._appt_seq = 0

    # -- registration helpers -------------------------------------------------

    def add_person(self, person: Person, aliases: list[str] | None = None) -> None:
        self._people[person.id] = person
        self._aliases[person.name.lower()] = person.id
        self._aliases[person.id.lower()] = person.id
        for a in aliases or []:
            self._aliases[a.lower()] = person.id
        if person.role is Role.elder and self._primary_elder_id is None:
            self._primary_elder_id = person.id

    # -- reads ----------------------------------------------------------------

    def resolve_person(self, person: str) -> Person:
        key = (person or "").strip().lower()
        pid = self._aliases.get(key)
        if pid is None:
            known = ", ".join(sorted({p.name for p in self._people.values()}))
            raise PersonNotFoundError(
                f"Unknown person {person!r}. Known household members: {known}."
            )
        return self._people[pid]

    def primary_elder(self) -> Person:
        if self._primary_elder_id is None:
            raise PersonNotFoundError("No elder registered in the household.")
        return self._people[self._primary_elder_id]

    def family_contacts(self) -> list[Person]:
        return [p for p in self._people.values() if p.role is Role.family]

    def medications_for(self, person_id: str) -> list[Medication]:
        return list(self._meds.get(person_id, []))

    def upcoming_appointments(self, person_id: str, limit: int = 5) -> list[Appointment]:
        now = now_utc()
        upcoming = [a for a in self._appts.get(person_id, []) if ensure_aware(a.when) >= now]
        upcoming.sort(key=lambda a: ensure_aware(a.when))
        return upcoming[:limit]

    def recent_events(self, person_id: str, limit: int = 10) -> list[Event]:
        events = sorted(
            self._events.get(person_id, []), key=lambda e: ensure_aware(e.at), reverse=True
        )
        return events[:limit]

    def dose_logs(self, person_id: str, since: datetime | None = None) -> list[DoseLog]:
        logs = self._doses.get(person_id, [])
        if since is not None:
            since = ensure_aware(since)
            logs = [d for d in logs if ensure_aware(d.at) >= since]
        return sorted(logs, key=lambda d: ensure_aware(d.at), reverse=True)

    def adherence(self, person_id: str, days: int = 7) -> float:
        since = now_utc() - timedelta(days=days)
        logs = [d for d in self._doses.get(person_id, []) if ensure_aware(d.at) >= since]
        counted = [d for d in logs if d.status in (DoseStatus.taken, DoseStatus.missed)]
        if not counted:
            return 1.0
        taken = sum(1 for d in counted if d.status is DoseStatus.taken)
        return round(taken / len(counted), 3)

    # -- writes ---------------------------------------------------------------

    def add_dose(
        self, person_id: str, med: str, status: DoseStatus, at: datetime
    ) -> tuple[DoseLog, bool]:
        at = ensure_aware(at)
        existing = self._doses.setdefault(person_id, [])
        # Duplicate guard: same med + same status within 30 minutes (the "I already took it" case).
        already = any(
            d.med.lower() == med.lower()
            and d.status is status
            and abs((ensure_aware(d.at) - at).total_seconds()) <= 1800
            for d in existing
        )
        log = DoseLog(med=med, status=status, at=at)
        if not already:
            existing.append(log)
            self.add_event(
                person_id,
                type="dose",
                detail=f"{med} {status.value} at {at.isoformat(timespec='minutes')}",
                at=at,
            )
        return log, already

    def add_appointment(self, person_id: str, kind: str, when: datetime) -> Appointment:
        self._appt_seq += 1
        appt = Appointment(id=f"appt-{self._appt_seq}", kind=kind, when=ensure_aware(when))
        self._appts.setdefault(person_id, []).append(appt)
        self.add_event(
            person_id,
            type="appointment_booked",
            detail=f"{kind} on {appt.when.isoformat(timespec='minutes')}",
            at=now_utc(),
        )
        return appt

    def add_event(self, person_id: str, type: str, detail: str, at: datetime) -> Event:
        event = Event(type=type, detail=detail, at=ensure_aware(at))
        self._events.setdefault(person_id, []).append(event)
        return event

    # -- memory query ---------------------------------------------------------

    def query_memory(self, person_id: str, question: str) -> tuple[str, list[Event]]:
        """Cross-session recall over stored state, via the shared heuristic (memory_query)."""
        person = self._people[person_id]
        recent_dose_logs = self.dose_logs(person_id, since=now_utc() - timedelta(days=2))
        recent_events = self.recent_events(person_id, limit=50)
        return answer_question(person.name, question, recent_dose_logs, recent_events)


# --------------------------------------------------------------------------- seed data


def seeded_repository() -> InMemoryRepository:
    """Sample household used until the real user's data is loaded (Week 2, AGENTS.md §10).

    Placeholder elder 'Ramesh' with a realistic multi-med routine. Replace with the real demo
    user's actual meds + appointment pattern before the video.
    """
    repo = InMemoryRepository()
    now = now_utc()

    ramesh = Person(id="elder-1", name="Ramesh", role=Role.elder)
    arjun = Person(
        id="fam-1",
        name="Arjun",
        role=Role.family,
        relation="son",
        phone="+10000000000",
        email="family@example.com",
    )
    repo.add_person(ramesh, aliases=["dad", "appa", "father"])
    repo.add_person(arjun, aliases=["son"])

    repo._meds["elder-1"] = [
        Medication(name="Metformin", dose="500mg", schedule=["08:00", "20:00"], supply_count=24),
        Medication(name="Amlodipine", dose="5mg", schedule=["08:00"], supply_count=30),
        Medication(name="Atorvastatin", dose="10mg", schedule=["21:00"], supply_count=8),
    ]

    # 7 days of dose history: mostly taken, one missed evening dose yesterday.
    for day in range(7, 0, -1):
        d = now - timedelta(days=day)
        morning = d.replace(hour=8, minute=5, second=0, microsecond=0)
        evening = d.replace(hour=20, minute=10, second=0, microsecond=0)
        repo._doses.setdefault("elder-1", []).extend(
            [
                DoseLog(med="Metformin", status=DoseStatus.taken, at=morning),
                DoseLog(med="Amlodipine", status=DoseStatus.taken, at=morning),
                DoseLog(
                    med="Metformin",
                    status=DoseStatus.missed if day == 1 else DoseStatus.taken,
                    at=evening,
                ),
            ]
        )

    # Appointments.
    repo.add_appointment("elder-1", "Cardiology follow-up", now + timedelta(days=3, hours=2))
    repo.add_appointment("elder-1", "Physiotherapy", now + timedelta(days=8))

    # Events that make cross-session recall demoable.
    repo.add_event(
        "elder-1",
        type="call",
        detail="Physiotherapist Dr. Meera called to reschedule; said she would call back Friday.",
        at=now - timedelta(days=2, hours=3),
    )
    repo.add_event(
        "elder-1",
        type="visit",
        detail="Arjun visited for dinner and refilled the pill organizer.",
        at=now - timedelta(days=1, hours=5),
    )
    repo.add_event(
        "elder-1",
        type="missed_dose",
        detail="Metformin evening dose missed.",
        at=(now - timedelta(days=1)).replace(hour=20, minute=10, second=0, microsecond=0),
    )
    return repo
