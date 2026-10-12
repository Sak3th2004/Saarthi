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
| `book_appointment(person, kind, when)` | Save an appointment record; no external booking |
| `list_appointments(person)` | Upcoming appointments |
| `notify_family(person, message, urgency)` | Record a family message (no send) |
| `review_family_notification` / `confirm_family_notification` | Scoped SES email after explicit approval |
| `review_weekly_digest(person, days?)` | Assemble a weekly digest for the same confirm path |
| `record_event(person, type, detail)` | Write an event into memory |
| `query_memory(person, question)` | Cross-session recall; never gives medical advice |
| `check_in(person)` | Watch: missed doses, low supply, stale activity after local grace |

Each tool returns **structured content** (dashboard renders it) **plus a `speech` line** (Alexa+
speaks it).

Appointment creation returns `external_booking_status=not_requested`. Calendar-linked records
report `calendar_saved` in `list_appointments`. Family email uses SES when `SES_SENDER_EMAIL`
is set: review, then confirm (messages and weekly digests). Sample graph contacts
(`elder-1` / `fam-1`) are never email targets. Optional live test recipient:
`SES_APPROVED_RECIPIENT` (must match a saved family email and be a verified SES identity).
Watch policy defaults: `Asia/Calcutta` 08:00–21:00 with a 2h grace and low-supply threshold 10
(see `.env.example`). See [mcp-contract](../docs/mcp-contract.md).

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

`get_memory_graph(person, limit=30)` returns saved person/medication/appointment/event nodes
and their edges for the dashboard, without a model invocation. Limit is 1-100 per category;
`truncated` identifies categories with additional records. Only upcoming appointments are
shown. Contact fields are omitted from the person node, but saved event text can contain
personal information, so this is not a public/anonymized export. The tool is read-only.
View IDs are deterministic content hashes, not database IDs; identical records collapse,
edited records acquire a new view ID, and multi-collection reads are not an atomic snapshot.
Family relationship expansion, browser rendering and authentication remain unfinished.

Embedded callers use `from saarthi_mcp import build_server` and retain the returned server.
Each call owns its repository and orchestrator; there is no shared module-level `mcp` export.
The CLI remains `python -m saarthi_mcp`. Instance isolation does not replace Cognito access
control or database household authorization; public deployment is still pending.

Tools use the `HouseholdRepository` interface (`repository.py`). Both `InMemoryRepository`
(empty by default) and `Neo4jRepository` are implemented. Config is read from the
environment or `mcp-server/.env` (`config.py`, see [`../.env.example`](../.env.example)).

Named medication recall only considers matching medication records and matching supporting
events. Missing records remain unknown; ambiguous or unrecognized medication questions request
clarification. Dose recall currently searches the last two days of dose logs;
morning/evening use the server's local timezone. General event recall searches the selected
person's full saved history for literal terms before limiting candidates to 50. It ranks by
distinct matching terms and then observation time, excludes internal execution metadata,
and returns the saved detail with its timestamp and up to three supporting events. This is
keyword retrieval, not semantic search or medical reasoning. Database results are bounded,
but the search still scans that person's event history; larger deployments need measurement.

See [memory testing](../memory/README.md) for the isolated Neo4j test database and the real
HTTP/server-restart persistence test. Default `pytest` skips the live database suite.
# Local Google Calendar connection (Windows)

Install `python -m pip install -c constraints.txt -e ./mcp-server[calendar]` from the
repository root. Create a private, Git-ignored `.local-artifacts/google-calendar.env`
containing `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_CALENDAR_ACCOUNT`, and
`GOOGLE_REDIRECT_URI=http://127.0.0.1:8080/oauth/google/callback`. Register that exact URI
on a Google web OAuth client and enable Calendar API. Add the account under test users
if the Google OAuth application is in Testing.

Set `SAARTHI_GOOGLE_CONFIG` to that file's absolute path, `SAARTHI_HOST=127.0.0.1`, and
`SAARTHI_PORT=8080`, then run `python -m saarthi_mcp` with the desired repository backend.
Open `http://127.0.0.1:8080/oauth/google/connect`. Consent asks for account email and
events on owned calendars. No events are modified by connecting. The local events route
reads at most 25 upcoming primary-calendar events, preserving all-day dates. The dashboard
has an explicit load button and never assigns these events to a household person itself.

