import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { HouseholdSetup } from './HouseholdSetup';
import { createHouseholdSetupClient, householdDefinition, parseHouseholdReview, parseHouseholdSaved, parseHouseholdStatus } from './householdSetupClient';

const definition = () => householdDefinition('elder-id', ' A user-entered name ', [{ id: 'family-id', name: ' Another entered name ', relation: ' daughter ' }]);
const review = () => ({ review_token: 'opaque-review', household: definition(), persistence: 'persistent' as const, expires_in_seconds: 600 });

describe('household setup validation', () => {
  it('starts without sample people or automatic server calls', () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    try {
      const html = renderToStaticMarkup(createElement(HouseholdSetup, { onOpen: vi.fn() }));
      expect(html).toContain('Check household status');
      expect(html).not.toContain('Confirm and save household');
      expect(html).not.toContain('A user-entered name');
      expect(fetcher).not.toHaveBeenCalled();
    } finally { vi.unstubAllGlobals(); }
  });
  it('preserves row IDs, trims input and saves only entered relationships', () => {
    const household = definition();
    expect(household.people[1]).toMatchObject({ id: 'family-id', name: 'Another entered name', relation: 'daughter' });
    expect(household.relationships).toEqual([{ from_person: 'family-id', to_person: 'elder-id', relation: 'daughter' }]);
    expect(household.people.every(person => person.medications.length === 0 && person.aliases.length === 0)).toBe(true);
    expect(householdDefinition('elder-id', 'Different input', []).people[0].id).toBe('elder-id');
    expect(() => householdDefinition('one', ' Person ', [{ id: 'two', name: 'person', relation: 'son' }])).toThrow('distinct');
    expect(() => householdDefinition('one', 'Person', [{ id: 'two', name: 'Other', relation: ' ' }])).toThrow('relationship');
  });
  it('requires a real status and explicit storage mode', () => {
    expect(parseHouseholdStatus({ configured: false, persistence: 'session' })).toEqual({ configured: false, persistence: 'session' });
    expect(() => parseHouseholdStatus({ configured: 'false', persistence: 'persistent' })).toThrow();
    expect(() => parseHouseholdStatus({ configured: true, persistence: 'persistent' })).toThrow();
    expect(() => parseHouseholdStatus({ configured: false, persistence: 'unknown' })).toThrow();
  });
  it('rejects a review that changes names, storage, adds medical facts or omits relationships', () => {
    expect(parseHouseholdReview(review(), definition()).household).toEqual(definition());
    for (const change of [
      (value: ReturnType<typeof review>) => { value.household.people[0].name = 'Different person'; },
      (value: ReturnType<typeof review>) => { value.household.relationships = []; },
      (value: ReturnType<typeof review>) => { Object.assign(value.household.people[0], { medications: [{ name: 'Unrequested' }] }); },
      (value: ReturnType<typeof review>) => { Object.assign(value, { persistence: 'unknown' }); },
    ]) {
      const value = review(); change(value);
      expect(() => parseHouseholdReview(value, definition())).toThrow();
    }
  });
  it('accepts different JSON key ordering without weakening content validation', () => {
    const value = review();
    value.household.people[0] = Object.fromEntries(Object.entries(value.household.people[0]).reverse()) as typeof value.household.people[0];
    expect(parseHouseholdReview(value, definition()).review_token).toBe('opaque-review');
  });
  it('does not acknowledge a partial save or a different household', () => {
    const value = { status: 'saved', primary_person: { id: 'elder-id', name: 'A user-entered name', role: 'elder' }, persistence: 'persistent' };
    expect(parseHouseholdSaved(value, review()).status).toBe('saved');
    expect(() => parseHouseholdSaved({ ...value, status: 'pending' }, review())).toThrow();
    expect(() => parseHouseholdSaved({ ...value, primary_person: { ...value.primary_person, id: 'other' } }, review())).toThrow();
    expect(() => parseHouseholdSaved({ ...value, persistence: 'session' }, review())).toThrow();
  });
});

describe('household setup requests', () => {
  it('posts status to the same-origin local endpoint and explains disabled setup', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('', { status: 404 }));
    await expect(createHouseholdSetupClient(fetcher).status()).rejects.toThrow('disabled');
    expect(fetcher).toHaveBeenCalledExactlyOnceWith('/local/household/status', expect.objectContaining({ method: 'POST', body: '{}', credentials: 'same-origin' }));
  });
  it('never automatically retries an uncertain confirmation and retains the token for deliberate retry', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValueOnce(new Error('Connection lost')).mockResolvedValueOnce(Response.json({
      status: 'saved', primary_person: { id: 'elder-id', name: 'A user-entered name', role: 'elder' }, persistence: 'persistent',
    }));
    const client = createHouseholdSetupClient(fetcher), pending = review();
    await expect(client.confirm(pending)).rejects.toThrow('Connection lost');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(pending.review_token).toBe('opaque-review');
    expect(await client.confirm(pending)).toMatchObject({ status: 'saved' });
    expect(fetcher.mock.calls.map(call => call[1]?.body)).toEqual([
      '{"review_token":"opaque-review","confirmed":true}', '{"review_token":"opaque-review","confirmed":true}',
    ]);
  });
  it('surfaces server failures and rejects unreadable success responses', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json({ error: 'Review expired.' }, { status: 400 }))
      .mockResolvedValueOnce(new Response('<html>Proxy failed</html>'));
    const client = createHouseholdSetupClient(fetcher);
    await expect(client.confirm(review())).rejects.toThrow('Review expired');
    await expect(client.confirm(review())).rejects.toThrow('unreadable');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
