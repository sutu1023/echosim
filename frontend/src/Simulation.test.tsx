import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { Simulation } from './Simulation';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
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
  expect(screen.getByText('暂无角色记录')).toBeVisible();
  fireEvent.click(screen.getByText('停止推演'));
  await screen.findByText('已停止 · 1 条事件');
  expect(screen.getByText('发现故障', { selector: 'p' })).toBeVisible();
  expect(screen.getByText('停止推演')).toBeDisabled();
});

test('late history from a previous work cannot replace the selected work', async () => {
  let finish: (response: Response) => void = () => {};
  vi.stubGlobal('EventSource', class { onmessage = null; onerror = null; addEventListener() {} close() {} });
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    if (url.includes('/old/') && url.endsWith('/preparations')) return new Promise(resolve => { finish = resolve; });
    const task = { id: 'snew', kind: 'simulation', state: 'succeeded', events: [{ id: 'enew', sequence: 1, content: { actor_id: '1', action: '新作品剧情', result: '新后果' } }], version_id: 'v' };
    return Promise.resolve(new Response(JSON.stringify(url.endsWith('/preparations') ? { tasks: [task] } : url.endsWith('/story-graph') ? { state: 'missing', content: null } : task)));
  }));
  const view = render(<Simulation csrf="c" workId="old" />);
  view.rerender(<Simulation csrf="c" workId="new" />);
  await screen.findByText('新作品剧情');
  await act(async () => { finish(new Response(JSON.stringify({ tasks: [{ id: 'old', kind: 'simulation', state: 'running' }] }))); });
  expect(screen.getByText('新作品剧情')).toBeVisible();
  expect(screen.getAllByLabelText('当前剧情知识图谱')).toHaveLength(1);
});

test('SSE duplicates deduplicate and polling recovers with terminal playback queued', async () => {
  const sources: Source[] = [];
  class Source {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: (() => void) | null = null;
    close = vi.fn();
    constructor() { sources.push(this); }
    addEventListener() {}
  }
  const event = { id: 'live', sequence: 1, content: { actor_id: '1', action: '第一条动作' } };
  let events: typeof event[] = [];
  let state = 'running';
  vi.stubGlobal('EventSource', Source);
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const task = { id: 's', kind: 'simulation', state, events, version_id: 'v' };
    return Promise.resolve(new Response(JSON.stringify(url.endsWith('/preparations') ? { tasks: [task] } : url.endsWith('/story-graph') ? { state: 'missing', content: null } : task)));
  }));
  render(<Simulation csrf="c" workId="live" />);
  await screen.findByText('生成中 · 0 条事件');
  act(() => { sources.at(-1)?.onmessage?.({ data: JSON.stringify(event) } as MessageEvent); sources.at(-1)?.onmessage?.({ data: JSON.stringify(event) } as MessageEvent); });
  fireEvent.click(screen.getByText('立即显示本事件'));
  expect(screen.getAllByRole('article')).toHaveLength(1);
  act(() => sources.at(-1)?.onerror?.());
  expect(sources.at(-1)?.close).toHaveBeenCalled();
  events = [event, { id: 'last', sequence: 2, content: { actor_id: '2', action: '最后完整事件' } }]; state = 'succeeded';
  await screen.findByText('已完成 · 2 条事件', {}, { timeout: 4000 });
  fireEvent.click(screen.getByText('显示剩余剧情'));
  expect(screen.getByText('最后完整事件', { selector: 'p' })).toBeVisible();
  expect(screen.getAllByRole('article')).toHaveLength(2);
});