OAuth state is browser-bound, one-use and expires after ten minutes. PKCE is enabled.
The returned account must match the configured email. Access/refresh tokens are encrypted
for the Windows login with DPAPI in the sibling `.tokens` file, with atomic replacements.
No callback query strings are written to server access logs. A corrupt or expired connection
fails explicitly; reconnect rather than assuming a successful Calendar response.

This is local connection support, not a deployed authenticated Calendar service. Public
bindings are rejected. Cloud token storage, per-household authorization, create/reschedule
operations, appointment reconciliation and deployment remain outstanding.

Reviewed appointment creation is now implemented locally. Open a saved person's notebook,
enter a title, start/end local times and IANA timezone, then review and confirm. The same
service is exposed as `review_calendar_appointment` / `confirm_calendar_appointment` when
Calendar is explicitly enabled. Reviews expire after ten minutes; their fields cannot be
changed at confirmation. Unknown people, nonexistent/repeated DST times, expired reviews,
nonlocal web writes, and confirmations without explicit approval are rejected.

Confirmed writes create private calendar entries with no guests or reminders and atomically
save the matching appointment plus an audit event in household memory. A deterministic ID
lets a retry reconcile an existing Google entry rather than post another copy. If Google
succeeds but memory fails, the response says so and the same review can finish memory saving.
Different owner/details conflict rather than silently overwriting. A clinic booking is never
implied. Rescheduling, cancellation and cloud authorization/storage remain outstanding.

## Local household setup

For the local administrator form, set `SAARTHI_LOCAL_SETUP=1` before starting the server
on `127.0.0.1:8080`. In the dashboard, choose Check household status. An existing household
can be opened; an empty store accepts user-entered names and family relationships after
review and explicit confirmation. No sample person, dose or appointment is generated.
Review tokens expire after ten minutes; a repeated successful confirmation does not import
again. If a save is uncertain, check status before retrying. Setup cannot replace or reset
existing records, including an old store that needs administrator repair.

The three `/local/household/` POST routes require a loopback peer, exact local Host/Origin,
JSON content and bounded request size. They are disabled by default and are not agent tools.
This is not Cognito authentication and must not be used as a public access-control mechanism.
Choose the Neo4j backend for persistence; memory mode explicitly reports session-only storage.

`query_memory` accepts an optional IANA `time_zone`. Relative-day and daypart recall requires
it, filters observation timestamps in that zone and excludes future records. Unsupported
date expressions ask for clarification rather than returning an unrelated latest record.
`get_memory_graph` also returns saved `RELATED_TO` edges; `detail` retains the entered
relationship and source/target preserve its direction. `truncated.person` indicates additional
family relationships beyond the view limit. Other people's contacts and care records are omitted.

## Cognito protection (prepared, live pool setup required)

`SAARTHI_AUTH=local` is the default and now refuses non-loopback bindings. For the
authenticated MCP endpoint, set `SAARTHI_AUTH=cognito`, `AWS_REGION`, `COGNITO_USER_POOL_ID`,
`COGNITO_CLIENT_ID`, `COGNITO_RESOURCE_URL` and `COGNITO_ALLOWED_SUBJECTS` (comma-separated
Cognito `sub` UUIDs). Each server still serves one household. Only the explicitly allowed
subjects can use it; merely signing up or possessing a token from the pool does not grant access.

The verifier uses FastMCP's RS256/JWKS validation plus exact issuer, resource audience,
client ID, access-token purpose, required `<COGNITO_RESOURCE_URL>/notebook` scope and expiration checks.
Browser authorization must request `resource=COGNITO_RESOURCE_URL`. Public traffic requires
HTTPS termination at deployment. Local administrator and Google Calendar routes cannot be
enabled in Cognito mode; their existing single-computer credential flow is not cloud ready.

The prepared `infra/cognito.json` defines an invite-only Lite pool, software MFA, classic
hosted login, code-only public client, 15-minute tokens and notebook scope. AWS template
validation passed; no live pool or user has been created. Creating the stack and inviting
the owner are separate reviewed actions. Do not publish without configuring the subject allowlist.

JWT verification is offline after public-key retrieval. Sign-out clears browser state and
the hosted-login session; a copied access token can remain valid until its 15-minute expiry.
To immediately remove a caregiver, remove the subject from the server configuration and restart.

