import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { Workspace } from './Workspace';

const ok = (data: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status }));
afterEach(() => vi.unstubAllGlobals());

test('login reports errors and offers only enabled quick accounts', async () => {
  window.history.replaceState({}, '', '/login');
  vi.stubGlobal('fetch', vi.fn((url: string) => url.endsWith('capabilities') ? ok({ quick_login: true }) : ok({ error: { message: '用户名或密码错误' } }, 401)));
  render(<Workspace />);
  expect(await screen.findByRole('button', { name: '体验账号 A' })).toBeVisible();
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'alice' } });
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'wrong' } });
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  expect(await screen.findByText('用户名或密码错误')).toBeVisible();
});

test('empty list and uncertain submission reuses key until explicitly changed', async () => {
  window.history.replaceState({}, '', '/works');
  const writes: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('capabilities')) return ok({ quick_login: false });
    if (url.endsWith('current')) return ok({ username: 'alice', csrf: 'csrf' });
    if (init?.method === 'POST') { writes.push(init); return Promise.reject(new Error('offline')); }
    return ok({ items: [], next_cursor: null });
  }));
  render(<Workspace />);
  expect(await screen.findByText('还没有作品')).toBeVisible();
  expect(screen.queryByText('体验账号 A')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('作品标题'), { target: { value: '春山' } });
  fireEvent.click(screen.getByRole('button', { name: '创建作品' }));
  await screen.findByText('暂时无法连接服务，请重试');
  fireEvent.click(screen.getByRole('button', { name: '重试创建' }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[0].headers).toEqual(writes[1].headers);
  await waitFor(() => expect(screen.getByRole('button', { name: '重试创建' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('作品标题'), { target: { value: '秋水' } });
  fireEvent.click(screen.getByRole('button', { name: '以新标题创建' }));
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(writes[2].headers).not.toEqual(writes[0].headers);
});

test('private detail is text and clearly unavailable for creation features', async () => {
  window.history.replaceState({}, '', '/works/123');
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    if (url.endsWith('capabilities')) return ok({ quick_login: false });
    if (url.endsWith('current')) return ok({ username: 'alice', csrf: 'csrf' });
    return ok({ id: '123', title: '<script>private</script>', created_at: '2026-11-13T00:00:00Z' });
  }));
  render(<Workspace />);
  expect(await screen.findByText('<script>private</script>')).toBeVisible();
  expect(screen.getByText('故事准备尚未提供')).toBeVisible();
  expect(document.querySelector('script')).toBeNull();
});

test('quick login blocks duplicate submission then opens own works', async () => {
  window.history.replaceState({}, '', '/login');
  let resolveLogin: ((response: Response) => void) | undefined;
  let posts = 0;
  let signedIn = false;
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('capabilities')) return ok({ quick_login: true });
    if (url.endsWith('dev-sessions')) { posts++; return new Promise<Response>(resolve => { resolveLogin = resolve; }); }
    if (url.endsWith('current')) return signedIn ? ok({ username: 'experience-a', csrf: 'a' }) : ok({}, 401);
    return ok({ items: [], next_cursor: null });
  }));
  render(<Workspace />);
  const button = await screen.findByRole('button', { name: '体验账号 A' });
  fireEvent.click(button); fireEvent.click(button);
  expect(button).toBeDisabled();
  expect(posts).toBe(1);
  signedIn = true;
  resolveLogin?.(new Response(JSON.stringify({ username: 'experience-a', csrf: 'a' }), { status: 200 }));
  expect(await screen.findByText('还没有作品')).toBeVisible();
  expect(screen.getByText('experience-a')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '切换体验账号' }));
  await screen.findByRole('button', { name: '体验账号 A' });
  expect(screen.queryByText('请输入用户名和密码')).not.toBeInTheDocument();
});

test('unauthenticated deep link redirects without leaking detail', async () => {
  window.history.replaceState({}, '', '/works/private');
  const mock = vi.fn((url: string) => url.endsWith('capabilities') ? ok({ quick_login: false }) : ok({}, 401));
  vi.stubGlobal('fetch', mock);
  render(<Workspace />);
  expect(await screen.findByRole('button', { name: '登录' })).toBeVisible();
  expect(window.location.pathname).toBe('/login');
  expect(mock.mock.calls.some(([url]) => url.endsWith('/works/private'))).toBe(false);
});

test('title and login fields validate on blur', async () => {
  window.history.replaceState({}, '', '/login');
  vi.stubGlobal('fetch', vi.fn((url: string) => url.endsWith('capabilities') ? ok({ quick_login: false }) : ok({}, 401)));
  render(<Workspace />);
  fireEvent.blur(await screen.findByLabelText('用户名'));
  expect(screen.getByText('请输入用户名和密码')).toBeVisible();
});

test('pending work submission preserves the retry identity', async () => {
  window.history.replaceState({}, '', '/works');
  const writes: RequestInit[] = [];
  let rejectWrite: ((error: Error) => void) | undefined;
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('capabilities')) return ok({ quick_login: false });
    if (url.endsWith('current')) return ok({ username: 'alice', csrf: 'a' });
    if (init?.method === 'POST') { writes.push(init); return new Promise<Response>((_, reject) => { rejectWrite = reject; }); }
    return ok({ items: [], next_cursor: null });
  }));
  render(<Workspace />);
  const input = await screen.findByLabelText('作品标题');
  fireEvent.change(input, { target: { value: '春山' } });
  const form = input.closest('form')!;
  fireEvent.submit(form); fireEvent.submit(form);
  expect(writes).toHaveLength(1);
  expect(screen.getByRole('button', { name: '重试创建' })).toBeDisabled();
  rejectWrite?.(new Error('offline'));
  await screen.findByText('暂时无法连接服务，请重试');
  fireEvent.submit(form);
  expect(writes).toHaveLength(2);
  expect(writes[1].headers).toEqual(writes[0].headers);
});
