import { useEffect, useMemo, useRef, useState } from 'react';
import { App } from './App';
import { client, createDashboardClient, httpCaller } from './client';
import { authConfig, beginLogin, finishLogin, logoutUrl, signInErrorMessage, type AuthConfig, type Session } from './auth';
import { NotebookAccessError } from './notebookErrors';
import { createHouseholdManagementClient } from './householdManagementClient';
import { createHouseholdCalendarClient } from './householdCalendarClient';
import { createHouseholdNotifyClient } from './householdNotifyClient';
import { createTalkClient } from './talkClient';
import { createAwsVoiceClient } from './awsVoiceClient';

const localTalk = createTalkClient(httpCaller(() => new URL('/mcp', window.location.href)));

export function AuthGate({ environment = import.meta.env }: { environment?: Record<string, string | undefined> }) {
  const configuration = useMemo(() => {
    try { return { config: authConfig(environment, window.location.origin), error: '' }; }
    catch { return { config: null, error: 'Sign-in is not configured for this address. Contact the household administrator.' }; }
  }, [environment]);
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const started = useRef(false);
  useEffect(() => {
    if (started.current || !configuration.config || window.location.pathname !== '/auth/callback') return;
    started.current = true;
    const callback = new URL(window.location.href);
    window.history.replaceState({}, '', '/');
    setBusy(true);
    const config = configuration.config;
    void Promise.resolve().then(() => finishLogin(config, callback, window.sessionStorage)).then(setSession)
      .catch(error => setError(signInErrorMessage(error))).finally(() => setBusy(false));
  }, [configuration]);
  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => { setSession(null); setError('Your session expired. Sign in again.'); }, Math.max(0, session.expiresAt - Date.now() - 5000));
    return () => window.clearTimeout(timer);
  }, [session]);
  const authenticated = useMemo(() => createDashboardClient(httpCaller(() => new URL('/mcp', window.location.href), () => {
    if (!session || Date.now() >= session.expiresAt - 5000) throw new NotebookAccessError();
    return session.accessToken;
  })), [session]);
  const accessToken = () => {
    if (!session || Date.now() >= session.expiresAt - 5000) throw new NotebookAccessError();
    return session.accessToken;
  };
  const householdManagement = useMemo(() => createHouseholdManagementClient(accessToken), [session]);
  const householdCalendar = useMemo(() => createHouseholdCalendarClient(accessToken), [session]);
  const householdNotify = useMemo(() => createHouseholdNotifyClient(accessToken), [session]);
  const talk = useMemo(() => createTalkClient(httpCaller(() => new URL('/mcp', window.location.href), accessToken)), [session]);
  const voice = useMemo(() => createAwsVoiceClient(accessToken), [session]);
  if (!configuration.error && !configuration.config) return <App client={client} talk={localTalk} />;
  async function signIn(config: AuthConfig) {
    setBusy(true); setError('');
    try { window.location.assign(await beginLogin(config, window.sessionStorage)); }
    catch (error) { setBusy(false); setError(signInErrorMessage(error)); }
  }
  if (session && configuration.config) return <>
    <div className="auth-controls"><button className="button secondary" onClick={() => { setSession(null); window.location.assign(logoutUrl(configuration.config!)); }}>Sign out</button></div>
    <App key={session.expiresAt} client={authenticated} localTools={false} householdManagement={householdManagement} householdCalendar={householdCalendar} householdNotify={householdNotify} talk={talk} voice={voice} />
  </>;
  return <main className="signin-page">
    <header className="signin-brand"><span className="brand-mark" aria-hidden="true">s</span><span>saarthi<small>a little closer, every day</small></span></header>
    <div className="signin-layout">
      <section className="signin-story" aria-labelledby="signin-story-title">
        <p className="eyebrow">THE LITTLE THINGS THAT BRING US CLOSER</p>
        <h2 id="signin-story-title">Everyday details.<br />A little less distance.</h2>
        <p className="signin-intro">A familiar voice. A remembered detail. A plan you can share. Keep the things that matter to your family in one place.</p>
        <div className="signin-art" aria-hidden="true">
          <svg viewBox="0 0 440 240" focusable="false"><path className="signin-art-path" d="M88 62 Q120 186 222 125 T366 60 M222 125 Q270 188 359 194" /><circle cx="88" cy="62" r="19" /><circle cx="366" cy="60" r="14" /><circle cx="359" cy="194" r="22" /><circle className="signin-art-halo" cx="222" cy="125" r="64" /><rect className="signin-art-book" x="181" y="76" width="82" height="104" rx="12" transform="rotate(-8 222 128)" /><path className="signin-art-lines" d="M198 105 L241 99 M201 122 L244 116 M203 139 L231 135" /></svg>
        </div>
        <ul className="signin-features"><li><span>01</span>Remember together</li><li><span>02</span>Make a plan</li><li><span>03</span>Keep family close</li></ul>
      </section>
      <section className="signin-card" aria-labelledby="signin-title" aria-busy={busy}>
        <span className="signin-lock" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="5" y="10" width="14" height="11" rx="3" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" /></svg></span>
        <p className="eyebrow">SAARTHI FAMILY NOTEBOOK</p>
        <h1 id="signin-title">A private place<br />for your family.</h1>
        <p className="signin-description">Sign in with your approved caregiver account to open the household notebook.</p>
        {(error || configuration.error) && <p className="notice error" role="alert">{error || configuration.error}</p>}
        {configuration.config && <button className="button primary signin-button" disabled={busy} onClick={() => void signIn(configuration.config!)}>{busy ? 'Completing sign-in…' : 'Sign in'}<span aria-hidden="true">→</span></button>}
        <p className="signin-access-note">Your household notebook opens after sign-in. Calendar connections are managed separately inside.</p>
      </section>
    </div>
    <footer className="signin-footer">Built around the things you share.</footer>
  </main>;
}
