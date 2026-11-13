import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { App } from './App';

const response = (status: number) => ({ status, json: async () => ({ status: status === 200 ? 'ok' : 'degraded', version: 'test-1', dependencies: { database: status === 200 ? 'up' : 'down' } }) });

describe('runtime status', () => {
  it('shows loading while request is pending', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    render(<App />);
    expect(screen.getByRole('status')).toHaveTextContent('正在检查服务');
    expect(screen.getByRole('button')).toBeDisabled();
  });
  it('shows healthy version and honest unavailable business state', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(200)));
    render(<App />);
    expect(await screen.findByText('服务运行正常')).toBeInTheDocument();
    expect(screen.getByText('test-1')).toBeInTheDocument();
    expect(screen.getByText('创作功能尚未开放')).toBeInTheDocument();
  });
  it('shows degraded state and can recover by retry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response(503)).mockResolvedValue(response(200)));
    render(<App />);
    expect(await screen.findByText('数据库暂时不可用')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重新检查/ }));
    expect(await screen.findByText('服务运行正常')).toBeInTheDocument();
  });
  it('shows connection error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    render(<App />);
    expect(await screen.findByText('暂时无法连接服务')).toBeInTheDocument();
    expect(screen.getByRole('button')).toBeEnabled();
  });
  it('rejects malformed success responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 200, json: async () => ({status: 'ok'}) }));
    render(<App />);
    expect(await screen.findByText('暂时无法连接服务')).toBeInTheDocument();
  });
});
