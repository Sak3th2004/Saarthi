# SPRINT_PLAN.md — Saarthi · 7 weeks (Sep 4 → Oct 23, 2026)

> Companion to `AGENTS.md`. This is the scrum-master layer: what ships each week, what must be
> true by Friday, and the checkpoints that keep you from building on a bad assumption.
> **Submit target: Wed Oct 21 (buffer)** — hard deadline is **Fri Oct 23, 12:00pm PT**.

---

## How to run this (solo scrum)

- **Board (3 columns):** `Backlog` → `This Sprint` → `Done`. Only pull from Backlog at sprint start.
- **Daily standup with yourself (10 min):** What shipped yesterday? What ships today? What's blocked? → then add any snag to `FRICTION_LOG.md` (that's your 10% bonus, don't skip it).
- **Sprint = 1 week.** Fri = "review": does the week's *Done-by-Friday* hold? If not, cut scope, don't slip the whole plan.
- **Golden rule — vertical slice first:** by end of Week 3 you have a *thin* end-to-end path working (tool call → agent → memory write → dashboard shows it). Everything after that is *deepening* a thing that already works, never a leap of faith at the end.

---

## Milestone map (the 4 checkpoints that matter)

| By end of | You can demo… |
|---|---|
| **Week 1** | An MCP tool callable over Streamable HTTP + all 4 unknowns verified |
| **Week 3** | Thin vertical slice: "log a dose" flows through an agent, writes to the graph, shows on dashboard |
| **Week 5** | Full product on real-user data, edge cases handled, dashboard polished |
| **Week 7** | Deployed, video recorded, submission filed |

---

## WEEK 1 (Sep 4–10) — Foundations + burn down every unknown

**Sprint goal:** repo scaffolded, one MCP tool callable, and *zero* open unknowns.

**Days 1–3 — verify before you build (do these FIRST):**
- [ ] Confirm **Alexa+ / MCP access** — can you drive an MCP server through Alexa+? If gated, lock the fallback: demo via MCP Inspector / a Claude MCP client. Log the answer either way.
- [ ] Pick **AWS region** where Bedrock (Claude) **and** AgentCore both exist (likely `us-east-1` / `us-west-2`). Enable Bedrock model access — approval can lag, do it now.
- [ ] Submit the **$150 AWS credits** form (closes Oct 21, 12pm PT).
- [ ] Line up your **real demo user** — collect one real parent/grandparent's actual meds + appointment pattern.

**Rest of week:**
- [ ] Monorepo per `AGENTS.md §8`. `LICENSE` (MIT) at root + visible in GitHub About. `README`, `AGENTS.md`, `CLAUDE.md` (`@AGENTS.md`), `FRICTION_LOG.md`.
- [ ] Stand up the FastMCP server, Streamable HTTP, spec 2025-11-25+. Ship **one** tool: `get_household_summary()` returning stub data.
- [ ] Prove it: call that tool from MCP Inspector (or Alexa+ if access confirmed).

**Done by Friday:** a real MCP tool responds over Streamable HTTP; all 4 unknowns closed; repo public with license.
**Risk:** if Bedrock access isn't approved by Fri, keep building against a local model shim and swap later — don't idle.

---

## WEEK 2 (Sep 11–17) — Memory core (the differentiator)

**Sprint goal:** the knowledge graph is live and the MCP server reads/writes it.

- [ ] Neo4j Aura free instance; connection in secrets (not git).
- [ ] Implement the graph schema (`AGENTS.md §6`): Person, Medication, Appointment, Event, Preference + edges.
- [ ] Tools: `record_event`, `query_memory`, real `get_household_summary`, `get_medication_schedule`, `log_dose`.
- [ ] Seed the graph with your **real user's** data.

**Done by Friday:** you can `log_dose`, then `query_memory("did dad take his evening pills?")` and get the right answer **from the graph** — cross-session recall working. This is your winning demo moment; get it early.
**Risk:** don't over-model the graph. 5 node types is enough for the demo.

---

## WEEK 3 (Sep 18–24) — Agents + the thin vertical slice

**Sprint goal:** Strands orchestrator + 2 sub-agents, end-to-end path demoable.

- [ ] Orchestrator agent (router only — no domain logic). Bedrock (Claude) as the reasoning model.
- [ ] **MedGuardian** agent (reminders, adherence, refill alerts) and **Appointments** agent (book/list).
- [ ] Every agent action writes to the graph.
- [ ] Wire agents behind the MCP tools.

