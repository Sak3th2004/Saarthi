"""Neo4j-backed household repository — the real cross-session memory graph (AGENTS.md §6).

Implements the same ``HouseholdRepository`` interface as ``InMemoryRepository`` using real Cypher,
so the MCP tool contract is unchanged. Works identically on local Docker Neo4j and Neo4j Aura
(Bolt is the same); only the connection URI/credentials differ.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from neo4j import Driver, GraphDatabase
from neo4j.time import DateTime as Neo4jDateTime

from saarthi_mcp.config import Neo4jSettings
from saarthi_mcp.memory_query import answer_question
from saarthi_mcp.models import (
    Appointment,
    DoseLog,
    DoseStatus,
    Event,
    Medication,
    Person,
    Role,
)
from saarthi_mcp.repository import PersonNotFoundError
from saarthi_mcp.timeutil import ensure_aware, now_utc

_CONSTRAINTS = (
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT appointment_id IF NOT EXISTS FOR (a:Appointment) REQUIRE a.id IS UNIQUE",
    "CREATE INDEX medication_name IF NOT EXISTS FOR (m:Medication) ON (m.name)",
    "CREATE INDEX event_at IF NOT EXISTS FOR (e:Event) ON (e.at)",
    "CREATE INDEX dose_at IF NOT EXISTS FOR (d:Dose) ON (d.at)",
)


def _dt(value) -> datetime:
    if isinstance(value, Neo4jDateTime):
        return ensure_aware(value.to_native())
    return ensure_aware(value)


class Neo4jRepository:
    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._db = database
        self._ensure_schema()

    @classmethod
    def from_settings(cls, s: Neo4jSettings) -> "Neo4jRepository":
        driver = GraphDatabase.driver(s.uri, auth=(s.username, s.password))
        return cls(driver, s.database)

    def close(self) -> None:
        self._driver.close()

    # -- low-level helpers ----------------------------------------------------

    def _read(self, cypher: str, **params):
        with self._driver.session(database=self._db) as s:
            return s.execute_read(lambda tx: list(tx.run(cypher, **params)))

    def _write(self, cypher: str, **params):
        with self._driver.session(database=self._db) as s:
            return s.execute_write(lambda tx: list(tx.run(cypher, **params)))

    def _ensure_schema(self) -> None:
        with self._driver.session(database=self._db) as s:
            for stmt in _CONSTRAINTS:
                s.run(stmt).consume()

    def _person(self, node) -> Person:
        return Person(
            id=node["id"],
            name=node["name"],
            role=Role(node["role"]),
            relation=node.get("relation"),
            phone=node.get("phone"),
            email=node.get("email"),
        )

    def _medication(self, node) -> Medication:
        return Medication(
            name=node["name"],
            dose=node["dose"],
            schedule=list(node.get("schedule") or []),
            supply_count=node.get("supply_count"),
        )

    def _appointment(self, node) -> Appointment:
        return Appointment(
            id=node["id"], kind=node["kind"], when=_dt(node["when"]), status=node["status"]
        )

    def _event(self, node) -> Event:
        return Event(type=node["type"], detail=node["detail"], at=_dt(node["at"]))

    def _dose(self, node) -> DoseLog:
        return DoseLog(med=node["med"], status=DoseStatus(node["status"]), at=_dt(node["at"]))

    # -- reads ----------------------------------------------------------------

    def resolve_person(self, person: str) -> Person:
        key = (person or "").strip().lower()
        recs = self._read(
            """
            MATCH (p:Person)
            WHERE toLower(p.id) = $key OR toLower(p.name) = $key
               OR $key IN [a IN coalesce(p.aliases, []) | toLower(a)]
            RETURN p LIMIT 1
            """,
            key=key,
        )
        if not recs:
            names = ", ".join(sorted(r["n"] for r in self._read("MATCH (p:Person) RETURN p.name AS n")))
            raise PersonNotFoundError(
                f"Unknown person {person!r}. Known household members: {names}."
            )
        return self._person(recs[0]["p"])

    def _person_by_id(self, person_id: str) -> Person:
        recs = self._read("MATCH (p:Person {id:$id}) RETURN p", id=person_id)
        if not recs:
            raise PersonNotFoundError(f"Unknown person id {person_id!r}.")
        return self._person(recs[0]["p"])

    def primary_elder(self) -> Person:
        recs = self._read(
            "MATCH (p:Person {role:'elder'}) RETURN p ORDER BY coalesce(p.primary,false) DESC LIMIT 1"
        )
        if not recs:
            raise PersonNotFoundError("No elder registered in the household.")
        return self._person(recs[0]["p"])

    def family_contacts(self) -> list[Person]:
        recs = self._read("MATCH (p:Person {role:'family'}) RETURN p ORDER BY p.name")
        return [self._person(r["p"]) for r in recs]

    def medications_for(self, person_id: str) -> list[Medication]:
        recs = self._read(
            "MATCH (:Person {id:$id})-[:TAKES]->(m:Medication) RETURN m ORDER BY m.name",
            id=person_id,
        )
        return [self._medication(r["m"]) for r in recs]

    def upcoming_appointments(self, person_id: str, limit: int = 5) -> list[Appointment]:
        recs = self._read(
            """
            MATCH (:Person {id:$id})-[:HAS_APPOINTMENT]->(a:Appointment)
            WHERE a.when >= $now
            RETURN a ORDER BY a.when ASC LIMIT $limit
            """,
            id=person_id,
            now=now_utc(),
            limit=limit,
        )
        return [self._appointment(r["a"]) for r in recs]

    def recent_events(self, person_id: str, limit: int = 10) -> list[Event]:
        recs = self._read(
            "MATCH (:Person {id:$id})-[:EXPERIENCED]->(e:Event) RETURN e ORDER BY e.at DESC LIMIT $limit",
            id=person_id,
            limit=limit,
        )
        return [self._event(r["e"]) for r in recs]

    def dose_logs(self, person_id: str, since: datetime | None = None) -> list[DoseLog]:
        recs = self._read(
            """
            MATCH (:Person {id:$id})-[:LOGGED]->(d:Dose)
            WHERE $since IS NULL OR d.at >= $since
            RETURN d ORDER BY d.at DESC
            """,
            id=person_id,
            since=ensure_aware(since) if since else None,
        )
        return [self._dose(r["d"]) for r in recs]

    def adherence(self, person_id: str, days: int = 7) -> float:
        recs = self._read(
            """
            MATCH (:Person {id:$id})-[:LOGGED]->(d:Dose)
            WHERE d.at >= $since AND d.status IN ['taken','missed']
            RETURN d.status AS status
            """,
            id=person_id,
            since=now_utc() - timedelta(days=days),
        )
        if not recs:
            return 1.0
        taken = sum(1 for r in recs if r["status"] == "taken")
        return round(taken / len(recs), 3)

    # -- writes ---------------------------------------------------------------

    def add_dose(
        self, person_id: str, med: str, status: DoseStatus, at: datetime
    ) -> tuple[DoseLog, bool]:
        at = ensure_aware(at)
        dup = self._read(
            """
            MATCH (:Person {id:$id})-[:LOGGED]->(d:Dose)
            WHERE toLower(d.med) = toLower($med) AND d.status = $status
              AND abs(duration.inSeconds(d.at, $at).seconds) <= 1800
            RETURN count(d) AS c
            """,
            id=person_id,
            med=med,
            status=status.value,
            at=at,
        )
        log = DoseLog(med=med, status=status, at=at)
        if dup and dup[0]["c"] > 0:
            return log, True
        detail = f"{med} {status.value} at {at.isoformat(timespec='minutes')}"
        self._write(
            """
            MATCH (p:Person {id:$id})
            CREATE (p)-[:LOGGED]->(d:Dose {med:$med, status:$status, at:$at})
            WITH p, d
            OPTIONAL MATCH (p)-[:TAKES]->(m:Medication) WHERE toLower(m.name) = toLower($med)
            FOREACH (_ IN CASE WHEN m IS NULL THEN [] ELSE [1] END | CREATE (d)-[:OF]->(m))
            CREATE (p)-[:EXPERIENCED]->(e:Event {type:'dose', detail:$detail, at:$at})
            """,
            id=person_id,
            med=med,
            status=status.value,
            at=at,
            detail=detail,
        )
        return log, False

    def add_appointment(self, person_id: str, kind: str, when: datetime) -> Appointment:
        when = ensure_aware(when)
        aid = f"appt-{uuid.uuid4().hex[:8]}"
        detail = f"{kind} on {when.isoformat(timespec='minutes')}"
        self._write(
            """
            MATCH (p:Person {id:$id})
            CREATE (p)-[:HAS_APPOINTMENT]->(a:Appointment {id:$aid, kind:$kind, when:$when, status:'scheduled'})
            CREATE (p)-[:EXPERIENCED]->(e:Event {type:'appointment_booked', detail:$detail, at:$now})
            """,
            id=person_id,
            aid=aid,
            kind=kind,
            when=when,
            detail=detail,
            now=now_utc(),
        )
        return Appointment(id=aid, kind=kind, when=when, status="scheduled")

    def add_event(self, person_id: str, type: str, detail: str, at: datetime) -> Event:
        at = ensure_aware(at)
        self._write(
            """
            MATCH (p:Person {id:$id})
            CREATE (p)-[:EXPERIENCED]->(e:Event {type:$type, detail:$detail, at:$at})
            """,
            id=person_id,
            type=type,
            detail=detail,
            at=at,
        )
        return Event(type=type, detail=detail, at=at)

    def query_memory(self, person_id: str, question: str) -> tuple[str, list[Event]]:
        person = self._person_by_id(person_id)
        recent_dose_logs = self.dose_logs(person_id, since=now_utc() - timedelta(days=2))
        recent_events = self.recent_events(person_id, limit=50)
        return answer_question(person.name, question, recent_dose_logs, recent_events)

    # -- seeding helpers (beyond the read/write interface) --------------------

    def wipe(self) -> None:
        """Delete all nodes/relationships. Destructive — dev/demo DB only."""
        self._write("MATCH (n) DETACH DELETE n")

    def upsert_person(
        self, person: Person, aliases: list[str] | None = None, primary: bool = False
    ) -> None:
        self._write(
            """
            MERGE (p:Person {id:$id})
            SET p.name=$name, p.role=$role, p.relation=$relation, p.phone=$phone,
                p.email=$email, p.aliases=$aliases, p.primary=$primary
            """,
            id=person.id,
            name=person.name,
            role=person.role.value,
            relation=person.relation,
            phone=person.phone,
            email=person.email,
            aliases=aliases or [],
            primary=primary,
        )

    def add_medication(self, person_id: str, med: Medication) -> None:
        self._write(
            """
            MATCH (p:Person {id:$id})
            MERGE (p)-[:TAKES]->(m:Medication {name:$name})
            SET m.dose=$dose, m.schedule=$schedule, m.supply_count=$supply
            """,
            id=person_id,
            name=med.name,
            dose=med.dose,
            schedule=med.schedule,
            supply=med.supply_count,
        )

    def relate(self, person_a_id: str, person_b_id: str, relation: str) -> None:
        self._write(
            """
            MATCH (a:Person {id:$a}), (b:Person {id:$b})
            MERGE (a)-[r:RELATED_TO]->(b) SET r.relation=$relation
            """,
            a=person_a_id,
            b=person_b_id,
            relation=relation,
        )


def seed_neo4j(repo: Neo4jRepository, wipe: bool = True) -> None:
    """Seed the sample household into Neo4j (mirrors repository.seeded_repository).

    Replace with the real demo user's data before the video (AGENTS.md §10). Destructive when
    ``wipe=True`` — intended for preparing the dev/demo database, not production data.
    """
    if wipe:
        repo.wipe()
    now = now_utc()

    repo.upsert_person(
        Person(id="elder-1", name="Ramesh", role=Role.elder),
        aliases=["dad", "appa", "father"],
        primary=True,
    )
    repo.upsert_person(
        Person(
            id="fam-1",
            name="Arjun",
            role=Role.family,
            relation="son",
            phone="+10000000000",
            email="family@example.com",
        ),
        aliases=["son"],
    )
    repo.relate("fam-1", "elder-1", "son")

    for med in (
        Medication(name="Metformin", dose="500mg", schedule=["08:00", "20:00"], supply_count=24),
        Medication(name="Amlodipine", dose="5mg", schedule=["08:00"], supply_count=30),
        Medication(name="Atorvastatin", dose="10mg", schedule=["21:00"], supply_count=8),
    ):
        repo.add_medication("elder-1", med)

    for day in range(7, 0, -1):
        d = now - timedelta(days=day)
        morning = d.replace(hour=8, minute=5, second=0, microsecond=0)
        evening = d.replace(hour=20, minute=10, second=0, microsecond=0)
        repo.add_dose("elder-1", "Metformin", DoseStatus.taken, morning)
        repo.add_dose("elder-1", "Amlodipine", DoseStatus.taken, morning)
        repo.add_dose(
            "elder-1",
            "Metformin",
            DoseStatus.missed if day == 1 else DoseStatus.taken,
            evening,
        )

    repo.add_appointment("elder-1", "Cardiology follow-up", now + timedelta(days=3, hours=2))
    repo.add_appointment("elder-1", "Physiotherapy", now + timedelta(days=8))

    repo.add_event(
        "elder-1",
        "call",
        "Physiotherapist Dr. Meera called to reschedule; said she would call back Friday.",
        now - timedelta(days=2, hours=3),
    )
    repo.add_event(
        "elder-1",
        "visit",
        "Arjun visited for dinner and refilled the pill organizer.",
        now - timedelta(days=1, hours=5),
    )
    repo.add_event(
        "elder-1",
        "missed_dose",
        "Metformin evening dose missed.",
        (now - timedelta(days=1)).replace(hour=20, minute=10, second=0, microsecond=0),
    )
