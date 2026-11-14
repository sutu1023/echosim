import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { StoryGraph } from './StoryGraph';
const request = vi.fn();
vi.mock('./Workspace', () => ({ request: (...args: unknown[]) => request(...args) }));
afterEach(() => { vi.clearAllMocks(); vi.useRealTimers(); });
const fixture = (revision = 0) => ({ state: 'ready', version_id: 'v', canon_revision: revision, content: {
  entities: Array.from({ length: 100 }, (_, i) => ({ id: String(i), kind: 'object', name: '物品' + i, source: 's', quote: '<script>secret</script>' })),
  relations: Array.from({ length: 200 }, (_, i) => ({ id: 'r' + i, subject: '0', object: '1', predicate: '关联' + i, source: 's', active: true })), sources: { s: { text: '<script>secret</script>' } }
} });
test('full capacity is accessible with sources, safe text, zoom and keyboard pan', async () => {
  request.mockResolvedValue(fixture()); render(<StoryGraph workId="a" csrf="c" />);
  await screen.findByText('关键物品：物品99');
  expect(screen.getAllByText('查看来源')).toHaveLength(100);
  expect(screen.getAllByText('查看关系来源')).toHaveLength(200);
  fireEvent.click(screen.getAllByText('查看来源')[0]);
  expect(screen.getByText('<script>secret</script>')).toBeVisible();
  expect(screen.getByLabelText('事实来源')).toHaveFocus();
  fireEvent.click(screen.getByText('关闭来源'));
  expect(screen.getAllByText('查看来源')[0]).toHaveFocus(); expect(document.querySelector('script')).toBeNull();
  fireEvent.click(screen.getByText('放大'));
  const svg = screen.getByRole('img'); fireEvent.keyDown(svg, { key: 'ArrowRight' });
  expect(svg.querySelector('g')?.getAttribute('transform')).toContain('270 220');
  fireEvent.click(screen.getByText('适应画布')); expect(svg.querySelector('g')?.getAttribute('transform')).toContain('scale(1)');
});
test('failure preserves graph and explicit retry replaces only accepted version', async () => {
  request.mockResolvedValueOnce(fixture()).mockRejectedValueOnce(new Error()).mockResolvedValue(fixture(1));
  render(<StoryGraph workId="a" csrf="c" />); await screen.findByText('关键物品：物品99');
  await screen.findByRole('alert', {}, { timeout: 4000 });
  expect(screen.getByText('关键物品：物品99')).toBeVisible();
  fireEvent.click(screen.getByText('重试读取'));
  await waitFor(() => expect(screen.getByText(/主线版本 1/)).toBeVisible());
});
test('missing baseline offers durable build and queued status', async () => {
  request.mockResolvedValue({ state: 'missing', version_id: 'v', canon_revision: 0, content: null });
  render(<StoryGraph workId="a" csrf="c" />); fireEvent.click(await screen.findByText('构建或重试'));
  await waitFor(() => expect(request).toHaveBeenCalledWith('/works/a/story-graph/builds', { method: 'POST', headers: { 'X-CSRF-Token': 'c' } }));
});

test('a late response from another work cannot replace the current graph', async () => {
  let finish: (value: unknown) => void = () => {};
  request.mockImplementation((url: string) => url.includes('/old/') ? new Promise(resolve => { finish = resolve; }) : Promise.resolve({ ...fixture(2), version_id: 'new' }));
  const view = render(<StoryGraph workId="old" csrf="c" />);
  view.rerender(<StoryGraph workId="new" csrf="c" />);
  await screen.findByText(/主线版本 2/);
  finish(fixture(0));
  await waitFor(() => expect(screen.getByText(/主线版本 2/)).toBeVisible());
  expect(screen.getAllByLabelText('当前剧情知识图谱')).toHaveLength(1);
});

test('polling preserves a version until adoption and rejects older revisions', async () => {
  vi.useFakeTimers();
  const accepted = fixture(0);
  const changedWithoutAdoption = { ...fixture(0), content: { ...fixture(0).content, entities: [] } };
  request.mockResolvedValueOnce(accepted).mockResolvedValueOnce(changedWithoutAdoption).mockResolvedValueOnce(fixture(1)).mockResolvedValueOnce(fixture(0));
  render(<StoryGraph workId="a" csrf="c" />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText('关键物品：物品99')).toBeVisible();
  await act(async () => { vi.advanceTimersByTime(2000); });
  expect(screen.getByText('关键物品：物品99')).toBeVisible();
  expect(screen.getByText(/主线版本 0/)).toBeVisible();
  await act(async () => { vi.advanceTimersByTime(2000); });
  expect(screen.getByText(/主线版本 1/)).toBeVisible();
  await act(async () => { vi.advanceTimersByTime(2000); });
  expect(screen.getByText(/主线版本 1/)).toBeVisible();
});
