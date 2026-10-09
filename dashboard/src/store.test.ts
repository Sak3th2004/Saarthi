import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { App } from './App';
import { createDashboardStore } from './store';
import type { DashboardClient, HouseholdView, MemoryAnswer } from './contracts';

const person = { id: 'saved-person', name: 'User supplied name', role: 'elder' as const };
const view = { person, medications: [], appointments: [], events: [], graph: { schema_version: 1, person_id: person.id, generated_at: '2026-10-06T10:00:00Z', nodes: [], edges: [], truncated: {}, speech: '' } } satisfies HouseholdView;
function stub() {
  return { load: vi.fn().mockResolvedValue(view), recordEvent: vi.fn().mockResolvedValue({ person, event: { type: 'note', detail: 'saved', at: '2026-10-06T10:00:00Z' }, speech: 'Saved' }), queryMemory: vi.fn() } satisfies DashboardClient;
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }

describe('dashboard lifecycle', () => {
  it('does not expose local administrator or Calendar controls in authenticated mode', () => {
    const html = renderToStaticMarkup(createElement(App, { client: stub(), localTools: false }));
    expect(html).not.toContain('Check household status');
    expect(html).not.toContain('Connect calendar on this computer');
    expect(html).toContain('provided by your caregiver');
  });
  it('starts empty and makes no automatic requests or demo claims', () => {
    const client = stub();
    const html = renderToStaticMarkup(createElement(App, { client }));
    expect(html).toContain('Your notebook is waiting.');
    expect(html).not.toContain(person.name);
    expect(client.load).not.toHaveBeenCalled();
    expect(html).not.toContain('100%');
  });
  it('discards stale loads when the user opens a different person', async () => {
    const client = stub();
    const delayed = deferred<HouseholdView>();
    client.load.mockReturnValueOnce(delayed.promise).mockResolvedValueOnce({ ...view, person: { ...person, id: 'second' } });
    const store = createDashboardStore(client);
    const first = store.load('first');
    await store.load('second');
    delayed.resolve(view); await first;
    expect(store.getSnapshot().view?.person.id).toBe('second');
  });
  it('clears private data if a later connection fails', async () => {
    const client = stub(); const store = createDashboardStore(client);
    await store.load('person'); client.load.mockRejectedValueOnce(new Error('not found'));
    await store.load('unknown');
    expect(store.getSnapshot().view).toBeNull();
    expect(store.getSnapshot().phase).toBe('error');
  });
  it('preserves save acknowledgement when refreshing fails', async () => {
    const client = stub(); const store = createDashboardStore(client);
    await store.load('person'); client.load.mockRejectedValueOnce(new Error('offline'));
    expect(await store.save('note', 'arbitrary input')).toBe(true);
    expect(store.getSnapshot().notice).toContain('was saved');
    expect(store.getSnapshot().notice).toContain('could not refresh');
    expect(client.recordEvent).toHaveBeenCalledExactlyOnceWith(person.id, { type: 'note', detail: 'arbitrary input' });
  });
  it('does not double-submit or silently retry a failed save', async () => {
    const client = stub(); const store = createDashboardStore(client);
    await store.load('person');
    const delayed = deferred<never>(); client.recordEvent.mockReturnValueOnce(delayed.promise);
    const first = store.save('note', 'one');
    expect(await store.save('note', 'one')).toBe(false);
    delayed.resolve(undefined as never); await first;
    client.recordEvent.mockRejectedValueOnce(new Error('unknown outcome'));
    expect(await store.save('note', 'two')).toBe(false);
    expect(client.recordEvent).toHaveBeenCalledTimes(2);
    expect(store.getSnapshot().saveError).toContain('before submitting again');
  });
  it('discards an old answer after household changes', async () => {
    const client = stub(); const store = createDashboardStore(client);
    await store.load('person');
    const delayed = deferred<MemoryAnswer>(); client.queryMemory.mockReturnValueOnce(delayed.promise);
    const query = store.query('old question');
    await store.load('another');
    delayed.resolve({ person, question: 'old question', answer: 'private', supporting_events: [], speech: 'private' }); await query;
    expect(store.getSnapshot().answer).toBeNull();
  });
});
