# Saarthi — care companion with memory

Saarthi turns a household care notebook into a **companion with cross-session memory**: medications, appointments, people, and daily patterns — coordinated by agents and visible to family.

**Hackathon:** Amazon Build, Ship, Shape — Alexa+ (self-hosted MCP) + AWS Builder + Open Source.  
**Demo path:** caregiver family notebook (Cognito) + live MCP tools. Alexa+ Preview is optional; judges see the same MCP server via Inspector or the notebook.

Spec: [`AGENTS.md`](./AGENTS.md) · Friction log: [`FRICTION_LOG.md`](./FRICTION_LOG.md)

---

## What works today

| Surface | Capability |
|---|---|
| **MCP server** | Streamable HTTP, spec 2025-11-25+ — meds, doses, appointments, memory query, check-in, family notify |
| **Memory** | Neo4j Aura household graph (or in-memory for local smoke tests) |
| **Family notebook** | React dashboard — live memory graph, adherence, timeline, Cognito sign-in |
| **Calendar** | Google Calendar connect/load/write after Cognito (explicit caregiver action) |
| **Family email** | SES review → confirm for messages and **weekly digests** (no auto-send) |
| **Watch** | `check_in` flags missed doses, low supply (≤10), and stale activity after a local grace window (`Asia/Calcutta` 08:00–21:00, 2h grace by default) |
| **Agents** | Strands orchestrator → MedGuardian / Appointments / FamilyLoop / Watch on Bedrock (opt-in) |

**Not a medical device.** Tracks saved records and escalates to people; never diagnoses, doses, or invents wellbeing from silence.

---

## Quick start (judges)

Requires **Python 3.11+**, Node 20+, and (for the live demo) AWS Cognito + Neo4j + optional SES/Bedrock credentials in `mcp-server/.env` (see [`.env.example`](./.env.example)).

```bash
python -m pip install --upgrade pip
python -m pip install -c constraints.txt -e ./agents -e "./mcp-server[dev,agents]"
cd dashboard && npm ci && cd ..
```

**MCP server**

```bash
cd mcp-server
python -m saarthi_mcp          # http://127.0.0.1:8080/mcp
pytest -q
```

**Inspector:** `npx @modelcontextprotocol/inspector` → Streamable HTTP → `http://127.0.0.1:8080/mcp`

**Dashboard** (use `http://localhost:5173`, not `127.0.0.1`, for Cognito)

```bash
cd dashboard
npm run dev
```

Vite proxies `/household/*` and `/mcp` to the server on port 8080.

**Offline tests (no Bedrock spend)**

```powershell
$env:SAARTHI_AGENTS = 'off'
.\mcp-server\.venv\Scripts\python.exe -m pytest -c mcp-server/pyproject.toml mcp-server/tests agents/tests -q
cd dashboard; npm test -- --run
```

---

## Architecture

```
Caregiver notebook / MCP Inspector / Alexa+
        │  MCP · Streamable HTTP
        ▼
Saarthi MCP Server (FastMCP) ──▶ Orchestrator (Strands, Bedrock)
                                      ├ MedGuardian  ├ Appointments
                                      ├ FamilyLoop   └ Watch
                                      ▼
                         Neo4j household graph (+ optional AgentCore Memory)
                                      ▲
                         Family Dashboard (React · Cognito)
```

| Path | Role |
|---|---|
| [`/mcp-server`](./mcp-server) | FastMCP tools + Cognito household routes |
| [`/agents`](./agents) | Strands orchestrator + specialists |
| [`/memory`](./memory) | Neo4j schema and persistence notes |
| [`/dashboard`](./dashboard) | Family notebook UI |
| [`/infra`](./infra) | Cognito, hosting scaffolding (Amplify / App Runner) |

---

## Bedrock agents (optional)

```powershell
$env:SAARTHI_AGENTS = 'bedrock'
$env:AWS_REGION = 'us-east-1'
$env:BEDROCK_MODEL_ID = 'us.amazon.nova-pro-v1:0'   # strongest available on this account
.\mcp-server\.venv\Scripts\python.exe -m saarthi_mcp
```

`SAARTHI_AGENTS=off` uses direct repository handlers (CI / offline). A failed model call never invents a successful write.

---

## Safety

- No diagnosis, dosing advice, or autonomous medical action.
- Anomaly / missed check-in → escalate to family (`notify_family` / reviewed SES).
- Email and calendar writes require explicit caregiver confirmation.
- Medication and personal data stay behind Cognito; least privilege for SES/Calendar.

## License

[MIT](./LICENSE).
