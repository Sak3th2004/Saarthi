import { useRef, useState, type FormEvent } from 'react';
import type { CheckInResult, DashboardClient, DoseInput, Medication, Person } from './contracts';
import { notebookErrorMessage } from './notebookErrors';

/** A caregiver enters the time; schedules and the current clock never supply a dose time. */
export function doseTimeToISO(local: string): string {
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(local);
  if (!parts) throw new Error('Enter the date and time of the dose record.');
  const [, year, month, day, hour, minute] = parts.map(Number);
  const date = new Date(local);
  if (!Number.isFinite(date.getTime()) || date.getFullYear() !== year || date.getMonth() + 1 !== month ||
      date.getDate() !== day || date.getHours() !== hour || date.getMinutes() !== minute) {
    throw new Error('That date or time does not exist in your device timezone. Check it before saving.');
  }
  return date.toISOString();
}

function readableTime(at: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'long' }).format(new Date(at));
}

export function CareActions({ person, medications, client, onSaved }: {
  person: Person; medications: Medication[]; client: DashboardClient; onSaved: (personId: string) => void;
}) {
  const [med, setMed] = useState('');
  const [status, setStatus] = useState('');
  const [at, setAt] = useState('');
  const [review, setReview] = useState<DoseInput | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const [check, setCheck] = useState<CheckInResult | null>(null);
  const [checkError, setCheckError] = useState('');
  const saveLock = useRef(false), checkLock = useRef(false);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const edit = (setter: (value: string) => void, value: string) => {
    setter(value); setReview(null); setNotice(''); setError('');
  };

  function prepare(event: FormEvent) {
    event.preventDefault();
    if (saveLock.current) return;
    setError(''); setNotice(''); setReview(null);
    if (!medications.some(item => item.name === med) || !['taken', 'missed'].includes(status)) {
      setError('Choose a saved medication and whether the dose was taken or missed.'); return;
    }
    try { setReview({ med, taken: status === 'taken', at: doseTimeToISO(at) }); }
    catch (error) { setError(error instanceof Error ? error.message : 'Check the entered time.'); }
  }

  async function confirm() {
    if (!review || saveLock.current) return;
    saveLock.current = true; setBusy(true); setError(''); setNotice('');
    try {
      const result = await client.logDose(person.id, review);
      setNotice(`${result.already_logged ? 'Already recorded; no new dose was added.' : 'Dose record saved.'} ${result.dose.med}: ${result.dose.status}, ${readableTime(result.dose.at)}.`);
      setReview(null); setAt(''); setStatus(''); setCheck(null);
      onSaved(person.id);
    } catch (error) {
      setError(notebookErrorMessage(error, 'Saving was not confirmed. Refresh the notebook and check its timeline before trying again. Your entered details are still here.'));
    } finally { saveLock.current = false; setBusy(false); }
  }

  async function checkRecords() {
    if (checkLock.current || saveLock.current) return;
    checkLock.current = true; setChecking(true); setCheck(null); setCheckError('');
    try { setCheck(await client.checkIn(person.id)); }
    catch (error) { setCheckError(notebookErrorMessage(error, 'Could not check the saved records. No conclusion can be drawn from this failed check.')); }
    finally { checkLock.current = false; setChecking(false); }
  }

  return <div className="equal-grid">
    <section className="card" aria-labelledby="dose-record-heading">
      <h3 id="dose-record-heading">Record a taken or missed dose</h3>
      <p className="muted">Record only what you know happened for {person.name}. This does not change medication instructions or send a reminder.</p>
      {medications.length ? <form onSubmit={prepare}>
        <fieldset disabled={busy || checking}>
          <label htmlFor="dose-medication">Saved medication</label>
          <select id="dose-medication" value={med} required onChange={event => edit(setMed, event.target.value)}>
            <option value="">Choose medication</option>
            {medications.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
          </select>
          <label htmlFor="dose-status">What happened?</label>
          <select id="dose-status" value={status} required onChange={event => edit(setStatus, event.target.value)}>
            <option value="">Choose taken or missed</option><option value="taken">Taken</option><option value="missed">Missed</option>
          </select>
          <label htmlFor="dose-time">Date and time of this record</label>
          <input id="dose-time" type="datetime-local" value={at} required aria-describedby="dose-zone" onChange={event => edit(setAt, event.target.value)} />
          <p className="form-hint" id="dose-zone">Use your device timezone: {zone}. If the person is elsewhere, enter the equivalent time here. Check the displayed timezone offset in the review.</p>
          <button className="button secondary" type="submit">Review dose record</button>
        </fieldset>
      </form> : <p className="empty">Add the person's medication facts before recording a dose.</p>}
      {review && <div className="record-detail" aria-live="polite">
        <h4>Review before saving</h4><p>{person.name}: {review.med} — {review.taken ? 'taken' : 'missed'}</p>
        <p><time dateTime={review.at}>{readableTime(review.at)}</time> ({zone})</p>
        <p>This records one {review.taken ? 'taken' : 'missed'} dose. Review the person, medication and time before confirming.</p>
        <button className="button primary" type="button" disabled={busy || checking} onClick={() => void confirm()}>{busy ? 'Saving dose record…' : 'Confirm dose record'}</button>
        <button className="button secondary" type="button" disabled={busy} onClick={() => setReview(null)}>Cancel review</button>
      </div>}
      {notice && <p className="notice" role="status">{notice}</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
    </section>
    <section className="card" aria-labelledby="check-records-heading">
      <h3 id="check-records-heading">Check saved records</h3>
      <p className="muted">Review recorded missed doses, remaining supply and recent activity. This does not contact {person.name} or notify the family.</p>
      <button className="button secondary" type="button" disabled={checking || busy} onClick={() => void checkRecords()}>{checking ? 'Checking records…' : 'Check for recorded concerns'}</button>
      {check && <div className="record-detail" aria-live="polite">
        <h4>{check.concerns.length ? 'Records to follow up' : 'No concerns returned by this check'}</h4>
        {check.concerns.length > 0 && <ul>{check.concerns.map((concern, index) => <li key={index}>{concern}</li>)}</ul>}
        <p>{check.speech}</p>
        <p>Last saved activity: {check.last_activity ? <time dateTime={check.last_activity}>{readableTime(check.last_activity)}</time> : 'No activity record returned'}.</p>
        <p>These records cannot confirm anyone's wellbeing. No family message has been sent.</p>
      </div>}
      {checkError && <p className="notice error" role="alert">{checkError}</p>}
    </section>
  </div>;
}
