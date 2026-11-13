import { useCallback, useEffect, useState } from 'react';
import { fetchHealth, type Health } from './health';

export function App() {
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [health, setHealth] = useState<Health | null>(null);
  const refresh = useCallback(async () => {
    setState('loading');
    try { setHealth(await fetchHealth()); setState('ready'); }
    catch { setHealth(null); setState('error'); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const label = state === 'loading' ? '正在检查服务' : state === 'error' ? '暂时无法连接服务' : health?.status === 'ok' ? '服务运行正常' : '数据库暂时不可用';
  return <div className="shell">
    <header><a className="wordmark" href="/" aria-label="EchoSim 首页"><span className="mark">e</span> EchoSim</a><span className="edition">创作空间 · 初始版本</span></header>
    <main>
      <p className="eyebrow">让角色行动，让故事生长</p>
      <h1>每一个选择，<br/>都有故事的回响。</h1>
      <p className="intro">从世界设定到角色行动，从可能的剧情到属于你的下一章。<br className="wide"/>EchoSim，陪你探索故事尚未发生的部分。</p>
      <section className="status-card" aria-labelledby="status-title">
        <div className="status-heading"><span className={`dot ${state === 'ready' && health?.status === 'ok' ? 'healthy' : ''}`} /><h2 id="status-title">运行状态</h2><span className="version">{health?.version ?? '—'}</span></div>
        <p className="status-label" role="status" aria-live="polite">{label}</p>
        <p className="status-note">{state === 'error' ? '请检查服务是否启动，然后重试。' : state === 'ready' && health?.status === 'degraded' ? '服务已响应，数据库连接需要恢复。' : '这里显示应用与数据库的连接状态。'}</p>
        <button onClick={() => void refresh()} disabled={state === 'loading'}>{state === 'loading' ? '检查中…' : '重新检查'}<span aria-hidden="true"> ↗</span></button>
      </section>
      <aside className="coming"><span className="number">01 — 起点</span><p>创作功能尚未开放</p><span>你现在可以创建和管理自己的作品。角色推演与章节创作将逐步加入。</span><p><a href="/works">进入我的作品 →</a></p></aside>
    </main>
    <footer><span>EchoSim</span><span>故事的下一种可能。</span></footer>
  </div>;
}
