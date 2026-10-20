import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { Person } from './contracts';
import {
  householdCalendarErrorMessage,
  parseAppointmentReview,
  parseAppointmentSaved,
  type AppointmentReview,
  type AppointmentSaved,
  type HouseholdCalendarClient,
} from './householdCalendarClient';

async function localRequest(action: string, input: unknown) {
  const response = await fetch(`/oauth/google/appointments/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
    signal: AbortSignal.timeout(60_000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.error === 'string' ? result.error : 'Could not complete this request.');
  return result;
}

export function AppointmentEditor({
  person, onSaved, calendar, draft,
}: {
  person: Person;
  onSaved: (status: AppointmentSaved['status']) => void;
  calendar?: HouseholdCalendarClient;
  draft?: { id: string; title: string; start_local: string; end_local: string; time_zone: string };
}) {
  const [title, setTitle] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [zone, setZone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [review, setReview] = useState<AppointmentReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const lock = useRef(false);
  const draftVersion = useRef(0);
  useEffect(() => {
    if (!draft) return;
    draftVersion.current += 1;
    setTitle(draft.title); setStart(draft.start_local); setEnd(draft.end_local); setZone(draft.time_zone);
    setReview(null); setMessage(''); setError('');
  }, [draft?.id]);
  const edit = (setter: (s: string) => void, value: string) => { setter(value); setReview(null); setMessage(''); setError(''); };

  async function prepare(event: FormEvent) {
    event.preventDefault();
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setReview(null); setMessage('');
    const version = draftVersion.current;
    try {
      const draft = { person: person.id, title, start_local: start, end_local: end, time_zone: zone };
      if (calendar) {
        const prepared = await calendar.reviewAppointment(draft);
        if (version === draftVersion.current) setReview(prepared);
      } else {
        const prepared = parseAppointmentReview(await localRequest('review', draft), person.id, draft);
        if (version === draftVersion.current) setReview(prepared);
      }
    } catch (e) {
      if (version === draftVersion.current) setError(calendar ? householdCalendarErrorMessage(e) : (e instanceof Error ? e.message : 'Could not prepare the review.'));
    } finally { lock.current = false; setBusy(false); }
  }

  async function confirm() {
    if (!review || lock.current) return;
    lock.current = true; setBusy(true); setError(''); setMessage('');
    const version = draftVersion.current;
    try {
      const result = calendar
        ? await calendar.confirmAppointment(review.review_token)
        : parseAppointmentSaved(await localRequest('confirm', { review_token: review.review_token, confirmed: true }));
      if (version === draftVersion.current) {
        setMessage(result.message);
        if (result.status === 'saved') { setReview(null); setTitle(''); setStart(''); setEnd(''); }
      }
      onSaved(result.status);
    } catch (e) {
      if (version === draftVersion.current) setError((calendar ? householdCalendarErrorMessage(e) : (e instanceof Error ? e.message : 'Save was not confirmed.'))
        + ' Retry this review before creating another appointment.');
    } finally { lock.current = false; setBusy(false); }
  }

  return <section className="appointment-editor" aria-labelledby="appointment-create-heading" aria-busy={busy}>
    <h3 id="appointment-create-heading">Add an appointment for {person.name}</h3>
    <form onSubmit={prepare}>
      <fieldset className="form-fields" disabled={busy}>
        <legend className="sr-only">Appointment details</legend>
        <div className="form-grid">
        <div className="form-field form-field-wide"><label htmlFor="appointment-title">Appointment title</label><input id="appointment-title" value={title} onChange={e => edit(setTitle, e.target.value)} required maxLength={200} /></div>
        <div className="form-field"><label htmlFor="appointment-start">Starts</label><input id="appointment-start" type="datetime-local" value={start} onChange={e => edit(setStart, e.target.value)} required /></div>
        <div className="form-field"><label htmlFor="appointment-end">Ends</label><input id="appointment-end" type="datetime-local" value={end} onChange={e => edit(setEnd, e.target.value)} required /></div>
        <div className="form-field form-field-wide"><label htmlFor="appointment-zone">Timezone</label><input id="appointment-zone" value={zone} onChange={e => edit(setZone, e.target.value)} required maxLength={80} aria-describedby="appointment-time-hint" /></div>
        </div>
        <p className="form-hint" id="appointment-time-hint">Times use the timezone above. Review only shows what would be saved — it does not write to Google or household memory until you confirm.</p>
        <button className="button secondary" type="submit">Review appointment</button>
      </fieldset>
    </form>
    {review && <div className="record-detail review-card" aria-live="polite"><p className="eyebrow">READY FOR YOUR REVIEW</p><h3>Review before saving</h3><p>{review.person.name} · {review.title}</p>
      <p>{review.start} to {review.end}<br />{review.time_zone}</p><p>Calendar: {review.calendar_account}</p>
      <p>{review.notice}</p><p className="muted">This review expires after ten minutes.</p>
      <button className="button primary" type="button" disabled={busy} onClick={() => void confirm()}>{busy ? 'Saving…' : 'Confirm and save appointment'}</button>
    </div>}
    {error && <p className="notice error" role="alert">{error}</p>}
    {message && <p className="notice" role="status">{message} Refresh the notebook to see updated records.</p>}
  </section>;
}
