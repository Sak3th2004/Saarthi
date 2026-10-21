# Voice Turns

A small MIT-licensed TypeScript controller for bounded browser voice conversations with MCP read-and-draft tools. No runtime dependencies, React requirement, cloud account, bundled model, or credentials. Extracted from the working Saarthi companion; Saarthi imports this source directly.

## Build and test independently

Copy this folder into a standalone directory. With Node 22 or newer:

```sh
npm install
npm test
npm run build
```

## Run a real local MCP request

Install the official MCP Python SDK in a virtual environment: `python -m pip install -r examples/requirements.txt`. This example uses the v2 `MCPServer` API.

```sh
python examples/server.py --save-note "Write a new harmless fact of your own here."
python examples/server.py
```

In another terminal, from this package folder:

```sh
node examples/read-note.mjs a phrase from your note
```

This uses the official TypeScript SDK and Streamable HTTP to read your SQLite note through a real MCP tool. The example performs literal phrase matching; it is not an LLM or a demonstration of semantic retrieval. No supplied notes means no results. The database stays in `examples/notes.sqlite3` and is gitignored. The example server listens on loopback only and has no authentication: do not expose it publicly.

## Connect a browser voice provider

```ts
import { startConversationSession, createMcpAsk, playVoiceBlob } from './dist/index.js';

// Supply an already-connected official MCP client and application-specific
// validation. Authentication and household/actor scope belong to your server.
const ask = createMcpAsk(mcpClient, {
  tool: 'your_approved_read_or_draft_tool',
  argumentsFor: question => ({ question }),
  parse: validateYourAnswer,
});

// Start only inside an explicit user action, after showing the auto-send notice.
const session = startConversationSession({
  capture: callbacks => voiceProvider.listen(callbacks),
  ask,
  play: async (text, signal, started) => {
    const blob = await voiceProvider.synthesize(text, signal);
    await playVoiceBlob(blob, signal, started);
  },
  phase: showPhase,
  transcript: showLiveTranscript,
  level: showInputLevel,
  countdown: showTurnCountdown,
  reply: showValidatedAnswer,
  ended: showEndReason,
});
// On End, hidden tab, navigation, logout, or actor change:
session.stop();
```

`capture` must return `{ stop(), abort() }`. `stop` closes capture and flushes final captions before calling `ended`; `abort` immediately releases microphone, stream, worklet and timers. Emit `started` only after recording begins, `transcript(text, interim)` with the complete current utterance, and `level` with a finite nonnegative normalized input-energy level. `failed` ends the session. The provider must impose its own setup timeout and per-turn recording limit; Saarthi's AWS adapter uses 45 seconds for setup and 60 seconds for capture. The package does not implement recognition or synthesis itself.

`play` must resolve only when audio ends and release playback when its signal is aborted. `ask` must validate the tool result before returning `{ speech, action? }`; `action` denotes a draft requiring human review. This controller never executes the draft. It stops after playback when a draft exists.

## Behavior and limits

- Energy and transcript changes determine a pause boundary: at least 3.2 seconds since audible input and 1.8 seconds since changed words. Final captions are required before submission.
- One capture, request and playback at a time. Capture is closed during requests and audio playback.
- Six turns or three minutes maximum; silence without words stops after 25 seconds of recording. Request timeout 45 seconds; playback timeout 60 seconds.
- Cancel invalidates late results and aborts pending work. No automatic retries or background conversation.
- Tests cover pauses, partial/final races, cancellation, timeout, draft boundaries, callback order, MCP error responses and audio cleanup.

This is pause detection, not a speech/person classifier, wake-word engine or full-duplex barge-in system. Background noise can delay completion. Device recognition and playback behavior still need real browser/microphone testing. Server authorization, spending limits, privacy notices, record persistence and action confirmation remain application responsibilities. MCP annotations are hints, not security controls. Bind only a developer-approved read/draft tool, never a general tool dispatcher or a sending/purchasing tool.

## License and provenance

MIT, copyright 2026 Sai Saketh Ram G and Saarthi contributors. This package contains no Amazon branding or Alexa connection. Optional example development dependency: official MCP TypeScript client; optional Python example dependency: official MCP Python SDK. No npm package publication is implied by this source tree.