Read-only model access checks: `python scripts/check_bedrock.py --model FOUNDATION_MODEL_ID --profiles`.
Paid bounded smoke check: append `--invoke PROFILE_ID --max-output-tokens 256`. It sends no
household data, does not accept agreements, and logs only status/token usage. A connection
check is not a quality benchmark or confirmation that the complete agent workflow works.

Cognito resource binding requires Essentials or Plus with Managed Login; Lite/classic does not issue the requested API audience. The infrastructure template uses Essentials, ManagedLoginVersion 2 and a default DashboardLoginBranding resource. Obtain billing-tier approval before updating an existing pool. Keep strict MCP audience validation; never treat a Cognito client_id or an ID token as the resource audience. After migration, sign out and authenticate again for a fresh audience-bound access token. Reference: https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-cognito-resource-indicators-protection-oauth-2-0-resources/

Cognito mode registers /household/manage/status, review and confirm automatically. These are authenticated browser endpoints, not agent tools. Every request verifies the bearer access token and exact configured dashboard origin. Reviews bind to the caregiver subject for ten minutes; explicit confirmation and an unchanged directory revision are required. Neo4j serializes metadata writes in a transaction and writes household_setup audit events with the author subject. Completed reviews can be retried within the current server process; after restart, check status and create a new review. Family records do not grant account membership. Local unauthenticated mode has no /household/manage routes.

Cognito mode also exposes authenticated Calendar and family-notification routes when
`SAARTHI_GOOGLE_CONFIG` or SES is configured: `/household/calendar/connect`, `/household/calendar/events`,
`/household/calendar/appointments/review|confirm`, and `/household/notify/review|confirm`. Google tokens
are stored per Cognito subject (Windows DPAPI). `POST /household/calendar/connect` returns JSON
`{handoff_url}` pointing at `http://127.0.0.1:8080/oauth/google/household-connect` so the OAuth
cookie is issued on the MCP host (Vite `:5173` cannot share a proxied Set-Cookie with the
`127.0.0.1:8080` Google callback). Family notification emails are saved with the
`set_person_email` household management change (family members only). For the demo household,
set Saketh / Vasist emails in the Cognito dashboard Household manager (family members), then
review/confirm a family notification. Live SES send also needs `SES_SENDER_EMAIL` equal to a
verified SES identity in the configured region (owner confirmation — do not invent).

### Cognito local launcher (`.local-artifacts`)

`python .local-artifacts/run_cognito_backend.py` loads, in order (later wins):

1. `mcp-server/.env` — Neo4j and base settings
2. `.local-artifacts/cognito-backend.env` — Cognito + `SAARTHI_GOOGLE_CONFIG` path pointer
3. `.local-artifacts/ses.env` — `SES_APPROVED_RECIPIENT` / `SES_SENDER_EMAIL` (optional file)

Keep Google OAuth secrets in `.local-artifacts/google-calendar.env` and point
`SAARTHI_GOOGLE_CONFIG` at that absolute path (do not paste client secrets into
`cognito-backend.env`). If the path is set but the file is missing, the launcher exits with a
clear error; leave `SAARTHI_GOOGLE_CONFIG` empty to disable Calendar (tests do this).

### October 12 integration verification

Authenticated Calendar never exposes the legacy unauthenticated appointment write routes.
Each caregiver must connect their own Google account after signing in; legacy local tokens are
not automatically assigned to a caregiver. Confirmations use the subject that reviewed the action.

Family email review, claim and outcome persist in Neo4j. Confirmation checks the caregiver and
the current linked family recipients. A review is claimed before calling SES. Repeating that
confirmation cannot send twice, including after a restart. A timeout or crash may leave an
uncertain outcome: check the recipient before making a new review. SES acceptance is reported
as `accepted` with `submitted_to`; `delivered_to` stays empty because inbox delivery is not verified.
The ordinary check runner disables paid model and live email tests. A deliberate live SES test
additionally requires `SAARTHI_RUN_SES_TESTS=1` and the configured approved recipient.

This connector still uses Windows DPAPI and loopback OAuth. Public hosting and portable encrypted
Google token storage remain unfinished. Hosting scaffolds are not evidence of a deployed service.
