import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { HouseholdManager, describeHouseholdChange } from './HouseholdManager';
import { HouseholdManagementError, createHouseholdManagementClient, householdManagementErrorMessage, parseHouseholdDirectory, parseHouseholdChangeReview, parseHouseholdChangeSaved, type HouseholdChange, type HouseholdDirectory } from './householdManagementClient';

const directory = (): HouseholdDirectory => ({ revision: 'a'.repeat(64), persistence: 'persistent', primary_person_id: 'one', people: [
  { id: 'one', name: 'Entered older adult', role: 'elder' }, { id: 'two', name: 'Entered relative', role: 'family' },
], relationships: [{ from_person: 'two', to_person: 'one', relation: 'son' }] });
const change = (): HouseholdChange => ({ kind: 'rename_person', person_id: 'one', name: 'Corrected name' });
const review = () => ({ review_token: 'opaque-review', change: change(), persistence: 'persistent' as const, expires_in_seconds: 600 as const });

describe('household management response verification', () => {
  it('loads arbitrary saved names and an empty household without inventing profiles', () => {
    expect(parseHouseholdDirectory(directory())).toEqual(directory());
    expect(parseHouseholdDirectory({ ...directory(), primary_person_id: null, people: [], relationships: [] }).people).toEqual([]);
  });
  it.each([
    { revision: 'invalid' }, { persistence: 'pretend' }, { primary_person_id: 'two' }, { primary_person_id: 'missing' },
    { people: [directory().people[0], directory().people[0]] },
    { relationships: [{ from_person: 'two', to_person: 'missing', relation: 'son' }] },
    { relationships: [{ from_person: 'one', to_person: 'one', relation: 'son' }] },
    { relationships: [...directory().relationships, ...directory().relationships] },
    { people: [{ id: 'one', name: '<script>', role: 'admin' }] },
  ])('rejects inconsistent directory data: %j', replacement => {
    expect(() => parseHouseholdDirectory({ ...directory(), ...replacement })).toThrow();
  });
  it('accepts reordered review keys but rejects changed or additional unseen details', () => {
    expect(parseHouseholdChangeReview({ ...review(), change: { name: 'Corrected name', person_id: 'one', kind: 'rename_person' } }, change(), 'persistent')).toEqual(review());
    for (const replacement of [
      { change: { ...change(), name: 'Someone else' } }, { change: { ...change(), person_id: 'two' } },
      { change: { ...change(), email: 'unrequested@example.invalid' } }, { change: { kind: 'delete_person', person_id: 'one' } },
      { persistence: 'session' }, { expires_in_seconds: 3600 }, { review_token: '' },
    ]) expect(() => parseHouseholdChangeReview({ ...review(), ...replacement }, change(), 'persistent')).toThrow();
  });
  it('rejects directories beyond the supported household limits', () => {
    const people = Array.from({ length: 101 }, (_, index) => ({ id: `person-${index}`, name: `Person ${index}`, role: 'family' }));
    expect(() => parseHouseholdDirectory({ ...directory(), primary_person_id: null, people, relationships: [] })).toThrow();
    const relationships = people.slice(1).flatMap(from => people.slice(1).filter(to => to.id !== from.id).map(to => ({ from_person: from.id, to_person: to.id, relation: 'relative' }))).slice(0, 501);
    expect(() => parseHouseholdDirectory({ ...directory(), primary_person_id: null, people: people.slice(1), relationships })).toThrow();
  });
  it('verifies the saved effect for every change rather than trusting a success label', () => {
    const changes: HouseholdChange[] = [
      { kind: 'add_person', id: 'new-person', name: 'New relative', role: 'family' }, change(),
      { kind: 'set_primary', person_id: 'second-elder' },
      { kind: 'set_relationship', from_person: 'two', to_person: 'one', relation: 'elder son' },
    ];
    for (const changed of changes) {
      const pending = { ...review(), change: changed };
      expect(() => parseHouseholdChangeSaved({ status: 'saved', directory: directory() }, pending)).toThrow();
      const saved = directory();
      if (changed.kind === 'add_person') saved.people.push({ id: changed.id, name: changed.name, role: changed.role });
      if (changed.kind === 'rename_person') saved.people[0].name = changed.name;
      if (changed.kind === 'set_primary') { saved.people.push({ id: changed.person_id, name: 'Another older adult', role: 'elder' }); saved.primary_person_id = changed.person_id; }
      if (changed.kind === 'set_relationship') saved.relationships[0].relation = changed.relation;
      expect(parseHouseholdChangeSaved({ status: 'saved', directory: saved }, pending)).toEqual(saved);
      expect(() => parseHouseholdChangeSaved({ status: 'pending', directory: saved }, pending)).toThrow();
      expect(() => parseHouseholdChangeSaved({ status: 'saved', directory: { ...saved, persistence: 'session' } }, pending)).toThrow();
    }
  });
});

