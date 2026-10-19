import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { AuthGate } from './AuthGate';
import { loadRuntimeConfig } from './runtimeConfig';
import './styles.css';

const root = createRoot(document.getElementById('root')!);
async function start() {
  let environment: Record<string, string | undefined> = import.meta.env;
  if (!import.meta.env.DEV) {
    try {
      environment = await loadRuntimeConfig(window.location.origin);
    } catch {
      root.render(<main><section className="card"><h1>Sign-in is unavailable</h1><p>Could not load this notebook's sign-in settings. Please reload shortly.</p></section></main>);
      return;
    }
  }
  root.render(<StrictMode><AuthGate environment={environment} /></StrictMode>);
}
void start();
