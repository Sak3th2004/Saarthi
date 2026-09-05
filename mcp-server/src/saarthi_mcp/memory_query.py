"""The cross-session recall heuristic — shared by every repository backend.

Kept pure (takes already-fetched data, returns an answer) so the in-memory and Neo4j backends
give identical answers. Week 2 keeps this a keyword heuristic over real graph data; a later pass
can add graph-native reasoning without changing the tool contract.
"""

from __future__ import annotations

import re

from saarthi_mcp.models import DoseLog, DoseStatus, Event
from saarthi_mcp.timeutil import ensure_aware

MED_WORDS = ("pill", "dose", "medication", "meds", "take", "took", "taken")
_VERB = {DoseStatus.taken: "took", DoseStatus.missed: "missed", DoseStatus.skipped: "skipped"}


def _time_window(q: str) -> tuple[int, int] | None:
    if "evening" in q or "night" in q:
        return (17, 23)
    if "morning" in q:
        return (4, 12)
    if "afternoon" in q:
        return (12, 17)
    return None


def answer_question(
    person_name: str,
    question: str,
    recent_dose_logs: list[DoseLog],
    recent_events: list[Event],
) -> tuple[str, list[Event]]:
    """Answer a recall question from pre-fetched dose logs (recent, desc) and events (recent, desc)."""
    q = (question or "").lower()

    if any(w in q for w in MED_WORDS):
        logs = list(recent_dose_logs)
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
                if e.type == "dose" and latest.med.lower() in e.detail.lower()
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
