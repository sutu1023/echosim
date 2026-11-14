import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { NarrativePlayer, type NarrativeEvent } from './NarrativePlayer';
const event: NarrativeEvent = { id: 'e', sequence: 1, content: { actor_id: '1', segments: [
  { segment_id: '1', kind: 'narration', text: '推开配电柜。' },
  { segment_id: '2', kind: 'dialogue', speaker_id: '1', text: '找到线索了。' },
] } };
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
test('animates only newly validated events and reveals once', () => {
  vi.useFakeTimers();
  const { rerender } = render(<NarrativePlayer events={[]} names={{ '1': '林岫' }} />);
  expect(screen.queryByText('找到线索了。')).not.toBeInTheDocument();
  rerender(<NarrativePlayer events={[event]} names={{ '1': '林岫' }} />);
  act(() => vi.advanceTimersByTime(34));
  expect(screen.getByText('推')).toBeVisible();
  fireEvent.click(screen.getByText('立即显示本事件'));
  expect(screen.getByText('找到线索了。', { selector: 'p' })).toBeVisible();
  expect(screen.getByText('林岫')).toBeVisible();
  rerender(<NarrativePlayer events={[event]} names={{ '1': '林岫' }} />);
  expect(screen.getAllByText('找到线索了。', { selector: 'p' })).toHaveLength(1);
});
test('restored history is complete and text is inert', () => {
  render(<NarrativePlayer events={[{ ...event, content: { actor_id: '1', segments: [{ segment_id: '1', kind: 'narration', text: '<script>bad()</script>' }] } }]} names={{}} />);
  expect(screen.getByText('<script>bad()</script>', { selector: 'p' })).toBeVisible();
  expect(document.querySelector('script')).toBeNull();
});
test('reduced motion shows segments and replay duplicates are not rendered', () => {
  vi.useFakeTimers();
  vi.stubGlobal('matchMedia', () => ({ matches: true }));
  const { rerender } = render(<NarrativePlayer events={[]} names={{}} />);
  rerender(<NarrativePlayer events={[event, event]} names={{}} />);
  act(() => vi.advanceTimersByTime(34));
  expect(screen.getAllByText('找到线索了。', { selector: 'p' })).toHaveLength(1);
  expect(screen.queryByText('立即显示本事件')).not.toBeInTheDocument();
});
test('scrolling back preserves reading position until explicit follow', () => {
  const { rerender } = render(<NarrativePlayer events={[event]} names={{}} />);
  const flow = screen.getByLabelText('剧情事件');
  Object.defineProperties(flow, { scrollHeight: { value: 1000, configurable: true }, clientHeight: { value: 200, configurable: true } });
  flow.scrollTop = 100;
  fireEvent.scroll(flow);
  expect(screen.getByText('回到最新剧情')).toBeVisible();
  expect(screen.queryByText('新内容 · 回到最新剧情')).not.toBeInTheDocument();
  rerender(<NarrativePlayer events={[event, { ...event, id: 'e2', sequence: 2 }]} names={{}} />);
  expect(flow.scrollTop).toBe(100);
  fireEvent.click(screen.getByText('新内容 · 回到最新剧情'));
  expect(flow.scrollTop).toBe(1000);
});

test('out-of-order restored events appear in sequence order', () => {
  render(<NarrativePlayer events={[{ ...event, id: 'e2', sequence: 2 }, event]} names={{}} />);
  const articles = screen.getAllByRole('article');
  expect(articles.map(a => a.getAttribute('aria-label'))).toEqual(['事件 1', '事件 2']);
});
