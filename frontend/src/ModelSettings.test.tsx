import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { ModelSettings } from './ModelSettings';
afterEach(() => vi.unstubAllGlobals());
test('saves configuration, clears secret and prevents pending duplicate', async () => {
  let finish: (r: Response) => void = () => {};
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ configuration: null }))).mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
  vi.stubGlobal('fetch', fetcher); render(<ModelSettings csrf="csrf" />);
  await screen.findByText('尚未保存模型配置');
  fireEvent.change(screen.getByLabelText('供应商密钥'), { target: { value: 'private-marker' } });
  fireEvent.click(screen.getByText('验证并保存'));
  expect(screen.getByText('验证并保存')).toBeDisabled();
  finish(new Response(JSON.stringify({ id: 'one', model: 'model', api_key: '********' })));
  await screen.findByText('验证成功，配置已保存 · 密钥 ********');
  expect(screen.getByLabelText('供应商密钥')).toHaveValue('');
  expect(fetcher).toHaveBeenCalledTimes(2);
});
test('failed replacement keeps previous configuration and clears key', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ configuration: { id: 'old', model: 'old-model' } }))).mockResolvedValueOnce(new Response(JSON.stringify({ error: { message: '供应商拒绝' } }), { status: 502 })));
  render(<ModelSettings csrf="csrf" />); await screen.findByText('已保存配置 · 密钥 ********');
  fireEvent.change(screen.getByLabelText('供应商密钥'), { target: { value: 'bad-key' } });
  fireEvent.click(screen.getByText('验证并保存'));
  await screen.findByText('本次操作未更改已保存配置');
  await waitFor(() => expect(screen.getByLabelText('供应商密钥')).toHaveValue(''));
});
