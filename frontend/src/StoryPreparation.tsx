import { useEffect, useRef, useState } from 'react';
import { request } from './Workspace';
export type Story = { synopsis: string; world_rules: string; outline: string; characters: { id: string; name: string; identity: string; goal: string; background: string; public_information: string; private_knowledge: string }[] };
type Version = { id: string; content: Story; created_at: string };
export type Task = { id: string; state: string; kind: string; error_code: string | null };
export const taskErrors: Record<string, string> = { MODEL_TIMEOUT: '模型响应超时，请重试', MODEL_INVALID_RESPONSE: '模型输出不完整，请重新生成', MODEL_AUTH_FAILED: '模型鉴权失败，请检查模型设置', MODEL_RATE_LIMITED: '模型额度不足或请求过于频繁', WORKER_LOST: '后台进程已中断，请重新发起任务', TASK_TIMEOUT: '任务超过时限', TASK_FAILED: '任务执行失败，请重试', STORY_CONFLICT: '生成内容与设定冲突，请重新推演' };
export const states: Record<string, string> = { queued: '排队中', running: '生成中', stopping: '正在停止', stopped: '已停止', succeeded: '已完成', failed: '失败' };
export function StoryContent({ story }: { story: Story }) {
  return <><h3>概要</h3><p>{story.synopsis}</p><h3>世界规则</h3><p>{story.world_rules}</p><h3>简短大纲</h3><p>{story.outline}</p><h3>角色</h3>{story.characters.map(c => <article className="panel" key={c.id}><h3>{c.name} · {c.identity}</h3><p>目标：{c.goal}</p><p>背景：{c.background}</p><p>公开信息：{c.public_information}</p><p>角色私有知识：{c.private_knowledge}</p></article>)}</>;
}
export function StoryPreparation({ csrf, workId }: { csrf: string; workId: string }) {
  const [versions, setVersions] = useState<Version[]>([]);
  const [head, setHead] = useState<{ candidate_id: string; confirmed_id: string } | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [idea, setIdea] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  const pending = useRef(false);
  const retry = useRef<{ input: string; key: string } | null>(null);
  const prefix = '/works/' + workId;
  const active = tasks.some(t => ['queued', 'running', 'stopping'].includes(t.state));
  async function load() {
    try { const data = await request(prefix + '/preparations'); setVersions(data.versions); setHead(data.head); setTasks(data.tasks); setError(''); }
    catch (e) { setError(e instanceof Error ? e.message : '加载失败'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); const timer = setInterval(() => void load(), 2000); return () => clearInterval(timer); }, [workId]);
  const selected = versions.find(v => v.id === head?.candidate_id);
  const invalid = !idea.trim() || Array.from(idea.trim()).length > 2000;
  async function run(confirm = false) {
    setTouched(true); if (pending.current || active || (!confirm && invalid)) return;
    pending.current = true; setBusy(true); setError('');
    try {
      const input = JSON.stringify(confirm ? { version_id: selected?.id, expected_candidate_id: selected?.id } : { idea: idea.trim(), base_version_id: selected?.id ?? null });
      if (retry.current?.input !== input) retry.current = { input, key: crypto.randomUUID() };
      await request(prefix + (confirm ? '/preparation-confirmations' : '/preparation-tasks'), { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': retry.current!.key }, body: input });
      retry.current = null; if (!confirm) setIdea(''); setTouched(false); await load();
    } catch (e) { setError(e instanceof Error ? e.message : '请求失败'); }
    finally { setBusy(false); pending.current = false; }
  }
  return <section><h2>故事准备</h2><p className="muted">输入一句创意，生成概要、规则、大纲与三名角色；确认后开始推演。</p>
    <p role="status" aria-live="polite">{loading ? '正在加载…' : active ? '后台任务正在执行，离开页面不会中断' : head?.confirmed_id ? '已有确认设定' : '尚未确认设定'}</p>
    {error && <p role="alert">{error}<button onClick={() => void load()}>重新加载</button></p>}
    <form onSubmit={e => { e.preventDefault(); void run(); }}><label htmlFor="story-idea">{selected ? '修改意见' : '一句故事创意'}</label><textarea id="story-idea" value={idea} disabled={busy || active} onBlur={() => setTouched(true)} onChange={e => setIdea(e.target.value)} />
      <p className="field-error">{touched && invalid ? '请输入 1–2000 个字符' : ''}</p><button disabled={busy || active || loading}>{selected ? '生成新的完整版本' : '生成故事与角色'}</button></form>
    {selected && <section className="panel"><h2>{selected.id === head?.confirmed_id ? '已确认设定' : '候选设定 · 尚未确认'}</h2><p>以下内容为模型创作新增。</p><StoryContent story={selected.content} /><button disabled={busy || active || selected.id === head?.confirmed_id} onClick={() => void run(true)}>确认本版设定与三名角色</button></section>}
    {head?.confirmed_id && <a className="button-link" href={prefix + '/simulation'}>开始角色推演 →</a>}
    <h3>生成记录</h3>{tasks.filter(t => t.kind === 'preparation').map(t => <p key={t.id}>{states[t.state]} {t.error_code ? '· ' + (taskErrors[t.error_code] || '任务未完成，请重试') : ''}</p>)}
    {versions.filter(v => v.id !== selected?.id).map(v => <details key={v.id}><summary>历史版本 · {new Date(v.created_at).toLocaleString('zh-CN')}</summary><StoryContent story={v.content} /></details>)}
  </section>;
}
