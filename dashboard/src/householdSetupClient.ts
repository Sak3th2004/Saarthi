import type { Person } from './contracts';

export type Persistence = 'persistent' | 'session';
export interface FamilyDraft { id: string; name: string; relation: string }
interface HouseholdMember extends Person {
  relation: string | null; phone: null; email: null; aliases: []; medications: [];
}
export interface HouseholdDefinition {
  primary_person_id: string;
  people: HouseholdMember[];
  relationships: { from_person: string; to_person: string; relation: string }[];
}
export interface HouseholdStatus { configured: boolean; primary_person?: Person; persistence: Persistence }
export interface HouseholdReview { review_token: string; household: HouseholdDefinition; persistence: Persistence; expires_in_seconds: number }
export interface HouseholdSaved { status: 'saved'; primary_person: Person; persistence: Persistence }

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The household response was incomplete.');
  return value as Record<string, unknown>;
}
function persistence(value: unknown): Persistence {
  if (value !== 'persistent' && value !== 'session') throw new Error('The server did not confirm where records will be stored.');
  return value;
}
function person(value: unknown): Person {
  const item = object(value);
  if (typeof item.id !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(item.id) ||
      typeof item.name !== 'string' || !item.name.trim() || item.role !== 'elder') {
    throw new Error('The server did not identify the saved household.');
  }
  return { id: item.id, name: item.name, role: item.role };
}

export function householdDefinition(primaryId: string, name: string, family: FamilyDraft[]): HouseholdDefinition {
  const trimmed = name.trim();
  if (!trimmed || family.some(member => !member.name.trim() || !member.relation.trim())) {
    throw new Error('Enter the older adult’s name and a name and relationship for each family member.');
  }
  const names = [trimmed, ...family.map(member => member.name.trim())];
  if (new Set(names.map(value => value.toLowerCase())).size !== names.length) {
    throw new Error('Use distinct names so each person can be identified.');
  }
  return {
    primary_person_id: primaryId,
    people: [
      { id: primaryId, name: trimmed, role: 'elder', relation: null, phone: null, email: null, aliases: [], medications: [] },
      ...family.map(member => ({ id: member.id, name: member.name.trim(), role: 'family' as const,
        relation: member.relation.trim(), phone: null, email: null, aliases: [] as [], medications: [] as [] })),
    ],
    relationships: family.map(member => ({ from_person: member.id, to_person: primaryId, relation: member.relation.trim() })),
  };
}

export function parseHouseholdStatus(value: unknown): HouseholdStatus {
  const item = object(value);
  if (typeof item.configured !== 'boolean') throw new Error('The server did not confirm household status.');
  return { configured: item.configured, persistence: persistence(item.persistence),
    ...(item.configured ? { primary_person: person(item.primary_person) } : {}) };
}

export function parseHouseholdReview(value: unknown, submitted: HouseholdDefinition): HouseholdReview {
  const item = object(value);
  if (typeof item.review_token !== 'string' || !item.review_token.trim() || item.expires_in_seconds !== 600) {
    throw new Error('The household review was incomplete. Nothing was confirmed.');
  }
  const household = object(item.household);
  // All fields this form can save must match the details the user entered.
  // The server may change JSON key order, but cannot introduce unseen care data.
  const equal = (actual: unknown, expected: unknown): boolean => {
    if (Array.isArray(expected)) return Array.isArray(actual) && actual.length === expected.length && expected.every((v, i) => equal(actual[i], v));
    if (expected && typeof expected === 'object') {
      if (!actual || typeof actual !== 'object' || Array.isArray(actual)) return false;
      const a = actual as Record<string, unknown>, e = expected as Record<string, unknown>;
      return Object.keys(a).length === Object.keys(e).length && Object.keys(e).every(key => equal(a[key], e[key]));
    }
    return actual === expected;
  };
  if (!equal(household, submitted)) throw new Error('The returned review differs from the details you entered. Nothing was confirmed.');
  return { review_token: item.review_token, household: structuredClone(submitted), persistence: persistence(item.persistence), expires_in_seconds: 600 };
}

export function parseHouseholdSaved(value: unknown, review: HouseholdReview): HouseholdSaved {
  const item = object(value);
  const primary = person(item.primary_person);
  const expected = review.household.people.find(member => member.id === review.household.primary_person_id)!;
  const storage = persistence(item.persistence);
  if (item.status !== 'saved' || primary.id !== expected.id || primary.name !== expected.name || storage !== review.persistence) {
    throw new Error('Saving was not confirmed. Check household status before starting another setup.');
  }
  return { status: 'saved', primary_person: primary, persistence: storage };
}

async function request(action: 'status' | 'review' | 'confirm', input: unknown, fetcher: typeof fetch): Promise<unknown> {
  const response = await fetcher(`/local/household/${action}`, {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input), signal: AbortSignal.timeout(30_000),
  });
  if (response.status === 404) throw new Error('Household setup is disabled on this server. Open the local server with household setup enabled.');
  let result: unknown;
  try { result = await response.json(); }
  catch { throw new Error('The server returned an unreadable response. Check household status before trying another setup.'); }
  if (!response.ok) {
    const error = object(result).error;
    throw new Error(typeof error === 'string' ? error : 'The household request could not be completed.');
  }
  return result;
}

export function createHouseholdSetupClient(fetcher: typeof fetch = fetch) {
  return {
    async status() { return parseHouseholdStatus(await request('status', {}, fetcher)); },
    async review(definition: HouseholdDefinition) { return parseHouseholdReview(await request('review', definition, fetcher), definition); },
    async confirm(review: HouseholdReview) {
      return parseHouseholdSaved(await request('confirm', { review_token: review.review_token, confirmed: true }, fetcher), review);
    },
  };
}
