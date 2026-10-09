import { useMemo, useState, useSyncExternalStore, type FormEvent } from 'react';
import type { DashboardClient, SavedEvent } from './contracts';
import { createDashboardStore } from './store';
import { MemoryMap } from './Graph';
import { Calendar } from './Calendar';

export function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}
const isInternal = (event: SavedEvent) => ['agent_action', 'household_setup'].includes(event.type);

export function App({ client }: { client: DashboardClient }) {
  const store = useMemo(() => createDashboardStore(client), [client]);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const [person, setPerson] = useState('');
  const [detail, setDetail] = useState('');
  const [eventType, setEventType] = useState('note');
  const [question, setQuestion] = useState('');
  const [showInternal, setShowInternal] = useState(false);
  const view = state.view;
  const events = view?.events.filter(event => showInternal || !isInternal(event)) ?? [];
  const connect = (event: FormEvent) => { event.preventDefault(); setDetail(''); setQuestion(''); void store.load(person); };
  const save = async (event: FormEvent) => { event.preventDefault(); if (await store.save(eventType, detail)) setDetail(''); };
  const query = (event: FormEvent) => { event.preventDefault(); void store.query(question); };

  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to notebook</a>
    <aside className="sidebar">
      <a className="brand" href="#main" aria-label="Saarthi home"><span className="brand-mark" aria-hidden="true">s</span><span>saarthi<span className="brand-caption">a little closer, every day</span></span></a>
      <span className="nav-label">FAMILY NOTEBOOK</span>
      <nav aria-label="Notebook sections"><a className="nav-item active" href="#main"><span aria-hidden="true">◫</span> Overview</a><a className="nav-item" href="#memory"><span aria-hidden="true">◎</span> Saved connections</a><a className="nav-item" href="#timeline"><span aria-hidden="true">≡</span> Timeline</a><a className="nav-item" href="#routine"><span aria-hidden="true">▦</span> Routine</a></nav>
      <div className="sidebar-bottom"><div className="leaf-mark" aria-hidden="true">↗</div><p>Everyday details.<br />Remembered together.</p><small>Your family's saved details, in one place.</small></div>
    </aside>
    <div className="workspace">
      <header className="topbar"><span>Family space <span className="separator">/</span> Notebook</span><span className={`connection-badge ${view ? 'connected' : ''}`}><span className="status-dot" />{state.phase === 'loading' ? 'Opening notebook' : view ? 'Records loaded' : 'Not connected'}</span></header>
      <main id="main" tabIndex={-1}>
        <div className="page-heading"><div><p className="eyebrow">THE LITTLE THINGS MATTER</p><h1>{view ? `${view.person.name}'s notebook` : 'A place to stay connected.'}</h1><p className="subtitle">{view ? 'The routines, plans and moments your family has saved.' : 'Bring the everyday details together, one saved moment at a time.'}</p></div>{view && <button className="button secondary" disabled={state.saving || state.phase === 'loading'} onClick={() => void store.load(view.person.id)}>Refresh records <span aria-hidden="true">↻</span></button>}</div>
        <form className="connect-panel" onSubmit={connect}><div><label htmlFor="person">Open a person's notebook</label><p>Use a name or ID already saved in your household.</p></div><div className="connect-controls"><input id="person" autoComplete="off" value={person} onChange={e => setPerson(e.target.value)} placeholder="Saved name or person ID" required maxLength={200} disabled={state.saving} /><button className="button primary" disabled={state.phase === 'loading' || state.saving}>Open notebook <span aria-hidden="true">→</span></button></div></form>
        {state.error && <p className="notice error" role="alert">{state.error}</p>}
        {state.phase === 'loading' && <div className="loading-state" role="status"><span className="loading-ring" />Loading saved records…</div>}
        {!view && state.phase !== 'loading' && <section className="welcome-panel"><div className="notebook-illustration" aria-hidden="true"><span /><span /><span /></div><p className="eyebrow">START WITH YOUR FAMILY</p><h2>Your notebook is waiting.</h2><p>Open a saved person's records to see their connections, routines and timeline.</p><p className="muted">No saved household yet? Complete household setup on the server first.</p></section>}
        {view && <>
          <div className="summary-strip"><div><span className="summary-number">{view.medications.length}</span><span>Saved medications</span></div><div><span className="summary-number">{view.appointments.length}</span><span>Upcoming records</span></div><div><span className="summary-number">{view.events.filter(e => !isInternal(e)).length}</span><span>Notes in this view</span></div><p>Showing saved information.<br />Missing records do not tell us how someone is doing.</p></div>
          <div className="main-grid"><section className="card graph-card" id="memory"><div className="section-heading"><div><p className="eyebrow">THE BIGGER PICTURE</p><h2>Saved connections</h2></div><span className="tag">{view.graph.nodes.length} records</span></div><MemoryMap key={view.person.id} graph={view.graph} /><p className="view-note">Loaded {formatDate(view.graph.generated_at)} · Times shown in your device timezone.</p></section>
          <section className="card recall-card"><p className="eyebrow">PICK UP WHERE YOU LEFT OFF</p><h2>Find a saved detail</h2><p className="muted">Ask about something your family has recorded.</p><form onSubmit={query}><label htmlFor="question">What would you like to remember?</label><textarea id="question" value={question} onChange={e => setQuestion(e.target.value)} maxLength={2000} required rows={3} placeholder="Ask a question about your saved notes" /><button className="button primary" disabled={state.searching || state.saving}>{state.searching ? 'Looking through records…' : 'Find in memory'}<span aria-hidden="true">↗</span></button></form>{state.queryError && <p className="notice error" role="alert">{state.queryError}</p>}{state.answer && <div className="answer" aria-live="polite"><p className="answer-question">{state.answer.question}</p><p>{state.answer.answer}</p><h3>Supporting records</h3>{state.answer.supporting_events.length ? <ul className="evidence-list">{state.answer.supporting_events.map((e, i) => <li key={i}><p>{e.detail}</p><time dateTime={e.at}>{formatDate(e.at)}</time></li>)}</ul> : <p className="muted">No supporting event records were returned.</p>}</div>}</section></div>
          <div className="main-grid"><section className="card" id="timeline"><div className="section-heading"><div><p className="eyebrow">ONE MOMENT AT A TIME</p><h2>Saved timeline</h2></div><label className="checkbox-label"><input type="checkbox" checked={showInternal} onChange={e => setShowInternal(e.target.checked)} />Show system activity</label></div>{events.length ? <ol className="timeline">{events.map((e, index) => <li key={`${e.at}-${index}`}><span className={`timeline-dot ${isInternal(e) ? 'system' : ''}`} /><div><span className="event-type">{isInternal(e) ? 'System activity · ' : ''}{e.type.replaceAll('_', ' ')}</span><p>{e.detail}</p><time dateTime={e.at}>{formatDate(e.at)}</time></div></li>)}</ol> : <p className="empty">No notes in this view yet. Add a moment you'd like to remember.</p>}</section><section className="card note-card"><p className="eyebrow">KEEP THE FAMILY IN THE LOOP</p><h2>Add a moment</h2><p className="muted">Save what happened in your own words.</p><form onSubmit={save}><label htmlFor="event-type">Type of note</label><input id="event-type" value={eventType} onChange={e => setEventType(e.target.value)} maxLength={80} required disabled={state.saving} /><label htmlFor="detail">What would you like to save?</label><textarea id="detail" value={detail} onChange={e => setDetail(e.target.value)} rows={5} maxLength={4000} required disabled={state.saving} /><p className="form-hint">Saved with the current time. This adds a record; it does not send a message to anyone.</p><button className="button primary" disabled={state.saving}>{state.saving ? 'Saving…' : 'Save note'}<span aria-hidden="true">+</span></button></form>{state.notice && <p className="notice" role="status">{state.notice}</p>}{state.saveError && <p className="notice error" role="alert">{state.saveError}</p>}</section></div>
          <section className="routine-section" id="routine"><p className="eyebrow">AS YOUR FAMILY SAVED IT</p><h2>Routines & plans</h2><div className="equal-grid"><section className="card"><h3>Medication schedule</h3><p className="muted">Saved instructions only. These records are not medical advice.</p>{view.medications.length ? <ul className="routine-list">{view.medications.map((med, i) => <li key={`${med.name}-${i}`}><div><strong>{med.name}</strong><p>{med.dose}</p></div><span className="time-pill">{med.schedule.length ? med.schedule.join(' · ') : 'Time not recorded'}</span></li>)}</ul> : <p className="empty">No medication schedule saved.</p>}</section><section className="card"><h3>Upcoming appointment records</h3><p className="muted">Saved plans, not confirmation from a clinic or calendar provider.</p>{view.appointments.length ? <ul className="routine-list">{view.appointments.map(appt => <li key={appt.id}><div><strong>{appt.kind}</strong><p><time dateTime={appt.when}>{formatDate(appt.when)}</time></p></div><span className="tag">{appt.status}</span></li>)}</ul> : <p className="empty">No upcoming appointment records saved.</p>}</section></div></section>
        </>}
        <Calendar person={view?.person} />
        <footer className="page-footer"><span>saarthi · family notebook</span><span>Built around the things you share.</span></footer>
      </main>
    </div>
  </div>;
}
