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


def validate_change(change: dict) -> dict:
    if not isinstance(change, dict):
        raise ValueError("A household change must be an object.")
    fields = {
        "add_person": {"id", "name", "role"},
        "rename_person": {"person_id", "name"},
        "set_relationship": {"from_person", "to_person", "relation"},
        "set_primary": {"person_id"},
    }
    action = change.get("kind")
    if not isinstance(action, str) or action not in fields or set(change) != fields[action] | {"kind"}:
        raise ValueError("Choose one supported household change with only its required fields.")
    result = {"kind": action}
    for key in fields[action]:
        value = change[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Household change fields must be nonempty text.")
        value = value.strip()
        if key in {"id", "person_id", "from_person", "to_person"}:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
                raise ValueError("Person IDs must use up to 80 letters, numbers, underscores or hyphens.")
        elif key == "role":
            if value not in {"elder", "family"}:
                raise ValueError("Person role must be elder or family.")
        elif len(value) > 200 or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("Names and relationships must be plain text of at most 200 characters.")
        result[key] = value
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
        result["people"].append({k: change[k] for k in ("id", "name", "role")})
        if result["primary_person_id"] is None and change["role"] == "elder":
            result["primary_person_id"] = change["id"]
    elif action in {"rename_person", "set_primary"}:
        person = people.get(change["person_id"])
        if person is None:
            raise ValueError("Choose a saved household member.")
        if action == "rename_person":
            ensure_label_available(labels, [change["name"]], person["id"])
            person["name"] = change["name"]
        else:
            if person["role"] != "elder":
                raise ValueError("The default notebook must belong to a person receiving care.")
            result["primary_person_id"] = person["id"]
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


def change_audit(before: dict, after: dict, change: dict) -> tuple[list[str], str]:
    """Record actual metadata differences, never claims about medications or invitations."""
    action = change["kind"]
    if action == "set_relationship":
        ids = [change["from_person"], change["to_person"]]
        old = [r for r in before["relationships"] if (r["from_person"], r["to_person"]) == tuple(ids)]
        new = [r for r in after["relationships"] if (r["from_person"], r["to_person"]) == tuple(ids)]
    elif action == "set_primary":
        old, new = before["primary_person_id"], after["primary_person_id"]
        ids = sorted({v for v in (old, new) if v is not None})
    else:
        ids = [change.get("id", change.get("person_id"))]
        old = next((p for p in before["people"] if p["id"] == ids[0]), None)
        new = next(p for p in after["people"] if p["id"] == ids[0])
    return ids, json.dumps({"kind": action, "before": old, "after": new}, ensure_ascii=False, sort_keys=True)
