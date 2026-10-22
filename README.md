# Saarthi — a family notebook that remembers

Saarthi helps a family keep track of an older adult's notes, relationships, medication records and appointments. Ask by voice or text, inspect the supporting record in the memory graph, then review an appointment or family message before anything is sent.

**[Open the live notebook](https://dj2rbmh5yr.us-east-1.awsapprunner.com)** · **[Reusable Voice Turns toolkit](https://github.com/Sak3th2004/voice-turns)** · **[MIT license](./LICENSE)**

Amazon Build, Ship, Shape entry: Alexa+ web experience, AWS Builder and Open Source. This is an **Alexa-style web simulation backed by a real MCP server and live services**, not an Alexa device connection or Alexa's voice. The deployed application release is `8395d12`; later documentation-only commits do not change that running image.

## Try it as a judge

Use the reviewer credentials supplied **privately in the submission's testing instructions**. No AWS account or installation is needed. The reviewer account does not require MFA; it opens one shared demonstration household, not a separate private household for each visitor. Use harmless invented details, not personal health information.

1. Open the live notebook, sign in, and open the saved older adult's notebook from **People & relationships**.
2. Save a harmless note with a detail you choose. Ask about it using **Talk to Saarthi** or **Find a saved detail**. Inspect **Supporting records** and **See in saved connections**. Ask about something you have not recorded: an unknown answer should remain unknown.
3. Refresh or sign out and back in, reopen the notebook and ask about your note. Saved graph records persist across sessions; the conversation's short context is separate.
4. With microphone permission, choose **Start conversation**, speak, pause and ask a follow-up. **End conversation** stops the session, including playback. Typed questions and manual microphone/playback controls remain available.
5. Try an appointment or family-message draft. Review the recipient, date and timezone before confirming. Preview does not send. Confirmation makes a **real external change**: a Google Calendar event or SES email, with a matching household record.

### Integration limits for reviewers

- **Google Calendar:** the hosted release is bound to its configured, approved Google account; arbitrary visitors cannot attach another Google account. The reviewer can use an existing connection without knowing its Google password. If it needs reconnecting, contact the submitter. Calendar events belong to that account and are not automatically assigned to a family member. A calendar event is not a booking with a clinic.
- **Email:** SES is in sandbox mode. Use the saved, verified family recipient in the private instructions. Other recipients cannot receive email until verified; delivery may reach Spam. A successful send response is not proof of inbox placement.
- **Voice:** requires HTTPS, microphone permission, AudioWorklet support and secure WebSocket access on port 8443. If blocked, use text. Audio is sent to Amazon Transcribe and spoken text to Amazon Polly; Saarthi does not persist microphone recordings.
- **Check saved records:** this is an on-demand review of recorded concerns. Missing medication or activity data cannot establish anyone's wellbeing. It does not automatically contact the family.

## What is implemented

| Area | Running behavior |
|---|---|
| MCP | Official Python SDK server and TypeScript SDK client, Streamable HTTP; protocol negotiation covered by integration tests |
| Persistent memory | Neo4j Aura stores people, relationships, events, medication/dose records and appointments; the UI renders these records and exact citation links |
| Conversation | Strands on Amazon Bedrock, currently `us.amazon.nova-pro-v1:0`; bounded read-tool selection and saved excerpts with supporting evidence |
| Family notebook | React, TypeScript and Vite; Cognito sign-in, approved-subject access, graph, timeline and reviewed forms |
| Appointments | Google Calendar read and reviewed creation; reconciliation handles a calendar write that succeeded before a memory save failed |
| Family updates | Reviewed SES messages and manually requested weekly digests; no scheduled automatic digest |
| Care records | Medication schedules, taken/missed dose logging, adherence views and on-demand Watch checks; no prescribing or diagnosis |
| Voice companion | Amazon Transcribe streaming captions, Amazon Polly Kajal neural voice, bounded conversational turns and an original Saarthi character |
| Hosting | AWS App Runner, immutable ECR image, Secrets Manager, IAM, CloudFormation and GitHub Actions |

Specialist routing covers MedGuardian, Appointments, FamilyLoop and Watch. Domain tools remain bounded and testable. The conversation model cannot independently send email or create calendar events: drafts move into existing review and confirmation flows.

**Not shipped:** AgentCore Runtime or Memory, SMS, background reminders, autonomous purchasing, actual Alexa-device integration, unrestricted multi-household signup or general medical reasoning. The table above describes the implemented release.

## Architecture

```text
Browser family notebook
  ├─ official MCP client → authenticated Streamable HTTP /mcp
  ├─ voice capture → signed Amazon Transcribe stream
  └─ short-answer playback ← Amazon Polly
                         │
AWS App Runner: Python MCP server + React static assets
  ├─ Cognito identity + approved household subjects
  ├─ Strands / Bedrock → bounded specialist and conversation tools
  ├─ Neo4j Aura → durable household records + evidence graph
  ├─ reviewed Google Calendar / SES actions
  └─ Secrets Manager → service secrets + subject-scoped Calendar tokens
```

One deployment serves one household. Saved records live in Neo4j and hosted Calendar tokens in Secrets Manager. Pending OAuth flows, confirmation reviews, short conversation context and voice rate counters are in memory: a restart expires these temporary states. Start a fresh review after a restart; do not interpret a lost response as a successful send. `/health` is liveness, not proof that every integration is available.

## Run from source

Requires **Python 3.11+** and **Node 22.12+**. Docker is needed for the isolated database integration suite. Clone, then run installation commands from the repository root:

```sh
git clone https://github.com/Sak3th2004/Saarthi.git
cd Saarthi
```

Windows PowerShell:

```powershell
python -m venv mcp-server/.venv
mcp-server/.venv/Scripts/python.exe -m pip install -c constraints.txt mcp
mcp-server/.venv/Scripts/python.exe -m pip install -c constraints.txt -e ./agents -e "./mcp-server[dev,agents,calendar]"
Copy-Item .env.example mcp-server/.env
```

Linux/macOS:

```sh
python3 -m venv mcp-server/.venv
mcp-server/.venv/bin/python -m pip install -c constraints.txt mcp
mcp-server/.venv/bin/python -m pip install -c constraints.txt -e ./agents -e './mcp-server[dev,agents,calendar]'
cp .env.example mcp-server/.env
```

Copy the example only on a fresh installation; preserve existing private configuration. In `mcp-server/.env`, set:

```dotenv
SAARTHI_HOST=127.0.0.1
SAARTHI_PORT=8080
SAARTHI_AUTH=local
SAARTHI_LOCAL_SETUP=1
SAARTHI_BACKEND=memory
SAARTHI_AGENTS=off
SAARTHI_GOOGLE_CONFIG=
```

Start the server in the first terminal:

```powershell
mcp-server/.venv/Scripts/python.exe -m saarthi_mcp
```

On Linux/macOS substitute `mcp-server/.venv/bin/python`. In a second terminal:

```sh
cd dashboard
npm ci
npm run dev
```

Open `http://localhost:5173`. With no dashboard configuration, local authentication is the default. For an existing checkout, remove stale `VITE_COGNITO_*` overrides and use `VITE_AUTH_MODE=local` in ignored `dashboard/.env.local`. Local household setup lets you create your own records; there are no built-in answers or demonstration records. This configuration uses temporary in-memory storage and direct handlers, so it does not demonstrate hosted AI, Calendar or email. Stop each process with Ctrl+C.

For persistence set `SAARTHI_BACKEND=neo4j` and supply `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` and `NEO4J_DATABASE` for your own database. Local unauthenticated mode must remain on loopback. Never expose it publicly.

### Enable real services

Use [`.env.example`](./.env.example) and the committed [hosting template](./infra/hosted-notebook.json) as the configuration reference. Secrets belong in ignored environment files locally and Secrets Manager when hosted.

- **Bedrock:** provide AWS credentials through the normal SDK credential chain, `AWS_REGION=us-east-1`, `SAARTHI_AGENTS=bedrock` and a model ID your account can invoke. This release uses `BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0`. Calls incur charges. A failed model call never invents a successful write.
- **Cognito:** use a user pool, OAuth authorization-code app client and exact callback/logout/resource URLs. Set `COGNITO_ALLOWED_SUBJECTS` to explicitly approved subject IDs. Client IDs are public; passwords, tokens and secrets are not.
- **Calendar:** enable Google Calendar API and configure a web OAuth client, approved account and exact `/oauth/google/callback` URI for the serving origin. The hosted template selects cloud token storage; local setup is described in the [server's Calendar instructions](./mcp-server/README.md#local-google-calendar-connection-windows). Add the approved Google account as a test user if the OAuth app is in Testing.
- **SES:** configure a verified sender with `SES_SENDER_EMAIL`; sandbox recipients must also be verified. Reviewed messages use the saved family recipient.
- **AWS voice:** set `SAARTHI_AWS_VOICE=1` with Cognito authentication and the Polly/Transcribe permissions in the hosting template. Local mode can use available browser speech APIs instead.

Inspect local MCP with `npx @modelcontextprotocol/inspector`: select Streamable HTTP at `http://127.0.0.1:8080/mcp`. The deployed endpoint requires authorized OAuth; anonymous calls are rejected.

Build the container from the root with `docker build -f mcp-server/Dockerfile -t saarthi:local .`. The hosted template requires existing Cognito, database, Google and SES resources; it does not create their accounts or accept provider agreements. Configure exact public callbacks before using a new deployment. Do not deploy the local unauthenticated configuration.

## Voice behavior and limits

**Start conversation** explicitly enables automatic submission after a speech pause and automatic playback. The microphone closes during model requests and playback, then opens for a follow-up. Drafts pause for review. End, page hiding, unmount, errors, six turns or three minutes stop the session. Incomplete final captions are retained for manual review instead of submitted. There is no wake word or full-duplex interruption.

Turn detection combines audio levels and transcript changes; it is a heuristic, not a speech classifier. Background noise and long pauses can affect timing. Manual text and microphone modes remain available. Supporting evidence stays visible even when speech is abbreviated.

Signed streaming links expire after 30 seconds. Manual capture stops after a minute. Server rolling limits permit 20 stream links per actor/hour, 100 links globally/day, 100 speech requests per actor/hour and 100,000 characters globally/day. These counters reset on process restart and **are not a spending cap**. Browser capture duration is also not a server-enforced billing limit. Review AWS billing separately.

## Verification

From the root (substitute the Linux/macOS Python path where appropriate):

```powershell
mcp-server/.venv/Scripts/python.exe scripts/check.py
mcp-server/.venv/Scripts/python.exe scripts/check.py --neo4j
```

The second command starts and stops a dedicated disposable Docker test graph on port 17687; it does not reset the hosted household. Both commands disable paid model and email tests. In `dashboard`:

```sh
npm test
npm run build
```

Release `8395d12` passed **572 backend tests (4 skipped)** with the database suite, **262 dashboard/toolkit tests (1 skipped)** and the production build. Five separate bounded real Bedrock checks passed using disposable generated records. GitHub Actions builds and smoke-tests the release container before publishing its immutable image. These results do not imply every browser or provider failure has been tested.

For the reusable toolkit alone, run `npm ci`, `npm test`, and `npm run build` in `dashboard/packages/voice-turns`; its 17 tests are included in the dashboard total above. Its [official-SDK example](./dashboard/packages/voice-turns/examples) runs a real Streamable HTTP client/server using notes supplied by the caller.

## Open-source contribution

**[Voice Turns](https://github.com/Sak3th2004/voice-turns)** is a separate public MIT project extracted from the controller actually used by Saarthi. It provides provider-neutral bounded voice turns, cancellation, final-caption handling, abortable playback and a fixed-tool MCP read/draft adapter, with zero runtime dependencies. It includes tests and a runnable official-MCP-SDK example. It is source-distributed, not published to npm, and does not claim speech recognition or model intelligence of its own.

## Safety and operational limits

- Tracks saved facts and coordinates people; no diagnosis, dosing advice or autonomous medical action.
- Calendar and email writes require explicit review and confirmation. Recorded concerns do not automatically notify anyone.
- The reviewed household is shared between approved accounts. This release is not a public, multi-tenant care service.
- Google OAuth Testing refresh tokens can expire after seven days. The owner must reconnect when required; see [Google's token-expiration guidance](https://developers.google.com/identity/protocols/oauth2/#expiration).
- Aura Free can pause after 72 hours of inactivity. Check and resume it as necessary before demonstrations and throughout judging; see [Neo4j instance actions](https://neo4j.com/docs/aura/managing-instances/instance-actions/).
- Keep the app and connected services available through judging. Budget alerts and voice rate limits are not hard account-wide spending limits.

The [friction log](./FRICTION_LOG.md) records development obstacles, workarounds and product feedback. Entries describe what happened on their recorded dates; the implemented-feature table above describes the current release.

## License

[MIT](./LICENSE).
