import { describe, expect, it, vi } from 'vitest';
import { authConfig, beginLogin, finishLogin, SignInError, signInErrorMessage } from './auth';

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
  it('reports a missing pending request without attempting a token exchange', async () => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(finishLogin(config, new URL(config.redirect + '?code=private&state=private'), storage(), fetcher))
      .rejects.toMatchObject({ code: 'AUTH_START' });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('handles blocked browser storage without revealing its exception', async () => {
    const saved = storage(); saved.setItem = () => { throw new Error('private browser state'); };
    await expect(beginLogin(config, saved)).rejects.toMatchObject({ code: 'AUTH_STORAGE' });
    saved.getItem = () => { throw new Error('private browser state'); };
    await expect(finishLogin(config, new URL(config.redirect), saved)).rejects.toMatchObject({ code: 'AUTH_STORAGE' });
  });
  it.each([
    ['invalid_grant', 'AUTH_CODE'], ['invalid_client', 'AUTH_CLIENT'],
    ['unauthorized_client', 'AUTH_CLIENT'], ['private-unknown-error', 'AUTH_EXCHANGE'],
  ])('classifies %s with no response text or request secrets', async (providerError, expected) => {
    const saved = storage(); const login = new URL(await beginLogin(config, saved, 1000));
    const callback = new URL(config.redirect + '?code=private-code&state=' + login.searchParams.get('state'));
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: providerError, error_description: 'private details' }, { status: 400 }));
    await expect(finishLogin(config, callback, saved, fetcher, 2000)).rejects.toMatchObject({ code: expected });
    expect(saved.length).toBe(0);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][1]?.redirect).toBe('error');
  });
  it.each(['network', 'bad-json', 'empty-json', 'null-json'])('reports %s without leaking content', async condition => {
    const saved = storage(); const login = new URL(await beginLogin(config, saved, 1000));
    const callback = new URL(config.redirect + '?code=private-code&state=' + login.searchParams.get('state'));
    const fetcher = vi.fn<typeof fetch>();
    if (condition === 'network') fetcher.mockRejectedValue(new Error('private connection details'));
    else fetcher.mockResolvedValue(condition === 'bad-json' ? new Response('private non-JSON body') : Response.json(condition === 'null-json' ? null : {}));
    await expect(finishLogin(config, callback, saved, fetcher, 2000)).rejects.toMatchObject({ code: condition === 'network' ? 'AUTH_NETWORK' : 'AUTH_SESSION' });
    expect(saved.length).toBe(0);
  });
  it.each([
    ['access_denied', 'AUTH_DENIED'], ['invalid_request', 'AUTH_REQUEST'],
    ['invalid_scope', 'AUTH_SCOPE'], ['invalid_resource', 'AUTH_RESOURCE'],
    ['invalid_client', 'AUTH_CLIENT'], ['unauthorized_client', 'AUTH_CLIENT'],
    ['unsupported_response_type', 'AUTH_FLOW'], ['server_error', 'AUTH_SERVICE'],
    ['temporarily_unavailable', 'AUTH_SERVICE'], ['login_required', 'AUTH_LOGIN_REQUIRED'],
    ['private-unrecognized-error', 'AUTH_PROVIDER'], ['toString', 'AUTH_PROVIDER'],
  ])('reports provider callback %s without exposing provider text', async (providerError, expected) => {
    const saved = storage(); const login = new URL(await beginLogin(config, saved, 1000));
    const callback = new URL(config.redirect);
    callback.search = new URLSearchParams({ state: login.searchParams.get('state')!, error: providerError, error_description: 'private provider details' }).toString();
    const fetcher = vi.fn<typeof fetch>();
    const result = await finishLogin(config, callback, saved, fetcher, 2000).catch(error => error);
    expect(result).toBeInstanceOf(SignInError);
    expect(result.code).toBe(expected);
    expect(signInErrorMessage(result)).not.toContain('private');
    expect(saved.length).toBe(0);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each(['wrong-state', 'expired', 'duplicate-error', 'code-and-error'])('rejects malformed provider callbacks: %s', async condition => {
    const saved = storage(); const login = new URL(await beginLogin(config, saved, 1000));
    const callback = new URL(config.redirect);
    callback.search = new URLSearchParams({ state: login.searchParams.get('state')!, error: 'invalid_scope' }).toString();
    if (condition === 'wrong-state') callback.searchParams.set('state', 'other');
    if (condition === 'duplicate-error') callback.searchParams.append('error', 'invalid_request');
    if (condition === 'code-and-error') callback.searchParams.set('code', 'private-code');
    const fetcher = vi.fn<typeof fetch>();
    const expected = condition === 'wrong-state' ? 'AUTH_STATE' : condition === 'expired' ? 'AUTH_EXPIRED' : 'AUTH_CALLBACK';
    await expect(finishLogin(config, callback, saved, fetcher, condition === 'expired' ? 602000 : 2000)).rejects.toMatchObject({ code: expected });
    expect(fetcher).not.toHaveBeenCalled();
    expect(saved.length).toBe(0);
  });
  it('does not trust an error message or an arbitrary diagnostic code', () => {
    const error = new SignInError('AUTH_CODE'); error.message = 'private password';
    expect(signInErrorMessage(error)).toContain('AUTH_CODE');
    expect(signInErrorMessage(error)).not.toContain('private');
    expect(signInErrorMessage(new Error('private server response'))).toContain('AUTH_UNKNOWN');
    Object.defineProperty(error, 'code', { value: 'private callback URL' });
    expect(signInErrorMessage(error)).toContain('AUTH_UNKNOWN');
    expect(signInErrorMessage(error)).not.toContain('private');
  });
});
