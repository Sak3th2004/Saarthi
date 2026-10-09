import { useRef, useState, type FormEvent } from 'react';
import { createHouseholdSetupClient, householdDefinition, type FamilyDraft, type HouseholdReview, type HouseholdStatus } from './householdSetupClient';

export function HouseholdSetup({ onOpen }: { onOpen: (personId: string) => void }) {
  const [client] = useState(() => createHouseholdSetupClient());
  const [primaryId] = useState(() => crypto.randomUUID());
  const [name, setName] = useState('');
  const [family, setFamily] = useState<FamilyDraft[]>([]);
  const [status, setStatus] = useState<HouseholdStatus | null>(null);
  const [review, setReview] = useState<HouseholdReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const lock = useRef(false);

  function edited() { setReview(null); setError(''); }
  async function checkStatus() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try { const result = await client.status(); setStatus(result); if (result.configured) setReview(null); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not check the household.'); }
    finally { lock.current = false; setBusy(false); }
  }
  async function prepare(event: FormEvent) {
    event.preventDefault();
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setReview(null);
    try { setReview(await client.review(householdDefinition(primaryId, name, family))); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not prepare the household review.'); }
    finally { lock.current = false; setBusy(false); }
  }
  async function confirm() {
    if (!review || lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const result = await client.confirm(review);
      setStatus({ configured: true, primary_person: result.primary_person, persistence: result.persistence });
      setSaved(true); setReview(null);
    } catch (e) {
      setError((e instanceof Error ? e.message : 'Saving was not confirmed.') + ' Check household status, or retry this same review.');
    } finally { lock.current = false; setBusy(false); }
  }
  function updateFamily(id: string, key: 'name' | 'relation', value: string) {
    edited(); setFamily(previous => previous.map(member => member.id === id ? { ...member, [key]: value } : member));
  }
  const storage = review?.persistence ?? status?.persistence;
  return <section className="card" aria-labelledby="household-setup-heading" aria-busy={busy}>
    <div className="section-heading"><div><p className="eyebrow">YOUR HOUSEHOLD</p><h2 id="household-setup-heading">Start with the people you care about</h2></div><span className="tag">On this computer</span></div>
    <p className="muted">Set up a household using details you have permission to save. Existing household records are preserved.</p>
    <div className="connect-controls"><button className="button secondary" type="button" disabled={busy} onClick={() => void checkStatus()}>{busy ? 'Working…' : 'Check household status'}</button></div>
    {storage === 'session' && <p className="notice error" role="status">This server uses temporary memory. Records last only while the server process runs.</p>}
    {storage === 'persistent' && <p className="muted">This server saves records in the connected database.</p>}
    {status?.configured && status.primary_person && <div className="record-detail">
      <p role="status">{saved ? 'Household saved for ' : 'A household is already set up for '}{status.primary_person.name}.</p>
      <button type="button" className="button primary" disabled={busy} onClick={() => onOpen(status.primary_person!.id)}>Open household notebook</button>
    </div>}
    {status && !status.configured && <form onSubmit={prepare}>
      <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: '18px 0 0', display: 'grid', gap: 10 }}>
        <legend className="sr-only">New household details</legend>
        <label htmlFor="household-elder">Older adult’s name</label>
        <input id="household-elder" value={name} maxLength={200} required autoComplete="off" onChange={e => { edited(); setName(e.target.value); }} />
        {family.map((member, index) => <fieldset key={member.id} className="record-detail" style={{ border: 0, display: 'grid', gap: 8 }}>
          <legend>Family member {index + 1}</legend>
          <label htmlFor={`name-${member.id}`}>Name</label><input id={`name-${member.id}`} value={member.name} maxLength={200} required autoComplete="off" onChange={e => updateFamily(member.id, 'name', e.target.value)} />
          <label htmlFor={`relation-${member.id}`}>Relationship to the older adult</label><input id={`relation-${member.id}`} value={member.relation} maxLength={200} required onChange={e => updateFamily(member.id, 'relation', e.target.value)} />
          <button type="button" className="button secondary" onClick={() => { edited(); setFamily(previous => previous.filter(item => item.id !== member.id)); }}>Remove family member {index + 1}</button>
        </fieldset>)}
        <div className="connect-controls">
          <button type="button" className="button secondary" disabled={family.length >= 99} onClick={() => { edited(); setFamily(previous => [...previous, { id: crypto.randomUUID(), name: '', relation: '' }]); }}>Add family member</button>
          <button type="submit" className="button primary">Review household</button>
        </div>
        <p className="form-hint">Family members are optional. Adding someone here does not invite them or give them account access.</p>
      </fieldset>
    </form>}
    {review && <div className="record-detail">
      <h3>Review before saving</h3>
      <ul>{review.household.people.map(member => <li key={member.id}><strong>{member.name}</strong> — {member.role === 'elder' ? 'Older adult' : `${member.relation} of ${review.household.people[0].name}`}</li>)}</ul>
      <p className="muted">This saves the people and relationships above. The review expires after ten minutes.</p>
      <button type="button" className="button primary" disabled={busy} onClick={() => void confirm()}>Confirm and save household</button>
    </div>}
    {error && <p className="notice error" role="alert">{error}</p>}
  </section>;
}
