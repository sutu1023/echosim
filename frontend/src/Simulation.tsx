import { useEffect, useRef, useState } from 'react';
import { request } from './Workspace';
import { states, taskErrors } from './StoryPreparation';
import type { Task } from './StoryPreparation';
type Event = { id: string; sequence: number; content: { actor_id: string; action: string; dialogue: string; result: string; round: number; visibility: string[] } };
type SimulationData = Task & { events: Event[]; version_id: string };
export function Simulation({ csrf, workId }: { csrf: string; workId: string }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [data, setData] = useState<SimulationData | null>(null);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const pending = useRef(false);
  const loadGeneration = useRef(0);
  const key = useRef<string | null>(null);
  const prefix = '/works/' + workId;
  async function load() {
    const generation = ++loadGeneration.current;
    try {
      const history = await request(prefix + '/preparations');
      if (generation !== loadGeneration.current) return;
      const sims = history.tasks.filter((t: Task) => t.kind === 'simulation'); setTasks(sims);
      const id = selected || sims[0]?.id;
      if (id) { const detail = await request(prefix + '/simulations/' + id); if (generation !== loadGeneration.current) return; setSelected(id); setData(detail); }
      setError('');
    } catch (e) { setError(e instanceof Error ? e.message : '读取失败'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); const timer = setInterval(() => void load(), 2000); return () => clearInterval(timer); }, [workId, selected]);
  useEffect(() => {
    if (!selected) return;
    const source = new EventSource('/api/v1' + prefix + '/simulations/' + selected + '/stream');
    source.onmessage = event => {
      const next = JSON.parse(event.data) as Event;
      setData(previous => previous?.id === selected ? { ...previous, events: [...new Map([...previous.events, next].map(e => [e.id, e])).values()].sort((a, b) => a.sequence - b.sequence) } : previous);
    };
    source.addEventListener('status', event => { const state = JSON.parse((event as MessageEvent).data).state; setData(previous => previous ? { ...previous, state } : previous); if (['failed', 'stopped', 'succeeded'].includes(state)) source.close(); });
    source.onerror = () => source.close();
    return () => source.close();
  }, [selected, workId]);
  async function act(stop = false) {
    if (pending.current) return; pending.current = true; setBusy(true); setError('');
    try {
      key.current ??= crypto.randomUUID();
      const result = await request(prefix + '/simulations' + (stop ? '/' + selected + '/stop' : ''), { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': key.current }, body: '{}' });
      if (!stop) { key.current = null; setSelected(result.id); } else setData(result);
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : '操作失败'); }
    finally { pending.current = false; setBusy(false); }
  }
  const active = tasks.some(t => ['queued', 'running', 'stopping'].includes(t.state));
  return <section><h2>角色推演</h2><p>三名角色 · 三轮 · 一个场景。结果是可能剧情，尚未进入正式剧情。</p>
    <div className="actions"><button disabled={busy || active || loading} onClick={() => void act()}>开始新的三轮推演</button><button className="secondary" disabled={busy || !data || !['queued', 'running'].includes(data.state)} onClick={() => void act(true)}>停止推演</button></div>
    {error && <p role="alert">{error}<button onClick={() => void load()}>重新加载</button></p>}
    <p role="status" aria-live="polite">{loading ? '正在加载…' : data ? `${states[data.state]} · ${data.events.length}/9 条事件` : '还没有推演记录'}</p>
    {data?.error_code && <p role="alert">任务未完成：{taskErrors[data.error_code] || '生成失败'}。已完成事件保留，可重新发起推演。</p>}
    {tasks.length > 0 && <><label htmlFor="simulation-history">推演历史</label><select id="simulation-history" value={selected} onChange={e => { setData(null); setSelected(e.target.value); }}>{tasks.map((t, i) => <option key={t.id} value={t.id}>记录 {tasks.length-i} · {states[t.state]}</option>)}</select></>}
    <div aria-label="剧情事件">{data?.events.map(e => <article className="panel" key={e.id}><p className="eyebrow">第 {e.content.round} 轮 · 事件 {e.sequence} · 角色 {e.content.actor_id}</p><h3>{e.content.action}</h3>{e.content.dialogue && <blockquote>{e.content.dialogue}</blockquote>}<p>{e.content.result}</p><small>角色可见性：{e.content.visibility.join('、')}</small></article>)}</div>
    <a href={prefix + '/prepare'}>返回故事准备</a>
  </section>;
}
