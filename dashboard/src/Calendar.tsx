import { useState } from 'react';
import type { Person } from './contracts';
import { AppointmentEditor } from './AppointmentEditor';

type CalendarEvent = { id: string; summary: string; start: { date?: string; dateTime?: string }; end: { date?: string; dateTime?: string }; status?: string };

/** Reads the connected calendar only when requested; does not invent or book events. */
export function Calendar({ person }: { person?: Person }) {
  const [events, setEvents] = useState<CalendarEvent[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [more, setMore] = useState(false);
  async function load() {
    if (busy) return;
    setBusy(true); setError(''); setEvents(null);
    try {
      const response = await fetch('/oauth/google/events', { cache: 'no-store', signal: AbortSignal.timeout(45_000) });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (data.source !== 'google_calendar' || !Array.isArray(data.events) || data.events.length > 25 ||
          data.events.some((e: CalendarEvent) => typeof e.id !== 'string' || typeof e.summary !== 'string' || !e.start ||
            !(typeof e.start.date === 'string' || typeof e.start.dateTime === 'string'))) throw new Error();
      setEvents(data.events); setMore(data.more_available === true);
    } catch { setError('Could not read Google Calendar. Connect your account and try again.'); }
    finally { setBusy(false); }
  }
  return <section className="card" aria-labelledby="calendar-heading">
    <p className="eyebrow">YOUR CONNECTED CALENDAR</p><h2 id="calendar-heading">Google Calendar</h2>
    <p className="muted">Upcoming events from the connected account. These belong to the calendar account and are not automatically assigned to a household member.</p>
    <p><a href="http://127.0.0.1:8080/oauth/google/connect" target="_blank" rel="noreferrer">Connect calendar on this computer</a></p>
    <button className="button secondary" disabled={busy} onClick={() => void load()}>{busy ? 'Reading calendar…' : 'Load upcoming events'}</button>
    {error && <p role="alert" className="notice error">{error}</p>}
    {events && (events.length ? <ul className="routine-list">{events.map(event => <li key={event.id}><div><strong>{event.summary}</strong><p>{event.start.date ? `${event.start.date} · All day` : new Date(event.start.dateTime!).toLocaleString()}</p></div><span className="tag">{event.status ?? 'Calendar event'}</span></li>)}</ul> : <p>No upcoming events were returned by Google Calendar.</p>)}
    {more && <p className="muted">Showing the first 25 upcoming events. More are available in Google Calendar.</p>}
    {person ? <AppointmentEditor key={person.id} person={person} onSaved={() => void load()} /> : <p className="muted">Open a saved person's notebook to add an appointment with a review before saving.</p>}
  </section>;
}
