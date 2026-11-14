import { useEffect, useRef, useState } from 'react';
import { request } from './Workspace';
import { states, taskErrors } from './StoryPreparation';
import type { Task } from './StoryPreparation';
import { StoryGraph } from './StoryGraph';
import { AdoptionPanel } from './AdoptionPanel';
import { NarrativePlayer, type NarrativeEvent } from './NarrativePlayer';
type SimulationData = Task & { events: NarrativeEvent[]; canon_revision?: number | null; version_id: string; characters?: Record<string, string>; progress?: { stage?: string; ending_reason?: string } };
const stages: Record<string, string> = { planning: '正在安排剧情', generating: '正在生成剧情', validating: '正在检查剧情', complete: '本次推演结束' };
function merge(old: SimulationData | null, next: SimulationData) {
  return old?.id === next.id ? { ...next, events: [...new Map([...old.events, ...next.events].map(e => [e.id, e])).values()].sort((a,b) => a.sequence-b.sequence) } : next;
}
export function Simulation(props: { csrf: string; workId: string; title?: string }) { return <SimulationView key={props.workId} {...props} />; }
function SimulationView({ csrf, workId, title }: { csrf: string; workId: string; title?: string }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [checks, setChecks] = useState<Task[]>([]);
  const [data, setData] = useState<SimulationData | null>(null);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [graphReady, setGraphReady] = useState(false);
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
      setChecks(history.tasks.filter((t: Task) => t.kind === 'adoption'));
      const sims = history.tasks.filter((t: Task) => t.kind === 'simulation'); setTasks(sims);
      const id = selected || sims[0]?.id;
      if (id) { const detail = await request(prefix + '/simulations/' + id); if (generation !== loadGeneration.current) return; setSelected(id); setData(old => merge(old, detail)); }
      setError('');
    } catch (e) { setError(e instanceof Error ? e.message : '读取失败'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); const timer = setInterval(() => void load(), 2000); return () => { clearInterval(timer); ++loadGeneration.current; }; }, [workId, selected]);
  useEffect(() => {
    if (!selected) return;
    const source = new EventSource('/api/v1' + prefix + '/simulations/' + selected + '/stream');
    source.onmessage = event => {
      const next = JSON.parse(event.data) as NarrativeEvent;
      setData(previous => previous?.id === selected ? { ...previous, events: [...new Map([...previous.events, next].map(e => [e.id, e])).values()].sort((a, b) => a.sequence - b.sequence) } : previous);
    };
    source.addEventListener('status', event => { const state = JSON.parse((event as MessageEvent).data).state; setData(previous => previous?.id === selected ? { ...previous, state } : previous); if (['failed', 'stopped', 'succeeded'].includes(state)) source.close(); });
    source.onerror = () => source.close();
    return () => source.close();
  }, [selected, workId]);
  async function act(stop = false) {
    if (pending.current) return; pending.current = true; setBusy(true); setError('');
    try {
      key.current ??= crypto.randomUUID();
      const result = await request(prefix + '/simulations' + (stop ? '/' + selected + '/stop' : ''), { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': key.current }, body: '{}' });
      if (!stop) { key.current = null; setData({ ...result, events: [] }); setSelected(result.id); } else setData(result);
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : '操作失败'); }
    finally { pending.current = false; setBusy(false); }
  }
  const active = tasks.some(t => ['queued', 'running', 'stopping'].includes(t.state) || ['pending', 'checking'].includes(t.decision ?? ''));
  const names = Object.entries(data?.characters ?? {});
  return <section className="simulation-layout">
    <div className="simulation-reference">
      <section className="simulation-cover">
        <a href="/works">← 我的作品</a>
        <h2>{title ?? '当前作品'}</h2>
        <a href={prefix + '/prepare'}>返回故事准备</a>
      </section>
      <StoryGraph workId={workId} csrf={csrf} onReady={setGraphReady} />
    </div>
    <section className="simulation-reader" aria-label="剧情回放">
      <div className="reader-heading"><h2>剧情回放</h2>
    {tasks.length > 0 && <><label htmlFor="simulation-history">推演历史</label><select id="simulation-history" value={selected} onChange={e => { ++loadGeneration.current; setLoading(true); setData(null); setSelected(e.target.value); }}>{tasks.map((t, i) => <option key={t.id} value={t.id}>记录 {tasks.length-i} · {states[t.state]}</option>)}</select></>}
      </div>
    {data?.progress?.stage && ['queued', 'running', 'stopping'].includes(data.state) && <p className="muted">{stages[data.progress.stage]}</p>}
    {data?.progress?.ending_reason === 'event_limit' && <p className="notice">本次推演结束，冲突尚未解决。未完待续。</p>}
    {data && <NarrativePlayer key={workId + data.id} events={data.events} names={data.characters ?? {}} />}
    {!data && <p className="muted">{loading ? '正在读取剧情…' : '开始推演后，在这里阅读剧情。'}</p>}
    </section>
    <aside className="simulation-tools">
      <section><h2>出场角色</h2>
        {names.length ? <ul className="simulation-cast">{names.map(([id, name]) => <li key={id}>{name}</li>)}</ul> : <p className="muted">暂无角色记录</p>}
      </section>
      <section><h2>角色推演</h2>
    <div className="actions"><button disabled={busy || active || loading || !graphReady} onClick={() => void act()}>开始推演</button><button className="secondary" disabled={busy || !data || !['queued', 'running'].includes(data.state)} onClick={() => void act(true)}>停止推演</button></div>
    {error && <p role="alert">{error}<button onClick={() => void load()}>重新加载</button></p>}
    <p role="status" aria-live="polite">{loading ? '正在加载…' : data ? `${states[data.state]} · ${data.events.length} 条事件` : '还没有推演记录'}</p>
    {data?.error_code && <p role="alert">任务未完成：{taskErrors[data.error_code] || '生成失败'}。已完成事件保留；{['pending', 'checking'].includes(data.decision ?? '') ? '请先采纳或放弃本次结果。' : '可重新发起推演。'}</p>}
      </section>
    {data && ['pending','adopted','discarded'].includes(data.decision || '') && <a href={prefix+'/chapters?simulation='+data.id}>打开章节草稿</a>}
    {data && <AdoptionPanel key={data.id} csrf={csrf} workId={workId} result={data} checks={checks} refresh={load} />}
    </aside>
  </section>;
}
