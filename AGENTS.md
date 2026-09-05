# AGENTS.md — Saarthi (working codename; rename freely)

> **Read this file completely before writing any code.** It is the single source of truth.
> If a decision isn't here, **stop and ask — do not guess** (see §0.5).
> For Claude Code: create a `CLAUDE.md` at root whose only line is `@AGENTS.md`.

---

## 0. One-line pitch

**Saarthi turns Alexa+ from a stateless Q&A box into a care companion with memory** — an agentic system that helps an aging parent live independently by remembering meds, appointments, people, and daily patterns across sessions, coordinating a set of autonomous agents, and keeping the family in the loop.

**Hackathon:** Amazon "Build, Ship, Shape" — Primary track **Alexa+** (self-hosted MCP path) + Mini challenges **AWS Builder** + **Open Source**.
**Submit by:** Fri **Oct 23, 2026, 12:00pm PT**. We have ~7 weeks.

---

## 0.5 Stop-and-ask protocol (do NOT guess)

The agent proceeds on its own within this plan (small PRs, tests, conventional commits).
It **must stop and ask you first** when any of these is true:
- A decision isn't covered in this doc.
- It would add a library / service / dependency not in §7.
- It touches secrets, credentials, billing, or a live deploy.
- The MCP spec/transport or an AWS service choice is ambiguous.
- It's about to encode any health/medical logic (§11) or a purchasing action.
- A required test can't be written, or is failing and the fix isn't obvious.

**Default: when unsure, log a FRICTION_LOG line and ask — never invent a decision.**

---

## 1. Who it's for (real, not imaginary)

- **Primary user:** an elderly parent living alone or semi-independently (ground it in a real person — ideally in a multigenerational Indian household you have access to for the demo).
- **Secondary user:** the adult child / family caregiver who worries but can't be there 24/7.
- **Real problem, real numbers:** medication non-adherence is one of the most common, costly, preventable causes of hospitalization in older adults; social isolation compounds it. This is not a toy scenario — bring one real user's real routine into the demo.

---

## 2. Why THIS wins (map every feature to the scorecard)

Judging = 4 equally-weighted criteria + up to 10% friction-log bonus. Build to the rubric:

| Criterion | How we win it |
|---|---|
| **Tech Implementation** | Real self-hosted MCP server (spec **2025-11-25+**, **Streamable HTTP**), genuine multi-agent orchestration on AWS — not a wrapper. |
| **Design** | A polished family dashboard that *visibly renders the memory graph* + voice flow. Complete, coherent product, not a script. |
| **Potential Impact** | Real user, measurable problem, credible path to Fire TV/Ring/Alexa ecosystem beyond the hackathon. |
| **Quality of Idea** | Judges explicitly call "basic MCP wrapper" *obvious* and "agentic workflow that orchestrates across services + maintains state across sessions" *creative*. The **persistent knowledge graph is the differentiator** — 95% of entries won't have real cross-session memory. |
| **+10% bonus** | Keep a **friction log from day 1** (see §13). Free points most people skip. |

**Mini challenges (stack both — you can only *win* one, but enter both):**
- **AWS Builder:** Bedrock + AgentCore Runtime + AgentCore Memory + Strands SDK + SNS/SES + Cognito = the multi-service agentic pipeline they call "creative."
- **Open Source:** repo is MIT/Apache-2.0 (license visible in the GitHub **About** box) + one real PR into the MCP/Strands ecosystem during the window.

---

## 3. Architecture

```
                        ┌─────────────────────────────┐
        VOICE           │          Alexa+             │
   "Did dad take his    │   (MCP client, Preview)     │
    evening pills?"     └──────────────┬──────────────┘
                                       │ MCP · Streamable HTTP · spec 2025-11-25+
                                       ▼
                        ┌─────────────────────────────┐
                        │   Saarthi MCP Server        │  ← the Alexa+ integration surface
                        │   (Python, FastMCP)         │     exposes TOOLS (see §5)
                        └──────────────┬──────────────┘
                                       ▼
                        ┌─────────────────────────────┐
                        │  Orchestrator Agent          │  Strands SDK, on
                        │  (supervisor / router)       │  Bedrock AgentCore Runtime
                        └───┬─────┬─────┬─────┬────┬────┘
                            ▼     ▼     ▼     ▼    ▼
                        Med   Appt  Family Watch Essentials   ← sub-agents (§4)
                       Guardian gent  Loop  (anomaly) (reorder)
                            │     │     │     │    │
                            └─────┴──┬──┴─────┴────┘
                                     ▼
                 ┌───────────────────────────────────────────┐
                 │  MEMORY (the differentiator)               │
                 │  • Neo4j Aura  → relational household graph │
                 │  • AgentCore Memory → episodic recall       │
                 └───────────────────────────────────────────┘
                                     ▲
                        ┌────────────┴──────────────┐
                        │  Family Dashboard (React)   │  ← renders the graph LIVE
                        │  Amplify/Vercel · Cognito    │     (your demo money-shot)
                        └─────────────────────────────┘

   Reasoning LLM: Amazon Bedrock (Claude).  Notifications: SNS (SMS) / SES (email).
```

