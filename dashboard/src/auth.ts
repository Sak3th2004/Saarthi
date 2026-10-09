/** Cognito authorization code + PKCE. Only the pending verifier is kept in session storage. */
export interface AuthConfig { domain: string; clientId: string; resource: string; redirect: string }
export interface Session { accessToken: string; expiresAt: number }
const pendingKey = 'saarthi.cognito.pending';

export function authConfig(env: Record<string, string | undefined>, origin: string): AuthConfig | null {
  const mode = env.VITE_AUTH_MODE ?? 'local';
  if (mode === 'local') {
    if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(origin).hostname)) throw new Error('Sign-in must be configured before publishing this notebook.');
    return null;
  }
  if (mode !== 'cognito') throw new Error('Unknown sign-in configuration.');
  const domain = env.VITE_COGNITO_DOMAIN ?? '', clientId = env.VITE_COGNITO_CLIENT_ID ?? '';
  const resource = env.VITE_COGNITO_RESOURCE_URL ?? '', redirect = env.VITE_COGNITO_REDIRECT_URI ?? '';
  if (!/^https:\/\/[a-z0-9-]+\.auth\.[a-z0-9-]+\.amazoncognito\.com$/.test(domain) || !/^[a-z0-9]{1,128}$/.test(clientId)) throw new Error('Cognito sign-in is not fully configured.');
  const url = new URL(redirect), audience = new URL(resource);
  if (url.origin !== origin || url.pathname !== '/auth/callback' || url.search || url.hash || url.username || url.password ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && url.hostname === 'localhost')) ||
      audience.username || audience.password || audience.search || audience.hash || audience.pathname === '/' ||
      (audience.protocol !== 'https:' && !(audience.protocol === 'http:' && audience.hostname === 'localhost'))) throw new Error('The sign-in redirect or resource URL is invalid.');
  return { domain, clientId, resource, redirect };
}

function encoded(bytes: Uint8Array): string { return btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', ''); }
export async function beginLogin(config: AuthConfig, storage: Storage, now = Date.now()): Promise<string> {
  const verifier = encoded(crypto.getRandomValues(new Uint8Array(32)));
  const state = encoded(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = encoded(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
  storage.setItem(pendingKey, JSON.stringify({ verifier, state, at: now, config }));
  const params = new URLSearchParams({ response_type: 'code', client_id: config.clientId, redirect_uri: config.redirect,
    scope: 'openid saarthi/notebook', state, code_challenge: challenge, code_challenge_method: 'S256', resource: config.resource });
  return config.domain + '/oauth2/authorize?' + params;
}

export async function finishLogin(config: AuthConfig, callback: URL, storage: Storage, fetcher: typeof fetch = fetch, now = Date.now()): Promise<Session> {
  const raw = storage.getItem(pendingKey);
  storage.removeItem(pendingKey);
  if (!raw || callback.origin + callback.pathname !== config.redirect || callback.searchParams.getAll('state').length !== 1 || callback.searchParams.getAll('code').length !== 1 || callback.searchParams.has('error')) throw new Error('Sign-in was not completed. Please start again.');
  const pending = JSON.parse(raw);
  if (!pending || typeof pending.state !== 'string' || pending.state !== callback.searchParams.get('state') ||
      typeof pending.verifier !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(pending.verifier) ||
      typeof pending.at !== 'number' || pending.at > now || now - pending.at > 600_000 ||
      JSON.stringify(pending.config) !== JSON.stringify(config)) throw new Error('Sign-in expired or could not be verified. Please start again.');
  const code = callback.searchParams.get('code');
  if (!code || code.length > 4096) throw new Error('Invalid sign-in response.');
  const response = await fetcher(config.domain + '/oauth2/token', { method: 'POST', credentials: 'omit',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', client_id: config.clientId, redirect_uri: config.redirect,
      code_verifier: pending.verifier, code }), signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error('Cognito did not complete sign-in. Please start again.');
  const result = await response.json();
  if (result.token_type !== 'Bearer' || typeof result.access_token !== 'string' || !result.access_token ||
      !Number.isInteger(result.expires_in) || result.expires_in <= 0 || result.expires_in > 3600) throw new Error('Cognito returned an invalid session.');
  // Signature, audience and household membership are verified by the MCP server, never by this UI.
  return { accessToken: result.access_token, expiresAt: now + result.expires_in * 1000 };
}

export function logoutUrl(config: AuthConfig): string {
  return config.domain + '/logout?' + new URLSearchParams({ client_id: config.clientId, logout_uri: new URL(config.redirect).origin + '/' });
}