**Done by Friday — VERTICAL SLICE:** MCP tool → orchestrator → MedGuardian → dose logged → graph updated → visible via `query_memory`. One clean path, working. From here you only deepen.
**Risk:** if AgentCore Runtime is fighting you, run agents locally / on Fargate this week and move to AgentCore in Week 6. Don't let infra block product.

---

## WEEK 4 (Sep 25 – Oct 1) — Family loop, safety, notifications

**Sprint goal:** the "coordinates across people" story is real.

- [ ] **FamilyLoop** agent + `notify_family` via **SNS** (SMS) / **SES** (email) + weekly digest.
- [ ] **Watch** (anomaly) agent: missed check-ins / missed doses → escalate to a human. Escalation only.
- [ ] Bake in the **guardrails** (`AGENTS.md §11`): a medical question → surface saved facts + "talk to your doctor," never advice.
- [ ] Dashboard skeleton starts (React/Vite/Tailwind/shadcn), auth via Cognito.

**Done by Friday:** a simulated "2 missed doses" triggers a real SMS to the family contact; a medical question is safely refused-and-escalated.

---

## WEEK 5 (Oct 2–8) — Dashboard polish + edge cases (the "not scripted" proof)

**Sprint goal:** it looks like a product and survives poking.

- [ ] Dashboard: **live graph viz** (react-force-graph/d3), adherence charts (Recharts), an action feed showing what agents did and why.
- [ ] Load full **real-user** data; make the whole flow demoable on that data.
- [ ] Build in 3–4 edge cases for the video: dose conflict ("I already took it" vs log), ambiguous voice (which person/med), unreachable-family fallback chain, Bedrock-timeout graceful degrade.
- [ ] Optional: **Essentials** reorder agent (the "purchasing" creative hook) — behind explicit confirmation.

**Done by Friday:** a stranger could watch the dashboard and understand it in 30s; the 4 edge cases work on camera.

---

## WEEK 6 (Oct 9–15) — Deploy for real + open source + friction log

**Sprint goal:** running on AWS, not localhost.

- [ ] Deploy agents to **Bedrock AgentCore Runtime**; host the MCP server alongside; AgentCore Memory wired for episodic recall.
- [ ] Frontend on Amplify/Vercel. End-to-end works from a clean clone per README.
- [ ] Harden: retries, timeouts, empty-state, wrong-person.
- [ ] **Open Source mini:** one genuine PR into the MCP/Strands ecosystem — save URL, repo, GitHub username, 3-line what/how/why.
- [ ] Consolidate `FRICTION_LOG.md` (aim 8–15 honest entries).

**Done by Friday:** judges could clone + run it, and reach the live deploy. Feature freeze after this.

---

## WEEK 7 (Oct 16–22) — Demo video + submission

**Sprint goal:** filed early, nothing rushed.

- [ ] **Demo video < 3 min**, public YouTube/Vimeo, English. Lead with your strongest 30s (the cross-session memory recall). Show 2–3 edge cases. No unlicensed music/footage.
- [ ] Write submission text (what it does + how), **Product Feedback for every tool/API/SDK** (describe AWS services here for the AWS Builder mini).
- [ ] Select **Alexa+ track + both mini challenges** on the form. Fill Open Source fields.
- [ ] "What changed during the window" explanation.
- [ ] Run the full `AGENTS.md §16` checklist. **Submit by Wed Oct 21** — never the last hour.

**Done by Wednesday:** submitted, verified, buffer to spare.

---

## Risk triggers (decide fast, don't spiral)

- **Alexa+ access gated** → fallback to MCP Inspector / Claude client demo. (Verify Week 1.)
- **AgentCore blocks you past Week 3** → agents on Fargate/Lambda, keep AgentCore Memory only.
- **Behind by Friday** → cut scope within the week (drop Essentials, trim edge cases), never slip the plan.
- **Neo4j limits** → note the limit in feedback, don't fight it; demo scale is fine.

---

## What "winning" looks like by the end (self-check vs the rubric)

- **Tech:** real self-hosted MCP + genuine multi-agent + AWS pipeline. ✔ by W6
- **Design:** coherent dashboard, intuitive flow. ✔ by W5
- **Impact:** real user, real problem, credible "what's next." ✔ throughout
- **Idea:** cross-session memory graph = the non-obvious core. ✔ by W2
- **+10%:** friction log filed. ✔ by W6
