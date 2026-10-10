import type { Person } from './contracts';

export type Persistence = 'persistent' | 'session';
export interface HouseholdDirectory {
  revision: string; persistence: Persistence; primary_person_id: string | null;
  people: Person[];
  relationships: { from_person: string; to_person: string; relation: string }[];
}
export type HouseholdChange =
  | { kind: 'add_person'; id: string; name: string; role: 'elder' | 'family' }
  | { kind: 'rename_person'; person_id: string; name: string }
  | { kind: 'set_relationship'; from_person: string; to_person: string; relation: string }
  | { kind: 'set_primary'; person_id: string };
export interface HouseholdChangeReview {
  review_token: string; change: HouseholdChange; expires_in_seconds: 600; persistence: Persistence;
}
const messages = {
  access: 'Your sign-in was not accepted. Sign out and sign in again.',
  conflict: 'The household or review has changed. Refresh the people list and review your change again.',
  unavailable: 'The household service is unavailable. Check household status before trying to save again.',
  response: 'The household response could not be verified. Refresh the people list before making another change.',
  input: 'Check the names, people and relationship you entered, then review the change again.',
} as const;
export class HouseholdManagementError extends Error {
  constructor(readonly code: keyof typeof messages) { super(messages[code]); }
}
export function householdManagementErrorMessage(error: unknown) {
  return error instanceof HouseholdManagementError && Object.hasOwn(messages, error.code) ? messages[error.code] : messages.unavailable;
}
function fail(): never { throw new HouseholdManagementError('response'); }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}
function id(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(value)) return fail();
  return value;
}
function label(value: unknown): string {
  if (typeof value !== 'string' || !value.trim() || value !== value.trim() || value.length > 200 || /[\u0000-\u001f\u007f]/.test(value)) return fail();
  return value;
}
function storage(value: unknown): Persistence {
  if (value !== 'persistent' && value !== 'session') return fail();
  return value;
}
function exactKeys(value: Record<string, unknown>, keys: string[]) {
  if (Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) fail();
}
export function parseHouseholdDirectory(value: unknown): HouseholdDirectory {
  const item = object(value);
  if (typeof item.revision !== 'string' || !/^[a-f0-9]{64}$/.test(item.revision) || !Array.isArray(item.people) || !Array.isArray(item.relationships)) return fail();
  if (item.people.length > 100 || item.relationships.length > 500) return fail();
  const people = item.people.map(value => {
    const person = object(value);
    if (person.role !== 'elder' && person.role !== 'family') return fail();
    return { id: id(person.id), name: label(person.name), role: person.role } as Person;
  });
  const ids = new Set(people.map(person => person.id));
  if (ids.size !== people.length) return fail();
  const primary = item.primary_person_id === null ? null : id(item.primary_person_id);
  if (primary !== null && !people.some(person => person.id === primary && person.role === 'elder')) return fail();
  const pairs = new Set<string>();
  const relationships = item.relationships.map(value => {
    const relationship = object(value);
    const from = id(relationship.from_person), to = id(relationship.to_person);
    const pair = `${from}/${to}`;
    if (from === to || !ids.has(from) || !ids.has(to) || pairs.has(pair)) return fail();
    pairs.add(pair);
    return { from_person: from, to_person: to, relation: label(relationship.relation) };
  });
  return { revision: item.revision, persistence: storage(item.persistence), primary_person_id: primary, people, relationships };
}
export function parseHouseholdChange(value: unknown): HouseholdChange {
  const item = object(value);
  switch (item.kind) {
    case 'add_person':
      exactKeys(item, ['kind', 'id', 'name', 'role']);
      if (item.role !== 'elder' && item.role !== 'family') return fail();
      return { kind: item.kind, id: id(item.id), name: label(item.name), role: item.role };
    case 'rename_person':
      exactKeys(item, ['kind', 'person_id', 'name']);
      return { kind: item.kind, person_id: id(item.person_id), name: label(item.name) };
    case 'set_primary':
      exactKeys(item, ['kind', 'person_id']);
      return { kind: item.kind, person_id: id(item.person_id) };
    case 'set_relationship':
      exactKeys(item, ['kind', 'from_person', 'to_person', 'relation']);
      if (item.from_person === item.to_person) return fail();
      return { kind: item.kind, from_person: id(item.from_person), to_person: id(item.to_person), relation: label(item.relation) };
    default: return fail();
  }
}
export function parseHouseholdChangeReview(value: unknown, submitted: HouseholdChange, persistence: Persistence): HouseholdChangeReview {
  const item = object(value), change = parseHouseholdChange(item.change);
  if (typeof item.review_token !== 'string' || !item.review_token.trim() || item.review_token.length > 8192 || item.expires_in_seconds !== 600 ||
      storage(item.persistence) !== persistence || JSON.stringify(change) !== JSON.stringify(parseHouseholdChange(submitted))) return fail();
  return { review_token: item.review_token, change, expires_in_seconds: 600, persistence };
}
export function parseHouseholdChangeSaved(value: unknown, review: HouseholdChangeReview): HouseholdDirectory {
  const item = object(value), directory = parseHouseholdDirectory(item.directory), change = review.change;
  if (item.status !== 'saved' || directory.persistence !== review.persistence) return fail();
  const matches = change.kind === 'add_person' ? directory.people.some(person => person.id === change.id && person.name === change.name && person.role === change.role)
    : change.kind === 'rename_person' ? directory.people.some(person => person.id === change.person_id && person.name === change.name)
    : change.kind === 'set_primary' ? directory.primary_person_id === change.person_id
    : directory.relationships.some(relation => relation.from_person === change.from_person && relation.to_person === change.to_person && relation.relation === change.relation);
  if (!matches) return fail();
  return directory;
}
export function createHouseholdManagementClient(getToken: () => string, fetcher: typeof fetch = fetch) {
  async function request(action: 'status' | 'review' | 'confirm', input: unknown): Promise<unknown> {
    let token: string;
    try { token = getToken(); if (!token) throw new Error(); }
    catch { throw new HouseholdManagementError('access'); }
    let response: Response;
    try {
      response = await fetcher(`/household/manage/${action}`, {
        method: 'POST', credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(30_000),
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify(input),
      });
    } catch { throw new HouseholdManagementError('unavailable'); }
    if (response.status === 401 || response.status === 403) throw new HouseholdManagementError('access');
    if (response.status === 409) throw new HouseholdManagementError('conflict');
    if (response.status === 400 || response.status === 422) throw new HouseholdManagementError('input');
    if (!response.ok) throw new HouseholdManagementError('unavailable');
    try { return await response.json(); } catch { return fail(); }
  }
  return {
    async status() { return parseHouseholdDirectory(await request('status', {})); },
    async review(directory: HouseholdDirectory, change: HouseholdChange) {
      const submitted = parseHouseholdChange(change);
      return parseHouseholdChangeReview(await request('review', { expected_revision: directory.revision, change: submitted }), submitted, directory.persistence);
    },
    async confirm(review: HouseholdChangeReview) {
      return parseHouseholdChangeSaved(await request('confirm', { review_token: review.review_token, confirmed: true }), review);
    },
  };
}
export type HouseholdManagementClient = ReturnType<typeof createHouseholdManagementClient>;
