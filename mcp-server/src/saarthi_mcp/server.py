"""The Saarthi MCP server — FastMCP over Streamable HTTP (AGENTS.md §5).

Tools return a ``ToolResult`` carrying both a human-readable ``speech`` line (Alexa+ speaks it)
and structured content (the dashboard renders it). Domain data comes from a
``HouseholdRepository`` so the Neo4j graph can replace the in-memory backend in Week 2 without
touching this contract.
"""

from __future__ import annotations

from datetime import datetime

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import ToolResult

from saarthi_mcp.config import Settings, load_settings
from saarthi_mcp.models import (
    AppointmentList,
    AppointmentResult,
    CheckInResult,
    DoseLogResult,
    DoseStatus,
    EventResult,
    HouseholdSummary,
    MedicationSchedule,
    MemoryAnswer,
    NotifyResult,
    Person,
    Urgency,
)
from saarthi_mcp.repository import (
    HouseholdRepository,
    PersonNotFoundError,
    ensure_aware,
    now_utc,
    seeded_repository,
)

mcp: FastMCP = FastMCP(
    name="Saarthi",
    instructions=(
        "Saarthi is a care companion with memory for an elderly household. Use these tools to "
        "read and update medications, appointments, events, and family notifications, and to "
        "recall facts across sessions. Saarthi is NOT a medical device: never diagnose, dose, or "
        "give medical advice — surface saved facts and defer to a doctor."
    ),
)

# Repository holder, injected by build_server(). Kept module-level so tools can close over it.
_state: dict[str, HouseholdRepository | None] = {"repo": None}


def _repo() -> HouseholdRepository:
    repo = _state["repo"]
    if repo is None:
        repo = _default_repo(load_settings())
        _state["repo"] = repo
    return repo


def _default_repo(settings: Settings) -> HouseholdRepository:
    if settings.backend == "memory":
        return seeded_repository()
    if settings.backend == "neo4j":
        raise NotImplementedError(
            "The Neo4j backend lands in Week 2. Set SAARTHI_BACKEND=memory for now."
        )
    raise ValueError(f"Unknown SAARTHI_BACKEND={settings.backend!r} (use 'memory' or 'neo4j').")


def build_server(repo: HouseholdRepository | None = None) -> FastMCP:
    """Inject a repository and return the configured MCP server."""
    _state["repo"] = repo if repo is not None else _default_repo(load_settings())
    return mcp


def _result(model) -> ToolResult:
    return ToolResult(content=model.speech, structured_content=model.model_dump(mode="json"))


def _resolve(person: str) -> Person:
    try:
        return _repo().resolve_person(person)
    except PersonNotFoundError as exc:
        raise ToolError(str(exc)) from exc


# --------------------------------------------------------------------------- tools


@mcp.tool
def get_household_summary() -> ToolResult:
    """Current state for the household's elder: meds, next appointments, recent events, adherence."""
    repo = _repo()
    elder = repo.primary_elder()
    meds = repo.medications_for(elder.id)
    appts = repo.upcoming_appointments(elder.id)
    events = repo.recent_events(elder.id, limit=5)
    adherence = repo.adherence(elder.id)

    next_appt = (
        f" Next up: {appts[0].kind} on {ensure_aware(appts[0].when).astimezone():%b %d}."
        if appts
        else ""
    )
    speech = (
        f"{elder.name} is on {len(meds)} medications with {int(adherence * 100)}% adherence this "
        f"week.{next_appt}"
    )
    return _result(
        HouseholdSummary(
            person=elder,
            medications=meds,
            upcoming_appointments=appts,
            recent_events=events,
            adherence_7d=adherence,
            speech=speech,
        )
    )


@mcp.tool
def get_medication_schedule(person: str) -> ToolResult:
    """List a person's medications, doses, and daily schedule."""
    p = _resolve(person)
    meds = _repo().medications_for(p.id)
    if meds:
        lines = ", ".join(f"{m.name} {m.dose} at {'/'.join(m.schedule)}" for m in meds)
        speech = f"{p.name} takes: {lines}."
    else:
        speech = f"No medications are on record for {p.name}."
    return _result(MedicationSchedule(person=p, medications=meds, speech=speech))


@mcp.tool
def log_dose(
    person: str, med: str, taken: bool, at: datetime | None = None
) -> ToolResult:
    """Record that a dose was taken (or missed). Guards against double-logging the same dose."""
    p = _resolve(person)
    when = ensure_aware(at) if at else now_utc()
    status = DoseStatus.taken if taken else DoseStatus.missed
    log, already = _repo().add_dose(p.id, med, status, when)
    if already:
        speech = f"{med} was already logged as {status.value} for {p.name} just now — no change."
    else:
        speech = f"Logged: {p.name} {status.value} {med} at {when.astimezone():%I:%M %p}.".replace(
            " 0", " "
        )
    return _result(DoseLogResult(person=p, dose=log, already_logged=already, speech=speech))


