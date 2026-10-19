import { authConfig } from './auth';

/** Production sign-in settings come from the same server as the notebook. */
export async function loadRuntimeConfig(origin: string, fetcher: typeof fetch = fetch): Promise<Record<string, string>> {
  const response = await fetcher('/app-config.json', {
    cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error('Configuration unavailable');
  const config: unknown = await response.json();
  if (!config || typeof config !== 'object' || Array.isArray(config) ||
      Object.values(config).some(value => typeof value !== 'string')) throw new Error('Invalid configuration');
  const environment = config as Record<string, string>;
  if (environment.VITE_AUTH_MODE !== 'cognito' || !authConfig(environment, origin)) {
    throw new Error('Hosted notebook requires sign-in');
  }
  return environment;
}
