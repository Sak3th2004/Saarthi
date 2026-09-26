"""User-supplied household setup; no sample records and no generated care facts.

Validate locally: python -m saarthi_mcp.household PATH --check
Import once:     python -m saarthi_mcp.household PATH --apply

The import is an administrator command, not an unauthenticated MCP tool. Keep
personal JSON files outside git, for example in .local-artifacts/household.json.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from saarthi_mcp.models import Medication, Person, Role

Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,80}$")]


class SavedMedication(Medication):
    model_config = ConfigDict(extra="forbid")


class HouseholdMember(Person):
    model_config = ConfigDict(extra="forbid")
    id: Identifier
    name: Nonempty
    aliases: list[Nonempty] = Field(default_factory=list, max_length=20)
    medications: list[SavedMedication] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_medications(self):
        names = set()
        for med in self.medications:
            key = med.name.strip().casefold()
            if not key or not med.dose.strip() or key in names:
                raise ValueError("Medication names and saved doses must be nonempty; names must be unique per person.")
            names.add(key)
            for at in med.schedule:
                if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", at):
                    raise ValueError("Saved schedule times must use HH:MM (24-hour clock).")
            if med.supply_count is not None and med.supply_count < 0:
                raise ValueError("Saved supply cannot be negative.")
        return self


class Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_person: Identifier
    to_person: Identifier
    relation: Nonempty


class HouseholdDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_person_id: Identifier
    people: list[HouseholdMember] = Field(min_length=1, max_length=100)
    relationships: list[Relationship] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_references(self):
        by_id = {p.id: p for p in self.people}
        if len(by_id) != len(self.people):
            raise ValueError("Person IDs must be unique.")
        primary = by_id.get(self.primary_person_id)
        if primary is None or primary.role is not Role.elder:
            raise ValueError("primary_person_id must identify an elder in this file.")
        labels = {}
        for person in self.people:
            for value in [person.id, person.name, *person.aliases]:
                key = value.lower()
                if key in labels and labels[key] != person.id:
                    raise ValueError("Names, IDs and aliases must identify only one person.")
                labels[key] = person.id
        for rel in self.relationships:
            if rel.from_person not in by_id or rel.to_person not in by_id:
                raise ValueError("Relationship endpoints must be people in this file.")
            if rel.from_person == rel.to_person:
                raise ValueError("A family relationship must connect different people.")
        return self


def load_household(path: str | Path) -> HouseholdDefinition:
    path = Path(path)
    # Limit input before parsing; this is a setup file, not a bulk data pipeline.
    with path.open("rb") as source:
        data = source.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("Household setup files must be at most 1 MB.")
    return HouseholdDefinition.model_validate_json(data)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="Private JSON household file; never commit real personal data")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate only; no database connection")
    mode.add_argument("--apply", action="store_true", help="Create household records atomically in an empty Neo4j graph")
    mode.add_argument("--schema", action="store_true", help="Print the input JSON schema without reading settings or data")
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(HouseholdDefinition.model_json_schema(), indent=2))
        return
    if args.path is None:
        parser.error("A private household JSON path is required with --check or --apply.")
    try:
        definition = load_household(args.path)
    except ValidationError as exc:
        locations = [".".join(map(str, error["loc"])) or "household" for error in exc.errors()]
        raise SystemExit("Invalid household setup at: " + ", ".join(locations)) from None
    except (OSError, ValueError):
        raise SystemExit("Could not read a valid household setup file (maximum 1 MB).") from None
    if args.check:
        print(f"Valid household setup: {len(definition.people)} people. No records written.")
        return
    from saarthi_mcp.config import load_settings
    from saarthi_mcp.neo4j_repo import Neo4jRepository

    settings = load_settings()
    if settings.backend != "neo4j" or settings.neo4j is None:
        raise SystemExit("Import requires SAARTHI_BACKEND=neo4j and NEO4J_* settings.")
    repo = Neo4jRepository.from_settings(settings.neo4j)
    try:
        repo.import_household(definition)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    finally:
        repo.close()
    print(f"Imported {len(definition.people)} people with setup history. No messages sent.")


if __name__ == "__main__":
    main()
