import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { Calendar } from './Calendar';
import {
  bookingStatusLabel,
  createHouseholdCalendarClient,
  externalBookingStatus,
  householdCalendarErrorMessage,
  parseAppointmentReview,
  parseAppointmentSaved,
  parseCalendarEvents,
} from './householdCalendarClient';

const eventsPayload = () => ({
  source: 'google_calendar' as const,
  events: [{ id: 'evt-1', summary: 'Physio', start: { dateTime: '2026-10-11T10:00:00+05:30' }, end: { dateTime: '2026-10-11T11:00:00+05:30' }, status: 'confirmed' }],
  more_available: false,
});

const reviewPayload = () => ({
  review_token: 'opaque-review',
  person: { id: 'elder-1', name: 'Ravi', role: 'elder' as const },
  title: 'Clinic visit',
  start: '2026-10-12T09:00:00+05:30',
  end: '2026-10-12T10:00:00+05:30',
  time_zone: 'Asia/Kolkata',
  calendar_account: 'care@example.test',
  notice: 'Creates a private calendar entry.',
});

describe('household calendar response verification', () => {
  it('accepts bounded Google events and rejects invented sources', () => {
    expect(parseCalendarEvents(eventsPayload()).events[0].summary).toBe('Physio');
    expect(() => parseCalendarEvents({ ...eventsPayload(), source: 'invented' })).toThrow();
    expect(() => parseCalendarEvents({ ...eventsPayload(), events: Array.from({ length: 26 }, (_, i) => ({ ...eventsPayload().events[0], id: `e${i}` })) })).toThrow();
  });
  it('verifies appointment review and save statuses without trusting free-form success', () => {
    expect(parseAppointmentReview(reviewPayload(), 'elder-1').calendar_account).toBe('care@example.test');
    expect(() => parseAppointmentReview(reviewPayload(), 'other')).toThrow();
    expect(parseAppointmentSaved({ status: 'saved', message: 'Saved to Google Calendar and household memory.' }).status).toBe('saved');
    expect(parseAppointmentSaved({ status: 'calendar_saved_memory_pending', message: 'Retry this review.' }).status).toBe('calendar_saved_memory_pending');
    expect(() => parseAppointmentSaved({ status: 'booked', message: 'Done' })).toThrow();
  });
  it('accepts live review person shape with id+name and no role', () => {
    const live = {
      ...reviewPayload(),
      person: { id: 'elder-1', name: 'Ravi' },
    };
    const parsed = parseAppointmentReview(live, 'elder-1');
    expect(parsed.person).toEqual({ id: 'elder-1', name: 'Ravi' });
    expect(parsed.person).not.toHaveProperty('role');
    expect(() => parseAppointmentReview({ ...live, person: { id: 'elder-1', name: 'Ravi', role: 'doctor' } }, 'elder-1')).toThrow();
  });
  it('derives list_appointments-style booking labels from calendar_event_id', () => {
    expect(externalBookingStatus({ calendar_event_id: 'abc' })).toBe('calendar_saved');
    expect(externalBookingStatus({ calendar_event_id: null })).toBe('not_requested');
    expect(externalBookingStatus({})).toBe('not_requested');
    expect(bookingStatusLabel('calendar_saved')).toBe('Saved on calendar');
    expect(bookingStatusLabel('not_requested')).toBe('Not requested on a calendar');
  });
});

