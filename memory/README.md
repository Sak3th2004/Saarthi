# /memory — Neo4j household graph (the differentiator)

Owner: the `memory` subagent. Built in **Week 2** (see `SPRINT_PLAN.md`).

This package will hold the Neo4j client, the graph schema (`AGENTS.md §6`: Person, Medication,
Appointment, Event, Preference + edges), and a clean read/write layer:
`record_event`, `query_memory`, meds & appointment get/set.

Until then, the MCP server runs on an in-memory backend that implements the same
`HouseholdRepository` interface (`mcp-server/src/saarthi_mcp/repository.py`). Week 2 provides a
`Neo4jRepository` implementing that interface — the MCP tool contract does not change.

## Local dev (real Neo4j, no cloud signup)

```bash
docker compose -f memory/docker-compose.yml up -d          # Neo4j at bolt://localhost:7687
cypher-shell -a bolt://localhost:7687 -u neo4j -p saarthi-dev-pw -f memory/schema.cypher
```

Then point the server at it:

```bash
# mcp-server/.env
SAARTHI_BACKEND=neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=saarthi-dev-pw
```

## Neo4j Aura (live/deployed demo)

Same code, different connection: set `NEO4J_URI=neo4j+s://<id>.databases.neo4j.io` plus the Aura
username/password in `.env` (gitignored). Nothing else changes — Bolt is identical.
