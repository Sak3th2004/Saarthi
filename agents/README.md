# /agents — Strands multi-agent (Bedrock/Claude)

Owner: the `agents` subagent. Built in **Week 3+** (see `SPRINT_PLAN.md`).

Orchestrator (router only) + sub-agents: MedGuardian, Appointments, FamilyLoop, Watch,
Essentials (`AGENTS.md §4`). Reasoning on Amazon Bedrock (Claude). Every agent action writes to
the `/memory` layer. Guardrails per `AGENTS.md §11` — no medical advice, escalate anomalies,
confirm purchases.
