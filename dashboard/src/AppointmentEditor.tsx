import { useRef, useState, type FormEvent } from 'react';
import type { Person } from './contracts';

type Review = { review_token: string; person: Person; title: string; start: string; end: string; time_zone: string; calendar_account: string; notice: string };

async function request(action: string, input: unknown) {
  const response = await fetch(`/oauth/google/appointments/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
    signal: AbortSignal.timeout(60_000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.error === 'string' ? result.error : 'Could not complete this request.');
  return result;
}

export function AppointmentEditor({ person, onSaved }: { person: Person; onSaved: () => void }) {
  const [title, setTitle] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [zone, setZone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const lock = useRef(false);
  const edit = (setter: (s: string) => void, value: string) => { setter(value); setReview(null); setMessage(''); setError(''); };
  async function prepare(event: FormEvent) {
    event.preventDefault();
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setReview(null); setMessage('');
    try {
      const result = await request('review', { person: person.id, title, start_local: start, end_local: end, time_zone: zone });
      if (typeof result.review_token !== 'string' || result.person?.id !== person.id || typeof result.title !== 'string' ||
          typeof result.start !== 'string' || typeof result.end !== 'string' || typeof result.time_zone !== 'string' ||
          typeof result.calendar_account !== 'string' || typeof result.notice !== 'string') throw new Error('The appointment review was incomplete. Nothing was saved.');
      setReview(result);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not prepare the review.'); }
    finally { lock.current = false; setBusy(false); }
  }
  async function confirm() {
    if (!review || lock.current) return;
    lock.current = true; setBusy(true); setError(''); setMessage('');
    try {
      const result = await request('confirm', { review_token: review.review_token, confirmed: true });
      if (!['saved', 'calendar_saved_memory_pending'].includes(result.status) || typeof result.message !== 'string') throw new Error('Save was not confirmed. Retry this review to check the same appointment.');
      setMessage(result.message);
      if (result.status === 'saved') { setReview(null); setTitle(''); setStart(''); setEnd(''); onSaved(); }
    } catch (e) { setError((e instanceof Error ? e.message : 'Save was not confirmed.') + ' Retry this review before creating another appointment.'); }
    finally { lock.current = false; setBusy(false); }
  }
  return <section aria-labelledby="appointment-create-heading">
    <h3 id="appointment-create-heading">Add an appointment for {person.name}</h3>
    <form onSubmit={prepare}>
      <fieldset disabled={busy}>
        <label htmlFor="appointment-title">Appointment title</label><input id="appointment-title" value={title} onChange={e => edit(setTitle, e.target.value)} required maxLength={200} />
        <label htmlFor="appointment-start">Starts</label><input id="appointment-start" type="datetime-local" value={start} onChange={e => edit(setStart, e.target.value)} required />
        <label htmlFor="appointment-end">Ends</label><input id="appointment-end" type="datetime-local" value={end} onChange={e => edit(setEnd, e.target.value)} required />
        <label htmlFor="appointment-zone">Timezone</label><input id="appointment-zone" value={zone} onChange={e => edit(setZone, e.target.value)} required maxLength={80} />
        <p className="muted">Times use the timezone above. Review does not save anything.</p>
        <button className="button secondary" type="submit">Review appointment</button>
      </fieldset>
    </form>
    {review && <div className="record-detail"><h3>Review before saving</h3><p>{review.person.name} · {review.title}</p>
      <p>{review.start} to {review.end}<br />{review.time_zone}</p><p>Calendar: {review.calendar_account}</p>
      <p>{review.notice}</p><p className="muted">This review expires after ten minutes.</p>
      <button className="button primary" type="button" disabled={busy} onClick={() => void confirm()}>{busy ? 'Saving…' : 'Confirm and save appointment'}</button>
    </div>}
    {error && <p className="notice error" role="alert">{error}</p>}
    {message && <p className="notice" role="status">{message} Refresh the notebook to see updated records.</p>}
  </section>;
}
