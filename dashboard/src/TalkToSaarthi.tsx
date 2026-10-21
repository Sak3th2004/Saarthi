import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import type { Person } from './contracts';
import { notebookErrorMessage } from './notebookErrors';
import { microphoneError, preferredVoice, recognitionFactory, type Recognition } from './speech';
import type { TalkAction, TalkClient, TalkReply } from './talkClient';
import type { AwsVoiceClient, VoiceCapture } from './awsVoiceClient';
import { startConversationSession } from './conversationSession';
import { playVoiceBlob } from './voicePlayback';
import { CompanionFace } from './CompanionFace';
import './talk.css';

export function TalkToSaarthi({ person, client, onDraft, onEvidence, canDraftFamily = true, voice }: {
  person: Person; client: TalkClient; onDraft: (action: TalkAction) => void; canDraftFamily?: boolean; voice?: AwsVoiceClient;
  onEvidence?: (record: TalkReply['evidence'][number]) => void;
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
  const [starting, setStarting] = useState(false);
  const [interim, setInterim] = useState(false);
  const [language, setLanguage] = useState('en-IN');
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [voiceId, setVoiceId] = useState('');
  const [speakingTurn, setSpeakingTurn] = useState<number|null>(null);
  const [micError, setMicError] = useState('');
  const [level, setLevel] = useState(0);
  const [loadingAudio, setLoadingAudio] = useState(false);
  const [sessionActive, setSessionActive] = useState(false);
  const [turnCountdown, setTurnCountdown] = useState<number|null>(null);
  const session = useRef<ReturnType<typeof startConversationSession>|null>(null);
  const endOfConversation = useRef<HTMLDivElement|null>(null);
  const cloudCapture = useRef<VoiceCapture|null>(null);
  const cloudAudio = useRef<HTMLAudioElement|null>(null);
  const audioUrl = useRef<string|null>(null);
  const audioRequest = useRef<AbortController|null>(null);
  const active = useRef(true), lock = useRef(false);
  const microphone = useRef<Recognition|null>(null);
  const microphoneTimer = useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
  const playbackVersion = useRef(0);
  const utteranceRef = useRef<SpeechSynthesisUtterance|null>(null);
  const conversation = useRef<string|undefined>(undefined);
  const voiceAvailable = Boolean(voice) || typeof window !== 'undefined' && Boolean(recognitionFactory(window));
  const browserPlayback = typeof window !== 'undefined' && 'speechSynthesis' in window;
  const playbackAvailable = Boolean(voice) || browserPlayback;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      session.current?.stop(); session.current = null;
      clearTimeout(microphoneTimer.current);
      cloudCapture.current?.abort(); audioRequest.current?.abort(); cloudAudio.current?.pause();
      if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
      if (microphone.current) {
        microphone.current.onresult = null; microphone.current.onerror = null; microphone.current.onend = null;
        microphone.current.onstart = null;
        microphone.current.abort();
      }
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    };
  }, []);
  useEffect(() => {
    if (!browserPlayback || voice) return;
    const refresh = () => setVoices(window.speechSynthesis.getVoices());
    refresh();
    window.speechSynthesis.addEventListener('voiceschanged', refresh);
    return () => window.speechSynthesis.removeEventListener('voiceschanged', refresh);
  }, [browserPlayback, voice]);
  useEffect(() => {
    endOfConversation.current?.scrollIntoView({ behavior: 'instant', block: 'nearest' });
  }, [turns.length]);
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const hidden = () => { if (document.hidden) session.current?.stop('Conversation paused while this page is hidden. Microphone is off.'); };
    document.addEventListener('visibilitychange', hidden);
    return () => document.removeEventListener('visibilitychange', hidden);
  }, []);
  function beginConversation() {
    if (!voice || lock.current || listening || starting) return;
    stopSpeaking(); setError(''); setMicError(''); setDraftNotice('');
    lock.current = true; setSessionActive(true);
    setVoiceNotice('Conversation mode sends your spoken question after a pause and reads the answer aloud. End conversation stops the microphone.');
    session.current = startConversationSession({
      capture: callbacks => voice.listen(language, callbacks),
      async ask(text, signal) {
        const reply = await client.ask(person.id, text, zone.trim(), conversation.current);
        if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
        conversation.current = reply.conversation_id;
        return reply;
      },
      async play(text, signal, started) {
        const blob = await voice.speak(text, signal);
        await playVoiceBlob(blob, signal, started);
      },
      phase(phase) {
        if (!active.current) return;
        setStarting(phase === 'opening'); setListening(phase === 'listening' || phase === 'finishing');
        setBusy(phase === 'thinking'); setSpeaking(phase === 'speaking');
        setLoadingAudio(phase === 'preparing');
      },
      transcript(text, partial) { if (active.current) { setQuestion(text); setInterim(partial); } },
      level(value) { if (active.current) setLevel(value); },
      countdown(seconds) { if (active.current) setTurnCountdown(seconds); },
      reply(reply) { if (active.current) { setTurns(previous => [...previous, reply].slice(-8)); setQuestion(''); setInterim(false); } },
      ended(message) {
        lock.current = false;
        if (active.current) { setSessionActive(false); setInterim(false); setVoiceNotice(message); }
      },
    });
  }
  function stopSpeaking() {
    playbackVersion.current += 1;
    if (browserPlayback) window.speechSynthesis.cancel();
    audioRequest.current?.abort(); cloudAudio.current?.pause(); cloudAudio.current = null;
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
    audioUrl.current = null; setLoadingAudio(false);
    utteranceRef.current = null; setSpeaking(false); setSpeakingTurn(null);
  }
  function listen() {
    if (busy || sessionActive) return;
    if (listening || starting) { if (voice) cloudCapture.current?.stop(); else microphone.current?.stop(); return; }
    if (voice) {
      stopSpeaking(); setMicError(''); setStarting(true); setVoiceNotice('Allow microphone access to begin live captions.');
      const original = question.trim();
      let failed = false;
      cloudCapture.current = voice.listen(language, {
        started() { if (active.current) { setStarting(false); setListening(true); setVoiceNotice('Listening with AWS live captions. Stop when you are done.'); } },
        transcript(text, partial) { if (active.current) { setQuestion([original, text].filter(Boolean).join(' ').slice(0, 2000)); setInterim(partial); } },
        ended() { if (active.current) { setStarting(false); setListening(false); setInterim(false); setLevel(0); cloudCapture.current = null; if (!failed) setVoiceNotice('Your words are ready. Check them, then choose Ask Saarthi.'); } },
        failed(message) { failed = true; if (active.current) { setMicError('aws-voice'); setVoiceNotice(message); } },
        level(value) { if (active.current) setLevel(value); },
      });
      return;
    }
    const factory = recognitionFactory(window);
    if (!factory) return;
    stopSpeaking(); setVoiceNotice('Waiting for microphone permission…'); setMicError(''); setStarting(true);
    const recognition = factory();
    recognition.lang = language; recognition.continuous = true; recognition.interimResults = true;
    const isCurrent = () => active.current && microphone.current === recognition;
    const originalQuestion = question.trim();
    let failed = false;
    recognition.onstart = () => {
      if (!isCurrent()) return;
      setStarting(false); setListening(true); setVoiceNotice('Listening. Your words will appear as you speak.');
      microphoneTimer.current = setTimeout(() => {
        if (isCurrent()) { recognition.stop(); setVoiceNotice('One-minute listening limit reached. Review your words before sending.'); }
      }, 60_000);
    };
    recognition.onresult = event => {
      if (!isCurrent()) return;
      const transcript = Array.from(event.results).map(result => result[0]?.transcript ?? '').join(' ');
      setQuestion([originalQuestion, transcript].filter(Boolean).join(' ').slice(0, 2000));
      setInterim(Array.from(event.results).some(result => result.isFinal === false));
    };
    recognition.onerror = event => {
      if (!isCurrent()) return;
      failed = true; clearTimeout(microphoneTimer.current); setVoiceNotice(microphoneError(event.error));
      setMicError(event.error); setListening(false); setStarting(false); setInterim(false);
    };
    recognition.onend = () => {
      if (!isCurrent()) return;
      clearTimeout(microphoneTimer.current); setListening(false); setStarting(false); setInterim(false);
      microphone.current = null;
      if (!failed) setVoiceNotice('Listening finished. Check your words, then choose Ask Saarthi.');
    };
    microphone.current = recognition;
    try { recognition.start(); } catch {
      microphone.current = null; setStarting(false); setVoiceNotice(microphoneError('unavailable')); setMicError('start-failed');
    }
  }
  async function ask(event: FormEvent) {
    event.preventDefault();
    if (lock.current || listening || starting || !question.trim()) return;
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
  async function readAloud(reply: TalkReply, index: number) {
    stopSpeaking();
    const version = playbackVersion.current;
    if (voice) {
      const request = new AbortController(); audioRequest.current = request;
      setLoadingAudio(true); setSpeakingTurn(index);
      try {
        const blob = await voice.speak(reply.speech, request.signal);
        if (!active.current || playbackVersion.current !== version) return;
        const url = URL.createObjectURL(blob); audioUrl.current = url;
        const audio = new Audio(url); cloudAudio.current = audio;
        const finish = () => { if (active.current && playbackVersion.current === version) stopSpeaking(); };
        audio.onended = finish;
        audio.onerror = () => { if (active.current && playbackVersion.current === version) { stopSpeaking(); setVoiceNotice('Audio could not play. Please try the play button again.'); } };
        await audio.play();
        if (active.current && playbackVersion.current === version) { setLoadingAudio(false); setSpeaking(true); }
      } catch (error) {
        if (active.current && playbackVersion.current === version && !request.signal.aborted) {
          stopSpeaking(); setVoiceNotice(error instanceof Error ? error.message : 'The voice could not play. Please try again.');
        }
      }
      return;
    }
    const utterance = new SpeechSynthesisUtterance(reply.speech);
    const browserVoice = voices.find(v => v.voiceURI === voiceId) ?? preferredVoice(voices, language);
    if (browserVoice) utterance.voice = browserVoice;
    utterance.lang = browserVoice?.lang ?? language; utterance.rate = 0.96; utterance.pitch = 1;
    utterance.onend = () => {
      if (active.current && playbackVersion.current === version) { setSpeaking(false); setSpeakingTurn(null); utteranceRef.current = null; }
    };
    utterance.onerror = () => {
      if (active.current && playbackVersion.current === version) { setSpeaking(false); setSpeakingTurn(null); setVoiceNotice('Your browser could not play this voice. Choose another voice in Voice settings.'); }
    };
    utteranceRef.current = utterance;
    window.speechSynthesis.speak(utterance); setSpeaking(true); setSpeakingTurn(index);
  }
  function clear() {
    session.current?.stop(); session.current = null;
    stopSpeaking();
    const previous = microphone.current; microphone.current = null;
    cloudCapture.current?.abort(); cloudCapture.current = null; setLevel(0);
    previous?.abort(); clearTimeout(microphoneTimer.current);
    setListening(false); setStarting(false); setInterim(false); setMicError('');
    conversation.current = undefined; setTurns([]); setQuestion(''); setError(''); setVoiceNotice(''); setDraftNotice('');
  }
  const mode = starting ? 'starting' : listening ? 'listening' : busy ? 'thinking' : loadingAudio ? 'loading-audio' : speaking ? 'speaking' : micError ? 'blocked' : 'ready';
  const modeLabel = { starting: 'Opening microphone', listening: 'Listening to you', thinking: 'Checking saved records', 'loading-audio': 'Preparing your voice answer', speaking: 'Speaking', blocked: 'Microphone needs attention', ready: 'Ready when you are' }[mode];
  return <section className="card talk-card" id="talk" aria-labelledby="talk-heading">
    <div className="talk-hero"><div><p className="eyebrow">YOUR FAMILY, A CONVERSATION AWAY</p><h2 id="talk-heading">Talk to Saarthi</h2><p>Small details. Remembered together.</p></div><span className="talk-person"><span aria-hidden="true">{person.name.slice(0, 1)}</span>{person.name}'s notebook</span></div>
    <div className="talk-statusbar"><span className={`talk-status-dot ${mode}`} /> <span role="status">{modeLabel}</span><span className="talk-status-note">Answers grounded in your saved records</span></div>
    <div className="talk-layout">
      <form onSubmit={event => void ask(event)} className="talk-composer">
        <div className={`voice-stage ${mode}`} style={{ '--voice-level': level } as CSSProperties}>
          <CompanionFace mode={mode} />
          <button type="button" className="voice-button" aria-label={listening || starting ? 'Stop listening' : 'Use microphone'} aria-pressed={listening || starting} disabled={busy || sessionActive || !voiceAvailable} onClick={listen}>
            {listening || starting ? <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" /></svg> : <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8" /></svg>}
          </button>
          <p>{sessionActive ? listening ? 'Take your time. I’m listening.' : speaking ? 'Your answer, from saved records.' : 'Stay here. We’ll continue together.' : listening ? 'Speak naturally. Stop when you’re done.' : starting ? 'Allow access in your browser.' : voiceAvailable ? 'Tap the microphone, or start a conversation below' : 'Type below to start a conversation'}</p>
        </div>
        {voice && <div className="companion-session">
          <div className="companion-session-heading"><span className="eyebrow">A LITTLE MORE LIKE TALKING</span><span className="session-badge">{sessionActive ? 'Conversation on' : 'Optional'}</span></div>
          <p>Start once. Ask, listen, then follow up.</p>
          <button type="button" className={`button ${sessionActive ? 'session-end' : 'primary'}`} disabled={!sessionActive && (busy || listening || starting)} onClick={() => sessionActive ? session.current?.stop() : beginConversation()}>{sessionActive ? 'End conversation' : 'Start conversation'}</button>
          <p className="form-hint">After a pause, your words are sent automatically. The microphone pauses during answers. Drafts always need your review. Up to six turns or three minutes.</p>
          {sessionActive && <p className="session-live" role="status">{turnCountdown !== null ? `Finishing in ${turnCountdown}… keep speaking if you need more time.` : speaking ? 'Microphone off while Saarthi answers.' : busy ? 'Microphone off while checking records.' : 'Microphone opens for your next question.'}</p>}
        </div>}
        <label htmlFor="talk-question">What would you like to ask?</label>
        <textarea id="talk-question" className={interim ? 'transcript-interim' : ''} rows={3} maxLength={2000} value={question} onChange={e => setQuestion(e.target.value)} required disabled={busy} readOnly={sessionActive || listening || starting} aria-describedby="transcript-hint" placeholder={`Ask about ${person.name}'s saved notes, plans or family`} />
        <p className="form-hint" id="transcript-hint">{interim ? 'Live transcript · words may change as you speak.' : listening ? 'Live transcript · listening for your next words.' : sessionActive ? 'Conversation mode is on. End it to edit or ask manually.' : 'Speak or type, then check your words before sending.'}</p>
        <div className="talk-controls">
          <button className="button primary" disabled={sessionActive || busy || listening || starting || !question.trim()}>{busy ? 'Checking records…' : 'Ask Saarthi'}<span aria-hidden="true">↗</span></button>
          <button type="button" className="button secondary" disabled={busy} onClick={clear}>New conversation</button>
        </div>
        <p className="form-hint">{voice ? 'Audio goes to AWS Transcribe while listening, for up to one minute per turn. Saarthi does not save the recording. Manual mode lets you review captions; conversation mode sends after a pause. Spoken answers use Amazon Polly.' : voiceAvailable ? 'Microphone starts only when you choose it. Your browser’s speech provider may process audio. Review the transcript before sending.' : 'Voice input is not supported by this browser. Type a question to use the same conversation.'}</p>
        {voiceNotice && <p role="status" className={`notice ${micError ? 'error' : ''}`}>{voiceNotice}{micError && <small className="voice-error-code">Browser diagnostic: {micError}</small>}</p>}
        <details className="talk-settings"><summary>Voice & conversation settings</summary>
          <label htmlFor="talk-language">Recognition language</label><select id="talk-language" value={language} disabled={sessionActive || listening || starting || busy} onChange={e => setLanguage(e.target.value)}><option value="en-IN">English · India</option><option value="en-US">English · United States</option><option value="en-GB">English · United Kingdom</option></select>
          {voice ? <p className="form-hint">Saarthi’s voice: Kajal · female Indian English · Amazon Polly Neural.</p> : playbackAvailable && <><label htmlFor="talk-voice">Read-aloud voice</label><select id="talk-voice" value={voiceId} onChange={e => { stopSpeaking(); setVoiceId(e.target.value); }}><option value="">Recommended available voice</option>{voices.filter(v => v.lang.toLowerCase().startsWith('en')).map(v => <option key={v.voiceURI} value={v.voiceURI}>{v.name} · {v.lang}</option>)}</select><p className="form-hint">Voices come from your browser or device. Availability and sound vary.</p></>}
          <label htmlFor="talk-zone">Timezone</label><input id="talk-zone" value={zone} maxLength={80} required disabled={sessionActive || busy || listening || starting} onChange={e => { setZone(e.target.value); conversation.current = undefined; setTurns([]); }} /><p className="form-hint">Changing timezone starts a new conversation.</p>
        </details>
        {error && <p className="notice error" role="alert">{error}</p>}
      </form>
      <div className="talk-conversation" aria-label="Conversation" aria-busy={busy}>
        {!turns.length && <div className="talk-empty"><span className="eyebrow">FROM REMEMBERING TO HELPING</span><h3>Pick up the thread.</h3><p>Find a detail, ask a follow-up, then turn it into a plan or a family update.</p><div className="talk-prompts">{['What appointments are saved?', 'Who is in the family?', 'Help me prepare a family update.'].map(prompt => <button type="button" key={prompt} disabled={sessionActive || busy || listening || starting} onClick={() => setQuestion(prompt)}>{prompt}<span aria-hidden="true">↗</span></button>)}</div><p className="form-hint">Suggestions fill the question box. They do not send a request or create records.</p></div>}
        {turns.map((reply, index) => <article className="talk-turn" key={`${reply.conversation_id}-${index}`}>
          <p className="talk-question"><span className="eyebrow">YOU</span>{reply.question}</p>
          <div className="talk-answer"><span className="eyebrow">SAARTHI</span><p className="talk-answer-text">{reply.answer}</p>
            {playbackAvailable && <button className="button secondary" type="button" disabled={sessionActive || listening || starting} onClick={() => speakingTurn === index ? stopSpeaking() : void readAloud(reply, index)}>{speakingTurn === index ? loadingAudio ? 'Cancel audio' : 'Stop speaking' : 'Hear a short answer'}</button>}
            <details><summary>Supporting records ({reply.evidence.length})</summary>{reply.evidence.length ? <ul className="evidence-list">{reply.evidence.map(e => <li key={e.id}><span className="evidence-kind">{e.kind === 'note' ? 'Saved note' : e.kind === 'relationship' ? 'Family connection' : e.kind === 'appointment' ? 'Appointment record' : 'Saved medication schedule'}</span><p>{e.text}</p>{e.at && <time dateTime={e.at}>{new Date(e.at).toLocaleString()}</time>}{onEvidence && Boolean(e.graph_node_ids?.length) && <button type="button" className="button secondary evidence-link" onClick={() => {
              session.current?.stop('Conversation paused while you look at a saved record.');
              stopSpeaking(); onEvidence(e);
            }}>See in saved connections <span aria-hidden="true">↗</span></button>}</li>)}</ul> : <p>No supporting records were found for this answer.</p>}<p className="form-hint">Checked: {reply.sources_checked.join(', ') || 'No records checked'}</p></details>
            {reply.action && <div className="talk-draft"><p>{reply.action.type === 'family_message' ? 'A family message draft is ready.' : 'An appointment draft is ready.'}</p>
              <p className="talk-answer-text">{reply.action.type === 'family_message' ? reply.action.message : `${reply.action.title}\n${reply.action.start_local.replace('T', ' ')} to ${reply.action.end_local.replace('T', ' ')}\n${reply.action.time_zone}`}</p>
              {reply.action.type === 'family_message' && !canDraftFamily ? <p>Family messaging requires the signed-in household dashboard.</p> : <button className="button secondary" type="button" onClick={() => { session.current?.stop('Conversation paused for draft review.'); onDraft(reply.action!); setDraftNotice('Draft copied to the form below. Review and confirm there to save or send.'); }}>Use this draft</button>}
            </div>}
            <p className="form-hint">{reply.notice}</p>
          </div>
        </article>)}
        {busy && <p role="status">Looking through {person.name}'s saved records. This can take a moment.</p>}
        {draftNotice && <p role="status" className="notice">{draftNotice}</p>}
        <div ref={endOfConversation} />
      </div>
    </div>
    <p className="talk-disclosure">An Alexa-style web experience by Saarthi · not an Alexa device connection. Every message and appointment needs your review before it is sent or saved.</p>
  </section>;
}
