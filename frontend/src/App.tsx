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
  return <div className="shell home-shell">
    <header><a className="wordmark" href="/" aria-label="EchoSim 首页"><span className="mark">e</span> EchoSim</a><a href="/works">我的作品</a></header>
    <main className="desk-grid">
      <section className="cover home-cover"><h1>从一句创意开始</h1>
        <p>准备设定与角色，观察剧情展开，再决定是否采纳为正式剧情。</p>
        <a href="/works">进入我的作品 →</a>
      </section>
      <section className="status-card" aria-labelledby="status-title">
        <div className="status-heading"><span className={`dot ${state === 'ready' && health?.status === 'ok' ? 'healthy' : ''}`} /><h2 id="status-title">运行状态</h2><span className="version">{health?.version ?? '—'}</span></div>
        <p className="status-label" role="status" aria-live="polite">{label}</p>
        <p className="status-note">{state === 'error' ? '请检查服务是否启动，然后重试。' : state === 'ready' && health?.status === 'degraded' ? '服务已响应，数据库连接需要恢复。' : '这里显示应用与数据库的连接状态。'}</p>
        <button onClick={() => void refresh()} disabled={state === 'loading'}>{state === 'loading' ? '检查中…' : '重新检查'}<span aria-hidden="true"> ↗</span></button>
      </section>
    </main>
  </div>;
}
