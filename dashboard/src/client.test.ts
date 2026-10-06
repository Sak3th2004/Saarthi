import { describe, expect, it, vi } from 'vitest';
import { createDashboardClient, parseView } from './client';

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

describe('real-record contract', () => {
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
});
