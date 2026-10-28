import { useCallback, useEffect, useRef, useState } from 'react';

type Health = {
  status: 'ready' | 'not_ready';
  version: string;
  time: string;
  dependencies: { postgres: string };
};
type View = {
  kind: 'loading' | 'empty' | 'error' | 'ready';
  data?: Health;
  message?: string;
  requestId?: string;
};

export function StatusCard({ view, retry }: { view: View; retry: () => void }) {
  return (
    <section className="card" aria-labelledby="service-title" aria-busy={view.kind === 'loading'}>
      <div className="card-label">RUNTIME / 01</div>
      <h2 id="service-title">服务状态</h2>
      <div role="status" aria-live="polite">
        {view.kind === 'loading' && (
          <>
            <span className="badge">获取中</span>
            <p>正在获取服务与数据库状态…</p>
          </>
        )}
        {view.kind === 'empty' && (
          <>
            <span className="badge">尚无状态</span>
            <p>还没有状态记录。获取状态后可检查运行环境。</p>
          </>
        )}
        {view.kind === 'error' && (
          <>
            <span className="badge danger">暂未就绪</span>
            <p>{view.message}</p>
            {view.requestId && <p className="meta">请求编号：{view.requestId}</p>}
          </>
        )}
        {view.kind === 'ready' && view.data && (
          <>
            <span className="badge success">服务已就绪</span>
            <dl>
              <div>
                <dt>运行版本</dt>
                <dd>{view.data.version}</dd>
              </div>
              <div>
                <dt>PostgreSQL</dt>
                <dd>连接正常</dd>
              </div>
              <div>
                <dt>检查时间</dt>
                <dd>
                  <time dateTime={view.data.time}>
                    {new Date(view.data.time).toLocaleString()} ·{' '}
                    {Intl.DateTimeFormat().resolvedOptions().timeZone}
                  </time>
                </dd>
              </div>
            </dl>
          </>
        )}
      </div>
      <button onClick={retry} disabled={view.kind === 'loading'}>
        {view.kind === 'loading' ? '正在获取…' : '重新检查'}
      </button>
    </section>
  );
}

function isHealth(value: unknown): value is Health {
  if (!value || typeof value !== 'object') return false;
  const d = value as Partial<Health>;
  return (
    d.status === 'ready' &&
    typeof d.version === 'string' &&
    !!d.version &&
    typeof d.time === 'string' &&
    !Number.isNaN(Date.parse(d.time)) &&
    d.dependencies?.postgres === 'up'
  );
}

