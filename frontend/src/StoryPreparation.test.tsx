import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { StoryPreparation, StoryContent } from './StoryPreparation';
vi.mock('./StoryGraph', () => ({ StoryGraph: () => null }));
afterEach(() => vi.unstubAllGlobals());
test('one idea submits durable generation and pending task disables duplicates', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ head: null, versions: [], tasks: [] }))).mockResolvedValueOnce(new Response(JSON.stringify({ id: 't', state: 'queued' }), { status: 202 })).mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ head: null, versions: [], tasks: [{ id: 't', kind: 'preparation', state: 'queued' }] }))));
  vi.stubGlobal('fetch', fetcher); render(<StoryPreparation csrf="csrf" workId="work" />);
  await screen.findByText('尚未确认设定');
  fireEvent.change(screen.getByLabelText('一句故事创意'), { target: { value: '雨夜灯塔熄灭，三个人寻找原因。' } });
  fireEvent.click(screen.getByText('生成故事与角色'));
  await screen.findByText('后台任务正在执行，离开页面不会中断');
  expect(screen.getByText('生成故事与角色')).toBeDisabled();
  expect(JSON.parse(fetcher.mock.calls[1][1].body).idea).toBe('雨夜灯塔熄灭，三个人寻找原因。');
});

test('generated content is rendered as text without executing markup', () => {
  render(<StoryContent story={{ synopsis: '<script>alert(1)</script>', world_rules: '规则', outline: '大纲', characters: [] }} />);
  expect(screen.getByText('<script>alert(1)</script>')).toBeVisible();
  expect(document.querySelector('script')).toBeNull();
});

test('adopted canon freezes preparation while preserving readable old settings', async () => {
  const content = { synopsis: '旧概要', world_rules: '规则', outline: '大纲', characters: [] };
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ head: { candidate_id: 'v', confirmed_id: 'v', canon_revision: 1 }, versions: [{ id: 'v', content }], tasks: [] })))));
  render(<StoryPreparation csrf="csrf" workId="frozen" />);
  expect(await screen.findByText('正式剧情已采纳，设定已锁定')).toBeVisible();
  expect(screen.getByText('旧概要')).toBeVisible();
  expect(screen.getByRole('button', { name: '生成新的完整版本' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '确认本版设定与三名角色' })).toBeDisabled();
});
