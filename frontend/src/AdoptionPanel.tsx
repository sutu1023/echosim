import { useRef, useState } from 'react';
import { request } from './Workspace';
import { taskErrors, type Task } from './StoryPreparation';
export function AdoptionPanel({ csrf, workId, result, checks, refresh }: { csrf: string; workId: string; result: Task & { canon_revision?: number | null; has_chapter?: boolean }; checks: Task[]; refresh: () => Promise<void> }) {
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const retry = useRef<{ action: string; key: string } | null>(null);
  async function choose(discard: boolean) {
    if (lock.current || (!discard && !ack)) return;
    lock.current = true; setBusy(true); setError('');
    const action = discard ? 'discards' : 'adoptions';
    retry.current = retry.current?.action === action ? retry.current : { action, key: crypto.randomUUID() };
    try {
      await request(`/works/${workId}/simulations/${result.id}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': retry.current.key }, body: JSON.stringify(discard ? {} : { expected_canon_revision: result.canon_revision, acknowledge_irreversible: true }) });
      retry.current = null; await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : '操作失败'); }
    finally { lock.current = false; setBusy(false); }
  }
  if (result.decision === 'adopted') return <p className="notice">本次推演已采纳，已成为后续剧情的依据。</p>;
  if (result.decision === 'discarded') return <p className="muted">本次结果已放弃，仅保留阅读。</p>;
  if (result.decision === 'checking') return <p role="status">正在检查采纳内容，完成后一起更新主线、记忆与图谱。</p>;
  if (result.decision !== 'pending') return null;
  const failed = checks.find(t => t.source_simulation_id === result.id && t.state === 'failed');
  return <section className="panel"><h3>决定本次剧情</h3>
    <p>{result.state === 'stopped' ? '本次推演提前停止；已有完整事件可以整次采纳。' : '采纳后从这段剧情继续；放弃后从原主线重新推演。'}</p>
    <p>采纳不可撤销；放弃后本次结果和已保存的草稿永久只读，不能再次采纳。</p>
    {failed && <p role="alert">采纳未完成：{taskErrors[failed.error_code ?? ''] ?? '请重试或放弃，正式剧情未改变'}</p>}
    {error && <p role="alert">{error}</p>}
    {!result.has_chapter && <label><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />我已阅读本次全部剧情，确认整次采纳且不可撤销</label>}
    <div className="actions">{result.has_chapter ? <a href={'/works/'+workId+'/chapters?simulation='+result.id}>核对并采纳修改稿</a> : <button disabled={busy || !ack} onClick={() => void choose(false)}>采纳本次推演</button>}<button className="secondary" disabled={busy} onClick={() => void choose(true)}>放弃本次结果</button></div>
  </section>;
}
