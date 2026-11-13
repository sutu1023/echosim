import { useEffect, useRef, useState } from 'react';

export function ModelTest() {
  const [enabled, setEnabled] = useState(false);
  const [key, setKey] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('正在检查测试入口…');
  const abort = useRef<AbortController | null>(null);
  useEffect(() => {
    let active = true;
    void fetch('/api/v1/model-connection-tests').then(async r => {
      const data = await r.json();
      if (active) { setEnabled(r.ok && data.enabled === true); setMessage(r.ok && data.enabled === true ? '请输入当次 KEY' : '测试入口未启用'); }
    }).catch(() => { if (active) setMessage('测试入口不可用'); });
    return () => { active = false; abort.current?.abort(); };
  }, []);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!key.trim() || pending) return;
    setPending(true); setMessage('正在调用模型，最多等待 60 秒…');
    const controller = new AbortController(); abort.current = controller;
    const timer = setTimeout(() => controller.abort(), 62000);
    try {
      const response = await fetch('/api/v1/model-connection-tests', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({api_key: key}), signal: controller.signal,
      });
      const data = await response.json();
      const errors: Record<string, string> = {
        MODEL_AUTH_FAILED: '供应商鉴权失败，请检查 KEY 与订阅权限',
        MODEL_RATE_LIMITED: '供应商额度不足或请求受限', MODEL_TIMEOUT: '模型调用超时',
        MODEL_UPSTREAM_ERROR: '供应商或网络故障', MODEL_INVALID_RESPONSE: '供应商返回无效响应',
        INVALID_REQUEST: 'KEY 格式无效', ORIGIN_FORBIDDEN: '不允许跨站请求',
      };
      if (!response.ok) setMessage(errors[data.error?.code] ?? '测试失败，请稍后重试');
      else if (data.status !== 'success' || typeof data.duration_ms !== 'number') setMessage('测试结果无效');
      else setMessage(`连接成功 · ${data.duration_ms} ms · Token 用量：${data.usage ? `${data.usage.prompt_tokens} 输入 / ${data.usage.completion_tokens} 输出 / ${data.usage.total_tokens} 合计` : '未提供'}`);
    } catch { setMessage('连接失败或等待超时，请检查服务后重试'); }
    finally { clearTimeout(timer); setKey(''); setPending(false); }
  }
  return <main className="shell"><a href="/">EchoSim 首页</a><h1>模型连接测试</h1>
    <p>仅本地开发测试，KEY 不保存，每次测试后清空。创作功能尚未开放。</p>
    <p>OpenCode Go · deepseek-v4.1-flash</p>
    <p>https://opencode.ai/inference/go/openai/v1</p>
    {enabled && <form onSubmit={e => void submit(e)}>
      <label htmlFor="model-key">当次 API KEY</label>
      <input id="model-key" type="password" autoComplete="off" maxLength={4096} value={key} disabled={pending} onChange={e => setKey(e.target.value)} />
      <button disabled={pending || !key.trim()}>{pending ? '测试中…' : '测试连接'}</button>
    </form>}
    <p role="status" aria-live="polite">{message}</p>
  </main>;
}
