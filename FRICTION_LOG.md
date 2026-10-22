# FRICTION_LOG.md — Saarthi

This log preserves development observations at the dates they occurred. Later entries may resolve earlier blockers. See the [project README](README.md) for the currently deployed capabilities. Internal planning and support files mentioned below are retained locally, not distributed with the application.

Format: **Task · steps · expected vs actual · severity · workaround · actionable suggestion.**

---

### 2026-09-05 · #001 · MCP spec version "2025-11-25+" vs library support
- **Task:** Stand up a FastMCP server that negotiates MCP spec **2025-11-25 or later** over
  Streamable HTTP (AGENTS.md §5).
- **Steps:** Chose `fastmcp` (the library AGENTS.md §7 names first). Checked which protocol
  version its Streamable HTTP transport advertises.
- **Expected vs actual:** Expected a one-line "target this spec version" switch. Actual: the
  negotiated `protocolVersion` is determined by the installed library release, not a flag; the
  spec date and the PyPI version are decoupled, so "2025-11-25+" requires pinning a recent
  enough release and verifying the handshake, not setting a constant.
- **Severity:** Low (handshake still succeeds with a modern client via version negotiation).
- **Workaround:** Pin a recent `fastmcp`, assert the negotiated protocol version in a test, and
  document it. Revisit if Alexa+ requires an exact version.
- **Suggestion:** MCP SDKs should expose the advertised `protocolVersion` prominently in docs
  and let servers assert a minimum, so builders can prove spec compliance to reviewers.

### 2026-09-05 · #002 · `gh` CLI absent on the build machine
- **Task:** Push the scaffolded repo to GitHub and manage the repo (About box license, etc.).
- **Steps:** `gh --version` → command not found. AWS CLI, git, Docker, Node all present.
- **Expected vs actual:** Expected `gh` to be available for repo automation. It is not
  installed, so repo settings (visibility, license-in-About) must be done via git + the web UI
  or by installing `gh` first.
- **Severity:** Low.
- **Workaround:** Use plain `git` for push; set visibility/About in the GitHub web UI, or
  install `gh` via winget.
- **Suggestion:** Document the exact GitHub tooling assumptions in the hackathon starter so
  builders provision `gh` up front.

### 2026-09-05 · #003 · Neo4j Aura username/database ≠ "neo4j"
- **Task:** Connect the MCP server to a fresh Neo4j Aura Free instance.
- **Steps:** Assumed the Aura defaults (`username=neo4j`, `database=neo4j`) per most docs/tutorials.
  Wrote a small probe that tried both the downloaded credentials and the `neo4j/neo4j` defaults.
- **Expected vs actual:** Expected `neo4j/neo4j` to work. Actual: `neo4j/neo4j` returned an
  `AuthError`; the working credentials had **both the username and the database set to the
  instance id** (`af7aef82`), exactly as in Aura's downloaded credentials file.
- **Severity:** Medium (would silently block all graph work if you hand-type the "known" defaults).
- **Workaround:** Always read username + database from Aura's downloaded credentials file; never
  assume `neo4j/neo4j`. Probe both before building.
- **Suggestion:** Aura's console + driver error messages should state the exact username/database
  for the instance (or make the "not neo4j" case an explicit hint on `AuthError`).

### 2026-09-10 · #004 · Bedrock availability metadata differs from working inference
- **Task:** Verify Claude access after the user reported AWS support enabled it.
- **Steps:** Used the existing AWS identity in `us-east-1`; checked foundation-model availability,
  then sent one tiny Converse request to the configured Sonnet 4.5 inference profile.
- **Expected vs actual:** Expected a consistently available status after enablement. Authorization,
  entitlement and region were available, but agreement status was `NOT_AVAILABLE`. Actual inference
  succeeded with `OK` (11 input + 4 output tokens).
- **Severity:** Low for this verification; the mixed metadata makes readiness hard to interpret.
- **Workaround:** Confirm the exact identity/model/region with a minimal inference request instead
  of concluding success or failure from agreement metadata alone. No account settings changed.