@mcp.tool
def book_appointment(person: str, kind: str, when: datetime) -> ToolResult:
    """Book an appointment of a given kind at a given time."""
    p = _resolve(person)
    appt = _repo().add_appointment(p.id, kind, ensure_aware(when))
    speech = f"Booked {kind} for {p.name} on {ensure_aware(appt.when).astimezone():%A %b %d, %I:%M %p}."
    return _result(AppointmentResult(person=p, appointment=appt, speech=speech))


@mcp.tool
def list_appointments(person: str) -> ToolResult:
    """List a person's upcoming appointments."""
    p = _resolve(person)
    appts = _repo().upcoming_appointments(p.id, limit=10)
    if appts:
        lines = "; ".join(
            f"{a.kind} on {ensure_aware(a.when).astimezone():%b %d %I:%M %p}" for a in appts
        )
        speech = f"{p.name} has {len(appts)} upcoming: {lines}."
    else:
        speech = f"{p.name} has no upcoming appointments."
    return _result(AppointmentList(person=p, appointments=appts, speech=speech))


@mcp.tool
def notify_family(person: str, message: str, urgency: Urgency = Urgency.info) -> ToolResult:
    """Record a message to route to the family. (Live SMS/email delivery is wired in Week 4.)"""
    p = _resolve(person)
    contacts = _repo().family_contacts()
    channels: list[str] = []
    for c in contacts:
        if c.phone:
            channels.append(f"sms:{c.phone}")
        if c.email:
            channels.append(f"email:{c.email}")
    _repo().add_event(
        p.id, type="notify", detail=f"[{urgency.value}] {message}", at=now_utc()
    )
    names = ", ".join(c.name for c in contacts) or "the family"
    speech = f"Noted a {urgency.value} message about {p.name} for {names}."
    return _result(
        NotifyResult(
            person=p, delivered_to=channels, urgency=urgency, message=message, speech=speech
        )
    )


@mcp.tool
def record_event(person: str, type: str, detail: str) -> ToolResult:
    """Write an event (visit, call, note, anomaly, …) into the household memory."""
    p = _resolve(person)
    event = _repo().add_event(p.id, type=type, detail=detail, at=now_utc())
    speech = f"Recorded for {p.name}: {detail}"
    return _result(EventResult(person=p, event=event, speech=speech))


# Phrases that indicate a request for medical *advice* (vs. a factual recall). AGENTS.md §11.
_ADVICE_MARKERS = (
    "should i",
    "should he",
    "should she",
    "is it safe",
    "how much",
    "what dose",
    "increase",
    "decrease",
    "side effect",
    "diagnos",
    "is it okay to",
    "can i take",
)


@mcp.tool
def query_memory(person: str, question: str) -> ToolResult:
    """Answer a question from the household memory (cross-session recall). Never gives medical advice."""
    p = _resolve(person)
    q = (question or "").lower()
    if any(m in q for m in _ADVICE_MARKERS):
        answer, events = _repo().query_memory(p.id, question)
        safe = (
            "I can share what's on record, but I can't give medical advice — please talk to the "
            f"doctor. Here's what I have: {answer}"
        )
        return _result(
            MemoryAnswer(
                person=p, question=question, answer=safe, supporting_events=events, speech=safe
            )
        )
    answer, events = _repo().query_memory(p.id, question)
    return _result(
        MemoryAnswer(
            person=p, question=question, answer=answer, supporting_events=events, speech=answer
        )
    )


@mcp.tool
def check_in(person: str) -> ToolResult:
    """Watch-agent entry point: is the person OK today? Flags missed doses and low supply."""
    repo = _repo()
    p = _resolve(person)
    today_start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    missed_today = sum(
        1
        for d in repo.dose_logs(p.id, since=today_start)
        if d.status is DoseStatus.missed
    )
    recent = repo.recent_events(p.id, limit=1)
    last_activity = recent[0].at if recent else None

    concerns: list[str] = []
    if missed_today:
        concerns.append(f"{missed_today} missed dose(s) today")
    for m in repo.medications_for(p.id):
        if m.supply_count is not None and m.supply_count <= 10:
            concerns.append(f"low supply of {m.name} ({m.supply_count} left)")

    ok = not concerns
    speech = (
        f"{p.name} looks fine today."
        if ok
        else f"Heads up on {p.name}: " + "; ".join(concerns) + "."
    )
    return _result(
        CheckInResult(
            person=p,
            ok=ok,
            missed_doses_today=missed_today,
            last_activity=last_activity,
            concerns=concerns,
            speech=speech,
        )
    )


def run() -> None:
    """Entry point: serve over Streamable HTTP (AGENTS.md §5)."""
    settings = load_settings()
    build_server()
    mcp.run(transport="http", host=settings.host, port=settings.port, path=settings.path)
