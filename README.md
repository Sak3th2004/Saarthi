# Saarthi — a care companion with memory for Alexa+

> Saarthi turns Alexa+ from a stateless Q&A box into a **care companion with memory** — an
> agentic system that helps an aging parent live independently by remembering meds,
> appointments, people, and daily patterns *across sessions*, coordinating a set of
> autonomous agents, and keeping the family in the loop.

**Hackathon:** Amazon "Build, Ship, Shape" — Alexa+ track + AWS Builder + Open Source minis.
See [`AGENTS.md`](./AGENTS.md) for the full spec, [`SPRINT_PLAN.md`](./SPRINT_PLAN.md) for the
schedule, and [`FRICTION_LOG.md`](./FRICTION_LOG.md) for the build friction log.

---

## Architecture (one glance)

```
Alexa+  ──MCP (Streamable HTTP, spec 2025-11-25+)──▶  Saarthi MCP Server (FastMCP)
                                                          │
                                                          ▼
                                            Orchestrator Agent (Strands, Bedrock)
                                     ┌──────┬──────┬───────┬──────┬──────────┐
                                  MedGuardian Appt FamilyLoop Watch Essentials
                                     └──────┴──────┴───────┴──────┴──────────┘
                                                          ▼
                              MEMORY:  Neo4j (household graph) + AgentCore Memory (episodic)
                                                          ▲
                                          Family Dashboard (React) — renders the graph live
```

---

## Repo layout

| Path | What lives here |
|---|---|
| [`/mcp-server`](./mcp-server) | Self-hosted MCP server (FastMCP, Streamable HTTP) + the tool contract (§5) |
| [`/memory`](./memory) | Neo4j client, graph schema, read/write layer (the differentiator) |
| [`/agents`](./agents) | Strands orchestrator + sub-agents (Bedrock/Claude) |
| [`/dashboard`](./dashboard) | React family dashboard (live graph, adherence charts, action feed) |
| [`/infra`](./infra) | IaC — AgentCore, Cognito, SNS/SES, hosting |
| [`/docs`](./docs) | Architecture diagram, demo script, submission answers |

---

## Quick start (MCP server — Week 1)

Requires **Python 3.11+**.

```bash
cd mcp-server
python -m venv .venv
# Windows (Git Bash):
source .venv/Scripts/activate
# macOS/Linux:
# source .venv/bin/activate
pip install -e ".[dev]"

# Run the server over Streamable HTTP:
python -m saarthi_mcp            # serves at http://127.0.0.1:8080/mcp

# Run the tests:
pytest
```

### Poke it with the MCP Inspector

```bash
npx @modelcontextprotocol/inspector
# In the Inspector UI:
#   Transport: Streamable HTTP
#   URL:       http://127.0.0.1:8080/mcp
# Then call get_household_summary, log_dose, query_memory, ...
```

The server currently runs on an **in-memory stub backend** (`SAARTHI_BACKEND=memory`) seeded
with sample household data so every tool in [`AGENTS.md §5`](./AGENTS.md) is callable today.
Week 2 swaps the backend for the Neo4j graph without changing the tool contract.

---

## Safety & guardrails

Saarthi is **not a medical device**. It tracks adherence and coordinates people; it does not
diagnose, dose, or give medical advice. Health questions surface saved facts + "talk to your
doctor." Anomalies escalate to a human. Purchasing requires explicit confirmation. See
[`AGENTS.md §11`](./AGENTS.md).

## License

[MIT](./LICENSE).
