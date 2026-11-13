import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ModelTest } from './ModelTest';
const response = (ok: boolean, data: unknown) => ({ok, json: async () => data});
describe('model connection', () => {
  it('hides form when disabled', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(false, {})));
    render(<ModelTest />);
    expect(await screen.findByText('测试入口未启用')).toBeInTheDocument();
    expect(screen.queryByLabelText('当次 API KEY')).not.toBeInTheDocument();
  });
  it('clears key on success and reports missing usage', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(true, {enabled: true})).mockResolvedValue(response(true, {status:'success',duration_ms:12,usage:null}));
    vi.stubGlobal('fetch', fetcher);
    render(<ModelTest />);
    const input = await screen.findByLabelText('当次 API KEY');
    expect(screen.getByRole('button')).toBeDisabled();
    fireEvent.change(input, {target:{value:'private-marker'}});
    fireEvent.click(screen.getByRole('button'));
    expect(await screen.findByText(/连接成功/)).toHaveTextContent('未提供');
    expect(input).toHaveValue('');
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({api_key:'private-marker'});
  });
  it.each(['MODEL_AUTH_FAILED','MODEL_RATE_LIMITED','MODEL_TIMEOUT','MODEL_UPSTREAM_ERROR','MODEL_INVALID_RESPONSE'])('clears after %s', async code => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response(true,{enabled:true})).mockResolvedValue(response(false,{error:{code,message:'private-marker'}})));
    render(<ModelTest />);
    const input = await screen.findByLabelText('当次 API KEY');
    fireEvent.change(input,{target:{value:'private-marker'}});
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => expect(input).toHaveValue(''));
    expect(screen.getByRole('status')).not.toHaveTextContent('private-marker');
  });
  it('prevents duplicate pending requests', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(true,{enabled:true})).mockImplementation(() => new Promise(() => {}));
    vi.stubGlobal('fetch', fetcher);
    render(<ModelTest />);
    fireEvent.change(await screen.findByLabelText('当次 API KEY'),{target:{value:'marker'}});
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByRole('button')).toBeDisabled();
    fireEvent.click(screen.getByRole('button'));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