describe('household calendar transport', () => {
  it.each([
    { title: 'A changed visit' }, { time_zone: 'Europe/London' },
    { start: '2026-10-12T09:30:00+05:30' }, { end: '2026-10-12T11:00:00+05:30' },
  ])('rejects changed appointment review details: %j', async replacement => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ...reviewPayload(), ...replacement }));
    await expect(createHouseholdCalendarClient(() => 'token', fetcher).reviewAppointment({
      person: 'elder-1', title: 'Clinic visit', start_local: '2026-10-12T09:00',
      end_local: '2026-10-12T10:00', time_zone: 'Asia/Kolkata',
    })).rejects.toThrow('could not be verified');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each([Response.json({ status: 'booked', message: 'Done' }), new Response('broken json')])(
    'treats malformed confirmation as an uncertain save without repeating it', async response => {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response);
      await expect(createHouseholdCalendarClient(() => 'token', fetcher).confirmAppointment('same-review'))
        .rejects.toThrow('may already be on your calendar');
      expect(fetcher).toHaveBeenCalledTimes(1);
    },
  );
  it('loads events and reviews appointments with the current bearer on /household/calendar/*', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(eventsPayload()))
      .mockResolvedValueOnce(Response.json(reviewPayload()));
    let token = 'current-session';
    const client = createHouseholdCalendarClient(() => token, fetcher);
    await client.events();
    token = 'new-session';
    await client.reviewAppointment({
      person: 'elder-1', title: 'Clinic visit', start_local: '2026-10-12T09:00', end_local: '2026-10-12T10:00', time_zone: 'Asia/Kolkata',
    });
    expect(fetcher).toHaveBeenNthCalledWith(1, '/household/calendar/events', expect.objectContaining({
      method: 'GET', credentials: 'omit', redirect: 'error',
      headers: expect.any(Headers),
    }));
    expect((fetcher.mock.calls[0][1]?.headers as Headers).get('Authorization')).toBe('Bearer current-session');
    expect(fetcher).toHaveBeenNthCalledWith(2, '/household/calendar/appointments/review', expect.objectContaining({
      method: 'POST', credentials: 'omit',
      body: JSON.stringify({
        person: 'elder-1', title: 'Clinic visit', start_local: '2026-10-12T09:00', end_local: '2026-10-12T10:00', time_zone: 'Asia/Kolkata',
      }),
    }));
    expect((fetcher.mock.calls[1][1]?.headers as Headers).get('Authorization')).toBe('Bearer new-session');
  });
  it('opens connect via loopback handoff URL without cookies or invented Google URLs', async () => {
    const handoff = 'http://127.0.0.1:8080/oauth/google/household-connect?h=opaque-handoff-token';
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ handoff_url: handoff }));
    const url = await createHouseholdCalendarClient(() => 'token', fetcher).connect();
    expect(url).toBe(handoff);
    expect(fetcher).toHaveBeenCalledExactlyOnceWith('/household/calendar/connect', expect.objectContaining({
      method: 'POST', credentials: 'omit', body: '{}',
    }));
    await expect(createHouseholdCalendarClient(() => 'token', vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ handoff_url: 'https://accounts.google.com/o/oauth2/auth' }),
    )).connect()).rejects.toThrow('could not be verified');
  });
  it.each([[401, 'Saarthi sign-in'], [403, 'Saarthi sign-in'], [503, 'Connect Google Calendar'], [400, 'Check the appointment']])(
    'maps HTTP %i to a safe actionable message',
    async (status, expected) => {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: 'SECRET' }, { status: status as number }));
      await expect(createHouseholdCalendarClient(() => 'token', fetcher).events()).rejects.toThrow(expected as string);
      expect(householdCalendarErrorMessage(new Error('secret'))).not.toContain('secret');
    },
  );
  it('never sends when the session token is missing', async () => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(createHouseholdCalendarClient(() => '', fetcher).events()).rejects.toThrow('sign-in');
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe('household calendar presentation', () => {
  it('separates Cognito sign-in from Google Calendar connection status', () => {
    const client = { connect: vi.fn(), events: vi.fn(), reviewAppointment: vi.fn(), confirmAppointment: vi.fn() };
    const html = renderToStaticMarkup(createElement(Calendar, { calendar: client }));
    expect(html).toContain('Connect Google Calendar');
    expect(html).toContain('Saarthi sign-in: active');
    expect(html).toContain('Google Calendar:');
    expect(html).toContain('Not confirmed yet');
    expect(html).not.toContain('Connect calendar (signed in)');
    expect(html).not.toContain('Connect calendar on this computer');
    expect(html).toContain('Load upcoming events');
  });
});
