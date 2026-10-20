import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { Person } from './contracts';
import {
  deliveryStatusLabel,
  householdNotifyErrorMessage,
  type HouseholdNotifyClient,
  type NotifyReview,
  type NotifyUrgency,
} from './householdNotifyClient';

/** Cognito-only: review → confirm family email. Never invents recipients or claims SMS. */
export function FamilyNotify({
  person, client, onSent, draft,
}: {
  person: Person;
  client: HouseholdNotifyClient;
  onSent?: () => void;
  draft?: { id: string; message: string };
}) {
  const [message, setMessage] = useState('');
  const [urgency, setUrgency] = useState<NotifyUrgency>('info');
  const [review, setReview] = useState<NotifyReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [statusLine, setStatusLine] = useState('');
  const lock = useRef(false);
  const draftVersion = useRef(0);

  useEffect(() => {
    if (!draft) return;
    draftVersion.current += 1;
    setMessage(draft.message);
    setUrgency('info');
    setReview(null);
    setError('');
    setStatusLine('');
  }, [draft?.id, draft?.message]);

  function edited(next: () => void) {
    next();
    setReview(null);
    setError('');
    setStatusLine('');
  }

  async function prepare(event: FormEvent) {
    event.preventDefault();
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setReview(null);
    setStatusLine('');
    const version = draftVersion.current;
    try {
      const prepared = await client.review(person.id, message, urgency);
      if (version === draftVersion.current) setReview(prepared);
    } catch (err) {
      if (version === draftVersion.current) setError(householdNotifyErrorMessage(err));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  async function prepareDigest() {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setReview(null);
    setStatusLine('');
    const version = draftVersion.current;
    try {
      const prepared = await client.reviewDigest(person.id, 7);
      if (version === draftVersion.current) setReview(prepared);
    } catch (err) {
      if (version === draftVersion.current) setError(householdNotifyErrorMessage(err));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  async function confirm() {
    if (!review || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setStatusLine('');
    const version = draftVersion.current;
    try {
      const result = await client.confirm(review);
      if (version === draftVersion.current) setStatusLine(`${deliveryStatusLabel(result.delivery_status)}. SMS is not enabled.`);
      if (result.delivery_status !== 'uncertain') {
        if (version === draftVersion.current) { setReview(null); setMessage(''); }
        onSent?.();
      }
    } catch (err) {
      if (version === draftVersion.current) setError(householdNotifyErrorMessage(err) + ' Retry this review before composing another message.');
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  const isDigest = review?.kind === 'digest';

  return <section className="card family-notify-card" id="family-updates" aria-labelledby="family-notify-heading" aria-busy={busy}>
    <p className="eyebrow">KEEP THE FAMILY INFORMED</p>
    <h2 id="family-notify-heading">Message the family</h2>
    <p className="muted">
      Review a message about {person.name} before any email is sent. Recipients come only from saved
      household family emails. SMS is not available.
    </p>
    <div className="family-compose-layout">
    <form onSubmit={prepare}>
      <fieldset className="form-fields" disabled={busy}>
        <legend className="sr-only">Compose family message</legend>
        <div className="form-field">
        <label htmlFor="family-notify-message">Message</label>
        <textarea
          id="family-notify-message"
          value={message}
          onChange={e => edited(() => setMessage(e.target.value))}
          rows={4}
          maxLength={4000}
          required
          aria-describedby="family-notify-review-hint"
        />
        </div>
        <div className="form-field form-field-short">
        <label htmlFor="family-notify-urgency">Urgency</label>
        <select
          id="family-notify-urgency"
          value={urgency}
          onChange={e => edited(() => setUrgency(e.target.value as NotifyUrgency))}
        >
          <option value="info">Info</option>
          <option value="warning">Warning</option>
          <option value="urgent">Urgent</option>
        </select>
        </div>
        <p className="form-hint" id="family-notify-review-hint">Review does not send email. Check your message and the saved recipients before confirming.</p>
        <button className="button primary" type="submit">{busy && !review ? 'Preparing review…' : 'Review family message'}</button>
      </fieldset>
    </form>
    <div className="digest-card">
      <p className="eyebrow">A WEEK IN ONE MESSAGE</p>
      <h3>Weekly digest</h3>
      <p className="muted">
        Assemble the last seven days of saved dose logs, appointments, and notes for review.
        Nothing is emailed until you confirm. This is not medical advice.
      </p>
      <button className="button secondary" type="button" disabled={busy} onClick={() => void prepareDigest()}>
        Review weekly digest
      </button>
    </div>
    </div>
    {review && <div className="record-detail review-card" aria-live="polite">
      <p className="eyebrow">READY FOR YOUR REVIEW</p>
      <h3>{isDigest ? 'Review weekly digest before sending' : 'Review before sending'}</h3>
      <p>{review.person.name} · {review.urgency}{isDigest ? ' · digest' : ''}</p>
      <pre className="digest-preview">{review.message}</pre>
      <p className="review-recipient">Recipients: {review.recipient_emails.join(', ')}</p>
      <p className="muted">{deliveryStatusLabel(review.delivery_status)}</p>
      <p>{review.notice}</p>
      <p className="muted">This review expires after ten minutes. SMS will not be sent.</p>
      <button className="button primary" type="button" disabled={busy} onClick={() => void confirm()}>
        {busy ? 'Sending…' : isDigest ? 'Confirm and send weekly digest' : 'Confirm and send email'}
      </button>
    </div>}
    {error && <p className="notice error" role="alert">{error}</p>}
    {statusLine && <p className="notice" role="status">{statusLine} Refresh the notebook to see the recorded update.</p>}
  </section>;
}