- **Suggestion:** Explain the relationship between agreement availability and existing entitlement
  in the API response/documentation, including when inference can succeed with `NOT_AVAILABLE`.

### 2026-09-10 · #005 · Initial Bedrock success followed by Marketplace payment rejection
- **Task:** Verify MCP → Strands → Bedrock → Neo4j after the reported model enablement.
- **Steps:** Implemented and tested bounded routing offline, then ran a live MCP request against
  an isolated graph. Probed streaming directly and repeated the initial standard Converse check.
- **Expected vs actual:** Expected the earlier working model to remain callable. The live flow
  failed before the dose action; both `ConverseStream` and `Converse` returned
  `AccessDeniedException` with `INVALID_PAYMENT_INSTRUMENT`, saying Marketplace subscription
  could not complete. The failed routing outcome was saved in the graph.
- **Severity:** High — blocks verification of the live agent slice.
- **Workaround:** Keep offline and isolated database tests running; prepare a support follow-up
  for the account owner. No billing settings changed, no model/transport bypass attempted.
- **Suggestion:** Surface pending subscription completion and payment prerequisites alongside
  model enablement, so an initial successful invocation is not mistaken for stable access.
- **September 13 follow-up:** Repeated tiny standard and streaming requests with the existing
  AWS credential chain. Both still returned `AccessDeniedException / INVALID_PAYMENT_INSTRUMENT`.
  Fresh request IDs were added to `docs/bedrock-support-followup.md`; no account settings changed.
  Offline verification passed 61 tests. This remains the same unresolved friction, not a new entry.
- **September 13 support follow-up:** With the user's authorization, read the existing support
  replies and sent the new diagnostic evidence to case 178867261800971, as AWS had requested.
  API success and stored message verified; status became `customer-action-completed`. Waiting
  for AWS to resolve the Marketplace blocker; no payment or support-plan settings changed.
- **September 13 isolation:** Six non-Claude Converse probes succeeded. Nova 2 Lite and
  DeepSeek also passed the existing MCP/Strands workflow using synthetic in-memory records.
  Claude agreement metadata remains unavailable. User emails show Sonnet acceptance and
  expiry in the same minute. Added this evidence to the same support case and verified
  submission; the blocker is not all Bedrock inference. Defaults were not changed.

### 2026-09-13 · #006 · Recorded actions appeared externally completed
- **Task:** Verify that MCP results truthfully describe appointment and family-message outcomes.
- **Steps:** Inspected the speech and structured contract, then exercised a fresh household
  with and without family contacts; checked persisted appointment history in isolated Neo4j.
- **Expected vs actual:** Expected recording to remain distinct from delivery/booking. Actual:
  speech said "Booked" and `delivered_to` listed candidate channels without provider calls.
- **Severity:** High for real-family use: a caller could assume a task had been completed.
- **Workaround:** Return explicit record-only states, separate candidate contacts, and use
  `appointment_recorded` for new history; verified speech, results and saved events with tests.
- **Suggestion:** Tool schemas should represent intended destinations and confirmed outcomes
  separately from the outset. This is feedback on Saarthi's own integration contract, not an AWS outage.
- **Related open decision:** Missing-data adherence/check-in semantics still need the user's
  answer under AGENTS.md §0.5/§11. No replacement health policy was inferred in this fix.

## September 26: Bedrock catalog availability differed from invocation access

**Task:** verify a capable reasoning model for the real agent pipeline. **Steps:** queried
model agreements, then made a bounded Grok invocation and a Kimi integration request using
synthetic records. **Expected vs actual:** Grok agreement metadata was AVAILABLE but inference
returned AccessDeniedException (request e7bf673d-2794-42df-8008-28bec4ad5159). Kimi K2.5
completed the MCP/Strands/Neo4j/restart test. **Severity:** blocks choosing a model from the
catalog alone. **Workaround:** use an explicitly tested model; no silent fallback. **Suggestion:**
expose account-specific inference eligibility and its required remediation alongside agreement status.

## September 26: isolated Neo4j became unavailable during validation

