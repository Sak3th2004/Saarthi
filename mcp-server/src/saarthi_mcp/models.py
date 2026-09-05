"""Domain + tool-result models.

Every tool returns a typed model (structured content the dashboard renders) plus a ``speech``
string (the human-readable line Alexa+ speaks). See AGENTS.md §5.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- enums


class Role(str, Enum):
    elder = "elder"
    family = "family"


class DoseStatus(str, Enum):
    taken = "taken"
    missed = "missed"
    skipped = "skipped"


class Urgency(str, Enum):
    info = "info"
    warning = "warning"
    urgent = "urgent"


# --------------------------------------------------------------------------- domain


class Person(BaseModel):
    id: str
    name: str
    role: Role
    relation: str | None = Field(
        default=None, description="e.g. 'son', 'physiotherapist' — relative to the elder"
    )
    phone: str | None = None
    email: str | None = None


class Medication(BaseModel):
    name: str
    dose: str
    schedule: list[str] = Field(description="24h times, e.g. ['08:00', '20:00']")
    supply_count: int | None = Field(default=None, description="Doses remaining, if tracked")


class DoseLog(BaseModel):
    med: str
    status: DoseStatus
    at: datetime


class Appointment(BaseModel):
    id: str
    kind: str
    when: datetime
    status: str = "scheduled"


class Event(BaseModel):
    type: str
    detail: str
    at: datetime


# --------------------------------------------------------------------------- tool results


class HouseholdSummary(BaseModel):
    person: Person
    medications: list[Medication]
    upcoming_appointments: list[Appointment]
    recent_events: list[Event]
    adherence_7d: float = Field(ge=0.0, le=1.0, description="Fraction of doses taken, last 7 days")
    speech: str


class MedicationSchedule(BaseModel):
    person: Person
    medications: list[Medication]
    speech: str


class DoseLogResult(BaseModel):
    person: Person
    dose: DoseLog
    already_logged: bool = Field(
        default=False, description="True if an identical dose was already recorded near this time"
    )
    speech: str


class AppointmentResult(BaseModel):
    person: Person
    appointment: Appointment
    speech: str


class AppointmentList(BaseModel):
    person: Person
    appointments: list[Appointment]
    speech: str


class NotifyResult(BaseModel):
    person: Person
    delivered_to: list[str]
    urgency: Urgency
    message: str
    speech: str


class EventResult(BaseModel):
    person: Person
    event: Event
    speech: str


class MemoryAnswer(BaseModel):
    person: Person
    question: str
    answer: str
    supporting_events: list[Event] = Field(default_factory=list)
    speech: str


class CheckInResult(BaseModel):
    person: Person
    ok: bool
    missed_doses_today: int
    last_activity: datetime | None
    concerns: list[str] = Field(default_factory=list)
    speech: str
