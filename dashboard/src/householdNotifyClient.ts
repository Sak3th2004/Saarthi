export type DeliveryStatus = 'recorded_only' | 'pending' | 'accepted' | 'uncertain' | 'failed';
export type NotifyUrgency = 'info' | 'warning' | 'urgent';

/** Review payloads may omit role (backend returns id+name only). */
export type NotifyPerson = {
  id: string;
  name: string;
  role?: 'elder' | 'family';
};

export type NotifyKind = 'message' | 'digest';

export type NotifyReview = {
  review_token: string;
  person: NotifyPerson;
  message: string;
  urgency: NotifyUrgency;
  recipient_emails: string[];
  delivery_status: 'pending';
  notice: string;
  kind?: NotifyKind;
};

export type NotifyConfirm = {
  person: NotifyPerson;
  delivered_to: string[];
  submitted_to: string[];
  candidate_channels: string[];
  delivery_status: Exclude<DeliveryStatus, 'pending'>;
  urgency: NotifyUrgency;
  message: string;
  speech: string;
};

const messages = {
  access: 'Your sign-in was not accepted. Sign out and sign in again.',
  unavailable: 'Family messaging is unavailable. Check that family emails are saved, then try again.',
  response: 'The family message response could not be verified.',
  outcome: 'The email result could not be confirmed. It may already have been sent. Retry the same review before composing another message.',
  input: 'Check the message and recipients, then review again.',
} as const;

export class HouseholdNotifyError extends Error {
  constructor(readonly code: keyof typeof messages) { super(messages[code]); }
}

export function householdNotifyErrorMessage(error: unknown) {
  return error instanceof HouseholdNotifyError && Object.hasOwn(messages, error.code)
    ? messages[error.code]
    : messages.unavailable;
}

function fail(): never { throw new HouseholdNotifyError('response'); }

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}

function person(value: unknown, expectedId?: string): NotifyPerson {
  const item = object(value);
  if (typeof item.id !== 'string' || !item.id || typeof item.name !== 'string' || !item.name.trim()) return fail();
  if (expectedId !== undefined && item.id !== expectedId) return fail();
  // Backend review returns {id, name} only; confirm may include role. Never invent a role.
  if (item.role === undefined) return { id: item.id, name: item.name };
  if (item.role !== 'elder' && item.role !== 'family') return fail();
  return { id: item.id, name: item.name, role: item.role };
}

function urgency(value: unknown): NotifyUrgency {
  if (value !== 'info' && value !== 'warning' && value !== 'urgent') return fail();
  return value;
}

function emails(value: unknown): string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 50) return fail();
  const list = value.map(entry => {
    if (typeof entry !== 'string' || !entry.trim() || entry !== entry.trim() || entry.length > 320 || !entry.includes('@')) return fail();
    return entry;
  });
  if (new Set(list).size !== list.length) return fail();
  return list;
}

export function parseNotifyReview(value: unknown, personId: string, message: string, expectedUrgency: NotifyUrgency = 'info'): NotifyReview {
  const item = object(value);
  if (typeof item.review_token !== 'string' || !item.review_token.trim() || item.review_token.length > 8192) return fail();
  if (item.message !== message || item.urgency !== expectedUrgency || item.delivery_status !== 'pending' || typeof item.notice !== 'string') return fail();
  const kind = item.kind;
  if (kind !== undefined && kind !== 'message' && kind !== 'digest') return fail();
  return {
    review_token: item.review_token,
    person: person(item.person, personId),
    message: item.message,
    urgency: urgency(item.urgency),
    recipient_emails: emails(item.recipient_emails),
    delivery_status: 'pending',
    notice: item.notice,
    ...(kind ? { kind } : {}),
  };
}

/** Digest body is server-assembled; verify shape without requiring a caller-known message. */
export function parseDigestReview(value: unknown, personId: string): NotifyReview {
  const item = object(value);
  if (typeof item.review_token !== 'string' || !item.review_token.trim() || item.review_token.length > 8192) return fail();
  if (typeof item.message !== 'string' || !item.message.trim() || item.message.length > 8000) return fail();
  if (item.urgency !== 'info' || item.delivery_status !== 'pending' || typeof item.notice !== 'string') return fail();
  if (item.kind !== undefined && item.kind !== 'digest') return fail();
  if (!item.message.includes('weekly household digest') || !item.message.includes('not medical advice')) return fail();
  return {
    review_token: item.review_token,
    person: person(item.person, personId),
    message: item.message,
    urgency: 'info',
    recipient_emails: emails(item.recipient_emails),
    delivery_status: 'pending',
    notice: item.notice,
    kind: 'digest',
  };
}

