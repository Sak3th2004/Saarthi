import { describe, expect, it, vi } from 'vitest';
import { createDashboardClient, httpCaller, parseView } from './client';
import { NotebookAccessError } from './notebookErrors';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryMap } from './Graph';
import type { MemoryGraph } from './contracts';

function graph() {
  return {
    schema_version: 1, person_id: 'arbitrary-person', generated_at: '2026-10-06T12:00:00Z',
    nodes: [
      { id: 'root', kind: 'person', label: 'A user', record: { id: 'arbitrary-person', name: 'A user', role: 'elder' } },
      { id: 'saved', kind: 'event', label: 'call', record: { type: 'call', detail: 'A newly supplied callback', at: '2026-10-06T10:00:00+05:30' } },
    ],
    edges: [{ source: 'root', target: 'saved', relation: 'EXPERIENCED' }],
    truncated: { event: false, medication: false, appointment: false }, speech: 'Saved records',
  };
}

function familyGraph(): MemoryGraph {
  const g = graph() as MemoryGraph;
  // A neighbor comes before the root to prove root selection uses the owner ID.
  g.nodes.unshift({ id: 'family-node', kind: 'person', label: 'Entered family name', record: { id: 'family-id', name: 'Entered family name', role: 'family' } });
  g.edges.push({ source: 'family-node', target: 'root', relation: 'RELATED_TO', detail: 'daughter' });
  g.edges.push({ source: 'root', target: 'family-node', relation: 'RELATED_TO', detail: 'parent' });
  g.truncated.person = false;
  return g;
}

describe('real-record contract', () => {
  it.each([401, 403])('maps HTTP %s from the actual MCP transport to a safe access error', async status => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('private server response', {
      status, headers: { 'WWW-Authenticate': 'Bearer realm="mcp"' },
    }));
    vi.stubGlobal('fetch', fetcher);
    try {
      const call = httpCaller(() => new URL('http://localhost:5173/mcp'), () => 'test-only-token');
      await expect(call('get_memory_graph', { person: 'saved-person' })).rejects.toBeInstanceOf(NotebookAccessError);
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });
  it('passes the explicitly selected timezone through MCP recall', async () => {
    const call = vi.fn().mockResolvedValue({ person: { id: 'person', name: 'Entered name', role: 'elder' }, question: 'callback today', answer: 'Saved fact', supporting_events: [], speech: 'Saved fact' });
    await createDashboardClient(call).queryMemory('person', 'callback today', 'Asia/Kolkata');
    expect(call).toHaveBeenCalledExactlyOnceWith('query_memory', { person: 'person', question: 'callback today', time_zone: 'Asia/Kolkata' });
  });
  it('loads from the graph without model calls or invented empty-state records', async () => {
    const call = vi.fn().mockResolvedValue(graph());
    const view = await createDashboardClient(call).load('a saved alias');
    expect(call).toHaveBeenCalledExactlyOnceWith('get_memory_graph', { person: 'a saved alias', limit: 30 });
    expect(view.person.id).toBe('arbitrary-person');
    expect(view.events[0].detail).toBe('A newly supplied callback');
    expect(view.medications).toEqual([]);
    expect(view.appointments).toEqual([]);
  });
  it.each(['mismatched person', 'dangling edge', 'wrong relationship', 'duplicate node', 'no time offset'])('rejects %s', corruption => {
    const g = graph();
    if (corruption === 'mismatched person') g.person_id = 'other-person';
    if (corruption === 'dangling edge') g.edges[0].target = 'missing';
    if (corruption === 'wrong relationship') g.edges[0].relation = 'TAKES';
    if (corruption === 'duplicate node') g.nodes.push(g.nodes[0]);
    if (corruption === 'no time offset') g.generated_at = '2026-10-06T12:00:00';
    expect(() => parseView(g)).toThrow();
  });
  it('never retries an unconfirmed mutation', async () => {
    const call = vi.fn().mockRejectedValue(new Error('connection lost'));
    await expect(createDashboardClient(call).recordEvent('person', { type: 'note', detail: 'new' })).rejects.toThrow();
    expect(call).toHaveBeenCalledTimes(1);
  });
  it('rejects answers belonging to another person', async () => {
    const call = vi.fn().mockResolvedValue({ person: { id: 'other', name: 'Other', role: 'elder' }, question: 'callback?', answer: 'Private', supporting_events: [], speech: 'Private' });
    await expect(createDashboardClient(call).queryMemory('person', 'callback?')).rejects.toThrow();
  });
  it('loads saved family links in their original directions without changing the notebook owner', () => {
    const view = parseView(familyGraph());
    expect(view.person.id).toBe('arbitrary-person');
    expect(view.graph.nodes.filter(node => node.kind === 'person')).toHaveLength(2);
    expect(view.graph.edges.filter(edge => edge.relation === 'RELATED_TO')).toEqual([
      { source: 'family-node', target: 'root', relation: 'RELATED_TO', detail: 'daughter' },
      { source: 'root', target: 'family-node', relation: 'RELATED_TO', detail: 'parent' },
    ]);
    expect(view.events).toHaveLength(1);
  });
  it.each(['disconnected person', 'duplicate person ID', 'nonperson endpoint', 'no root endpoint', 'missing detail', 'blank detail', 'duplicate edge', 'self edge', 'contact field', 'bad truncation'])('rejects family graph with %s', corruption => {
    const g = familyGraph();
    if (corruption === 'disconnected person') g.edges = g.edges.filter(edge => edge.relation !== 'RELATED_TO');
    if (corruption === 'duplicate person ID') g.nodes[0].record.id = g.person_id;
    if (corruption === 'nonperson endpoint') g.edges[1].source = 'saved';
    if (corruption === 'no root endpoint') {
      g.nodes.push({ id: 'another', kind: 'person', label: 'Another', record: { id: 'another-id', name: 'Another', role: 'family' } });
      g.edges[1].target = 'another';
    }
    if (corruption === 'missing detail') delete g.edges[1].detail;
    if (corruption === 'blank detail') g.edges[1].detail = '  ';
    if (corruption === 'duplicate edge') g.edges.push({ ...g.edges[1] });
    if (corruption === 'self edge') g.edges[1].target = 'family-node';
    if (corruption === 'contact field') g.nodes[0].record.email = 'private@example.invalid';
    if (corruption === 'bad truncation') Object.assign(g.truncated, { person: 'false' });
    expect(() => parseView(g)).toThrow();
  });
  it('retains compatibility with the original one-person graph and nullable detail serialization', () => {
    const g = graph();
    Object.assign(g.edges[0], { detail: null });
    expect(parseView(g).graph.nodes).toHaveLength(2);
    expect(parseView(g).graph.truncated.person).toBeUndefined();
  });
  it('renders directed relationship labels and keeps the identified owner at the center', () => {
    const html = renderToStaticMarkup(createElement(MemoryMap, { graph: parseView(familyGraph()).graph }));
    expect(html).toContain('value="person"');
    expect(html).toContain('Entered family name → A user: daughter');
    expect(html).toContain('A user → Entered family name: parent');
    expect(html).toContain('marker-end=');
    expect(html).toMatch(/aria-label="person: A user"[^>]*transform="translate\(300, 190\)"/);
    expect(html).not.toContain('private@example.invalid');
  });
});
