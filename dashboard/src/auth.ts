/** Cognito authorization code + PKCE. Only the pending verifier is kept in session storage. */
export interface AuthConfig { domain: string; clientId: string; resource: string; redirect: string }
export interface Session { accessToken: string; expiresAt: number }
const pendingKey = 'saarthi.cognito.pending';

// Fixed, shareable diagnostics. Never display an OAuth response, code, or token.
const signInMessages = {
  AUTH_STORAGE: 'This browser could not keep the sign-in request. Allow site storage and start again in the same tab.',
  AUTH_START: 'This tab has no pending sign-in request. Open the dashboard and choose Sign in from this tab.',
  AUTH_CALLBACK: 'The sign-in return address was incomplete or did not match this dashboard. Start a new sign-in.',
  AUTH_STATE: 'The sign-in response did not match this tab. Close older login tabs and start again here.',
  AUTH_EXPIRED: 'The sign-in request expired. Start a new sign-in and complete it within ten minutes.',
  AUTH_SETTINGS: 'The sign-in settings changed while you were signing in. Start a new sign-in.',
  AUTH_DENIED: 'The identity provider did not approve sign-in. Start again to complete the requested steps.',
  AUTH_PROVIDER: 'The identity provider rejected the sign-in configuration. Share this error code with the household administrator.',
  AUTH_CODE: 'The sign-in code was rejected or expired. Start a new sign-in; do not reload an old return page.',
  AUTH_CLIENT: 'The identity provider rejected this dashboard client. Share this error code with the household administrator.',
  AUTH_EXCHANGE: 'The identity provider could not complete the sign-in exchange. Start a new sign-in.',
  AUTH_NETWORK: 'The browser could not complete the secure connection to the sign-in service. Check connectivity and try again.',
  AUTH_SESSION: 'The sign-in service returned an unusable session. Share this error code with the household administrator.',
  AUTH_UNKNOWN: 'Sign-in could not be completed. Start a new sign-in.',
} as const;
type SignInCode = keyof typeof signInMessages;

export class SignInError extends Error {
  constructor(readonly code: SignInCode) {
    super(signInMessages[code]);
    this.name = 'SignInError';
  }
}

export function signInErrorMessage(error: unknown): string {
  const code = error instanceof SignInError && Object.hasOwn(signInMessages, error.code) ? error.code : 'AUTH_UNKNOWN';
  return `${signInMessages[code]} (${code})`;
}

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
  try { storage.setItem(pendingKey, JSON.stringify({ verifier, state, at: now, config })); }
  catch { throw new SignInError('AUTH_STORAGE'); }
  const params = new URLSearchParams({ response_type: 'code', client_id: config.clientId, redirect_uri: config.redirect,
    scope: 'openid saarthi/notebook', state, code_challenge: challenge, code_challenge_method: 'S256', resource: config.resource });
  return config.domain + '/oauth2/authorize?' + params;
}

export async function finishLogin(config: AuthConfig, callback: URL, storage: Storage, fetcher: typeof fetch = fetch, now = Date.now()): Promise<Session> {
  let raw: string | null;
  try { raw = storage.getItem(pendingKey); storage.removeItem(pendingKey); }
  catch { throw new SignInError('AUTH_STORAGE'); }
  if (!raw) throw new SignInError('AUTH_START');
  if (callback.origin + callback.pathname !== config.redirect || callback.hash || callback.username || callback.password ||
      callback.searchParams.getAll('state').length !== 1) throw new SignInError('AUTH_CALLBACK');
  let pending;
  try { pending = JSON.parse(raw); }
  catch { throw new SignInError('AUTH_START'); }
  if (!pending || typeof pending.state !== 'string' || !pending.state || pending.state !== callback.searchParams.get('state') ||
      typeof pending.verifier !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(pending.verifier)) throw new SignInError('AUTH_STATE');
  if (typeof pending.at !== 'number' || !Number.isFinite(pending.at) || pending.at > now || now - pending.at > 600_000) throw new SignInError('AUTH_EXPIRED');
  if (JSON.stringify(pending.config) !== JSON.stringify(config)) throw new SignInError('AUTH_SETTINGS');
  if (callback.searchParams.has('error')) {
    if (callback.searchParams.getAll('error').length !== 1 || callback.searchParams.has('code')) throw new SignInError('AUTH_CALLBACK');
    throw new SignInError(callback.searchParams.get('error') === 'access_denied' ? 'AUTH_DENIED' : 'AUTH_PROVIDER');
  }
  if (callback.searchParams.getAll('code').length !== 1) throw new SignInError('AUTH_CALLBACK');
  const code = callback.searchParams.get('code');
  if (!code || code.length > 4096) throw new SignInError('AUTH_CALLBACK');
  let response: Response;
  try { response = await fetcher(config.domain + '/oauth2/token', { method: 'POST', credentials: 'omit', redirect: 'error',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', client_id: config.clientId, redirect_uri: config.redirect,
      code_verifier: pending.verifier, code }), signal: AbortSignal.timeout(20_000) }); }
  catch { throw new SignInError('AUTH_NETWORK'); }
  if (!response.ok) {
    let reason: unknown;
    try { reason = (await response.json())?.error; } catch { /* Never display a response body. */ }
    if (reason === 'invalid_grant') throw new SignInError('AUTH_CODE');
    if (reason === 'invalid_client' || reason === 'unauthorized_client') throw new SignInError('AUTH_CLIENT');
    throw new SignInError('AUTH_EXCHANGE');
  }
  let result;
  try { result = await response.json(); } catch { throw new SignInError('AUTH_SESSION'); }
  if (!result || result.token_type !== 'Bearer' || typeof result.access_token !== 'string' || !result.access_token ||
      !Number.isInteger(result.expires_in) || result.expires_in <= 0 || result.expires_in > 3600) throw new SignInError('AUTH_SESSION');
  // Signature, audience and household membership are verified by the MCP server, never by this UI.
  return { accessToken: result.access_token, expiresAt: now + result.expires_in * 1000 };
}

export function logoutUrl(config: AuthConfig): string {
  return config.domain + '/logout?' + new URLSearchParams({ client_id: config.clientId, logout_uri: new URL(config.redirect).origin + '/' });
}