**Task:** prove user-data import rollback and concurrent setup. **Steps:** ran the isolated
graph tests; the local port refused connections because the test container was stopped.
**Expected vs actual:** a reachable graph was expected; the tests failed visibly instead of
falling back to memory. **Severity:** local validation interrupted. **Workaround:** restart the
same disposable test container and wait for health, then rerun. **Suggestion:** provide one
repeatable test runner that starts, health-checks and shuts down the isolated database.

## September 26: fresh dependency installation did not resolve MCP

**Task:** install the published source into an empty Python 3.11 environment. **Steps:**
installed both local packages with pip 24; retried official PyPI without cache; compared
published MCP versions and local metadata. **Expected vs actual:** MCP 2.1.1/2.2.0 were
listed by the index, but unconstrained resolution reported no compatible MCP distribution.
**Severity:** clean-install verification blocked. **Workaround under verification:** update pip
inside the disposable environment, install the exact working MCP version and constrain
FastMCP/MCP/Strands/Neo4j to the tested compatibility set. The exact MCP installation succeeded;
full fresh-environment verification is recorded separately in the progress log. **Suggestion:**
report the selected index and rejected candidate reasons clearly when dependency resolution
contradicts a direct package-index query. Cache alone was not an adequate explanation.

## October 6: Aura Free pause threatens retained project data

**Task:** retain the project graph for development and judging. **Steps:** reviewed the
October 5 reminder supplied by the owner and official pause/export documentation.
**Expected vs actual:** persistent project storage was planned; the email says the paused
Saarthi-db content will be deleted three days after the notice. **Severity:** urgent data-loss
risk. **Workaround requested:** owner resumes today and exports a private backup; restoration
has not been verified. **Suggestion:** show the deletion deadline and a direct resume/export
checklist together in the reminder. No deletion or paid upgrade was performed.

## October 6: Calendar authentication dependency approval

**Task:** connect the owner's real Google Calendar. **Steps:** owner enabled Calendar API,
created a web OAuth client, added the calendar account as a test user and saved credentials
in a Git-ignored local file. Presence checks printed only booleans, never credentials.
**Expected vs actual:** ready to implement OAuth; Google's authentication libraries are not
in the approved dependency list and google-auth-oauthlib is not installed. **Severity:** low,
project approval dependency rather than a Google service failure. **Workaround:** request
approval for google-auth and google-auth-oauthlib before adding them, per AGENTS section 0.5.
**Suggestion:** approve the authentication dependencies when choosing a calendar provider.
The callback and real Calendar connection are not yet implemented.

## October 6: Local OAuth landing blocked a link from chat

**Task:** owner opens Calendar connection. **Steps:** clicked the local connection URL
from chat; reproduced the browser's cross-site document navigation headers. **Expected vs
actual:** a consent landing page; received HTTP 403 Local access required. **Severity:**
blocked owner authorization. **Fix:** allow read-only top-level document navigation to the
landing page only; preserve exact loopback host/origin checks and block cross-site POST,
iframe and event-data requests. Also permit Google's authorization origin in form-action
CSP for the intended redirect. **Validation:** 17 Calendar tests passed; restarted local
server and verified the formerly rejected navigation returns 200. **Suggestion:** include
the actual external-link entry path in browser security regression checks.

Follow-up: owner clarified rejection happened after pressing Continue to Google. The
landing page's no-referrer policy can cause a browser HTML POST to send Origin: null,
conflicting with the same-origin check. Set only the landing page to same-origin referrer
policy; callback and redirect retain no-referrer. Null-origin requests remain rejected.
Added fixed-category diagnostics that never log URLs, codes, cookies or actual origins.
18 Calendar tests passed; actual local HTTP form submission returned 303 to accounts.google.com.
Real browser consent is still awaiting owner verification; no claim of successful access.

### October 9 - Model listing did not mean model access
Task: verify an advanced Bedrock model. Steps: list catalog, inspect GetFoundationModelAvailability, list profiles, make bounded non-personal smoke checks. Expected: an active catalog entry implies usable access. Actual: Claude active profiles report agreement unavailable; Kimi K3 available and a 16-token probe ended before text. Severity: medium. Workaround: distinguish catalog, entitlement/agreement and real invocation; 256-token cap produced text with 31 output tokens. Suggestion: show agreement state beside active profiles and explain reasoning token truncation in the console. No Claude agreement was accepted.

