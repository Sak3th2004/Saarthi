import { describe, expect, it, vi } from 'vitest';
import { loadRuntimeConfig } from './runtimeConfig';

const origin = 'https://notebook.example';
const configuration = {
  VITE_AUTH_MODE: 'cognito',
  VITE_COGNITO_DOMAIN: 'https://notebook.auth.us-east-1.amazoncognito.com',
  VITE_COGNITO_CLIENT_ID: 'client123',
  VITE_COGNITO_RESOURCE_URL: origin + '/mcp',
  VITE_COGNITO_REDIRECT_URI: origin + '/auth/callback',
};
function fetchConfig(value: unknown, status = 200) {
  return vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(value), { status }));
}
describe('hosted sign-in configuration', () => {
  it('loads validated settings without caching or following redirects', async () => {
    const fetcher = fetchConfig(configuration);
    expect(await loadRuntimeConfig(origin, fetcher)).toEqual(configuration);
    expect(fetcher).toHaveBeenCalledWith('/app-config.json', expect.objectContaining({
      cache: 'no-store', redirect: 'error', signal: expect.any(AbortSignal),
    }));
  });
  it.each([{}, null, [], { VITE_AUTH_MODE: 'local' }, { ...configuration, VITE_COGNITO_CLIENT_ID: 123 }])(
    'rejects missing, malformed or local-mode production settings: %j', async value => {
      await expect(loadRuntimeConfig(origin, fetchConfig(value))).rejects.toThrow();
    },
  );
  it('rejects a callback on another origin', async () => {
    await expect(loadRuntimeConfig(origin, fetchConfig({ ...configuration,
      VITE_COGNITO_REDIRECT_URI: 'https://another.example/auth/callback',
    }))).rejects.toThrow();
  });
  it('does not accept a service error as configuration', async () => {
    await expect(loadRuntimeConfig(origin, fetchConfig(configuration, 503))).rejects.toThrow();
  });
});
