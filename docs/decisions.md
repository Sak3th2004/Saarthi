# Decisions log — Saarthi

Locked decisions (the four Week 1 "unknowns" from `SPRINT_PLAN.md` + others). Update as things
change; this feeds the submission's "what changed during the window" answer.

## Week 1 verifications

| # | Unknown | Decision (2026-09-05) | Status |
|---|---|---|---|
| 1 | Alexa+ / MCP access | **Build to the fallback**: demo the MCP server via MCP Inspector / Claude MCP client (rules only require showing the MCP server working, `AGENTS.md §15`). Pursue Alexa+ Preview in parallel; if granted, add the voice shot on top — backend unchanged. | ✅ Path locked |
| 2 | AWS region | **`us-east-1`** — widest Bedrock (Claude) + AgentCore coverage. All infra, model IDs, secrets standardize here. | ✅ Locked |
| 3 | $150 AWS credits | User already has ~$150 in the AWS account. | ✅ Available |
| 4 | Real demo user | **Realistic placeholder** ("Ramesh" household) now; swap in a real family member's meds/appointments before the video (`AGENTS.md §10`). | ✅ Interim |

## Stack specifics

| Area | Decision |
|---|---|
| MCP library | `fastmcp` 4.x. Negotiated MCP spec **2026-07-28** (≥ target 2025-11-25). |
| Transport | Streamable HTTP at `/mcp`. |
| Structured memory | **Neo4j Aura** (free) for the live/deployed graph; local Docker Neo4j used for dev/test (identical Bolt protocol — switch is one env var). |
| Reasoning LLM | Amazon Bedrock (Claude) — default model id in `.env.example`, revisit at Week 3. |
| Backend selector | `SAARTHI_BACKEND=memory` (Week 1 stub) → `neo4j` (Week 2+). Tools depend only on the `HouseholdRepository` interface, so the swap does not change the MCP contract. |

## Open / needs user input

- **Neo4j Aura connection** (URI + password) → put in `mcp-server/.env` (gitignored). Until then,
  dev/test runs against local Docker Neo4j.
- **AWS credentials** (`aws configure`, region `us-east-1`) → needed from Week 3 to verify Bedrock
  model access + AgentCore, and for deploy.
- **Bedrock model access** for Claude in `us-east-1` → request early (approval can lag).
- **Real user data** → collect before the Week 5–7 polish/video.
