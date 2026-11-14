import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { AdoptionPanel } from './AdoptionPanel';
const result = { id: 's', state: 'stopped', kind: 'simulation', decision: 'pending', error_code: null, canon_revision: 0 };
afterEach(() => vi.unstubAllGlobals());
test('requires whole-result acknowledgement and sends expected revision', async () => {
  const fetch = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ id: 'adopt' }))));
  vi.stubGlobal('fetch', fetch);
  const refresh = vi.fn(async () => {});
  render(<AdoptionPanel csrf="c" workId="w" result={result} checks={[]} refresh={refresh} />);
  expect(screen.getByText('采纳本次推演')).toBeDisabled();
  expect(screen.getByText(/本次推演提前停止/)).toBeVisible();
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByText('采纳本次推演'));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  const call = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(call[0]).toContain('/simulations/s/adoptions');
  expect(JSON.parse(call[1].body as string)).toEqual({ expected_canon_revision: 0, acknowledge_irreversible: true });
});
test('failed and legacy results cannot be adopted and checking is observable', () => {
  const props = { csrf: 'c', workId: 'w', checks: [], refresh: async () => {} };
  const { rerender } = render(<AdoptionPanel {...props} result={{ ...result, decision: 'ineligible', state: 'failed' }} />);
  expect(screen.queryByText('采纳本次推演')).not.toBeInTheDocument();
  rerender(<AdoptionPanel {...props} result={{ ...result, decision: 'checking' }} />);
  expect(screen.getByRole('status')).toHaveTextContent('正在检查采纳内容');
});

test('uncertain response reuses idempotency key and stale revision remains visible', async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new Error('连接中断')).mockResolvedValueOnce(new Response(JSON.stringify({ error: { message: '主线已变化，请刷新' } }), { status: 409 }));
  vi.stubGlobal('fetch', fetch);
  const refresh = vi.fn(async () => {});
  render(<AdoptionPanel csrf="c" workId="w" result={result} checks={[]} refresh={refresh} />);
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByText('采纳本次推演'));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByText('采纳本次推演'));
  await screen.findByText('主线已变化，请刷新');
  expect(fetch.mock.calls[0][1].headers['Idempotency-Key']).toBe(fetch.mock.calls[1][1].headers['Idempotency-Key']);
  expect(refresh).not.toHaveBeenCalled();
});

test('a saved draft links to review before adoption and still permits discard', () => {
  render(<AdoptionPanel csrf="c" workId="w" result={{ ...result, has_chapter: true }} checks={[]} refresh={async () => {}} />);
  expect(screen.getByRole('link', { name: '核对并采纳修改稿' })).toHaveAttribute('href', '/works/w/chapters?simulation=s');
  expect(screen.queryByRole('button', { name: '采纳本次推演' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '放弃本次结果' })).toBeEnabled();
});
