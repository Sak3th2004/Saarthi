"""The cross-session recall heuristic — shared by every repository backend.

Kept pure (takes already-fetched data, returns an answer) so the in-memory and Neo4j backends
give identical answers. Week 2 keeps this a keyword heuristic over real graph data; a later pass
can add graph-native reasoning without changing the tool contract.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from saarthi_mcp.models import DoseLog, DoseStatus, Event
from saarthi_mcp.timeutil import ensure_aware, now_utc

MED_WORDS = {
    "pill", "pills", "dose", "doses", "medication", "medications", "meds",
    "medicine", "medicines", "take", "took", "taken", "missed", "skipped",
}
# Only these simple generic questions may fall back to the latest dose. Unrecognized
# terms could be an unknown medication name, so ask for clarification instead.
_RECALL_WORDS = MED_WORDS | set(
    "a an the did does has have had he she his her their they i my we our dad father "
    "appa mom mother mum parent s when what was were is are about any last "
    "latest recent record records logged log of for on in at this morning afternoon "
    "evening night today yesterday yet already ever status please tell me show".split()
)
_VERB = {DoseStatus.taken: "took", DoseStatus.missed: "missed", DoseStatus.skipped: "skipped"}

# These are retrieval controls, not care rules. Internal execution metadata is
# retained in the action feed but must never masquerade as household evidence.
INTERNAL_EVENT_TYPES = {"agent_action", "household_setup"}
_SEARCH_STOP_WORDS = set(
    "did can and for the his her our had has was are what when where which who whom whose does have been were would could should "
    "about this that these those with from into there their please tell show "
    "latest recent record records memory happened".split()
) | {"today", "yesterday", "morning", "afternoon", "evening"}


def event_search_terms(question: str) -> list[str]:
    """Bound and normalize literal search terms; never accept database query syntax."""
    words = re.findall(r"[^\W_]+", (question or "").lower(), flags=re.UNICODE)
    return list(dict.fromkeys(
        word for word in words if 3 <= len(word) <= 80 and word not in _SEARCH_STOP_WORDS
    ))[:24]


def rank_events(question: str, events: list[Event], limit: int = 50) -> list[Event]:
    """Rank saved evidence by distinct matching terms, then observation time.

    A retrieval score is relevance, never a probability that a care fact is true.
    The Neo4j candidate query uses the same ordering before its result limit.
    """
    terms = event_search_terms(question)
    scored = []
    seen = set()
    for event in events:
        identity = (event.type, event.detail, ensure_aware(event.at))
        if event.type in INTERNAL_EVENT_TYPES or identity in seen:
            continue
        seen.add(identity)
        haystack = (event.detail + " " + event.type).lower()
        score = sum(term in haystack for term in terms)
        if score:
            scored.append((score, ensure_aware(event.at), event.type, event.detail, event))
    scored.sort(key=lambda row: row[:4], reverse=True)
    return [row[4] for row in scored[:limit]]


def _name_pattern(name: str) -> str:
    return r"(?<![\w-])" + re.escape(name.strip()) + r"(?![\w-])"


_DAYPARTS = {"morning": (4, 12), "afternoon": (12, 17), "evening": (17, 24)}
_UNSUPPORTED_TIME = re.compile(
    r"\b(?:tomorrow|tonight|night|ago|earlier|later|before|after|since|until|between|"
    r"now|noon|midnight|weekend|weekends|weekday|weekdays|fortnight|lunchtime|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b"
    r"|\b(?:last|next|past|previous|this)\s+(?:day|week|month|year|hour|minute)s?\b"
    r"|\b(?:last|previous|next)\s+(?:morning|afternoon|evening)\b"
    r"|\b\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,4})?\b"
    r"|\b\d+\s*(?:days?|weeks?|months?|years?|hours?|minutes?|am|pm)\b"
    r"|\b\d{1,2}:\d{2}\b"
    r"|\b(?:\d{1,2}(?:st|nd|rd|th)|(?:19|20)\d{2})\b"
)


def _recall_clock(question: str, time_zone: str | None, current: datetime):
    """Parse only supported date restrictions; never quietly discard another range."""
    words = set(re.findall(r"\w+", question))
    parts = words & _DAYPARTS.keys()
    relative = bool(words & {"today", "yesterday"} or parts)
    if (_UNSUPPORTED_TIME.search(question) or len(parts) > 1
            or {"today", "yesterday"} <= words):
        return None, None, None, (
            "I can't reliably apply that time range. Please ask about today, yesterday, "
            "or one morning, afternoon, or evening, and provide your IANA time zone."
        )
    zone = timezone.utc
    if time_zone is not None:
        try:
            zone = ZoneInfo(time_zone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            return None, None, None, "Please provide a valid IANA time zone, such as Asia/Kolkata or UTC."
    elif relative:
        return None, None, None, "Please provide your IANA time zone, such as Asia/Kolkata or UTC, to check that time."
    day = None
    if relative:
        day = current.astimezone(zone).date()
        if "yesterday" in words:
            day -= timedelta(days=1)
    window = _DAYPARTS[next(iter(parts))] if parts else None
    return zone, day, window, None


def answer_question(
    person_name: str,
    question: str,
    recent_dose_logs: list[DoseLog],
    recent_events: list[Event],
    medication_names: list[str] | None = None,
    *,
    time_zone: str | None = None,
    now: datetime | None = None,
) -> tuple[str, list[Event]]:
    """Recall observed records with explicit temporal bounds, independent of server timezone.

    Dayparts mean today's local morning (04–12), afternoon (12–17), or evening
    (17–24), unless yesterday is specified. Unsupported ranges ask for clarification.
    """
    q = (question or "").lower()
    current = ensure_aware(now) if now is not None else now_utc()
    # Remove saved names before detecting dates: a person or medication can share
    # a month name, and is not itself a time restriction.
    names = {name.strip().lower() for name in (medication_names or []) if name.strip()}
    names.update(d.med.strip().lower() for d in recent_dose_logs if d.med.strip())
    temporal_question = q
    for saved_name in sorted(names | {person_name.lower()}, key=len, reverse=True):
        if saved_name.strip():
            temporal_question = re.sub(_name_pattern(saved_name), " ", temporal_question)
    zone, day, window, clarification = _recall_clock(temporal_question, time_zone, current)
    if clarification:
        return clarification, []

    def in_scope(at: datetime) -> bool:
        instant = ensure_aware(at)
        if instant > current:
            return False
        local = instant.astimezone(zone)
        return ((day is None or local.date() == day)
                and (window is None or window[0] <= local.hour < window[1]))

    recent_dose_logs = sorted(
        (dose for dose in recent_dose_logs if in_scope(dose.at)),
        key=lambda dose: ensure_aware(dose.at), reverse=True,
    )
    # Recent and relevance-ranked candidates can contain the same stored fact.
    recent_events = list({
        (event.type, event.detail, ensure_aware(event.at)): event for event in recent_events
        if in_scope(event.at)
    }.values())

    matched_names = {name for name in names if re.search(_name_pattern(name), q)}
    remainder = q
    for name in sorted(matched_names, key=len, reverse=True):
        remainder = re.sub(_name_pattern(name), " ", remainder)
    words = set(re.findall(r"\w+", q))
    remaining_words = set(re.findall(r"\w+", remainder))
    allowed_words = _RECALL_WORDS | set(re.findall(r"\w+", person_name.lower()))

    if words & MED_WORDS or (matched_names and remaining_words <= allowed_words):
        if len(matched_names) > 1 or not remaining_words <= allowed_words:
            return "Please ask about one medication using its saved name, or about the latest dose.", []
        logs = list(recent_dose_logs)
        if matched_names:
            name = next(iter(matched_names))
            logs = [d for d in logs if d.med.strip().lower() == name]
        if logs:
            latest = logs[0]
            when = ensure_aware(latest.at).astimezone(zone).isoformat(timespec="minutes")
            when += f" ({time_zone or 'UTC'})"
            answer = f"{person_name} {_VERB[latest.status]} {latest.med} on {when}."
            supporting = [
                e
                for e in recent_events
                if e.type == "dose"
                and ensure_aware(e.at) == ensure_aware(latest.at)
                and re.search(_name_pattern(latest.med), e.detail, flags=re.IGNORECASE)
            ][:3]
            return answer, supporting
        return f"I don't have a dose record matching that for {person_name} yet.", []

    ranked = rank_events(question, recent_events, limit=3)
    if ranked:
        top = ranked[0]
        when = ensure_aware(top.at).astimezone(zone).isoformat(timespec="minutes")
        when += f" ({time_zone or 'UTC'})"
        return f"On {when}: {top.detail}", ranked
    return f"I don't have anything on record about that for {person_name} yet.", []
