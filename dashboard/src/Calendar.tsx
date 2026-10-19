import { useState } from 'react';
import type { Person } from './contracts';
import { AppointmentEditor } from './AppointmentEditor';
import {
  householdCalendarErrorMessage,
  parseCalendarEvents,
  type CalendarEvent,
  type HouseholdCalendarClient,
} from './householdCalendarClient';

type GoogleLinkStatus = 'unknown' | 'connected' | 'not_connected';

/** Reads the connected calendar only when requested; does not invent or book events. */
export function Calendar({
  person,
  calendar,
  onAppointmentSaved,
}: {
  person?: Person;
  /** When set (Cognito), uses Bearer `/household/calendar/*`. Otherwise local `/oauth/google/*`. */
  calendar?: HouseholdCalendarClient;
  onAppointmentSaved?: (personId: string) => void;
}) {
  const [events, setEvents] = useState<CalendarEvent[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [more, setMore] = useState(false);
  const [connectNotice, setConnectNotice] = useState('');
  const [handoffUrl, setHandoffUrl] = useState<string | null>(null);
  const [googleStatus, setGoogleStatus] = useState<GoogleLinkStatus>('unknown');

  async function load() {
    if (busy) return;
    setBusy(true); setError(''); setEvents(null); setConnectNotice(''); setMore(false);
    try {
      if (calendar) {
        const data = await calendar.events();
        setEvents(data.events);
        setMore(data.more_available);
        setGoogleStatus('connected');
        setHandoffUrl(null);
      } else {
        const response = await fetch('/oauth/google/events', { cache: 'no-store', signal: AbortSignal.timeout(45_000) });
        if (!response.ok) throw new Error();
        const data = parseCalendarEvents(await response.json());
        setEvents(data.events);
        setMore(data.more_available);
        setGoogleStatus('connected');
        setHandoffUrl(null);
      }
    } catch (err) {
      const message = calendar
        ? householdCalendarErrorMessage(err)
        : 'Could not read Google Calendar. Connect your account and try again.';
      setError(message);
      if (message.includes('Connect Google Calendar') || message.includes('Connect your account')) {
        setGoogleStatus('not_connected');
      }
    } finally {
      setBusy(false);
    }
  }

  async function connectAuthenticated() {
    if (!calendar || busy) return;
    setBusy(true); setError(''); setConnectNotice(''); setHandoffUrl(null);
    try {
      const url = await calendar.connect();
      setHandoffUrl(url);
      setGoogleStatus('unknown');
      setConnectNotice('Use Continue to Google below to approve access. Then return here and load your events. The connection link expires after ten minutes.');
    } catch (err) {
      setError(householdCalendarErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const googleLabel = googleStatus === 'connected'
    ? 'Connected'
    : googleStatus === 'not_connected'
      ? 'Not connected'
      : 'Not confirmed yet';

  return <section className="card" aria-labelledby="calendar-heading">
    <p className="eyebrow">YOUR CONNECTED CALENDAR</p><h2 id="calendar-heading">Google Calendar</h2>
    <p className="muted">Upcoming events from the connected Google account. These belong to the calendar account and are not automatically assigned to a household member.</p>
    {calendar ? (
      <>
        <p className="muted" role="status">
          Saarthi sign-in: active · Google Calendar: <strong>{googleLabel}</strong>
        </p>
        <p className="muted">
          “Connect Google Calendar” links your Google account (separate from Saarthi sign-in). “Load upcoming events” reads that Google calendar after it is linked.
        </p>
        <p><button className="button secondary" type="button" disabled={busy} onClick={() => void connectAuthenticated()}>Connect Google Calendar</button></p>
      </>
    ) : (
      <p><a href="http://127.0.0.1:8080/oauth/google/connect" target="_blank" rel="noreferrer">Connect calendar on this computer</a></p>
    )}
    <button className="button secondary" disabled={busy} onClick={() => void load()}>{busy ? 'Reading calendar…' : 'Load upcoming events'}</button>
    {connectNotice && <p className="notice" role="status">{connectNotice}</p>}
    {handoffUrl && <p><a className="button secondary" href={handoffUrl} target="_blank" rel="noopener noreferrer">Continue to Google</a></p>}
    {error && <p role="alert" className="notice error">{error}</p>}
    {events && (events.length ? <ul className="routine-list">{events.map(event => <li key={event.id}><div><strong>{event.summary}</strong><p>{event.start.date ? `${event.start.date} · All day` : new Date(event.start.dateTime!).toLocaleString()}</p></div><span className="tag">{event.status ?? 'Calendar event'}</span></li>)}</ul> : <p>No upcoming events were returned by Google Calendar.</p>)}
    {more && <p className="muted">Showing the first 25 upcoming events. More are available in Google Calendar.</p>}
    {person ? <AppointmentEditor key={person.id} person={person} calendar={calendar} onSaved={status => {
      void load();
      if (status === 'saved') onAppointmentSaved?.(person.id);
    }} /> : <p className="muted">Open a saved person's notebook to add an appointment with a review before saving.</p>}
  </section>;
}
