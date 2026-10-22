# Saarthi MCP server

Python MCP server over Streamable HTTP, with the family notebook's authenticated routes and static hosting. See the [root README](../README.md) for complete source installation, current architecture, live URL and judge instructions.

## Tools and effects

| Tool or flow | Effect |
|---|---|
| `get_household_summary`, `get_medication_schedule`, `list_appointments` | Read saved household facts |
| `record_event`, `log_dose` | Save an event or dose record |
| `query_memory` | Retrieve saved facts with supporting records; absent facts remain unknown |
| `get_memory_graph` | Read saved nodes and directed relationships; bounded results report truncation |
| `talk_to_saarthi` | Bounded Bedrock/Strands conversation with evidence and optional drafts |
| `book_appointment` | Record an appointment; does not book with an external provider |
| `notify_family` | Record a message; does not send it |
| Calendar review/confirm | Create a real Google Calendar event only after confirmation |
| Family notification review/confirm | Send SES email only after confirmation |
| `review_weekly_digest` | Prepare a digest for the same explicit email confirmation flow |
| `check_in` | Review recorded concerns; does not contact anyone or establish wellbeing |

Responses contain structured results and human-readable text. A model-generated draft is not authorization to execute an external action.

## Run and inspect

Install from the repository root using its README, then run `python -m saarthi_mcp` in the virtual environment. Local configuration is read from `mcp-server/.env`; process environment variables take precedence.

For the local empty notebook, use `SAARTHI_AUTH=local`, `SAARTHI_BACKEND=memory`, `SAARTHI_AGENTS=off`, and optionally `SAARTHI_LOCAL_SETUP=1`. Household setup requires `127.0.0.1:8080`. Records in memory mode last only for that process. No demonstration records are loaded automatically.

Inspect local tools with `npx @modelcontextprotocol/inspector`, Streamable HTTP, `http://127.0.0.1:8080/mcp`. Embedded callers use `from saarthi_mcp import build_server`; each instance owns its repository and orchestrator.

Use `SAARTHI_BACKEND=neo4j` and your database configuration for persistence. Startup applies the schema/index setup. Never run sample-seeding helpers against an existing household.

## Authentication and hosting

Public binding requires Cognito authentication. Configure `AWS_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_RESOURCE_URL` and `COGNITO_ALLOWED_SUBJECTS`. A valid pool token alone does not grant household access: the subject must be explicitly approved.

Validation checks signature, issuer, resource audience, app client, access-token purpose, scope and expiry. Browser authorization requests the MCP resource URL. Cognito resource binding uses Essentials/Plus with Managed Login. Deployments require HTTPS.

Each deployment serves one shared household. Subject-scoped action reviews do not make household records private to each caregiver. Sign-out ends the browser session; already issued access tokens remain subject to their expiry and server allowlist checks.

The committed [App Runner template](../infra/hosted-notebook.json) hosts the MCP server and dashboard under one origin. It uses Secrets Manager for service secrets and per-subject Calendar credentials. Pending OAuth flows and short conversation context remain in memory. There is no AgentCore deployment.

## Local Google Calendar connection (Windows)

Create a Git-ignored `.local-artifacts/google-calendar.env` containing `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_CALENDAR_ACCOUNT` and `GOOGLE_REDIRECT_URI=http://127.0.0.1:8080/oauth/google/callback`. Register that exact callback on a Google web OAuth client, enable Calendar API and add the approved account as a test user when the OAuth app is in Testing.

Set `SAARTHI_GOOGLE_CONFIG` to the file's absolute path and run the server at `127.0.0.1:8080`. In local authentication mode, open `http://127.0.0.1:8080/oauth/google/connect`. Local credentials are protected with Windows DPAPI. This local flow is distinct from the hosted, authenticated flow.

In the hosted release, `SAARTHI_GOOGLE_MODE=hosted` selects Secrets Manager storage. Configure `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_CALENDAR_ACCOUNT`, `GOOGLE_REDIRECT_URI` and `SAARTHI_GOOGLE_TOKEN_PREFIX` as shown in the template. Connect through the signed-in dashboard. The approved Google account must match the configured account; an arbitrary different account is rejected.

Connecting grants access but does not modify events. Loading events reads the approved primary calendar and does not assign its events to a household member. Google Testing refresh tokens can expire after seven days; reconnect when necessary.

## Reviewed actions

Calendar review validates person, local date/time and IANA timezone, including ambiguous/nonexistent daylight-saving times. Confirmation uses the reviewed details to create a private event without guests or reminders and save an appointment/audit record. Stable event identity supports reconciliation after an uncertain result. It does not establish a clinic booking. Rescheduling and cancellation are not implemented.

Family email requires a configured SES sender and a saved, approved family recipient. Sandbox recipients must be verified. Review, claim and outcome persist in Neo4j; repeating a claimed confirmation cannot send the same message twice. An uncertain outcome must be checked before preparing a new send. SES acceptance is not verified inbox delivery; mail may reach Spam.

Local setup cannot replace an existing household. Cognito household management binds reviews to the approved caregiver and current directory revision. Adding a family record does not grant that person account access.

## Verification

From the root, run `python scripts/check.py`. For actual HTTP/database persistence and restart checks, run `python scripts/check.py --neo4j` with Docker available. That suite starts/stops a dedicated disposable graph at port 17687 and refuses the hosted database as its test reset target.

Paid Bedrock and real SES tests are opt-in. A healthy HTTP process is not proof of database, model, Calendar or email availability. Never infer wellbeing or medication adherence from absent records.