### October 9: Aura hostname unavailable during Cognito onboarding
Task attempted: start the real Neo4j-backed notebook with approved Cognito membership. Steps: loaded existing database configuration, started backend, retried DNS outside sandbox. Expected: server startup and anonymous MCP 401. Actual: Neo4j DNS resolution failed; Vite proxy returned 500 because no backend was listening. Severity: blocks household access. Workaround: owner can complete Cognito enrollment independently; no fake data fallback. Suggestion: expose database connectivity status before presenting notebook actions and provide Aura instance lifecycle diagnostics. Resolution pending.

### October 9: First Cognito invitation login rejected
Task attempted: caregiver signs in with emailed temporary password. Steps: verified enabled FORCE_CHANGE_PASSWORD account, seven-day temporary-password lifetime, matching dashboard client/domain, and official Cognito authentication-flow documentation. Expected: new-password challenge. Actual: owner reports incorrect username/password; deployed client supported refresh only. Severity: blocks onboarding. Workaround under verification: enable ALLOW_USER_PASSWORD_AUTH alongside refresh through a CloudFormation change set limited to DashboardClient, without replacement. No password requested/read, no email-verification bypass, no MFA change. 35 existing authentication/configuration checks passed. Suggestion: CloudFormation validation should flag local Cognito login clients that expose a login page but permit only refresh. Successful user sign-in still required to establish root cause.

October 9 Aura resume follow-up: the saved hostname began resolving through a public DNS resolver, but Windows retained the earlier failure. Explicitly clearing the DNS cache restored real database connectivity; no URI/credential change or database migration was needed. Existing readiness CLI now distinguishes DNS failure and optionally checks MCP's anonymous Bearer 401. Validation: 46 targeted checks passed, including actual HTTP responses/redirect refusal; real Aura plus local protected MCP check exited zero. Authenticated household operations remain a separate verification step.

### October 9: Cognito login succeeded but MCP rejected resource audience
Task attempted: open saved elder-1 through authenticated dashboard. Steps: inspect sanitized server logs and official resource-indicator availability announcement. Expected: access token bound to the MCP resource URL. Actual: aud absent; strict verifier correctly returned 401 while the UI displayed a generic missing-person/server error. Root cause: Lite/classic login was selected even though audience binding requires Essentials/Plus Managed Login. Severity: blocks authenticated notebook access. Workaround prepared: Essentials plus Managed Login v2/default branding, no resource replacements; CloudFormation template validated and change set remains unexecuted pending tier approval. Dashboard now distinguishes access rejection, and tests retain missing/wrong-audience denial. Suggestion: report unsupported resource binding at authorization time rather than silently issuing a token without aud.

### October 9: Generic OAuth callback failure hid the failing step
Task attempted: fresh sign-in after approved Managed Login migration. Steps: confirmed active v2 domain, exact callback, allowed code grant/scopes, public client and token endpoint CORS using a deliberately invalid connectivity probe. Expected: accepted browser callback. Actual: owner saw a generic completion error; browser connection unavailable, so no cause established. Severity: blocks real authenticated notebook testing. Workaround: fixed sanitized AUTH_ diagnostics covering callback/state/storage/expiry, provider rejection, exchange and network/session failure; no raw URLs, authorization codes, tokens or provider descriptions. 54 targeted checks and production build passed; Vite serves the changes. Suggestion: expose a safe failure stage by default instead of collapsing every OAuth error into one message. Fresh owner retry pending.

October 9 callback follow-up: owner reported AUTH_PROVIDER, establishing a provider error before token exchange. Added allowlisted OAuth error categories for request, scope, resource, client, flow, service and login-required failures; arbitrary provider text stays hidden. State, expiry and malformed-error rejection remain enforced. 67 targeted tests and production build passed; localhost serves the new diagnostics. Root cause and successful owner sign-in remain pending a fresh browser attempt. Suggestion: distinguish standardized OAuth failures without exposing callback URLs or personal descriptions.

