"""The household data layer.

``HouseholdRepository`` is the interface the MCP tools depend on. Week 1 ships
``InMemoryRepository`` (seeded sample data). Week 2 adds a ``Neo4jRepository`` implementing the
same interface — the MCP tool contract in ``server.py`` does not change.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from saarthi_mcp.household import HouseholdDefinition

from saarthi_mcp.memory_query import answer_question, rank_events
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
    "AmbiguousPersonError",
    "ensure_aware",
    "now_utc",
    "seeded_repository",
]


class PersonNotFoundError(ValueError):
    """Raised when a person string cannot be resolved to a known household member."""


class AmbiguousPersonError(PersonNotFoundError):
    """A label matches multiple people; the caller must select a unique ID."""


# --------------------------------------------------------------------------- interface


@runtime_checkable
class HouseholdRepository(Protocol):
    def resolve_person(self, person: str) -> Person: ...
    def primary_elder(self) -> Person: ...
    def family_contacts(self) -> list[Person]: ...
    def medications_for(self, person_id: str) -> list[Medication]: ...
    def upcoming_appointments(self, person_id: str, limit: int = 5) -> list[Appointment]: ...
    def recent_events(self, person_id: str, limit: int = 10) -> list[Event]: ...
    def search_events(self, person_id: str, question: str, limit: int = 50) -> list[Event]: ...
    def dose_logs(self, person_id: str, since: datetime | None = None) -> list[DoseLog]: ...
    def adherence(self, person_id: str, days: int = 7) -> float | None: ...
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
        self._aliases: dict[str, set[str]] = {}  # normalized label -> all matching IDs
        self._meds: dict[str, list[Medication]] = {}
        self._doses: dict[str, list[DoseLog]] = {}
        self._appts: dict[str, list[Appointment]] = {}
        self._events: dict[str, list[Event]] = {}
        self._relationships: list[tuple[str, str, str]] = []
        self._primary_elder_id: str | None = None
        self._appt_seq = 0

    # -- registration helpers -------------------------------------------------

    def import_household(self, definition: HouseholdDefinition) -> None:
        """Load validated user records into an empty process-local store."""
        if any((self._people, self._meds, self._doses, self._appts, self._events)):
            raise ValueError("Household import requires an empty store; existing records were not changed.")
        staged = InMemoryRepository()
        at = now_utc()
        for member in definition.people:
            person = Person.model_validate(member.model_dump())
            staged.add_person(person, aliases=member.aliases)
            staged._meds[person.id] = [Medication.model_validate(m.model_dump()) for m in member.medications]
            staged.add_event(person.id, "household_setup", "User-supplied household records imported.", at)
        staged._primary_elder_id = definition.primary_person_id
        staged._relationships = [(r.from_person, r.to_person, r.relation) for r in definition.relationships]
        self.__dict__.update(staged.__dict__)

    def add_person(self, person: Person, aliases: list[str] | None = None) -> None:
        # Replacing a record must not leave its old name/aliases pointing at it.
        for label, matches in list(self._aliases.items()):
            matches.discard(person.id)
            if not matches:
                del self._aliases[label]
        self._people[person.id] = person
        for value in [person.name, person.id, *(aliases or [])]:
            label = value.strip().lower()
            if label:
                self._aliases.setdefault(label, set()).add(person.id)
        if person.role is Role.elder and self._primary_elder_id is None:
            self._primary_elder_id = person.id

    # -- reads ----------------------------------------------------------------

    def resolve_person(self, person: str) -> Person:
        key = (person or "").strip().lower()
        matches = self._aliases.get(key, set())
        if not matches:
            raise PersonNotFoundError("Unknown person. Use a saved household member's name or ID.")
        if len(matches) > 1:
            raise AmbiguousPersonError("More than one person matches. Use a unique person ID.")
        return self._people[next(iter(matches))]

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

    def search_events(self, person_id: str, question: str, limit: int = 50) -> list[Event]:
        if not 1 <= limit <= 100:
            raise ValueError("Search limit must be between 1 and 100.")
        return rank_events(question, self._events.get(person_id, []), limit=limit)

    def adherence(self, person_id: str, days: int = 7) -> float | None:
        """Taken fraction among recorded taken/missed doses; absent records are unknown."""
        until = now_utc()
        since = until - timedelta(days=days)
        logs = [d for d in self._doses.get(person_id, []) if since <= ensure_aware(d.at) <= until]
        counted = [d for d in logs if d.status in (DoseStatus.taken, DoseStatus.missed)]
        if not counted:
            return None
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
            type="appointment_recorded",
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
        # Keep recent dose evidence while also finding relevant older general facts.
        candidates = recent_events + self.search_events(person_id, question, limit=50)
        return answer_question(
            person.name, question, recent_dose_logs, candidates,
            medication_names=[med.name for med in self.medications_for(person_id)],
        )


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
