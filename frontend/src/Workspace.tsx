import { Chapters } from './Chapters';
import { Simulation } from './Simulation';
import { StoryPreparation } from './StoryPreparation';
import { ModelSettings } from './ModelSettings';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

type Identity = { username: string; csrf: string };
type Work = { id: string; title: string; created_at: string };
class RequestError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}
export async function request(path: string, init?: RequestInit, timeout = 15000) {
  let response: Response;
  try { response = await fetch('/api/v1' + path, { ...init, credentials: 'same-origin', signal: AbortSignal.timeout(timeout) }); }
  catch { throw new RequestError('暂时无法连接服务，请重试', 0); }
  if (response.status === 204) return null;
  let data;
  try { data = await response.json(); }
  catch { throw new RequestError('服务响应异常，请重试', response.status); }
  if (!response.ok) throw new RequestError(data.error?.message || '请求失败，请重试', response.status);
  return data;
}
const clean = (value: string) => value.replace(/^[\s\u001c-\u001f\u0085]+|[\s\u001c-\u001f\u0085]+$/gu, '');
const titleError = (value: string) => Array.from(clean(value)).length < 1 || Array.from(clean(value)).length > 100 ? '请输入 1–100 个字符的作品标题' : '';

export function Workspace() {
  const [path, setPath] = useState(window.location.pathname);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [quick, setQuick] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [title, setTitle] = useState('');
  const [touched, setTouched] = useState(false);
  const [loginTouched, setLoginTouched] = useState(false);
  const [items, setItems] = useState<Work[]>([]);
  const [work, setWork] = useState<Work | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [attempt, setAttempt] = useState<{ key: string; title: string } | null>(null);
  const [reload, setReload] = useState(0);
  const move = (target: string) => { window.history.pushState({}, '', target); setPath(target); setError(''); };
  useEffect(() => {
    const pop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, []);
  useEffect(() => {
    let live = true;
    setLoading(true); setWork(null); setItems([]); setCursor(null);
    void (async () => {
      try {
        const capabilities = await request('/capabilities');
        if (!live) return;
        setQuick(capabilities.quick_login === true);
        let user: Identity | null = null;
        try { user = await request('/sessions/current'); }
        catch (e) { if (!(e instanceof RequestError) || e.status !== 401) throw e; }
        if (!live) return;
        setIdentity(user);
        if (path === '/login') return;
        if (!user) { move('/login'); setError('请登录后查看自己的作品'); return; }
        if (path === '/settings/models') return;
        const data = await request(path === '/works' ? '/works' : path.replace(/\/(prepare|simulation|chapters)$/, ''));
        if (!live) return;
        if (path === '/works') { setItems(data.items); setCursor(data.next_cursor); }
        else setWork(data);
      } catch (e) {
        if (live) {
          if (e instanceof RequestError && e.status === 401) { setIdentity(null); move('/login'); }
          setError(e instanceof Error ? e.message : '请求失败，请重试');
        }
      } finally { if (live) setLoading(false); }
    })();
    return () => { live = false; };
  }, [path, reload]);
  async function act(operation: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try { await operation(); }
    catch (e) {
      if (e instanceof RequestError && e.status === 401 && path !== '/login') { setIdentity(null); move('/login'); }
      setError(e instanceof Error ? e.message : '请求失败，请重试');
    } finally { pending.current = false; setBusy(false); }
  }
  const headers = () => ({ 'Content-Type': 'application/json', 'X-CSRF-Token': identity?.csrf || '' });
  async function login(event?: FormEvent, target?: string) {
    event?.preventDefault(); if (!target) setLoginTouched(true);
    if (!target && (!username.trim() || !password)) return;
    await act(async () => {
      const user = await request(target ? '/dev-sessions' : '/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(target ? { target } : { username, password }) });
      setPassword(''); setLoginTouched(false); setIdentity(user); setAttempt(null); setTitle(''); move('/works');
    });
  }
  async function create(event: FormEvent) {
    event.preventDefault(); if (pending.current) return; setTouched(true);
    if (titleError(title)) return;
    const normalized = clean(title);
    const next = attempt?.title === normalized ? attempt : { key: crypto.randomUUID(), title: normalized };
    setAttempt(next);
    await act(async () => {
      const result = await request('/works', { method: 'POST', headers: { ...headers(), 'Idempotency-Key': next.key }, body: JSON.stringify({ title: normalized }) });
      setAttempt(null); setTitle(''); setTouched(false); move('/works/' + result.id);
    });
  }
  const simulation = path.endsWith('/simulation');
  return <div className={simulation ? "shell workspace simulation-shell" : "shell workspace"}>
    <header><a className="wordmark" href="/"><span className="mark">e</span> EchoSim</a>
      <nav aria-label="账号导航">{identity && <><a href="/settings/models">模型设置</a><span>{identity.username}</span><button className="quiet" disabled={busy} onClick={() => void act(async () => { await request('/sessions/current', { method: 'DELETE', headers: headers() }); setIdentity(null); setAttempt(null); setTitle(''); move('/login'); })}>退出登录</button></>}</nav>
    </header>
    <main>
      
      <h1>{path === '/login' ? '回到你的故事' : path === '/works' ? '我的作品' : path === '/settings/models' ? '模型设置' : path.endsWith('/prepare') ? '故事准备' : path.endsWith('/simulation') ? '推演现场' : path.endsWith('/chapters') ? '章节与历史' : '作品详情'}</h1>
      <div role="status" aria-live="polite">{loading ? '正在加载…' : busy ? '正在提交…' : ''}</div>
      {error && <div className="notice" role="alert"><p>{error}</p>{!busy && !loading && <button className="quiet" onClick={() => { setError(''); setReload(x => x + 1); }}>重新加载</button>}</div>}
      {!loading && path === '/login' && <div className="desk-grid login-desk"><section className="panel login-panel">
        <p className="muted">使用预置账号登录，继续管理你的作品。</p>
        {identity && <p>当前账号：{identity.username} · <button className="quiet" onClick={() => move('/works')}>返回作品</button></p>}
        <form onSubmit={event => void login(event)} noValidate>
          <label htmlFor="username">用户名</label><input id="username" autoComplete="username" value={username} disabled={busy} onBlur={() => setLoginTouched(true)} onChange={event => setUsername(event.target.value)} aria-describedby="login-fields" />
          <label htmlFor="password">密码</label><input id="password" type="password" autoComplete="current-password" value={password} disabled={busy} onBlur={() => setLoginTouched(true)} onChange={event => setPassword(event.target.value)} aria-describedby="login-fields" />
          <p id="login-fields" className="field-error">{loginTouched && (!username.trim() || !password) ? '请输入用户名和密码' : ''}</p>
          <button type="submit" disabled={busy}>登录</button>
        </form>
        </section>{quick && <section className="cover quick-login"><h2>先体验一下</h2><p className="muted">两个独立的创作空间，一键切换。</p><div className="actions">{['A', 'B'].map(target => <button className="secondary" disabled={busy} key={target} onClick={() => void login(undefined, target)}>体验账号 {target}</button>)}</div></section>}
      </div>}
      {!loading && identity && path === '/settings/models' && <ModelSettings csrf={identity.csrf} />}
      {!loading && identity && path === '/works' && <div className="desk-grid library-desk">
        <section className="panel create-work"><h2>开启一部新作品</h2><form onSubmit={event => void create(event)} noValidate>
          <label htmlFor="work-title">作品标题</label><input id="work-title" value={title} disabled={busy} onBlur={() => setTouched(true)} onChange={event => setTitle(event.target.value)} aria-describedby="title-error" />
          <p id="title-error" className="field-error">{touched ? titleError(title) : '1–100 个字符，可以与已有作品同名'}</p>
          {attempt && attempt.title !== clean(title) && <p className="muted">上次提交结果可能尚未确认。以新标题创建会发起独立请求，原作品可能已经保存。</p>}
          <button disabled={busy}>{attempt ? attempt.title === clean(title) ? '重试创建' : '以新标题创建' : '创建作品'}</button>
        </form></section>
        <section className="panel work-list" aria-label="作品列表"><h2>作品列表</h2>
          {!items.length && !error && <div className="empty"><h2>还没有作品</h2><p>给故事起个名字，留住第一个灵感。</p></div>}
          {items.map(item => <article className="work-card" key={item.id}><a href={'/works/' + item.id} onClick={event => { event.preventDefault(); move('/works/' + item.id); }}>{item.title}</a><span className="muted">创建于 {new Date(item.created_at).toLocaleDateString('zh-CN')}</span></article>)}
          {cursor && <button className="secondary" disabled={busy} onClick={() => void act(async () => { const data = await request('/works?cursor=' + encodeURIComponent(cursor)); setItems(previous => [...previous, ...data.items]); setCursor(data.next_cursor); })}>加载更多</button>}
        </section>
        {quick && <button className="quiet" disabled={busy} onClick={() => move('/login')}>切换体验账号</button>}
      </div>}
      {!loading && identity && !simulation && path.startsWith('/works/') && <><button className="quiet" onClick={() => move('/works')}>← 返回我的作品</button>{work && <section className="cover detail"><h2>{work.title}</h2><p className="muted">创建于 {new Date(work.created_at).toLocaleString('zh-CN')}</p>{!path.endsWith('/prepare') && <a href={"/works/" + work.id + "/prepare"}>准备故事与角色 →</a>}{!path.endsWith("/chapters") && <a href={"/works/"+work.id+"/chapters"}>章节与历史 →</a>}</section>}</>}
      {!loading && identity && work && path.endsWith('/prepare') && <StoryPreparation csrf={identity.csrf} workId={work.id} />}
      {!loading && identity && work && path.endsWith('/chapters') && <Chapters key={work.id} workId={work.id} csrf={identity.csrf} />}
      {!loading && identity && work && path.endsWith('/simulation') && <Simulation csrf={identity.csrf} workId={work.id} title={work.title} />}
    </main>
  </div>;
}
