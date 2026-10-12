"""Strict metadata-only household edits shared by the two repositories."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import unicodedata


class DirectoryConflict(ValueError):
    """The household changed after the caregiver reviewed this edit."""


def canonical_directory(directory: dict) -> dict:
    people = sorted(deepcopy(directory["people"]), key=lambda p: p["id"])
    relationships = sorted(deepcopy(directory["relationships"]),
                           key=lambda r: (r["from_person"], r["to_person"], r["relation"]))
    if len(people) > 100 or len(relationships) > 500:
        raise ValueError("Household directory exceeds the supported size; no records were changed.")
    return {"primary_person_id": directory["primary_person_id"],
            "people": people, "relationships": relationships}


def directory_revision(directory: dict) -> str:
    payload = json.dumps(canonical_directory(directory), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_SCHEDULE_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")

# Caregiver-facing copy for medication schedule facts (never medical advice).
CAREGIVER_MEDICATION_NOTICE = (
    "This is a caregiver-saved medication schedule fact for household recall. "
    "It is not medical advice, a prescription, or a dosing recommendation."
)


def validate_change(change: dict) -> dict:
    if not isinstance(change, dict):
        raise ValueError("A household change must be an object.")
    fields = {
        "add_person": {"id", "name", "role"},
        "rename_person": {"person_id", "name"},
        "set_relationship": {"from_person", "to_person", "relation"},
        "set_primary": {"person_id"},
        "set_person_email": {"person_id", "email"},
        "add_medication": {"person_id", "name", "dose", "schedule"},
    }
    action = change.get("kind")
    if not isinstance(action, str) or action not in fields:
        raise ValueError("Choose one supported household change with only its required fields.")
    required = fields[action] | {"kind"}
    extras = set(change) - required
    # Optional caregiver-tracked remaining doses — never invented; omit if unknown.
    if action == "add_medication":
        if extras - {"supply_count"}:
            raise ValueError("Choose one supported household change with only its required fields.")
    elif extras:
        raise ValueError("Choose one supported household change with only its required fields.")
    if set(change) < required:
        raise ValueError("Choose one supported household change with only its required fields.")
    result = {"kind": action}
    for key in fields[action]:
        value = change[key]
        if key == "schedule":
            if not isinstance(value, list) or len(value) > 24:
                raise ValueError("Medication schedule must be a list of at most 24 HH:MM times.")
            times: list[str] = []
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError("Medication schedule times must be nonempty HH:MM text.")
                item = item.strip()
                if not _SCHEDULE_TIME.fullmatch(item):
                    raise ValueError("Saved schedule times must use HH:MM (24-hour clock).")
                times.append(item)
            result[key] = times
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Household change fields must be nonempty text.")
        value = value.strip()
        if key in {"id", "person_id", "from_person", "to_person"}:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
                raise ValueError("Person IDs must use up to 80 letters, numbers, underscores or hyphens.")
        elif key == "role":
            if value not in {"elder", "family"}:
                raise ValueError("Person role must be elder or family.")
        elif key == "email":
            if len(value) > 200 or not _EMAIL.fullmatch(value):
                raise ValueError("Enter a valid family email address of at most 200 characters.")
        elif len(value) > 200 or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("Names and relationships must be plain text of at most 200 characters.")
        result[key] = value
    if action == "add_medication" and "supply_count" in change:
        supply = change["supply_count"]
        if supply is not None and (type(supply) is not int or not 0 <= supply <= 9999):
            raise ValueError("Remaining doses must be a whole number from 0 to 9999, or omitted.")
        result["supply_count"] = supply
    return result


def ensure_label_available(labels: list[tuple[str, str]], values: list[str], person_id: str) -> None:
    wanted = {value.strip().lower() for value in values}
    if any(label.strip().lower() in wanted and owner != person_id for label, owner in labels):
        raise ValueError("That name or ID already identifies another household member.")


def preview_change(directory: dict, change: dict) -> dict:
    change = validate_change(change)
    before = canonical_directory(directory)
    result = deepcopy(before)
    people = {p["id"]: p for p in result["people"]}
    action = change["kind"]
    labels = [(p[k], p["id"]) for p in people.values() for k in ("id", "name")]
    if action == "add_person":
        if change["id"] in people:
            raise ValueError("This person ID is already saved.")
        ensure_label_available(labels, [change["id"], change["name"]], change["id"])
        result["people"].append({**{k: change[k] for k in ("id", "name", "role")}, "email": None})
        if result["primary_person_id"] is None and change["role"] == "elder":
            result["primary_person_id"] = change["id"]
    elif action in {"rename_person", "set_primary", "set_person_email"}:
        person = people.get(change["person_id"])
        if person is None:
            raise ValueError("Choose a saved household member.")
        if action == "rename_person":
            ensure_label_available(labels, [change["name"]], person["id"])
            person["name"] = change["name"]
        elif action == "set_primary":
            if person["role"] != "elder":
                raise ValueError("The default notebook must belong to a person receiving care.")
            result["primary_person_id"] = person["id"]
        else:
            if person["role"] != "family":
                raise ValueError("Family notification email can only be saved for family members.")
            person["email"] = change["email"]
    elif action == "add_medication":
        if change["person_id"] not in people:
            raise ValueError("Choose a saved household member.")
        # Medication facts are care records, not directory metadata; revision stays the same.
        return before
    else:
        source, target = change["from_person"], change["to_person"]
        if source not in people or target not in people or source == target:
            raise ValueError("A relationship must connect two different saved household members.")
        result["relationships"] = [r for r in result["relationships"]
                                   if (r["from_person"], r["to_person"]) != (source, target)]
        result["relationships"].append({k: change[k] for k in ("from_person", "to_person", "relation")})
    result = canonical_directory(result)
    if result == before:
        raise ValueError("These household details are already saved.")
    return result


def medication_fact(change: dict) -> dict:
    """Normalized caregiver-saved medication payload (supply optional; never advice)."""
    fact = {
        "person_id": change["person_id"],
        "name": change["name"],
        "dose": change["dose"],
        "schedule": list(change["schedule"]),
        "supply_count": change.get("supply_count"),
    }
    return fact


def change_audit(before: dict, after: dict, change: dict) -> tuple[list[str], str]:
    """Record actual differences for directory metadata or caregiver-saved medication facts."""
    action = change["kind"]
    if action == "set_relationship":
        ids = [change["from_person"], change["to_person"]]
        old = [r for r in before["relationships"] if (r["from_person"], r["to_person"]) == tuple(ids)]
        new = [r for r in after["relationships"] if (r["from_person"], r["to_person"]) == tuple(ids)]
    elif action == "set_primary":
        old, new = before["primary_person_id"], after["primary_person_id"]
        ids = sorted({v for v in (old, new) if v is not None})
    elif action == "add_medication":
        ids = [change["person_id"]]
        old = None
        new = medication_fact(change)
    else:
        ids = [change.get("id", change.get("person_id"))]
        old = next((p for p in before["people"] if p["id"] == ids[0]), None)
        new = next(p for p in after["people"] if p["id"] == ids[0])
    return ids, json.dumps({"kind": action, "before": old, "after": new}, ensure_ascii=False, sort_keys=True)