October 9 AUTH_REQUEST investigation: owner reports immediate return from Sign in. Read-only CloudTrail OAuth2_Authorize_GET events confirm configured client, redirect URI, resource URI, scopes, code flow and S256 method reach Cognito; responseParameters expose only HTTP 302, not the error description. Challenge values in audit logs are not usable to establish PKCE correctness. No browser connection is available. Requested only callback error_description via browser Network panel, excluding code/state/headers. No cloud settings, passwords, MFA policy or access checks changed. Root cause remains unresolved.

October 9 root cause established from owner-provided callback description: custom scopes requested for resource-binding must be assigned to the requested resource. Existing scope belonged to saarthi while resource was the localhost MCP URL. Corrected CloudFormation identifier and dependent client scope, frontend request and backend required scope. Reviewed change set replaced only the OAuth resource registration and modified the existing app client; same user pool, client, MFA, callback and Essentials tier verified live. Backend restarted; real Aura read-only connectivity passed and anonymous MCP initialization returns 401. Scope namespace and invalid-token regressions added. Fresh signed-in owner verification pending; no household records changed.

### October 10: Family records required manual IDs and administrative setup
Task attempted: open and maintain the real household after Cognito login. Expected: choose saved people and review family updates. Actual: the signed-in dashboard required a name/ID and local setup was intentionally inaccessible. Severity: onboarding friction. Workaround: authenticated directory with add/name/relationship/default changes, subject-bound expiring reviews, atomic Neo4j writes and audit records. Review also caught stale relationship metadata when changing the default elder; both repositories now derive it from saved edges. Suggestion: keep identity provisioning separate from household records while providing an authenticated setup path. Full backend checks: 433 passed, 2 opt-in checks skipped before the final focused relationship regression; dashboard: 104 passed, 1 opt-in skipped, production build passed. Real Aura read-only connectivity and anonymous API rejection passed. Signed-in visual confirmation remains pending.

### October 12: Cross-caregiver confirmation and uncertain external outcomes
Task attempted: audit another agent's uncommitted Calendar and family email integrations. Steps: inspect authenticated routes, exercise reviewed writes with signed subjects, inject provider/database failures and run concurrent confirmations against disposable Neo4j. Expected: only the reviewing caregiver can confirm and a retry cannot duplicate a send. Actual: Calendar subject forwarding used bound-method identity comparison; authenticated registration also exposed legacy write routes. Email reviews were ephemeral, recipients were not scoped by relationships, and provider acceptance was described as delivery. Severity: high. Workaround: explicit subject propagation, authenticated-only write registration, relationship-scoped recipients, durable send claims and outcomes, no automatic SES retries, honest accepted/uncertain states, and guarded dashboard refresh. Validation: 497 backend checks passed with 3 opt-in skips; 170 dashboard checks passed with 1 optional integration skip; production build passed. Real Aura connectivity and anonymous MCP rejection passed. No live email or Calendar write was made; owner receipt/save verification remains pending. Suggestion: connector examples should include subject-bound confirmation, restart-safe handling of non-idempotent actions and separate provider-acceptance versus inbox-delivery states.

### October 12: Live owner verification reached Gmail Spam
Task attempted: verify real Calendar saving and family email after the integration repair. Steps: owner signed in, connected Calendar, loaded events, confirmed the appointment appeared in Saarthi and Google, reviewed one family message and confirmed sending. Expected: real external effects and truthful status. Actual: owner received the exact message in the intended Gmail account's Spam folder. Severity: integration works; inbox placement needs improvement before wider use. Read-only SES follow-up: the configured sender is a verified gmail.com email identity; DKIM disabled / NotStarted. The exact Gmail filtering reason is unknown without message authentication results. Workaround: owner can mark this message Not spam; ask for an owned domain before preparing DKIM/aligned sender setup. No settings changed or messages resent. Suggestion: SES onboarding should explain that email-address verification alone is not a production sender-authentication or inbox-placement guarantee. Reference: https://aws.amazon.com/blogs/messaging-and-targeting/choosing-the-right-domain-for-optimal-deliverability-with-amazon-ses/

