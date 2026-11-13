import { useEffect, useRef, useState } from 'react';
import { request } from './Workspace';

type Configuration = { id: string; base_url: string; model: string; api_key: string };
export function ModelSettings({ csrf }: { csrf: string }) {
  const [current, setCurrent] = useState<Configuration | null>(null);
  const [model, setModel] = useState('deepseek-v4.1-flash');
  const [key, setKey] = useState('');
  const [status, setStatus] = useState('正在加载…');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [touched, setTouched] = useState(false);
  const pending = useRef(false);
  const base = 'https://opencode.ai/inference/go/openai/v1';
  async function load() {
    try { const data = await request('/model-configurations'); setCurrent(data.configuration); if (data.configuration) setModel(data.configuration.model); setStatus(data.configuration ? '已保存配置 · 密钥 ********' : '尚未保存模型配置'); setLoaded(true); }
    catch (e) { setError(e instanceof Error ? e.message : '加载失败'); setStatus(''); }
  }
  useEffect(() => { void load(); }, []);
  const invalid = !model.trim() || Array.from(model).length > 200 || !key || key.length > 4096 || /[^\x21-\x7e]/u.test(key);
  async function submit(save: boolean) {
    setTouched(true); if (pending.current || invalid || !loaded) return;
    pending.current = true; setBusy(true); setError(''); setStatus('正在验证模型连接…');
    try {
      const data = await request(save ? '/model-configurations' : '/model-configuration-tests', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify({ base_url: base, model, api_key: key, expected_version: current?.id ?? null }) }, 65000);
      if (save) setCurrent(data);
      setStatus(save ? '验证成功，配置已保存 · 密钥 ********' : '连接成功，尚未保存；保存时请重新输入密钥');
    } catch (e) { setError(e instanceof Error ? e.message : '验证失败'); setStatus('本次操作未更改已保存配置'); }
    finally { setKey(''); setTouched(false); setBusy(false); pending.current = false; }
  }
  return <section className="panel"><h2>生成模型 · OpenCode Go</h2><p className="muted">保存后可用于故事准备和推演。</p>
    <p>服务地址：{base}</p><p role="status" aria-live="polite">{status}</p>
    {error && <p role="alert">{error} <button disabled={busy} onClick={() => void load()}>重新加载配置</button></p>}
    <form noValidate onSubmit={e => { e.preventDefault(); void submit(true); }}>
      <label htmlFor="model-name">模型名称</label><input id="model-name" value={model} disabled={busy} onBlur={() => setTouched(true)} onChange={e => setModel(e.target.value)} />
      <label htmlFor="model-key">供应商密钥</label><input id="model-key" type="password" autoComplete="off" value={key} disabled={busy} onBlur={() => setTouched(true)} onChange={e => setKey(e.target.value)} aria-describedby="model-error" />
      <p id="model-error" className="field-error">{touched && invalid ? '请填写模型名称和有效密钥' : '密钥加密保存，不显示原值；更新配置需要重新输入密钥。'}</p>
      <div className="actions"><button disabled={busy || !loaded}>验证并保存</button><button className="secondary" type="button" disabled={busy || !loaded} onClick={() => void submit(false)}>仅测试连接</button></div>
    </form><a href="/works">返回我的作品</a>
  </section>;
}
