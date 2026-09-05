# Saarthi MCP server

The self-hosted **MCP server** that is Saarthi's Alexa+ integration surface — FastMCP over
**Streamable HTTP**, MCP spec **2025-11-25+** (the live handshake negotiates a newer version and
is asserted in tests). Implements the tool contract in [`AGENTS.md §5`](../AGENTS.md).

## Tools (AGENTS.md §5)

| Tool | Purpose |
|---|---|
| `get_household_summary()` | Elder's meds, next appointments, recent events, 7-day adherence |
| `get_medication_schedule(person)` | A person's meds, doses, daily schedule |
| `log_dose(person, med, taken, at?)` | Record a dose taken/missed (double-log guarded) |
| `book_appointment(person, kind, when)` | Book an appointment |
| `list_appointments(person)` | Upcoming appointments |
| `notify_family(person, message, urgency)` | Route a message to family (live SMS/email in Week 4) |
| `record_event(person, type, detail)` | Write an event into memory |
| `query_memory(person, question)` | Cross-session recall; never gives medical advice |
| `check_in(person)` | Watch entry point: missed doses / low supply |

Each tool returns **structured content** (dashboard renders it) **plus a `speech` line** (Alexa+
speaks it).

## Run

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash;  .venv/bin/activate elsewhere
pip install -e ".[dev]"
python -m saarthi_mcp              # http://127.0.0.1:8080/mcp
pytest -q
```

## Inspect

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP   URL: http://127.0.0.1:8080/mcp
```

## Architecture

Tools depend only on the `HouseholdRepository` interface (`repository.py`). Week 1 ships
`InMemoryRepository` (seeded sample household); Week 2 adds a `Neo4jRepository` implementing the
same interface — the tool contract does not change. Config is read from the environment
(`config.py`, see [`../.env.example`](../.env.example)); no secrets in code.