export function App() {
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [menuOpen, setMenuOpen] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setView({ kind: 'loading' });
    const timer = setTimeout(() => current.abort(), 5000);
    try {
      const res = await fetch('/health', { signal: current.signal, cache: 'no-store' });
      const body = await res.json();
      if (controller.current !== current) return;
      if (!res.ok || body.code !== 'OK' || !isHealth(body.data)) {
        setView({
          kind: 'error',
          message: '服务状态未能确认。请检查数据库与容器是否启动，再重新检查。',
          requestId: typeof body.request_id === 'string' ? body.request_id : undefined,
        });
      } else setView({ kind: 'ready', data: body.data });
    } catch {
      if (controller.current === current)
        setView({ kind: 'error', message: '未能获取服务状态。连接可能中断，请检查容器后重试。' });
    } finally {
      clearTimeout(timer);
    }
  }, []);
  useEffect(() => {
    void load();
    return () => {
      controller.current?.abort();
      controller.current = null;
    };
  }, [load]);
  const notFound = window.location.pathname !== '/';
  return (
    <div className="workspace">
      <a className="skip" href="#main">
        跳到主要内容
      </a>
      <header className="mobile-header">
        <strong>回响 EchoSim</strong>
        <button
          aria-expanded={menuOpen}
          aria-controls="sidebar"
          onClick={() => setMenuOpen(!menuOpen)}
        >
          目录
        </button>
      </header>
      <aside id="sidebar" className={menuOpen ? 'sidebar open' : 'sidebar'}>
        <a className="brand" href="/" aria-label="回响 EchoSim 首页">
          <span className="brand-icon" aria-hidden="true">
            ◎
          </span>
          <span>
            回响
            <br />
            EchoSim
          </span>
        </a>
        <p className="meta">// 多智能体讨论体验平台</p>
        <nav aria-label="当前阶段导航">
          <p className="eyebrow">DIRECTORY / 目录</p>
          <a
            className={!notFound ? 'selected' : ''}
            aria-current={!notFound ? 'page' : undefined}
            href="/"
          >
            运行档案
          </a>
          <a href="#milestones" onClick={() => setMenuOpen(false)}>
            项目里程碑
          </a>
          <a href="https://github.com/sutu1023/echosim" target="_blank" rel="noreferrer noopener">
            项目仓库 ↗
          </a>
        </nav>
        <div className="sidebar-note">
          <span className="badge">M0 · 骨架基线</span>
          <p>
            本页验证环境已就绪。
            <br />
            业务功能按规约逐步交付。
          </p>
        </div>
        <p className="meta sidebar-footer">STATUS / BUILDING THE BASELINE</p>
      </aside>
      <main id="main" tabIndex={-1}>
        <div className="page-meta">
          <span>PROJECT ARCHIVE</span>
          <span>阶段 00 / 启动</span>
        </div>
        <h1>
          {notFound ? (
            '页面不存在'
          ) : (
            <>
              让讨论的变化，
              <br />
              <em>留下回响。</em>
            </>
          )}
        </h1>
        {notFound ? (
          <section className="card">
            <h2>没有找到这个页面</h2>
            <p>地址可能有误，或对应能力尚未开放。</p>
            <a className="button" href="/">
              返回运行档案
            </a>
          </section>
        ) : (
          <>
            <p className="intro">
              从一则新闻出发，观察不同虚拟角色如何回应彼此。
              <br />
              当前交付运行骨架，先把可复现的起点建立起来。
            </p>
            <div className="notice">
              <strong>本课交付</strong>
              <span>建仓库 → 定规约 → 立规范 → 落骨架</span>
            </div>
            <div className="columns">
              <StatusCard view={view} retry={() => void load()} />
              <section className="card accent-card">
                <div className="card-label">BOUNDARY / 明确边界</div>
                <h2>AI 虚构推演</h2>
                <p>后续模拟用于理解角色互动，不代表真实民意或现实预测。</p>
                <p>本阶段没有推演或模型调用，不需要 API Key。</p>
              </section>
            </div>
            <section id="milestones" aria-labelledby="milestone-title">
              <div className="section-heading">
                <span aria-hidden="true">■</span>
                <h2 id="milestone-title">从基线开始，逐步交付</h2>
              </div>
              <ol className="timeline">
                {[
                  ['M0', '运行骨架', '规范、健康检查、容器、状态页与验证。'],
                  ['M1', '新闻与角色', '链接导入，确认背景并调整虚拟角色。'],
                  ['M2', '可追溯的讨论', '假模型推演、实时发言、取消与事件回放。'],
                  ['M3', '真实模型接入', '逐轮回顾、引用校验与明确的用量预算。'],
                  ['M4', '分享与公开体验', '只读分享、清理、HTTPS 与恢复演练。'],
                ].map(([id, title, description]) => (
                  <li key={id}>
                    <span className="timeline-id">{id}</span>
                    <div>
                      <h3>{title}</h3>
                      <p>{description}</p>
                      <span className="meta">
                        {id === 'M0' ? '本轮验收范围' : '后续里程碑 · 尚未实现'}
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          </>
        )}
        <footer>回响 EchoSim · MIT · 每次交付都留下可验证的记录</footer>
      </main>
    </div>
  );
}
