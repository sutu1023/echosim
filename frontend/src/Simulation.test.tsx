import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { Simulation } from './Simulation';
afterEach(() => vi.unstubAllGlobals());
test('restores events, stops running task and preserves completed events', async () => {
  const event = { id: 'e1', sequence: 1, content: { actor_id: '1', round: 1, action: '检查灯座', dialogue: '灯丝断了', result: '发现故障', visibility: ['1','2','3'] } };
  let state = 'running';
  vi.stubGlobal('EventSource', class { onmessage = null; onerror = null; addEventListener() {} close() {} });
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    if (url.endsWith('/stop')) state = 'stopped';
    const task = { id: 's', kind: 'simulation', state, events: [event], version_id: 'v' };
    return Promise.resolve(new Response(JSON.stringify(url.endsWith('/preparations') ? { tasks: [task] } : task)));
  }));
  render(<Simulation csrf="csrf" workId="w" />);
  expect(await screen.findByText('检查灯座')).toBeVisible();
  fireEvent.click(screen.getByText('停止推演'));
  await screen.findByText('已停止 · 1/9 条事件');
  expect(screen.getByText('发现故障')).toBeVisible();
  expect(screen.getByText('停止推演')).toBeDisabled();
});
