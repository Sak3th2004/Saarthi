# Family notebook dashboard

Implemented: saved-record graph, timeline, medication and appointment records, event entry,
and recall with supporting records. Uses the official MCP TypeScript client over Streamable
HTTP; there is no seeded data in the browser. Missing records never imply wellbeing.

From this directory run `npm ci`, then `npm run dev`. Start the Python MCP server separately
on `127.0.0.1:8080` using the root setup instructions and your configured repository. Open
`http://localhost:5173` (Cognito mode rejects `127.0.0.1` — redirect URI must match). The development proxy sends
`/mcp` calls to that server. Use the Neo4j backend for persistence; process-local memory is
not durable. AWS and database credentials stay on the server.

`npm run build` typechecks and builds the frontend. `npm test` runs offline checks. In
PowerShell, `$env:SAARTHI_RUN_DASHBOARD_HTTP_TEST='1'; npm test` additionally checks real HTTP
calls through Vite and the Python server using a fresh temporary test household. It does
not read or write Aura or call Bedrock. Set `SAARTHI_TEST_PYTHON` if the Python environment is
not at `mcp-server/.venv`. Test records are confined to ignored `.test-temp-*` directories.

Local Calendar connection, upcoming reads and reviewed appointment creation are implemented.
First-household setup is available when the backend starts with `SAARTHI_LOCAL_SETUP=1`
on `127.0.0.1:8080`. Use Check household status, enter names and relationships, review,
then explicitly confirm. Existing stores are never replaced. Memory mode lasts only for
the server process; Neo4j stores records persistently. This is local administrator setup,
not authentication or account invitations.

Recall has an editable timezone field, initially the device's timezone. Today/yesterday
and dayparts are evaluated in the submitted timezone; unsupported ranges ask for clarification.
The graph includes saved family relationships with their original direction and label,
without exposing contacts or the other person's notes.

Cognito sign-in, reviewed household management, Calendar, and family email notify are implemented
for signed-in caregivers. Production hosting/routing remains unfinished.
Keep this development server on loopback; a public deployment still requires production routing and TLS.
Browser visual verification is still pending.

For Cognito mode, use `dashboard/.env.example` and configure `VITE_AUTH_MODE=cognito`,
the hosted-login domain, public client ID, MCP resource audience, and exact `/auth/callback`
redirect. The local Cognito callback uses `http://localhost:5173/auth/callback` (open the
dashboard at localhost for this mode). Configure the matching backend as described in
`mcp-server/README.md`. No AWS keys or client secrets belong in Vite environment variables.

The login flow uses authorization code + S256 PKCE, a one-use state/verifier with ten-minute
expiry, and same-origin callback validation. Only the pending login is stored in sessionStorage;
access tokens remain in memory. Refreshing the page requires sign-in again. Session expiry and
sign-out remove the notebook UI. Backend membership checks remain authoritative. Local
administrator household setup stays hidden in Cognito mode. Publishing a build with local auth
mode fails closed in the UI and backend.

### Cognito Calendar + family notify

After sign-in the dashboard calls authenticated same-origin routes (Vite proxies to the MCP
server) with the resource-bound Bearer access token and the dashboard Origin:

- `POST /household/calendar/connect` — returns a one-time `handoff_url` on
  `http://127.0.0.1:8080/oauth/google/household-connect` so the OAuth cookie is set on the MCP
  host (not via the Vite `:5173` proxy). Google still callbacks to `127.0.0.1:8080`.
- `GET /household/calendar/events` — reads upcoming primary-calendar events
- `POST /household/calendar/appointments/review|confirm` — reviewed Calendar write + memory save
- `POST /household/notify/review|confirm` — reviewed family **email** via SES when configured
- `POST /household/notify/digest/review` — assemble a weekly digest, then the same confirm path

**First-time Cognito Calendar connect:** sign in on the dashboard (Saarthi Cognito) → **Connect
Google Calendar** (this is Google OAuth, not another Cognito step) → allow the `127.0.0.1:8080`
handoff window → approve Google → return and **Load upcoming events**. The old button label
“Connect calendar (signed in)” meant Cognito was already active — not that Google was linked.
Do not start connect only through a proxied fetch that expects a Set-Cookie on `:5173`.

**Family notification emails:** under People & relationships → Update family details → Set family
notification email (family members only). Review and confirm a free-text message, or **Review
weekly digest** for the last seven days of saved records. Notify uses those saved graph
emails only — never invent SES recipient addresses in the UI.

Appointment records in the notebook show `not_requested` or `calendar_saved` (same honesty as
`list_appointments`), not clinic confirmation. Family notify shows `delivery_status` as
`pending` / `recorded_only` / `accepted` / `uncertain` / `failed` and never claims SMS or inbox delivery.
Recipients are saved family emails connected to the selected person by a relationship, rechecked at confirmation.
Calendar and email reviews are bound to the signed-in caregiver. Local mode still uses
`/oauth/google/*` without Cognito.

## Original planned scope

Owner: the `dashboard` subagent. Built in **Week 4–5** (see `SPRINT_PLAN.md`).

React + Vite + TypeScript, Tailwind + shadcn/ui, TanStack Query. Live knowledge-graph view
(react-force-graph/d3), adherence charts (Recharts), and an agent "action feed" (what each agent
did and why). Cognito auth. It must survive judges poking it: empty/loading/wrong-person states.

Sign-in troubleshooting: start from the dashboard root in the same tab; do not reuse a callback URL. Callback failures display fixed AUTH_ codes for missing browser state, state mismatch, expiry/configuration changes, provider rejection, token exchange, connectivity and invalid sessions. Share only the error code. Codes, passwords, access tokens and provider error descriptions are never included in these messages; token POST requests do not follow redirects. The backend remains responsible for token signature, audience and household authorization.

Cognito resource binding: the custom scope must belong to the requested resource. The dashboard requests openid plus <VITE_COGNITO_RESOURCE_URL>/notebook. infra/cognito.json registers that exact MCP URL as the resource-server identifier and assigns its notebook scope to the app client. The backend derives the same scope from COGNITO_RESOURCE_URL. An old saarthi/notebook permission is not sufficient. Apply the matching infrastructure before signing in again.

Signed-in households: after Cognito sign-in the family directory loads once automatically (Refresh people re-reads). The default older adult is pre-selected; open their notebook with the named primary button (for example Open Ravi's notebook) — notebooks do not open until you click. Review and confirm adding a person, correcting a name, setting a directed relationship, or choosing the default older adult. These actions use authenticated /household/manage endpoints, with the same resource-bound Cognito access token and approved-subject checks as MCP. Adding a person does not create a login or send an invitation. Conflicting/expired reviews require a fresh directory read. Existing care records stay attached to the same person IDs. This deployment supports one shared household, not unrelated tenant accounts.
