// Run the local example server first. No credentials or paid services required.
import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';
import { createMcpAsk } from '../dist/index.js';

const question = process.argv.slice(2).join(' ').trim();
if (!question) throw new Error('Usage: node examples/read-note.mjs words to find');
const client = new Client({ name: 'voice-turns-example', version: '0.1.0' });
try {
  await client.connect(new StreamableHTTPClientTransport(new URL('http://127.0.0.1:8765/mcp')));
  const ask = createMcpAsk(client, {
    tool: 'read_note', argumentsFor: question => ({ question }),
    parse(value) {
      if (!value || typeof value.speech !== 'string' || value.action !== null) throw new Error('Invalid read-only answer');
      return value;
    },
  });
  console.log(JSON.stringify(await ask(question, new AbortController().signal), null, 2));
} finally { await client.close(); }
