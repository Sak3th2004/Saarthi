export type ExternalBookingStatus = 'not_requested' | 'calendar_saved';
export type CalendarEvent = {
  id: string;
  summary: string;
  start: { date?: string; dateTime?: string };
  end: { date?: string; dateTime?: string };
  status?: string;
};
export type CalendarEvents = {
  source: 'google_calendar';
  events: CalendarEvent[];
  more_available: boolean;
};
/** Review payloads may omit role (backend returns id+name only). */
export type AppointmentPerson = {
  id: string;
  name: string;
  role?: 'elder' | 'family';
};
export type AppointmentReview = {
  review_token: string;
  person: AppointmentPerson;
  title: string;
  start: string;
  end: string;
  time_zone: string;
  calendar_account: string;
  notice: string;
};
export type AppointmentSaved = {
  status: 'saved' | 'calendar_saved_memory_pending';
  message: string;
  calendar_event_id?: string;
};
export type AppointmentDraft = {
  person: string; title: string; start_local: string; end_local: string; time_zone: string;
};

const messages = {
  access: 'Your Saarthi sign-in was not accepted for calendar. Sign out and sign in again.',
  unavailable: 'Google Calendar is not available yet. Connect Google Calendar, finish approval in the loopback window, then try Load upcoming events again.',
  response: 'The calendar response could not be verified.',
  outcome: 'The appointment save could not be confirmed. It may already be on your calendar. Retry the same review before creating another appointment.',
  input: 'Check the appointment details, then review again.',
} as const;

export class HouseholdCalendarError extends Error {
  constructor(readonly code: keyof typeof messages) { super(messages[code]); }
}

export function householdCalendarErrorMessage(error: unknown) {
  return error instanceof HouseholdCalendarError && Object.hasOwn(messages, error.code)
    ? messages[error.code]
    : messages.unavailable;
}

function fail(): never { throw new HouseholdCalendarError('response'); }

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail();
  return value as Record<string, unknown>;
}

function person(value: unknown, expectedId: string): AppointmentPerson {
  const item = object(value);
  if (typeof item.id !== 'string' || item.id !== expectedId || typeof item.name !== 'string' || !item.name.trim()) return fail();
  // Backend review returns {id, name} only; never invent a role.
  if (item.role === undefined) return { id: item.id, name: item.name };
  if (item.role !== 'elder' && item.role !== 'family') return fail();
  return { id: item.id, name: item.name, role: item.role };
}

function eventStart(value: unknown): CalendarEvent['start'] {
  const start = object(value);
  const date = start.date;
  const dateTime = start.dateTime;
  if (typeof date === 'string' && date) return { date, ...(typeof dateTime === 'string' ? { dateTime } : {}) };
  if (typeof dateTime === 'string' && dateTime) return { dateTime, ...(typeof date === 'string' ? { date } : {}) };
  return fail();
}

export function parseCalendarEvents(value: unknown): CalendarEvents {
  const item = object(value);
  if (item.source !== 'google_calendar' || !Array.isArray(item.events) || item.events.length > 25) return fail();
  const events = item.events.map(entry => {
    const event = object(entry);
    if (typeof event.id !== 'string' || !event.id || typeof event.summary !== 'string') return fail();
    return {
      id: event.id,
      summary: event.summary,
      start: eventStart(event.start),
      end: object(event.end) as CalendarEvent['end'],
      ...(typeof event.status === 'string' ? { status: event.status } : {}),
    };
  });
  return { source: 'google_calendar', events, more_available: item.more_available === true };
}

export function parseAppointmentReview(value: unknown, personId: string, draft?: AppointmentDraft): AppointmentReview {
  const item = object(value);
  if (typeof item.review_token !== 'string' || !item.review_token.trim() || item.review_token.length > 8192) return fail();
  if (typeof item.title !== 'string' || typeof item.start !== 'string' || typeof item.end !== 'string') return fail();
  if (typeof item.time_zone !== 'string' || typeof item.calendar_account !== 'string' || typeof item.notice !== 'string') return fail();
  if (draft && (item.title !== draft.title || item.time_zone !== draft.time_zone ||
      item.start.slice(0, 16) !== draft.start_local || item.end.slice(0, 16) !== draft.end_local)) return fail();
  const timestamp = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:Z|[+-]\d{2}:\d{2})$/;
  if (!timestamp.test(item.start) || !timestamp.test(item.end) ||
      !Number.isFinite(Date.parse(item.start)) || Date.parse(item.end) <= Date.parse(item.start) ||
      !Number.isFinite(Date.parse(item.end))) return fail();
  return {
    review_token: item.review_token,
    person: person(item.person, personId),
    title: item.title,
    start: item.start,
    end: item.end,
    time_zone: item.time_zone,
    calendar_account: item.calendar_account,
    notice: item.notice,
  };
}

