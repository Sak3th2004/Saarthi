"""A bounded visualization of saved records, with no inferred care relationships.

Node IDs are deterministic view identities, not database IDs or authorization tokens.
Identical records collapse into one visual node. A changed record gets a new view ID.
Reads of different collections are not a transactionally consistent database snapshot.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, JsonValue

from saarthi_mcp.models import Person
from saarthi_mcp.repository import HouseholdRepository
from saarthi_mcp.timeutil import now_utc


class GraphNode(BaseModel):
    id: str
    kind: Literal["person", "medication", "appointment", "event"]
    label: str
    record: dict[str, JsonValue]


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: Literal["TAKES", "HAS_APPOINTMENT", "EXPERIENCED"]


class MemoryGraph(BaseModel):
    schema_version: Literal[1] = 1
    person_id: str
    generated_at: datetime
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: dict[str, bool]
    speech: str


def _identity(owner: str, kind: str, record: dict) -> str:
    canonical = json.dumps([owner, kind, record], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return kind + ":" + sha256(canonical.encode("utf-8")).hexdigest()


def memory_graph(repo: HouseholdRepository, person: Person, limit: int = 30) -> MemoryGraph:
    """Project one person's records; limits apply separately to each record category.

    Only upcoming appointments are included. Internal events keep their saved type;
    they are execution/setup history, not evidence of an observed care outcome.
    Contact fields are omitted. Person scoping does not establish caller authorization.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Graph limit must be an integer between 1 and 100.")
    root = GraphNode(
        id=_identity(person.id, "person", {"id": person.id}), kind="person", label=person.name,
        record=person.model_dump(mode="json", include={"id", "name", "role"}),
    )
    nodes = {root.id: root}
    edges = {}
    truncated = {}
    collections = (
        ("medication", "TAKES", sorted(repo.medications_for(person.id), key=lambda m: (m.name, m.dose))),
        ("appointment", "HAS_APPOINTMENT", repo.upcoming_appointments(person.id, limit=limit + 1)),
        ("event", "EXPERIENCED", repo.recent_events(person.id, limit=limit + 1)),
    )
    for kind, relation, records in collections:
        truncated[kind] = len(records) > limit
        for saved in records[:limit]:
            record = saved.model_dump(mode="json")
            node_id = _identity(person.id, kind, record)
            label = record[{"medication": "name", "appointment": "kind", "event": "type"}[kind]]
            nodes[node_id] = GraphNode(id=node_id, kind=kind, label=label, record=record)
            edges[node_id] = GraphEdge(source=root.id, target=node_id, relation=relation)
    more = " Some record categories have more entries than this view displays." if any(truncated.values()) else ""
    return MemoryGraph(
        person_id=person.id, generated_at=now_utc(), nodes=list(nodes.values()), edges=list(edges.values()),
        truncated=truncated,
        speech=f"Showing {len(nodes) - 1} saved record nodes for {person.name}.{more}",
    )
