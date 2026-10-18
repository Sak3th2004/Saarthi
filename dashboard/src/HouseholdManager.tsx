import { useEffect, useRef, useState, type FormEvent } from 'react';
import { householdManagementErrorMessage, type HouseholdChange, type HouseholdChangeReview, type HouseholdDirectory, type HouseholdManagementClient } from './householdManagementClient';
import { describeAddMedication, parseScheduleSlots, parseSupplyCount } from './medicationEntry';

export function describeHouseholdChange(change: HouseholdChange, directory: HouseholdDirectory) {
  const name = (id: string) => directory.people.find(person => person.id === id)?.name ?? 'Selected person';
  switch (change.kind) {
    case 'add_person': return `Add ${change.name} as ${change.role === 'elder' ? 'an older adult' : 'a family member'}.`;
    case 'rename_person': return `Change ${name(change.person_id)}'s name to ${change.name}. Their saved records stay with them.`;
    case 'set_primary': return `Make ${name(change.person_id)} the default older adult for this household.`;
    case 'set_relationship': return `Save: ${name(change.from_person)} is ${change.relation} of ${name(change.to_person)}.`;
    case 'set_person_email': return `Save ${change.email} as the family notification email for ${name(change.person_id)}.`;
    case 'add_medication': return describeAddMedication(change, name(change.person_id));
  }
}

/** Primary open CTA — uses the selected person's saved name; never invents a default elder. */
export function openNotebookLabel(name: string) {
  return `Open ${name}'s notebook`;
}

