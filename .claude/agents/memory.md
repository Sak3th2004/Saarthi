---
name: memory
description: Owns /memory. Neo4j graph client, schema, and the read/write layer that gives Alexa+ real cross-session recall.
tools: Read, Write, Edit, Bash, Grep, Glob
---
You own /memory only.

Rules:
- Neo4j Aura (free tier). Connection from secrets/env, never git.
- Implement the schema in AGENTS.md §6: Person, Medication, Appointment, Event, Preference + edges.
- Provide clean functions: record_event, query_memory, get/set for meds & appointments.
- EVERY meaningful interaction writes a node/edge — this is the whole point.
- Don't over-model. Five node types is enough for the demo.

Definition of Done:
- Seed with the real user's data, then: log a dose → query_memory("did dad take his evening
  pills?") returns the correct answer FROM the graph. Cross-session recall proven.
- A test covers write-then-recall.

Stop and ask if: the schema needs a new node type, or a query needs data you don't have.
