import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { afterEach, describe, it, expect, vi } from 'vitest';
import { App, StatusCard } from './App';

const valid = {
  code: 'OK',
  data: {
    status: 'ready',
    version: 'test-v1',
    time: '2026-10-28T00:00:00Z',
    dependencies: { postgres: 'up' },
  },
};
afterEach(() => vi.unstubAllGlobals());
describe('normal SC-UI-01', () => {
  it('checks real contract fields before claiming ready', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => valid }));
    render(<App />);
    await screen.findByText('服务已就绪');
    expect(screen.getByText('test-v1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新检查' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  });
});
describe('failure SC-UI-02 SC-UI-03 SC-UI-04', () => {
  it.each([
    () => Promise.reject(new Error('offline')),
    () => Promise.resolve({ ok: false, json: async () => ({ code: 'DEPENDENCY_UNAVAILABLE' }) }),
    () =>
      Promise.resolve({ ok: true, json: async () => ({ code: 'OK', data: { status: 'ready' } }) }),
    () =>
      Promise.resolve({
        ok: true,
        json: async () => {
          throw new Error('bad json');
        },
      }),
  ])('never fabricates healthy defaults', async (response) => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(response));
    render(<App />);
    await screen.findByText('暂未就绪');
    expect(screen.queryByText('服务已就绪')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新检查' })).toBeEnabled();
  });
});
describe('normal boundary SC-UI-07', () => {
  it('provides actionable empty and loading states', () => {
    const { rerender } = render(<StatusCard view={{ kind: 'empty' }} retry={() => {}} />);
    expect(screen.getByText('尚无状态')).toBeInTheDocument();
    expect(screen.getByRole('button')).toBeEnabled();
    rerender(<StatusCard view={{ kind: 'loading' }} retry={() => {}} />);
    expect(screen.getByRole('button')).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('正在获取');
  });
});
describe('failure security S11', () => {
  it('renders untrusted strings as text', () => {
    const { container } = render(
      <StatusCard
        view={{ kind: 'error', message: '<img src=x onerror=alert(1)>' }}
        retry={() => {}}
      />,
    );
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByRole('status')).toHaveTextContent('<img');
  });
});
