export type Health = { status: 'ok' | 'degraded'; version: string; dependencies: { database: 'up' | 'down' } };

export async function fetchHealth(): Promise<Health> {
  const response = await fetch('/health', { signal: AbortSignal.timeout(12000), cache: 'no-store' });
  if (response.status !== 200 && response.status !== 503) throw new Error('Unexpected health response');
  const data: unknown = await response.json();
  if (!data || typeof data !== 'object' || !('status' in data) || !('version' in data) || !('dependencies' in data)) throw new Error('Invalid health response');
  const health = data as Health;
  if (typeof health.version !== 'string' || !health.dependencies ||
    (response.status === 200 ? health.status !== 'ok' || health.dependencies.database !== 'up' : health.status !== 'degraded' || health.dependencies.database !== 'down')) throw new Error('Invalid health response');
  return health;
}
