/** Opt-in local integration. Creates a novel test household, never reads the Aura graph. */
import { spawn } from 'node:child_process';
import { createServer as tcpServer } from 'node:net';
import { mkdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer as viteServer } from 'vite';
import { expect, it } from 'vitest';
import { createDashboardClient, httpCaller } from './client';

it.runIf(process.env.SAARTHI_RUN_DASHBOARD_HTTP_TEST === '1')('saves and recalls arbitrary records through Vite, official MCP SDK and real Python HTTP server', async () => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const person = 'person-' + randomUUID();
  const marker = 'callback' + randomUUID().replaceAll('-', '');
  const directory = resolve(root, '.test-temp-dashboard-' + randomUUID());
  await mkdir(directory);
  const dataFile = resolve(directory, 'household.json');
  await writeFile(dataFile, JSON.stringify({ primary_person_id: person, people: [{ id: person, name: 'HTTP test ' + randomUUID(), role: 'elder', medications: [] }] }));
  const reserve = tcpServer();
  await new Promise(r => reserve.listen(0, '127.0.0.1', r));
  const address = reserve.address();
  if (!address || typeof address === 'string') throw new Error('No local test port');
  const backendPort = address.port;
  await new Promise((r, reject) => reserve.close(e => e ? reject(e) : r()));
  let backend;
  let frontend;
  try {
    const python = process.env.SAARTHI_TEST_PYTHON ?? resolve(root, process.platform === 'win32' ? 'mcp-server/.venv/Scripts/python.exe' : 'mcp-server/.venv/bin/python');
    backend = spawn(python, ['-m', 'saarthi_mcp'], {
      cwd: root, windowsHide: true, stdio: 'ignore',
      env: { ...process.env, SAARTHI_BACKEND: 'memory', SAARTHI_AGENTS: 'off', SAARTHI_HOST: '127.0.0.1', SAARTHI_PORT: String(backendPort), SAARTHI_MCP_PATH: '/mcp', SAARTHI_HOUSEHOLD_FILE: dataFile },
    });
    let launchError;
    backend.on('error', e => { launchError = e; });
    let ready = false;
    for (let attempt = 0; attempt < 80; attempt++) {
      if (launchError) throw new Error('Test Python could not start; set SAARTHI_TEST_PYTHON.');
      if (backend.exitCode !== null) throw new Error('Local test server exited before startup.');
      try { await fetch(`http://127.0.0.1:${backendPort}/mcp`, { signal: AbortSignal.timeout(500) }); ready = true; break; } catch { await new Promise(r => setTimeout(r, 200)); }
    }
    if (!ready) throw new Error('Test server startup timed out');
    frontend = await viteServer({ configFile: false, root: resolve(root, 'dashboard'), logLevel: 'silent', server: { host: '127.0.0.1', port: 0, proxy: { '/mcp': { target: `http://127.0.0.1:${backendPort}`, changeOrigin: true } } } });
    await frontend.listen();
    const frontAddress = frontend.httpServer.address();
    if (!frontAddress || typeof frontAddress === 'string') throw new Error('No dashboard port');
    const base = `http://127.0.0.1:${frontAddress.port}`;
    expect((await fetch(base)).status).toBe(200);
    const client = createDashboardClient(httpCaller(() => new URL('/mcp', base)));
    const before = await client.load(person);
    expect(before.medications).toEqual([]);
    expect(before.person.id).toBe(person);
    const saved = await client.recordEvent(person, { type: 'call', detail: marker + ' received an unscripted callback' });
    expect(saved.event.detail).toContain(marker);
    const fresh = createDashboardClient(httpCaller(() => new URL('/mcp', base)));
    const after = await fresh.load(person);
    expect(after.graph.nodes.length).toBe(before.graph.nodes.length + 1);
    const answer = await fresh.queryMemory(person, marker);
    expect(answer.answer).toContain(marker);
    expect(answer.supporting_events[0].detail).toBe(saved.event.detail);
    await expect(fresh.load('unknown-' + randomUUID())).rejects.toThrow();
  } finally {
    await frontend?.close();
    if (backend && backend.exitCode === null && backend.pid) {
      const exited = new Promise(r => backend.once('exit', () => r()));
      backend.kill();
      await exited;
    }
  }
}, 60_000);
