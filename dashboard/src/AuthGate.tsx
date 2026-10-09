import { useEffect, useMemo, useRef, useState } from 'react';
import { App } from './App';
import { client, createDashboardClient, httpCaller } from './client';
import { authConfig, beginLogin, finishLogin, logoutUrl, type AuthConfig, type Session } from './auth';
import { NotebookAccessError } from './notebookErrors';

export function AuthGate() {
  const configuration = useMemo(() => {
    try { return { config: authConfig(import.meta.env, window.location.origin), error: '' }; }
    catch { return { config: null, error: 'Sign-in is not configured for this address. Contact the household administrator.' }; }
  }, []);
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
    void finishLogin(configuration.config, callback, window.sessionStorage).then(setSession)
      .catch(() => setError('Sign-in could not be completed. Please try again.')).finally(() => setBusy(false));
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
  if (!configuration.error && !configuration.config) return <App client={client} />;
  async function signIn(config: AuthConfig) {
    setBusy(true); setError('');
    try { window.location.assign(await beginLogin(config, window.sessionStorage)); }
    catch { setBusy(false); setError('Could not start sign-in. Check your browser storage settings.'); }
  }
  if (session && configuration.config) return <>
    <div className="auth-controls"><button className="button secondary" onClick={() => { setSession(null); window.location.assign(logoutUrl(configuration.config!)); }}>Sign out</button></div>
    <App key={session.expiresAt} client={authenticated} localTools={false} />
  </>;
  return <main><section className="card"><p className="eyebrow">SAARTHI FAMILY NOTEBOOK</p><h1>A private place for your family.</h1><p>Sign in with your approved caregiver account to open the household notebook.</p>
    {(error || configuration.error) && <p className="notice error" role="alert">{error || configuration.error}</p>}
    {configuration.config && <button className="button primary" disabled={busy} onClick={() => void signIn(configuration.config!)}>{busy ? 'Completing sign-in…' : 'Sign in'}</button>}
  </section></main>;
}