export function parseAppointmentSaved(value: unknown): AppointmentSaved {
  const item = object(value);
  if (item.status !== 'saved' && item.status !== 'calendar_saved_memory_pending') return fail();
  if (typeof item.message !== 'string' || !item.message.trim()) return fail();
  return {
    status: item.status,
    message: item.message,
    ...(typeof item.calendar_event_id === 'string' ? { calendar_event_id: item.calendar_event_id } : {}),
  };
}

/** Derive list_appointments-style status from a graph appointment record. */
export function externalBookingStatus(record: { calendar_event_id?: unknown }): ExternalBookingStatus {
  return typeof record.calendar_event_id === 'string' && record.calendar_event_id
    ? 'calendar_saved'
    : 'not_requested';
}

export function bookingStatusLabel(status: ExternalBookingStatus) {
  return status === 'calendar_saved' ? 'Saved on calendar' : 'Not requested on a calendar';
}

export function createHouseholdCalendarClient(getToken: () => string, fetcher: typeof fetch = fetch) {
  async function authorized(path: string, init: RequestInit = {}): Promise<Response> {
    let token: string;
    try { token = getToken(); if (!token) throw new Error(); }
    catch { throw new HouseholdCalendarError('access'); }
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${token}`);
    try {
      return await fetcher(path, {
        ...init,
        credentials: 'omit',
        redirect: init.redirect ?? 'error',
        signal: init.signal ?? AbortSignal.timeout(45_000),
        headers,
      });
    } catch { throw new HouseholdCalendarError('unavailable'); }
  }

  async function json(path: string, init: RequestInit): Promise<unknown> {
    const response = await authorized(path, init);
    if (response.status === 401 || response.status === 403) throw new HouseholdCalendarError('access');
    if (response.status === 400 || response.status === 422) throw new HouseholdCalendarError('input');
    if (!response.ok) throw new HouseholdCalendarError('unavailable');
    try { return await response.json(); } catch { return fail(); }
  }

  return {
    /**
     * Starts Cognito Calendar connect. Returns a loopback handoff URL on :8080 so the
     * OAuth cookie is set on the MCP host (Vite :5173 cannot share that cookie with the
     * Google callback on 127.0.0.1:8080). Caller should open the URL in a new window.
     */
    async connect(): Promise<string> {
      const payload = await json('/household/calendar/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const item = object(payload);
      if (typeof item.handoff_url !== 'string' || !/^http:\/\/127\.0\.0\.1:8080\/oauth\/google\/household-connect\?h=[A-Za-z0-9_-]+$/.test(item.handoff_url)) {
        return fail();
      }
      return item.handoff_url;
    },

    async events(): Promise<CalendarEvents> {
      return parseCalendarEvents(await json('/household/calendar/events', { method: 'GET', cache: 'no-store' }));
    },

    async reviewAppointment(input: AppointmentDraft): Promise<AppointmentReview> {
      const body = {
        person: input.person, title: input.title, start_local: input.start_local,
        end_local: input.end_local, time_zone: input.time_zone,
      };
      return parseAppointmentReview(await json('/household/calendar/appointments/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(60_000),
      }), input.person, input);
    },

    async confirmAppointment(reviewToken: string): Promise<AppointmentSaved> {
      try { return parseAppointmentSaved(await json('/household/calendar/appointments/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_token: reviewToken, confirmed: true }),
        signal: AbortSignal.timeout(60_000),
      })); } catch (error) {
        if (error instanceof HouseholdCalendarError && (error.code === 'access' || error.code === 'input')) throw error;
        throw new HouseholdCalendarError('outcome');
      }
    },
  };
}

export type HouseholdCalendarClient = ReturnType<typeof createHouseholdCalendarClient>;