### October 19: Production build and hosted integration gaps
Task attempted: prepare the caregiver notebook for live submission. Steps: compile production UI, run the full Docker database suite, reproduce repeated Watch checks and oversized digests, exercise parallel dose submissions, build the Linux release image. Expected: deployable build and consistent real care records. Actual: nullable medication draft blocked TypeScript; audit events cleared stale-activity concerns; long notes exceeded the digest review limit; concurrent dose requests could double-count; Calendar storage only supported Windows. Severity: release blocking. Workaround: corrected draft typing, filtered internal events before limiting, bounded digests with explicit omission notices, atomic dose transactions, caregiver controls, authenticated HTTPS callbacks and per-subject Secrets Manager storage. Added CI and a same-origin hosting path. Verification includes 532 backend passes (3 opt-in skips), 207 dashboard passes and a successful frontend production build; full cloud acceptance remains pending. Linux image downloads encountered package-server timeouts; retry uses a longer bounded download timeout. Suggestion: starter integrations should include deployable token storage, complete production build checks and concurrency tests rather than only successful local flows.

### October 19: Local network interrupted verified release delivery
Task attempted: build and publish the Linux release for live reviewer access. Steps: retry PyPI downloads with caching/timeouts, reproduce the build in GitHub Actions, smoke-test HTTP routes, download the verified artifact with checksum/range checks. Expected: transfer the tested image for deployment. Actual: local package and artifact connections repeatedly timed out or ended mid-transfer, while GitHub build and smoke tests passed. Severity: deployment blocking on this network. Workaround: main-branch-only GitHub OIDC role with ECR publishing permissions, no notebook/Secrets Manager/deployment permissions; build publishing waits for backend, frontend and container checks. Suggestion: deployment starters should support verified artifacts and short-lived CI publishing credentials rather than depending on a developer laptop to rebuild and upload images. Additional verification: 534 backend tests passed with the live Bedrock/Strands integration enabled (2 skipped); 215 dashboard tests passed (1 skipped). Nova Pro responded; Claude Sonnet 4.5 agreement remains unavailable. Reviewer password login succeeded without MFA; original owner pool unchanged.

### October 20: GitHub immutable identifiers changed the OIDC subject
Task attempted: publish the checked release image using the restricted GitHub role. Steps: inspect failed role assumption and the corresponding CloudTrail identity, compare the actual subject with the IAM trust condition, update the exact subject and rerun the pipeline. Expected: the main-branch publisher assumes its role. Actual: this repository's subject includes immutable owner/repository IDs, so a name-only trust condition rejected it. Severity: deployment blocking. Workaround: match the complete observed immutable identity and main-branch reference without broadening the trust policy. Backend, dashboard, container and publishing jobs passed; the exact ECR digest was deployed. Suggestion: show the repository's effective OIDC subject alongside setup examples so a secure exact-match policy can be configured without investigating a failed run. Hosted browser acceptance remains pending; successful deployment alone does not establish the full user flow.

### October 20: Managed login reports OAuth disabled despite enabled client
Task attempted: repeat reviewer login after refreshing the live notebook. Steps: owner successfully reset the reviewer password, signed in without MFA and opened saved records, then refreshed and retried in both normal and incognito browsing; inspect deployed client, resource server, domain and sanitized CloudTrail events. Expected: repeatable sign-in. Actual: owner reports "Client is not enabled for OAuth2.0 flows"; API reports AllowedOAuthFlowsUserPoolClient=true and code flow, correct callbacks/scopes, active managed-login v2 domain and confirmed user. Authorization requests match the deployment; subsequent login POSTs return 400. CloudTrail redacts the failure details. Severity: blocks release acceptance. Recovery attempted: re-save all existing writable client settings unchanged, with before/after equality checks; no credentials, user pool, access permissions or MFA settings changed. Root cause and recovery are not established; fresh browser verification is pending. Suggestion: return a stable diagnostic reason that identifies the mismatching configuration field rather than a contradictory generic OAuth-disabled error.

