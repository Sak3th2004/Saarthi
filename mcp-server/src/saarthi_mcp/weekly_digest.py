"""Build a caregiver weekly digest from saved household records (not medical advice)."""

from __future__ import annotations

from datetime import timedelta

from saarthi_mcp.repository import HouseholdRepository, now_utc
from saarthi_mcp.timeutil import ensure_aware


def build_weekly_digest(repository: HouseholdRepository, person_id: str, *, days: int = 7) -> str:
    """Plain-text summary of the last ``days`` of saved events, doses, and appointments."""
    if type(days) is not int or not 1 <= days <= 31:
        raise ValueError("Digest window must be between 1 and 31 days.")
    person = repository.resolve_person(person_id)
    until = now_utc()
    since = until - timedelta(days=days)
    events = [
        event for event in repository.recent_events(person_id, limit=50)
        if ensure_aware(event.at) >= since
        and event.type not in {"agent_action", "household_setup"}
    ]
    events.sort(key=lambda event: ensure_aware(event.at), reverse=True)
    doses = [
        dose for dose in repository.dose_logs(person_id, since=since)
        if ensure_aware(dose.at) <= until
    ]
    taken = sum(1 for dose in doses if dose.status.value == "taken")
    missed = sum(1 for dose in doses if dose.status.value == "missed")
    low = [
        med for med in repository.medications_for(person_id)
        if med.supply_count is not None and med.supply_count <= 10
    ]
    upcoming = repository.upcoming_appointments(person_id, limit=5)

    lines = [
        f"Saarthi weekly household digest for {person.name}",
        f"Window: last {days} days (saved records only).",
        "",
        f"Dose logs in window: {taken} taken, {missed} missed (from saved entries only).",
    ]
    if low:
        lines.append("Low supply (≤10 remaining): " + ", ".join(
            f"{med.name} ({med.supply_count})" for med in low
        ))
    else:
        lines.append("Low supply: none recorded.")
    lines.append("")
    lines.append("Upcoming appointment records:")
    if upcoming:
        for appt in upcoming:
            when = ensure_aware(appt.when).isoformat(timespec="minutes")
            lines.append(f"- {appt.kind} at {when} (local record status: {appt.status})")
    else:
        lines.append("- None saved.")
    lines.append("")
    lines.append("Recent notes and events:")
    if events:
        for event in events[:20]:
            when = ensure_aware(event.at).isoformat(timespec="minutes")
            lines.append(f"- [{event.type}] {when}: {event.detail}")
    else:
        lines.append("- No caregiver notes in this window.")
    lines.extend([
        "",
        "This digest is assembled from household notebook records.",
        "It is not medical advice and does not confirm how someone is doing.",
    ])
    return "\n".join(lines)