describe('household management transport', () => {
  it('uses same-origin endpoints with the current bearer, without cookies, redirects or stored tokens', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json(directory())).mockResolvedValueOnce(Response.json(review()));
    let token = 'current-session';
    const client = createHouseholdManagementClient(() => token, fetcher);
    const status = await client.status(); token = 'new-session';
    await client.review(status, change());
    expect(fetcher).toHaveBeenNthCalledWith(1, '/household/manage/status', expect.objectContaining({
      method: 'POST', credentials: 'omit', redirect: 'error', body: '{}', signal: expect.any(AbortSignal),
      headers: { Authorization: 'Bearer current-session', 'Content-Type': 'application/json' },
    }));
    expect(fetcher).toHaveBeenNthCalledWith(2, '/household/manage/review', expect.objectContaining({
      body: JSON.stringify({ expected_revision: status.revision, change: change() }),
      headers: { Authorization: 'Bearer new-session', 'Content-Type': 'application/json' },
    }));
  });
  it.each([[401, 'Sign out'], [403, 'Sign out'], [409, 'Refresh'], [503, 'Check household status'], [400, 'Check the names']])('maps HTTP %i to a safe actionable message', async (status, expected) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: 'SECRET PROVIDER TEXT' }, { status: status as number }));
    await expect(createHouseholdManagementClient(() => 'token', fetcher).status()).rejects.toThrow(expected as string);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('never sends a request when session access is missing or expired', async () => {
    const fetcher = vi.fn<typeof fetch>();
    for (const getToken of [() => '', () => { throw new Error('token details'); }]) {
      await expect(createHouseholdManagementClient(getToken, fetcher).status()).rejects.toThrow('sign-in');
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('does not retry an ambiguous save or expose a transport error', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error('private transport detail'));
    await expect(createHouseholdManagementClient(() => 'token', fetcher).confirm(review())).rejects.toThrow('Check household status');
    expect(fetcher).toHaveBeenCalledExactlyOnceWith('/household/manage/confirm', expect.objectContaining({ body: '{"review_token":"opaque-review","confirmed":true}' }));
    expect(householdManagementErrorMessage(new Error('secret'))).not.toContain('secret');
    const malformed = new HouseholdManagementError('access');
    Object.assign(malformed, { code: '__proto__' });
    expect(householdManagementErrorMessage(malformed)).toBe(householdManagementErrorMessage(new Error()));
  });
  it('rejects unreadable success and forged review data', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response('<html>proxy</html>')).mockResolvedValueOnce(Response.json({ ...review(), change: { ...change(), person_id: 'two' } }));
    const client = createHouseholdManagementClient(() => 'token', fetcher);
    await expect(client.status()).rejects.toThrow('could not be verified');
    await expect(client.review(directory(), change())).rejects.toThrow('could not be verified');
  });
});

describe('household management presentation', () => {
  it('starts with a read action and no guessed family details or automatic writes', () => {
    const client = { status: vi.fn(), review: vi.fn(), confirm: vi.fn() };
    const markup = renderToStaticMarkup(createElement(HouseholdManager, { client, onOpen: vi.fn() }));
    expect(markup).toContain('Load saved people');
    expect(markup).not.toContain('Confirm and save change');
    expect(markup).not.toContain('elder-1');
    expect(client.confirm).not.toHaveBeenCalled();
    expect(client.review).not.toHaveBeenCalled();
  });
  it('describes directed relationships and retained records in plain language', () => {
    expect(describeHouseholdChange({ kind: 'set_relationship', from_person: 'two', to_person: 'one', relation: 'younger son' }, directory())).toBe('Save: Entered relative is younger son of Entered older adult.');
    expect(describeHouseholdChange(change(), directory())).toContain('Their saved records stay with them');
  });
});
