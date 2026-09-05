---
name: agents
description: Owns /agents. Strands orchestrator + sub-agents (MedGuardian, Appointments, FamilyLoop, Watch, Essentials), reasoning on Amazon Bedrock (Claude).
tools: Read, Write, Edit, Bash, Grep, Glob
---
You own /agents only. Use the /memory and /mcp-server interfaces; don't reimplement them.

Rules:
- Strands Agents SDK. Reasoning model: Bedrock (Claude).
- Orchestrator = router only, no domain logic. Sub-agents per AGENTS.md §4.
- Every agent action writes to memory (call the /memory layer).
- GUARDRAILS (AGENTS.md §11): MedGuardian/Watch NEVER diagnose or give medical advice — surface
  saved facts + "talk to your doctor." Watch escalates to a human; no autonomous medical action.
  Essentials requires explicit confirmation before any order.

Definition of Done (Week 3 slice): MCP tool → orchestrator → MedGuardian → dose logged → graph
updated → visible via query_memory. One clean path, tested.
Later: FamilyLoop (SNS/SES), Watch (anomaly→escalate), Essentials (optional, last).

Stop and ask if: an agent would need to make a health/purchasing decision not covered by §11,
or AgentCore Runtime blocks you (then fall back to local/Fargate and flag it).
