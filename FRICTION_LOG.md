# FRICTION_LOG.md — Saarthi

> One honest entry per snag. Format: **Task · steps · expected vs actual · severity ·
> workaround · one actionable suggestion.** Amazon reads these; aim for 8–15 across the build.

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
