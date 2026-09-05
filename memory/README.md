# /memory — Neo4j household graph (the differentiator)

Owner: the `memory` subagent. Built in **Week 2** (see `SPRINT_PLAN.md`).

This package will hold the Neo4j client, the graph schema (`AGENTS.md §6`: Person, Medication,
Appointment, Event, Preference + edges), and a clean read/write layer:
`record_event`, `query_memory`, meds & appointment get/set.

Until then, the MCP server runs on an in-memory backend that implements the same
`HouseholdRepository` interface (`mcp-server/src/saarthi_mcp/repository.py`). Week 2 provides a
`Neo4jRepository` implementing that interface — the MCP tool contract does not change.
