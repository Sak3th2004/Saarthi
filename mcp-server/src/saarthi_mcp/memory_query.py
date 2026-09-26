"""The cross-session recall heuristic — shared by every repository backend.

Kept pure (takes already-fetched data, returns an answer) so the in-memory and Neo4j backends
give identical answers. Week 2 keeps this a keyword heuristic over real graph data; a later pass
can add graph-native reasoning without changing the tool contract.
"""

from __future__ import annotations

import re

from saarthi_mcp.models import DoseLog, DoseStatus, Event
from saarthi_mcp.timeutil import ensure_aware

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


def _name_pattern(name: str) -> str:
    return r"(?<![\w-])" + re.escape(name.strip()) + r"(?![\w-])"


def _time_window(q: str) -> tuple[int, int] | None:
    words = set(re.findall(r"\w+", q))
    if words & {"evening", "night"}:
        return (17, 23)
    if "morning" in words:
        return (4, 12)
    if "afternoon" in words:
        return (12, 17)
    return None


def answer_question(
    person_name: str,
    question: str,
    recent_dose_logs: list[DoseLog],
    recent_events: list[Event],
    medication_names: list[str] | None = None,
) -> tuple[str, list[Event]]:
    """Answer a recall question from pre-fetched dose logs (recent, desc) and events (recent, desc)."""
    q = (question or "").lower()

    names = {name.strip().lower() for name in (medication_names or []) if name.strip()}
    names.update(d.med.strip().lower() for d in recent_dose_logs if d.med.strip())
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
        window = _time_window(q)
        if window is not None:
            lo, hi = window
            logs = [d for d in logs if lo <= ensure_aware(d.at).astimezone().hour < hi]
        if logs:
            latest = logs[0]
            when = ensure_aware(latest.at).astimezone().strftime("%A %I:%M %p").lstrip("0")
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

    # General recall: score each event by how many query terms it matches, then recency.
    # Tokenize on word characters so punctuation ("water?" -> "water") doesn't break matching.
    tokens = [tok for tok in re.findall(r"[a-z0-9]+", q) if len(tok) > 3]
    scored = []
    for e in recent_events:
        hay = (e.detail + " " + e.type).lower()
        score = sum(1 for tok in tokens if tok in hay)
        if score:
            scored.append((score, ensure_aware(e.at), e))
    if scored:
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        top = scored[0][2]
        when = ensure_aware(top.at).astimezone().strftime("%b %d").lstrip("0")
        return f"On {when}: {top.detail}", [t[2] for t in scored[:3]]
    return f"I don't have anything on record about that for {person_name} yet.", []