---

## 4. Agent roster (Strands multi-agent)

- **Orchestrator** — receives intent from MCP tool calls, routes to the right sub-agent, writes every outcome to memory. Never does domain work itself.
- **MedGuardian** — dose schedule, reminders, adherence logging, refill/low-supply alerts. **Never gives medical advice** (see §11).
- **Appointments** — books / reschedules / lists appointments, syncs a calendar.
- **FamilyLoop** — notifies family on events, sends a weekly digest, answers "how's dad doing?" from memory.
- **Watch** (anomaly/check-in) — detects missed check-ins or unusual patterns (e.g., no morning activity, 2 missed doses), escalates to a human. Escalation only — no autonomous medical action.
- **Essentials** (optional, do last) — reorders daily essentials; this is the "purchasing" hook judges call creative. Guard behind explicit confirmation.

Every agent is a Strands agent with a tight system prompt, a small tool set, and a memory-write step. Keep them small and testable.

---

## 5. MCP contract (lock this before coding agents start)

- **Transport:** Streamable HTTP. **Spec:** 2025-11-25 or later. Self-hosted (this is the required runtime hook — the repo must *import and call* the MCP server, not just mention it).
- **Tools (first pass — refine, don't balloon):**
  - `get_household_summary()` → current state for a person
  - `get_medication_schedule(person)` / `log_dose(person, med, taken, at)`
  - `book_appointment(person, kind, when)` / `list_appointments(person)`
  - `notify_family(person, message, urgency)`
  - `record_event(person, type, detail)` → writes to graph
  - `query_memory(person, question)` → reads across graph + episodic
  - `check_in(person)` → Watch agent entry point
- Return structured content + human-readable text so Alexa+ can speak it and the dashboard can render it.

---

## 6. Memory model (Neo4j — the star)

Sketch (evolve as needed):
```
(:Person {name, role:"elder"|"family", ...})
(:Medication {name, dose, schedule})
(:Appointment {kind, when, status})
(:Event {type, detail, at})     // visits, calls, missed doses, anomalies
(:Preference {...})

(Person)-[:TAKES]->(Medication)
(Person)-[:HAS_APPOINTMENT]->(Appointment)
(Person)-[:RELATED_TO {relation}]->(Person)
(Person)-[:EXPERIENCED]->(Event)
```
Rule: **every meaningful interaction writes a node/edge.** The graph is what lets Alexa+ answer "did the physio ever call back?" — demo that exact cross-session recall on camera.

---

## 7. Tech stack (LOCKED — don't re-litigate mid-build)

- **MCP server + agents:** Python 3.11+, FastMCP (or official MCP Python SDK), Strands Agents SDK.
- **LLM:** Amazon Bedrock (Claude).
- **Agent runtime:** Bedrock **AgentCore Runtime** (also hosts the MCP server → max AWS-mini surface). Fallback: ECS Fargate / API Gateway + Lambda.
- **Structured memory:** **Neo4j Aura** (free tier). **Episodic memory:** AgentCore Memory.
- **Frontend:** React + Vite + TypeScript, Tailwind + shadcn/ui, TanStack Query, Recharts (adherence charts), `react-force-graph` or d3 (graph viz).
- **Auth:** Cognito. **Notifications:** SNS (SMS) / SES (email).
- **Hosting FE:** Amplify Hosting or Vercel.
- **Repo:** single monorepo. **License:** MIT (or Apache-2.0) — file present AND visible in GitHub About.

---

## 8. Repo structure

```
saarthi/
  LICENSE                 # MIT, at root, shown in About
  README.md               # run instructions (judges test from this)
  AGENTS.md               # this file
  FRICTION_LOG.md         # updated daily (bonus points)
  /mcp-server             # FastMCP server + tool defs
  /agents                 # Strands agents (orchestrator + sub-agents)
  /memory                 # Neo4j client, graph schema, migrations
  /dashboard              # React app
  /infra                  # IaC (CDK/Terraform), AgentCore + Cognito + SNS/SES
  /docs                   # architecture diagram, demo script, submission answers
```

---

## 9. Coding conventions & Definition of Done

- **Small PRs, one concern each.** Conventional commits (`feat:`, `fix:`, `docs:`…).
- **Every feature ships with:** a test (pytest / vitest), a README note if it changes setup, a FRICTION_LOG entry if a tool fought you.
- **DoD for a task:** code + test passing + runnable from README + memory write verified + demo-able.
- No secrets in git — use env + a `.env.example`.
- Prefer boring, working code over clever code. This has to run live for judges.

---

## 10. Definition of "real product, not a demo"

- Runs end-to-end from a clean clone following README only.
- Uses **one real person's real routine** for the demo data.
- Deployed and reachable (not just localhost) by submission.
- Has a credible "what's next" (Fire TV surface for the dashboard, Ring camera for presence — mention in the impact answer).

---

## 11. Guardrails (non-negotiable — this handles health data)

- **Not a medical device.** The system tracks adherence and coordinates people; it **does not diagnose, dose, or give medical advice.** Any health question → surface saved facts + "talk to your doctor," never a recommendation.
- **Anomaly = escalate to a human**, never an autonomous medical action.
- **Purchasing/essentials** requires explicit confirmation before any order.
- **Privacy:** medication + personal data is sensitive. Least-privilege, encrypted at rest, family access gated by Cognito. State this in the submission — it's a maturity signal to judges.

---

## 12. AWS deployment plan (drives the AWS Builder mini)

1. Bedrock model access (Claude) enabled in region.
2. Agents on AgentCore Runtime; MCP server hosted alongside.
3. AgentCore Memory for episodic recall.
4. Cognito user pool for the dashboard.
5. SNS for SMS family alerts, SES for the weekly digest.
6. Neo4j Aura free instance, connection in secrets manager.
7. Document *every* service + how you used it in the Product Feedback answer (required for the mini).
8. Request the **$150 AWS credits** early (form closes Oct 21, 12pm PT).

---

## 13. Friction log discipline (free 10%)

Keep `FRICTION_LOG.md` from day one. For each snag:
> **Task attempted · steps taken · expected vs actual · severity · workaround · one actionable suggestion.**
Amazon's team reads these and passes a bonus to judges. Most people submit nothing here. Aim for 8–15 honest entries across the build.

---

## 14. Open Source mini setup

- License at root + visible in About.
- During the window, make **one real PR** to a public repo in the MCP/Strands/Alexa ecosystem (a genuine fix or a small feature — not a typo). Save the PR URL, repo URL, GitHub username, and a 3-line "what/how/why."

---

## 15. Risks & fallbacks (decide early)

- **Alexa+ Preview access may be gated.** If we can't drive the MCP server *through Alexa+* directly for the video, fall back to demonstrating the same MCP server via an MCP Inspector / Claude client — the rules require showing the **MCP server in action**, and the repo calling it at runtime. Confirm access in Week 1; log it either way.
- **AgentCore learning curve** — if it blocks us past Week 3, ship agents on Fargate/Lambda and keep AgentCore Memory only. Don't let infra sink the product.
- **Neo4j Aura free limits** — fine for demo scale; note the limit, don't over-model.

---

## 16. Submission checklist (from the official rules — verify before Oct 23)

- [ ] Public GitHub repo, all source + setup instructions, license visible in About.
- [ ] Repo **imports and calls** the MCP server at runtime (not just README mention).
- [ ] Demo video **< 3 min**, public on YouTube/Vimeo, English, best material first, no unlicensed music/footage.
- [ ] Video shows the MCP server / Alexa+ experience actually working.
- [ ] Text description: what it does + how it works.
- [ ] Product Feedback for **every** tool/API/SDK used (+ AWS services described here for the mini).
- [ ] Tracks + mini challenges selected on the form.
- [ ] Open Source fields: contribution URL, repo URL, GitHub username, description.
- [ ] "What changed during the window" explanation (since parts predate submission).
- [ ] Friction log entries attached/linked.
```
