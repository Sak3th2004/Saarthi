import { describe, expect, it, vi } from 'vitest';
import { authConfig, beginLogin, finishLogin } from './auth';

const env = { VITE_AUTH_MODE: 'cognito', VITE_COGNITO_DOMAIN: 'https://notebook.auth.us-east-1.amazoncognito.com',
  VITE_COGNITO_CLIENT_ID: 'client123', VITE_COGNITO_RESOURCE_URL: 'http://localhost:5173/mcp',
  VITE_COGNITO_REDIRECT_URI: 'http://localhost:5173/auth/callback' };
const config = authConfig(env, 'http://localhost:5173')!;
function storage(): Storage {
  const values = new Map<string, string>();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); },
    removeItem: key => { values.delete(key); }, clear: () => values.clear(), key: i => [...values.keys()][i] ?? null,
    get length() { return values.size; } };
}

describe('Cognito sign-in', () => {
  it('does not allow published notebooks to silently bypass sign-in', () => {
    expect(authConfig({}, 'http://127.0.0.1:5173')).toBeNull();
    expect(() => authConfig({}, 'https://notebook.example')).toThrow('Sign-in');
    expect(() => authConfig({ VITE_AUTH_MODE: 'cognito' }, 'http://localhost:5173')).toThrow();
    expect(() => authConfig({ ...env, VITE_COGNITO_REDIRECT_URI: 'http://attacker.example/auth/callback' }, 'http://localhost:5173')).toThrow();
  });
  it('binds the authorization code to a random verifier, state and resource', async () => {
    const saved = storage();
    const url = new URL(await beginLogin(config, saved, 1000));
    expect(url.searchParams.get('code_challenge_method')).toBe('S256');
    expect(url.searchParams.get('resource')).toBe(config.resource);
    expect(url.searchParams.get('scope')).toBe('openid saarthi/notebook');
    const pending = JSON.parse(saved.getItem('saarthi.cognito.pending')!);
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(pending.verifier)));
    const expected = btoa(String.fromCharCode(...digest)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
    expect(url.searchParams.get('code_challenge')).toBe(expected);
    expect(url.searchParams.get('state')).toBe(pending.state);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ access_token: 'a-signed-token', token_type: 'Bearer', expires_in: 900 }));
    const callback = new URL(config.redirect + '?code=once&state=' + pending.state);
    const result = await finishLogin(config, callback, saved, fetcher, 2000);
    expect(result).toEqual({ accessToken: 'a-signed-token', expiresAt: 902000 });
    expect(saved.length).toBe(0);
    expect((fetcher.mock.calls[0][1]?.body as URLSearchParams).get('code_verifier')).toBe(pending.verifier);
    await expect(finishLogin(config, callback, saved, fetcher, 2000)).rejects.toThrow();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each(['wrong-state', 'expired', 'wrong-redirect', 'duplicate-code', 'error'])('rejects %s before exchanging credentials', async condition => {
    const saved = storage();
    const login = new URL(await beginLogin(config, saved, 1000));
    const callback = new URL(config.redirect + '?code=one&state=' + login.searchParams.get('state'));
    if (condition === 'wrong-state') callback.searchParams.set('state', 'other');
    if (condition === 'wrong-redirect') callback.hostname = 'other.example';
    if (condition === 'duplicate-code') callback.searchParams.append('code', 'two');
    if (condition === 'error') callback.searchParams.set('error', 'access_denied');
    const fetcher = vi.fn<typeof fetch>();
    await expect(finishLogin(config, callback, saved, fetcher, condition === 'expired' ? 602000 : 2000)).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
    expect(saved.length).toBe(0);
  });
});
