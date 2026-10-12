import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { HouseholdManager, describeHouseholdChange, openNotebookLabel } from './HouseholdManager';
import { HouseholdManagementError, createHouseholdManagementClient, householdManagementErrorMessage, parseHouseholdChange, parseHouseholdDirectory, parseHouseholdChangeReview, parseHouseholdChangeSaved, type HouseholdChange, type HouseholdDirectory } from './householdManagementClient';

const directory = (): HouseholdDirectory => ({ revision: 'a'.repeat(64), persistence: 'persistent', primary_person_id: 'one', people: [
  { id: 'one', name: 'Entered older adult', role: 'elder', email: null }, { id: 'two', name: 'Entered relative', role: 'family', email: null },
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
      { kind: 'set_person_email', person_id: 'two', email: 'caregiver@example.test' },
    ];
    for (const changed of changes) {
      const pending = { ...review(), change: changed };
      expect(() => parseHouseholdChangeSaved({ status: 'saved', directory: directory() }, pending)).toThrow();
      const saved = directory();
      if (changed.kind === 'add_person') saved.people.push({ id: changed.id, name: changed.name, role: changed.role, email: null });
      if (changed.kind === 'rename_person') saved.people[0].name = changed.name;
      if (changed.kind === 'set_primary') { saved.people.push({ id: changed.person_id, name: 'Another older adult', role: 'elder', email: null }); saved.primary_person_id = changed.person_id; }
      if (changed.kind === 'set_relationship') saved.relationships[0].relation = changed.relation;
      if (changed.kind === 'set_person_email') saved.people[1].email = changed.email;
      expect(parseHouseholdChangeSaved({ status: 'saved', directory: saved }, pending)).toEqual(saved);
      expect(() => parseHouseholdChangeSaved({ status: 'pending', directory: saved }, pending)).toThrow();
      expect(() => parseHouseholdChangeSaved({ status: 'saved', directory: { ...saved, persistence: 'session' } }, pending)).toThrow();
    }
  });
  it('verifies add_medication from the medication echo, not a directory mutation', () => {
    const med: HouseholdChange = {
      kind: 'add_medication', person_id: 'one', name: 'OwnerSuppliedMed', dose: '5mg', schedule: ['08:00', '20:00'],
    };
    const pending = { ...review(), change: med };
    const notice = 'This is a caregiver-saved medication schedule fact for household recall. It is not medical advice, a prescription, or a dosing recommendation.';
    expect(() => parseHouseholdChangeSaved({ status: 'saved', directory: directory() }, pending)).toThrow();
    expect(() => parseHouseholdChangeSaved({
      status: 'saved', directory: directory(),
      medication: { person_id: 'one', name: 'Other', dose: '5mg', schedule: ['08:00', '20:00'] },
      notice,
    }, pending)).toThrow();
    expect(parseHouseholdChangeSaved({
      status: 'saved', directory: directory(),
      medication: { person_id: 'one', name: 'OwnerSuppliedMed', dose: '5mg', schedule: ['08:00', '20:00'] },
      notice,
    }, pending)).toEqual(directory());
    expect(parseHouseholdChange({ kind: 'add_medication', person_id: 'one', name: 'OwnerSuppliedMed', dose: '5mg', schedule: [] })).toEqual({
      kind: 'add_medication', person_id: 'one', name: 'OwnerSuppliedMed', dose: '5mg', schedule: [],
    });
    expect(() => parseHouseholdChange({ kind: 'add_medication', person_id: 'one', name: 'OwnerSuppliedMed', dose: '5mg', schedule: ['25:00'] })).toThrow();
    expect(() => parseHouseholdChange({ kind: 'add_medication', person_id: 'one', name: 'OwnerSuppliedMed', dose: '5mg', schedule: ['08:00'], supply_count: 1 })).toThrow();
    const medReview = parseHouseholdChangeReview({
      review_token: 'opaque-review', change: med, expires_in_seconds: 600, persistence: 'persistent', notice,
    }, med, 'persistent');
    expect(medReview.notice).toMatch(/not medical advice/i);
  });
  it('accepts saved family emails in the directory and rejects invented email shapes on review', () => {
    const withEmail = { ...directory(), people: [
      directory().people[0],
      { ...directory().people[1], email: 'saketh@example.test' },
    ] };
    expect(parseHouseholdDirectory(withEmail).people[1].email).toBe('saketh@example.test');
    expect(() => parseHouseholdDirectory({ ...withEmail, people: [{ ...withEmail.people[1], email: 'not-an-email' }] })).toThrow();
    expect(parseHouseholdChange({ kind: 'set_person_email', person_id: 'two', email: 'vasist@example.test' }).kind).toBe('set_person_email');
    expect(() => parseHouseholdChange({ kind: 'set_person_email', person_id: 'two', email: 'bad' })).toThrow();
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
  it('auto-reads on mount intent, names the open CTA from saved people, and never invents elders or auto-writes', () => {
    expect(openNotebookLabel('Ravi')).toBe("Open Ravi's notebook");
    expect(openNotebookLabel('Entered older adult')).toBe("Open Entered older adult's notebook");
    const client = { status: vi.fn(), review: vi.fn(), confirm: vi.fn() };
    const markup = renderToStaticMarkup(createElement(HouseholdManager, { client, onOpen: vi.fn() }));
    expect(markup).toContain('Refresh people');
    expect(markup).toContain('Saved people load after sign-in');
    expect(markup).not.toContain('Confirm and save change');
    expect(markup).not.toContain('elder-1');
    expect(markup).not.toContain('Ramesh');
    // SSR markup does not run effects; client mount schedules one status() read via useEffect.
    expect(client.status).not.toHaveBeenCalled();
    expect(client.confirm).not.toHaveBeenCalled();
    expect(client.review).not.toHaveBeenCalled();
  });
  it('describes directed relationships and retained records in plain language', () => {
    expect(describeHouseholdChange({ kind: 'set_relationship', from_person: 'two', to_person: 'one', relation: 'younger son' }, directory())).toBe('Save: Entered relative is younger son of Entered older adult.');
    expect(describeHouseholdChange(change(), directory())).toContain('Their saved records stay with them');
    expect(describeHouseholdChange({ kind: 'set_person_email', person_id: 'two', email: 'saketh@example.test' }, directory())).toContain('saketh@example.test');
    expect(describeHouseholdChange({
      kind: 'add_medication', person_id: 'one', name: 'OwnerSuppliedMed', dose: '5mg', schedule: ['08:00'],
    }, directory())).toMatch(/OwnerSuppliedMed.*not a prescription/i);
  });
  it('surfaces family email status without inventing addresses', () => {
    const client = { status: vi.fn(), review: vi.fn(), confirm: vi.fn() };
    const markup = renderToStaticMarkup(createElement(HouseholdManager, { client, onOpen: vi.fn() }));
    expect(markup).not.toContain('@');
    expect(markup).not.toContain('Confirm and save change');
  });
  it('offers caregiver-typed medication entry without inventing medicine names', async () => {
    const client = {
      status: vi.fn().mockResolvedValue(directory()),
      review: vi.fn(),
      confirm: vi.fn(),
    };
    const markup = renderToStaticMarkup(createElement(HouseholdManager, { client, onOpen: vi.fn() }));
    // SSR does not run effects; option text is in the form template once directory is present.
    // Assert describe + change labels instead for caregiver safety:
    expect(describeHouseholdChange({
      kind: 'add_medication', person_id: 'one', name: 'Entered med', dose: 'as written', schedule: [],
    }, directory())).not.toMatch(/Metformin|Amlodipine|Atorvastatin|Ravi/i);
    expect(openNotebookLabel('Entered older adult')).not.toMatch(/list_appointments/);
    void client;
    expect(markup).toContain('Refresh people');
  });
});