export function HouseholdManager({ client, onOpen, onChanged, disabled = false }: {
  client: HouseholdManagementClient; onOpen: (id: string) => void; onChanged?: () => void; disabled?: boolean;
}) {
  const [directory, setDirectory] = useState<HouseholdDirectory | null>(null);
  const [review, setReview] = useState<HouseholdChangeReview | null>(null);
  const [expiresAt, setExpiresAt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [kind, setKind] = useState<HouseholdChange['kind']>('add_person');
  const [name, setName] = useState('');
  const [role, setRole] = useState<'elder' | 'family'>('family');
  const [personId, setPersonId] = useState('');
  const [toPerson, setToPerson] = useState('');
  const [relation, setRelation] = useState('');
  const [email, setEmail] = useState('');
  const [dose, setDose] = useState('');
  const [scheduleText, setScheduleText] = useState('');
  const [supplyText, setSupplyText] = useState('');
  const [selected, setSelected] = useState('');
  const lock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!review) return;
    const timer = window.setTimeout(() => { setReview(null); setNotice('The review expired. Review the change again before saving.'); }, Math.max(0, expiresAt - Date.now()));
    return () => window.clearTimeout(timer);
  }, [review, expiresAt]);

  function edited() { setReview(null); setError(''); setNotice(''); }
  function useDirectory(result: HouseholdDirectory) {
    setDirectory(result);
    setSelected(current => result.people.some(person => person.id === current) ? current : result.primary_person_id ?? result.people[0]?.id ?? '');
  }
  function resetMedFields() { setName(''); setDose(''); setScheduleText(''); setSupplyText(''); }
  async function refresh() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setReview(null); setNotice('');
    try { const result = await client.status(); if (mounted.current) useDirectory(result); }
    catch (error) { if (mounted.current) { setDirectory(null); setError(householdManagementErrorMessage(error)); } }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  // One directory read after sign-in; notebook open stays an explicit click.
  useEffect(() => { void refresh(); }, []);
  async function prepare(event: FormEvent) {
    event.preventDefault();
    if (!directory || lock.current) return;
    const change: HouseholdChange | null = kind === 'add_person' ? { kind, id: crypto.randomUUID(), name: name.trim(), role }
      : kind === 'rename_person' ? { kind, person_id: personId, name: name.trim() }
      : kind === 'set_primary' ? { kind, person_id: personId }
      : kind === 'set_person_email' ? { kind, person_id: personId, email: email.trim() }
      : kind === 'add_medication' ? (() => {
        let supply: number | undefined;
        try { supply = parseSupplyCount(supplyText); }
        catch (err) { setError(err instanceof Error ? err.message : 'Remaining doses must be a whole number, or blank.'); return null; }
        return {
          kind, person_id: personId, name: name.trim(), dose: dose.trim(), schedule: parseScheduleSlots(scheduleText),
          ...(supply !== undefined ? { supply_count: supply } : {}),
        };
      })()
      : { kind, from_person: personId, to_person: toPerson, relation: relation.trim() };
    if (!change) return;
    if (change.kind === 'add_medication') {
      const typedSlots = scheduleText.split(/[,;|]+/).map(part => part.trim()).filter(Boolean);
      if (typedSlots.length !== change.schedule.length) {
        setError('Schedule times must use HH:MM on a 24-hour clock (for example 08:00, 20:00), or leave blank if unknown.');
        return;
      }
    }
    lock.current = true; setBusy(true); setError(''); setReview(null); setNotice('');
    try { const result = await client.review(directory, change); if (mounted.current) { setReview(result); setExpiresAt(Date.now() + result.expires_in_seconds * 1000); } }
    catch (error) { if (mounted.current) setError(householdManagementErrorMessage(error)); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  async function confirm() {
    if (!review || lock.current) return;
    if (Date.now() >= expiresAt) { setReview(null); setNotice('The review expired. Review the change again before saving.'); return; }
    lock.current = true; setBusy(true); setError('');
    const savedMedication = review.change.kind === 'add_medication';
    try {
      const result = await client.confirm(review);
      if (mounted.current) {
        useDirectory(result); setReview(null); setRelation(''); setEmail('');
        if (savedMedication) resetMedFields();
        else setName('');
        setNotice(savedMedication
          ? 'Medication schedule fact saved. Refresh the notebook to see it under Routines.'
          : 'Household change saved.');
        onChanged?.();
      }
    } catch (error) {
      // The write may have completed. Require a fresh read before another edit.
      if (mounted.current) { setDirectory(null); setReview(null); setError(householdManagementErrorMessage(error)); }
    } finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  const unavailable = busy || disabled;
  const familyMembers = directory?.people.filter(person => person.role === 'family') ?? [];
  const options = directory?.people.filter(person => {
    if (kind === 'set_primary' || kind === 'add_medication') return person.role === 'elder';
    if (kind === 'set_person_email') return person.role === 'family';
    return true;
  }) ?? [];
  const selectedPerson = directory?.people.find(person => person.id === selected);
  return <section className="card" aria-labelledby="household-manager-heading" aria-busy={busy}>
    <div className="section-heading"><div><p className="eyebrow">YOUR HOUSEHOLD</p><h2 id="household-manager-heading">People & relationships</h2></div>
      <button type="button" className="button secondary" disabled={unavailable} onClick={() => void refresh()}>{busy ? 'Working…' : 'Refresh people'}</button></div>
    <p className="muted">Saved people load after sign-in. Open a notebook or review a change to your family details.</p>
    {directory?.persistence === 'session' && <p className="notice error" role="status">This server uses temporary memory. Records last only while the server runs.</p>}
    {!directory && busy && <p className="muted" role="status">Loading saved people…</p>}
    {directory && <>
      {directory.people.length ? <div className="record-detail">
        <label htmlFor="saved-person">Whose notebook would you like to open?</label>
        <div className="connect-controls"><select id="saved-person" value={selected} disabled={unavailable} onChange={event => setSelected(event.target.value)}>
          {directory.people.map(person => <option key={person.id} value={person.id}>{person.name} — {person.role === 'elder' ? 'Older adult' : 'Family'}{person.id === directory.primary_person_id ? ' (default)' : ''}</option>)}
        </select><button type="button" className="button primary" disabled={unavailable || !selected} onClick={() => onOpen(selected)}>{selectedPerson ? openNotebookLabel(selectedPerson.name) : 'Open notebook'}</button></div>
        {familyMembers.length > 0 && <ul className="muted">{familyMembers.map(person => <li key={`email-${person.id}`}>{person.name}: {person.email ? `notification email ${person.email}` : 'no notification email saved yet'}</li>)}</ul>}
        {directory.relationships.length > 0 && <ul>{directory.relationships.map(relationship => <li key={`${relationship.from_person}/${relationship.to_person}`}>{directory.people.find(person => person.id === relationship.from_person)!.name} is {relationship.relation} of {directory.people.find(person => person.id === relationship.to_person)!.name}.</li>)}</ul>}
      </div> : <p className="notice">No people are saved yet. Start by adding the older adult, then add family members.</p>}
      <details className="record-detail" id="family-details"><summary>Update family details</summary>
        <form onSubmit={prepare}><fieldset disabled={unavailable} style={{ border: 0, padding: 0, marginTop: 14, display: 'grid', gap: 9 }}>
          <legend className="sr-only">Household change</legend>
          <label htmlFor="household-change">What would you like to change?</label>
          <select id="household-change" value={kind} onChange={event => {
            edited();
            setKind(event.target.value as HouseholdChange['kind']);
            setPersonId(''); setName(''); setEmail(''); setDose(''); setScheduleText('');
          }}>
            <option value="add_person">Add a person</option>
            <option value="rename_person" disabled={!directory.people.length}>Correct a name</option>
            <option value="set_relationship" disabled={directory.people.length < 2}>Set a relationship</option>
            <option value="set_primary" disabled={!directory.people.some(person => person.role === 'elder')}>Choose default older adult</option>
            <option value="set_person_email" disabled={!familyMembers.length}>Set family notification email</option>
            <option value="add_medication" disabled={!directory.people.some(person => person.role === 'elder')}>Add a medication schedule fact</option>
          </select>
          {kind !== 'add_person' && <><label htmlFor="change-person">{kind === 'set_relationship' ? 'This person' : kind === 'add_medication' ? 'Whose schedule' : 'Person'}</label><select id="change-person" value={personId} required onChange={event => { edited(); const next = event.target.value; setPersonId(next); if (kind === 'set_person_email') setEmail(directory.people.find(person => person.id === next)?.email ?? ''); }}><option value="">Choose a person</option>{options.map(person => <option key={person.id} value={person.id}>{person.name}</option>)}</select></>}
          {(kind === 'add_person' || kind === 'rename_person' || kind === 'add_medication') && <><label htmlFor="person-name">{kind === 'rename_person' ? 'Corrected name' : kind === 'add_medication' ? 'Medication name' : 'Name'}</label><input id="person-name" required maxLength={200} value={name} autoComplete="off" onChange={event => { edited(); setName(event.target.value); }} placeholder={kind === 'add_medication' ? 'As your family writes it' : undefined} /></>}
          {kind === 'add_person' && <><label htmlFor="person-role">Role</label><select id="person-role" value={role} onChange={event => { edited(); setRole(event.target.value as 'elder' | 'family'); }}><option value="elder">Older adult</option><option value="family">Family member</option></select><p className="form-hint">Adding a person does not send an invitation or give them sign-in access.</p></>}
          {kind === 'add_medication' && <>
            <label htmlFor="med-dose">Dose text</label>
            <input id="med-dose" required maxLength={200} value={dose} autoComplete="off" placeholder="As written on the bottle or plan" onChange={event => { edited(); setDose(event.target.value); }} />
            <label htmlFor="med-schedule">Schedule times (optional)</label>
            <input id="med-schedule" maxLength={200} value={scheduleText} autoComplete="off" placeholder="08:00, 20:00" onChange={event => { edited(); setScheduleText(event.target.value); }} aria-describedby="med-schedule-hint" />
            <p className="form-hint" id="med-schedule-hint">Use HH:MM on a 24-hour clock, separated by commas. Leave blank if the time is not recorded. Saves what you type — not medical advice.</p>
            <label htmlFor="med-supply">Remaining doses (optional)</label>
            <input id="med-supply" inputMode="numeric" maxLength={4} value={supplyText} autoComplete="off" placeholder="e.g. 30" onChange={event => { edited(); setSupplyText(event.target.value); }} aria-describedby="med-supply-hint" />
            <p className="form-hint" id="med-supply-hint">Whole number of doses left, if your family tracks it. Leave blank if unknown. Taken doses reduce this count; 10 or fewer is flagged as low supply.</p>
          </>}
          {kind === 'set_person_email' && <><label htmlFor="person-email">Family notification email</label><input id="person-email" type="email" required maxLength={200} value={email} autoComplete="email" placeholder="name@example.com" onChange={event => { edited(); setEmail(event.target.value); }} /><p className="form-hint">Saved on this family member for reviewed notify delivery. Does not invent SES addresses or send mail by itself.</p></>}
          {kind === 'set_relationship' && <><label htmlFor="person-relation">Is … (relationship)</label><input id="person-relation" required maxLength={200} value={relation} placeholder="For example: elder son" onChange={event => { edited(); setRelation(event.target.value); }} /><label htmlFor="relation-to">Of this person</label><select id="relation-to" required value={toPerson} onChange={event => { edited(); setToPerson(event.target.value); }}><option value="">Choose a person</option>{directory.people.filter(person => person.id !== personId).map(person => <option key={person.id} value={person.id}>{person.name}</option>)}</select><p className="form-hint">This updates the relationship in this direction only.</p></>}
          <button className="button secondary" type="submit">Review change</button>
        </fieldset></form>
      </details>
      {review && <div className="record-detail" aria-live="polite"><h3>Review before saving</h3><p>{describeHouseholdChange(review.change, directory)}</p>{review.notice && <p className="form-hint">{review.notice}</p>}<p className="form-hint">This review expires after ten minutes.</p><div className="connect-controls"><button className="button primary" type="button" disabled={unavailable} onClick={() => void confirm()}>Confirm and save change</button><button className="button secondary" type="button" disabled={unavailable} onClick={() => setReview(null)}>Cancel</button></div></div>}
    </>}
    {notice && <p className="notice" role="status">{notice}</p>}{error && <p className="notice error" role="alert">{error}</p>}
  </section>;
}
