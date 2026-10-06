"""The Saarthi MCP server — FastMCP over Streamable HTTP (AGENTS.md §5).

Tools return a ``ToolResult`` carrying both a human-readable ``speech`` line (Alexa+ speaks it)
and structured content (the dashboard renders it). Each server owns its injected repository
and orchestrator; creating another server cannot redirect existing tools to another household.
"""

from __future__ import annotations

from datetime import datetime
from functools import wraps
from inspect import signature
import json
from typing import Annotated

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import ToolResult

from saarthi_mcp.config import Settings, load_settings
from saarthi_mcp.graph import MemoryGraph, memory_graph
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
    InMemoryRepository,
    PersonNotFoundError,
    ensure_aware,
    now_utc,
)


def _default_repo(settings: Settings) -> HouseholdRepository:
    if settings.backend == "memory":
        repo = InMemoryRepository()
        if settings.household_file:
            from saarthi_mcp.household import load_household
            repo.import_household(load_household(settings.household_file))
        return repo
    if settings.backend == "neo4j":
        if settings.neo4j is None:
            raise ValueError(
                "SAARTHI_BACKEND=neo4j requires NEO4J_URI and NEO4J_PASSWORD (see .env.example)."
            )
        from saarthi_mcp.neo4j_repo import Neo4jRepository  # lazy: only import driver when used

        return Neo4jRepository.from_settings(settings.neo4j)
    raise ValueError(f"Unknown SAARTHI_BACKEND={settings.backend!r} (use 'memory' or 'neo4j').")


def _result(model) -> ToolResult:
    return ToolResult(content=model.speech, structured_content=model.model_dump(mode="json"))


