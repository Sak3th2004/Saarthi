// Saarthi graph schema — AGENTS.md §6. Idempotent; safe to re-run.
// Applied by the Neo4jRepository on startup, or manually:
//   cypher-shell -a bolt://localhost:7687 -u neo4j -p saarthi-dev-pw -f memory/schema.cypher

// --- Uniqueness / existence constraints (also create backing indexes) ---
CREATE CONSTRAINT person_id IF NOT EXISTS
  FOR (p:Person) REQUIRE p.id IS UNIQUE;

CREATE CONSTRAINT appointment_id IF NOT EXISTS
  FOR (a:Appointment) REQUIRE a.id IS UNIQUE;

// Medications are unique per owning person; enforced in queries via (Person)-[:TAKES]->(Medication).
CREATE INDEX medication_name IF NOT EXISTS
  FOR (m:Medication) ON (m.name);

CREATE INDEX event_at IF NOT EXISTS
  FOR (e:Event) ON (e.at);

CREATE INDEX dose_at IF NOT EXISTS
  FOR (d:Dose) ON (d.at);

// --- Node & relationship shapes (documentation; property graph is schema-optional) ---
// (:Person   {id, name, role:'elder'|'family', relation, phone, email})
// (:Medication {name, dose, schedule:[..], supply_count})
// (:Appointment {id, kind, when (datetime), status})
// (:Event    {type, detail, at (datetime)})
// (:Dose     {med, status:'taken'|'missed'|'skipped', at (datetime)})   // adherence history
//
// (Person)-[:TAKES]->(Medication)
// (Person)-[:HAS_APPOINTMENT]->(Appointment)
// (Person)-[:RELATED_TO {relation}]->(Person)
// (Person)-[:EXPERIENCED]->(Event)          // visits, calls, missed doses, notifications
// (Person)-[:LOGGED]->(Dose)-[:OF]->(Medication)