Follow-up: owner confirmed a fresh successful login and showed the saved note recalled after re-login. Recovery was observed after the unchanged configuration was re-saved; this does not establish a root cause.

### October 20: Appointment review rejected harmless surrounding whitespace
Task attempted: review a real Calendar appointment. Steps: owner entered details and received a response-verification error, then removed extra spaces and successfully reviewed and confirmed the event in both Saarthi and Google Calendar. Expected: harmless surrounding whitespace should not block review. Actual: the backend strips whitespace but the frontend compared the cleaned response with the original untrimmed title/timezone. Severity: user-blocking validation bug. Workaround/fix: compare against trimmed draft fields while retaining exact checks for changed appointment content and times. Regression failed before the fix and passed after it; 216 dashboard tests passed, one optional test skipped, production build and all GitHub release checks passed. Suggestion: document input normalization in API contracts and test cross-layer round trips using ordinary pasted text. No event was created by the failed Review operation according to the owner's correction; the successful save followed review and confirmation.


### 2026-10-20 — Bedrock follow-up evidence selection
- Task: make real conversational follow-ups cite freshly verified saved records.
- Steps: invoked the MCP tool with Nova Pro using a randomly named saved item, then requested a family update referring to the preceding answer.
- Expected vs actual: first retrieval succeeded; the follow-up attempted an evidence selection without reading records again and failed closed. Removing old evidence IDs from history alone did not fix it.
- Severity: high for conversational reliability; no external write occurred.
- Workaround: server re-reads bounded previous sources and provides fresh evidence IDs for each follow-up. Controlled regression proves removed records cannot be recovered merely from conversation history.
- Result: real Bedrock recall, follow-up draft, and unknown-item test passed with isolated test data.
- Suggestion: document evidence-refresh patterns for tool-based conversational agents; model instructions alone do not enforce retrieval provenance.

### 2026-10-20 — Browser microphone and robotic playback
- Task: offer natural spoken questions and readable live captions in the family notebook.
- Steps: inspected the browser speech path after the user reported unavailable recognition and unnatural read-aloud; obtained explicit approval for Amazon Transcribe and Polly.
- Expected vs actual: browser recognition and installed voices varied across clients; a supported API did not establish that its provider was reachable.
- Severity: high for the voice interaction; typed questions remained available.
- Workaround: authenticated, short-lived AWS streaming connections, real PCM capture, partial captions, concise evidence-backed spoken excerpts, and Polly Kajal neural playback. Permission and transport failures have specific recovery messages.
- Verification: real AWS synthesized a harmless sentence and Transcribe returned four partial updates and the matching final sentence. Backend authentication/limits, binary-frame integrity, delayed permission cancellation, live transcript replacement, and microphone stop cleanup have regression coverage. Physical microphone quality still requires the user's browser check.
- Suggestion: document a portable browser streaming recipe with permission lifecycle, partial-result replacement, and clear provider diagnostics. Do not treat the presence of browser SpeechRecognition as proof of working recognition.

## October 21 — extracting the reusable voice toolkit
Task attempted: build the release container after extracting shared voice code. Steps: dashboard unit/build checks, public PR CI, inspect Docker source allowlist, local container build. Expected: the extracted TypeScript source enters the image build context. Actual: dashboard/backend CI passed but Docker build excluded dashboard/packages. Severity: release blocker. Workaround: allow only the toolkit source, package metadata and license in .dockerignore; keep tests, local databases and secrets excluded. Suggestion: add new shared source directories to the Docker allowlist in the same change as the extraction and keep container checks required.

Task attempted: run an independent example with the official MCP Python SDK. Steps: use installed pinned SDK 2.1.1, invoke a real TypeScript SDK client over Streamable HTTP with a newly entered note. Expected: current server API and structured answer. Actual: the older FastMCP import is retired in SDK v2, and bare dict inference did not provide the expected structured response. Severity: medium. Workaround: use MCPServer, pass transport settings to run, explicitly declare structured_output and typed dictionary return, and close SQLite connections. Result: arbitrary note and unknown-note checks passed. Suggestion: show a complete v2 client/server example with structured output prominently beside the migration notice.
