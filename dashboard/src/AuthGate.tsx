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
  return <main><section className="card"><p className="eyebrow">SAARTHI FAMILY NOTEBOOK</p><h1>A private place for your family.</h1><p>Sign in with your approved caregiver account to open the household notebook.</p>
    {(error || configuration.error) && <p className="notice error" role="alert">{error || configuration.error}</p>}
    {configuration.config && <button className="button primary" disabled={busy} onClick={() => void signIn(configuration.config!)}>{busy ? 'Completing sign-in…' : 'Sign in'}</button>}
  </section></main>;
}
