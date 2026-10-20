import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { Person } from './contracts';
import { notebookErrorMessage } from './notebookErrors';
import { microphoneError, recognitionFactory, type Recognition } from './speech';
import type { TalkAction, TalkClient, TalkReply } from './talkClient';
import './talk.css';

export function TalkToSaarthi({ person, client, onDraft, canDraftFamily = true }: {
  person: Person; client: TalkClient; onDraft: (action: TalkAction) => void; canDraftFamily?: boolean;
}) {
  const [question, setQuestion] = useState('');
  const [zone, setZone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [turns, setTurns] = useState<TalkReply[]>([]);
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState('');
  const [voiceNotice, setVoiceNotice] = useState('');
  const [draftNotice, setDraftNotice] = useState('');
  const active = useRef(true), lock = useRef(false);
  const microphone = useRef<Recognition|null>(null);
  const conversation = useRef<string|undefined>(undefined);
  const voiceAvailable = typeof window !== 'undefined' && Boolean(recognitionFactory(window));
  const playbackAvailable = typeof window !== 'undefined' && 'speechSynthesis' in window;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      if (microphone.current) {
        microphone.current.onresult = null; microphone.current.onerror = null; microphone.current.onend = null;
        microphone.current.abort();
      }
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    };
  }, []);
  function stopSpeaking() { if (playbackAvailable) window.speechSynthesis.cancel(); setSpeaking(false); }
  function listen() {
    if (busy) return;
    if (listening) { microphone.current?.stop(); return; }
    const factory = recognitionFactory(window);
    if (!factory) return;
    stopSpeaking(); setVoiceNotice('');
    const recognition = factory();
    recognition.lang = 'en-IN'; recognition.continuous = false; recognition.interimResults = false;
    recognition.onresult = event => {
      if (!active.current) return;
      const transcript = Array.from(event.results).map(result => result[0]?.transcript ?? '').join(' ');
      setQuestion(transcript.slice(0, 2000));
      setVoiceNotice('Your words are ready below. Check them, then choose Ask Saarthi.');
    };
    recognition.onerror = event => { if (active.current) { setVoiceNotice(microphoneError(event.error)); setListening(false); } };
    recognition.onend = () => { if (active.current) setListening(false); };
    microphone.current = recognition;
    try { recognition.start(); setListening(true); } catch { setVoiceNotice(microphoneError('unavailable')); }
  }
  async function ask(event: FormEvent) {
    event.preventDefault();
    if (lock.current || listening || !question.trim()) return;
    lock.current = true; setBusy(true); setError(''); setDraftNotice(''); stopSpeaking();
    const prompt = question.trim();
    try {
      const reply = await client.ask(person.id, prompt, zone.trim(), conversation.current);
      if (!active.current) return;
      conversation.current = reply.conversation_id;
      setTurns(previous => [...previous, reply].slice(-8)); setQuestion('');
    } catch (error) {
      if (active.current) setError(notebookErrorMessage(error, 'The conversation could not be completed. No appointment or family message was sent. Try again, or start a new conversation if this session expired.'));
    } finally { lock.current = false; if (active.current) setBusy(false); }
  }
  function readAloud(reply: TalkReply) {
    stopSpeaking();
    const utterance = new SpeechSynthesisUtterance(reply.speech);
    utterance.lang = 'en-IN';
    utterance.onend = utterance.onerror = () => { if (active.current) setSpeaking(false); };
    window.speechSynthesis.speak(utterance); setSpeaking(true);
  }
  function clear() {
    stopSpeaking(); microphone.current?.abort(); setListening(false);
    conversation.current = undefined; setTurns([]); setQuestion(''); setError(''); setVoiceNotice(''); setDraftNotice('');
  }
  return <section className="card talk-card" id="talk" aria-labelledby="talk-heading">
    <div className="section-heading"><div><p className="eyebrow">A CONVERSATION WITH YOUR NOTEBOOK</p><h2 id="talk-heading">Talk to Saarthi</h2></div><span className="tag">{person.name}'s records</span></div>
    <p>Ask in your own words, follow up, or prepare a family update using saved details.</p>
    <p className="form-hint">An Alexa-style interaction in the Saarthi web app. This is not an Alexa device connection.</p>
    <div className="talk-layout">
      <form onSubmit={event => void ask(event)} className="talk-composer">
        <label htmlFor="talk-question">What would you like to ask?</label>
        <textarea id="talk-question" rows={4} maxLength={2000} value={question} onChange={e => setQuestion(e.target.value)} required disabled={busy || listening} placeholder={`Ask about ${person.name}'s saved notes, plans or family`} />
        <div className="talk-controls">
          {voiceAvailable && <button type="button" className="button secondary" aria-pressed={listening} disabled={busy} onClick={listen}>{listening ? 'Stop listening' : 'Use microphone'}</button>}
          <button className="button primary" disabled={busy || listening || !question.trim()}>{busy ? 'Reading saved records…' : 'Ask Saarthi'}</button>
        </div>
        <p className="form-hint">{voiceAvailable ? 'Microphone starts only when you choose it. Your browser’s speech provider may process audio. Review the transcript before sending.' : 'Voice input is not supported by this browser. Type a question to use the same conversation.'}</p>
        {voiceNotice && <p role="status" className="notice">{voiceNotice}</p>}
        <details className="talk-settings"><summary>Conversation settings</summary><label htmlFor="talk-zone">Timezone</label><input id="talk-zone" value={zone} maxLength={80} required disabled={busy} onChange={e => { setZone(e.target.value); conversation.current = undefined; setTurns([]); }} /><p className="form-hint">Changing timezone starts a new conversation.</p></details>
        <button type="button" className="button secondary" disabled={busy} onClick={clear}>New conversation</button>
        {error && <p className="notice error" role="alert">{error}</p>}
      </form>
      <div className="talk-conversation" aria-label="Conversation" aria-busy={busy}>
        {!turns.length && <div className="talk-empty"><div className="talk-emblem" aria-hidden="true">s</div><h3>Start with something that matters.</h3><p>Ask what is on record, then follow up. Answers include the saved details they come from.</p><p className="form-hint">A conversation can prepare a draft. Sending a message or saving an appointment still needs your review and confirmation.</p></div>}
        {turns.map((reply, index) => <article className="talk-turn" key={`${reply.conversation_id}-${index}`}>
          <p className="talk-question"><span className="eyebrow">YOU</span>{reply.question}</p>
          <div className="talk-answer"><span className="eyebrow">SAARTHI</span><p className="talk-answer-text">{reply.answer}</p>
            {playbackAvailable && <button className="button secondary" type="button" onClick={() => speaking ? stopSpeaking() : readAloud(reply)}>{speaking ? 'Stop speaking' : 'Read aloud'}</button>}
            <details><summary>Supporting records ({reply.evidence.length})</summary>{reply.evidence.length ? <ul className="evidence-list">{reply.evidence.map(e => <li key={e.id}><p>{e.text}</p>{e.at && <time dateTime={e.at}>{new Date(e.at).toLocaleString()}</time>}</li>)}</ul> : <p>No supporting records were found for this answer.</p>}<p className="form-hint">Checked: {reply.sources_checked.join(', ') || 'No records checked'}</p></details>
            {reply.action && <div className="talk-draft"><p>{reply.action.type === 'family_message' ? 'A family message draft is ready.' : 'An appointment draft is ready.'}</p>
              <p className="talk-answer-text">{reply.action.type === 'family_message' ? reply.action.message : `${reply.action.title}\n${reply.action.start_local.replace('T', ' ')} to ${reply.action.end_local.replace('T', ' ')}\n${reply.action.time_zone}`}</p>
              {reply.action.type === 'family_message' && !canDraftFamily ? <p>Family messaging requires the signed-in household dashboard.</p> : <button className="button secondary" type="button" onClick={() => { onDraft(reply.action!); setDraftNotice('Draft copied to the form below. Review and confirm there to save or send.'); }}>Use this draft</button>}
            </div>}
            <p className="form-hint">{reply.notice}</p>
          </div>
        </article>)}
        {busy && <p role="status">Looking through {person.name}'s saved records. This can take a moment.</p>}
        {draftNotice && <p role="status" className="notice">{draftNotice}</p>}
      </div>
    </div>
  </section>;
}