export function parseNotifyConfirm(value: unknown, review: NotifyReview): NotifyConfirm {
  const item = object(value);
  if (!['recorded_only', 'accepted', 'uncertain', 'failed'].includes(item.delivery_status as string)) return fail();
  if (item.message !== review.message || urgency(item.urgency) !== review.urgency) return fail();
  if (!Array.isArray(item.delivered_to) || !Array.isArray(item.candidate_channels)) return fail();
  if (item.delivered_to.some(entry => typeof entry !== 'string') || item.candidate_channels.some(entry => typeof entry !== 'string')) return fail();
  if (typeof item.speech !== 'string') return fail();
  // SES acceptance does not prove inbox delivery. Only report reviewed destinations.
  if (item.delivered_to.length !== 0 || !Array.isArray(item.submitted_to)) return fail();
  const reviewedChannels = new Set(review.recipient_emails.map(email => `email:${email}`));
  if (item.submitted_to.some(entry => typeof entry !== 'string' || !reviewedChannels.has(entry)) ||
      new Set(item.submitted_to).size !== item.submitted_to.length) return fail();
  if (item.delivery_status === 'accepted' && item.submitted_to.length !== reviewedChannels.size) return fail();
  if (item.delivery_status !== 'accepted' && item.submitted_to.length !== 0) return fail();
  return {
    person: person(item.person, review.person.id),
    delivered_to: item.delivered_to as string[],
    submitted_to: item.submitted_to as string[],
    candidate_channels: item.candidate_channels as string[],
    delivery_status: item.delivery_status as NotifyConfirm['delivery_status'],
    urgency: review.urgency,
    message: review.message,
    speech: item.speech,
  };
}

export function deliveryStatusLabel(status: DeliveryStatus) {
  switch (status) {
    case 'pending': return 'Pending confirmation — nothing sent yet';
    case 'accepted': return 'Email accepted for sending — inbox delivery is not confirmed';
    case 'uncertain': return 'Email status is unknown — it may have been sent. Check before composing another message';
    case 'failed': return 'Email was not accepted for sending';
    case 'recorded_only': return 'Recorded only — email was not sent';
  }
}

export function createHouseholdNotifyClient(getToken: () => string, fetcher: typeof fetch = fetch) {
  async function request(action: 'review' | 'digest/review' | 'confirm', input: unknown): Promise<unknown> {
    let token: string;
    try { token = getToken(); if (!token) throw new Error(); }
    catch { throw new HouseholdNotifyError('access'); }
    let response: Response;
    try {
      response = await fetcher(`/household/notify/${action}`, {
        method: 'POST',
        credentials: 'omit',
        redirect: 'error',
        signal: AbortSignal.timeout(60_000),
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify(input),
      });
    } catch { throw new HouseholdNotifyError('unavailable'); }
    if (response.status === 401 || response.status === 403) throw new HouseholdNotifyError('access');
    if (response.status === 400 || response.status === 422) throw new HouseholdNotifyError('input');
    if (!response.ok) throw new HouseholdNotifyError('unavailable');
    try { return await response.json(); } catch { return fail(); }
  }

  return {
    async review(personId: string, message: string, urgencyValue: NotifyUrgency = 'info') {
      const text = message.trim();
      if (!text) throw new HouseholdNotifyError('input');
      return parseNotifyReview(await request('review', { person: personId, message: text, urgency: urgencyValue }), personId, text, urgencyValue);
    },
    async reviewDigest(personId: string, days = 7) {
      if (!Number.isInteger(days) || days < 1 || days > 31) throw new HouseholdNotifyError('input');
      return parseDigestReview(await request('digest/review', { person: personId, days }), personId);
    },
    async confirm(review: NotifyReview) {
      try {
        return parseNotifyConfirm(await request('confirm', { review_token: review.review_token, confirmed: true }), review);
      } catch (error) {
        if (error instanceof HouseholdNotifyError && (error.code === 'access' || error.code === 'input')) throw error;
        throw new HouseholdNotifyError('outcome');
      }
    },
  };
}

export type HouseholdNotifyClient = ReturnType<typeof createHouseholdNotifyClient>;