def build_server(repo: HouseholdRepository | None = None, orchestrator=None) -> FastMCP:
    """Create an independent server; handlers capture only this instance's dependencies.

    Instance isolation is not user authorization. Public access still requires auth.
    """
    settings = load_settings()
    if repo is None:
        repo = _default_repo(settings)
    if orchestrator is None and settings.agent_mode == "bedrock":
        try:
            from saarthi_agents import CareOrchestrator
        except ImportError as exc:
            raise RuntimeError("Install the local agents package; see agents/README.md.") from exc
        orchestrator = CareOrchestrator(
            model_id=settings.bedrock_model_id, region=settings.aws_region,
        )
    mcp = FastMCP(
        name="Saarthi",
        instructions=(
            "Saarthi is a care companion with memory for an elderly household. Use these tools to "
            "read and update medications, appointments, events, and family notifications, and to "
            "recall facts across sessions. Saarthi is NOT a medical device: never diagnose, dose, or "
            "give medical advice — surface saved facts and defer to a doctor."
        ),
    )

    def _resolve(person: str) -> Person:
        try:
            return repo.resolve_person(person)
        except PersonNotFoundError as exc:
            raise ToolError(str(exc)) from exc


    def _orchestrated(operation: str):
        """Preserve the MCP schema while delegating approved arguments to the agent layer."""
        def decorate(function):
            @wraps(function)
            def wrapped(*args, **kwargs):
                if orchestrator is None:
                    return function(*args, **kwargs)
                bound = signature(function).bind(*args, **kwargs)
                bound.apply_defaults()
                person = _resolve(bound.arguments["person"])

                def action():
                    return function(*args, **kwargs).structured_content

                def record(outcome):
                    repo.add_event(
                        person.id, type="agent_action",
                        detail=json.dumps(outcome, ensure_ascii=False), at=now_utc(),
                    )

                try:
                    result = orchestrator.execute(
                        operation=operation,
                        arguments=json.loads(json.dumps(dict(bound.arguments), default=str)),
                        action=action, record=record,
                    )
                except ToolError:
                    raise
                except Exception as exc:
                    completed = getattr(exc, "completed_result", None)
                    if isinstance(completed, dict) and isinstance(completed.get("speech"), str):
                        warning = "The record was saved, but its activity feed entry could not be saved."
                        result = {**completed, "speech": completed["speech"] + " " + warning,
                                  "warnings": [warning]}
                        return ToolResult(content=result["speech"], structured_content=result)
                    raise ToolError(
                        "The agent could not complete this request. Check the recorded action "
                        "status before retrying."
                    ) from exc
                return ToolResult(content=result["speech"], structured_content=result)
            return wrapped
        return decorate


    # --------------------------------------------------------------------------- tools

    @mcp.tool(
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        output_schema=MemoryGraph.model_json_schema(),
    )
    def get_memory_graph(
        person: str, limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 30,
    ) -> ToolResult:
        """Read saved record nodes and edges for one person; limit applies to each category.

        Includes saved medications, upcoming appointment records and recent events.
        Truncation is explicit. This view does not infer wellbeing or confirm delivery.
        """
        return _result(memory_graph(repo, _resolve(person), limit=limit))


    @mcp.tool
    def get_household_summary() -> ToolResult:
        """Current state for the household's elder: meds, next appointments, recent events, adherence."""
        try:
            elder = repo.primary_elder()
        except PersonNotFoundError as exc:
            raise ToolError("No household is configured. Import your household records before using this tool.") from exc
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
    @_orchestrated("get_medication_schedule")
    def get_medication_schedule(person: str) -> ToolResult:
        """List a person's medications, doses, and daily schedule."""
        p = _resolve(person)
        meds = repo.medications_for(p.id)
        if meds:
            lines = ", ".join(f"{m.name} {m.dose} at {'/'.join(m.schedule)}" for m in meds)
            speech = f"{p.name} takes: {lines}."
        else:
            speech = f"No medications are on record for {p.name}."
        return _result(MedicationSchedule(person=p, medications=meds, speech=speech))


    @mcp.tool
    @_orchestrated("log_dose")
    def log_dose(
        person: str, med: str, taken: bool, at: datetime | None = None
    ) -> ToolResult:
        """Record that a dose was taken (or missed). Guards against double-logging the same dose."""
        p = _resolve(person)
        when = ensure_aware(at) if at else now_utc()
        status = DoseStatus.taken if taken else DoseStatus.missed
        log, already = repo.add_dose(p.id, med, status, when)
        if already:
            speech = f"{med} was already logged as {status.value} for {p.name} just now — no change."
        else:
            speech = f"Logged: {p.name} {status.value} {med} at {when.astimezone():%I:%M %p}.".replace(
                " 0", " "
            )
        return _result(DoseLogResult(person=p, dose=log, already_logged=already, speech=speech))


    @mcp.tool
    @_orchestrated("book_appointment")
    def book_appointment(person: str, kind: str, when: datetime) -> ToolResult:
        """Save an appointment record. This does not book with a provider or external calendar."""
        p = _resolve(person)
        appt = repo.add_appointment(p.id, kind, ensure_aware(when))
        speech = (
            f"Recorded {kind} for {p.name} on {ensure_aware(appt.when).astimezone():%A %b %d, %I:%M %p}. "
            "No booking request has been sent to a provider or calendar."
        )
        return _result(AppointmentResult(person=p, appointment=appt, speech=speech))


    @mcp.tool
    @_orchestrated("list_appointments")
    def list_appointments(person: str) -> ToolResult:
        """List a person's upcoming appointments."""
        p = _resolve(person)
        appts = repo.upcoming_appointments(p.id, limit=10)
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
        contacts = repo.family_contacts()
        channels: list[str] = []
        for c in contacts:
            if c.phone:
                channels.append(f"sms:{c.phone}")
            if c.email:
                channels.append(f"email:{c.email}")
        repo.add_event(
            p.id, type="notify", detail=f"[{urgency.value}] {message}", at=now_utc()
        )
        names = ", ".join(c.name for c in contacts) or "the family"
        speech = f"Recorded a {urgency.value} message about {p.name} for {names}. No SMS or email has been sent."
        return _result(
            NotifyResult(
                person=p, delivered_to=[], candidate_channels=channels,
                urgency=urgency, message=message, speech=speech
            )
        )


    @mcp.tool
    def record_event(person: str, type: str, detail: str) -> ToolResult:
        """Write an event (visit, call, note, anomaly, …) into the household memory."""
        p = _resolve(person)
        event = repo.add_event(p.id, type=type, detail=detail, at=now_utc())
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
    @_orchestrated("query_memory")
    def query_memory(person: str, question: str) -> ToolResult:
        """Answer a question from the household memory (cross-session recall). Never gives medical advice."""
        p = _resolve(person)
        q = (question or "").lower()
        if any(m in q for m in _ADVICE_MARKERS):
            answer, events = repo.query_memory(p.id, question)
            safe = (
                "I can share what's on record, but I can't give medical advice - please talk to the "
                f"doctor. Here's what I have: {answer}"
            )
            return _result(
                MemoryAnswer(
                    person=p, question=question, answer=safe, supporting_events=events, speech=safe
                )
            )
        answer, events = repo.query_memory(p.id, question)
        return _result(
            MemoryAnswer(
                person=p, question=question, answer=answer, supporting_events=events, speech=answer
            )
        )


    @mcp.tool
    def check_in(person: str) -> ToolResult:
        """Watch-agent entry point: is the person OK today? Flags missed doses and low supply."""
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


    return mcp


def run() -> None:
    """Entry point: serve over Streamable HTTP (AGENTS.md section 5)."""
    settings = load_settings()
    server = build_server()
    server.run(transport="http", host=settings.host, port=settings.port, path=settings.path)
