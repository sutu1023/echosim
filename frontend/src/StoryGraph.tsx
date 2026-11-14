import { useEffect, useRef, useState } from 'react';
import { request } from './Workspace';
type Entity = { id: string; kind: string; name: string; source: string; quote: string };
type Relation = { id: string; subject: string; object: string; predicate: string; source: string; quote: string; active: boolean };
type Graph = { version_id: string; canon_revision: number; state: string; content: null | { entities: Entity[]; relations: Relation[]; sources: Record<string, { text: string }> } };
const kinds: Record<string, string> = { character: '人物', place: '地点', object: '关键物品' };
export function StoryGraph(props: { workId: string; csrf: string; onReady?: (ready: boolean) => void }) { return <GraphView key={props.workId} {...props} />; }
function GraphView({ workId, csrf, onReady }: { workId: string; csrf: string; onReady?: (ready: boolean) => void }) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState('');
  const [source, setSource] = useState('');
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [changed, setChanged] = useState<string[]>([]);
  const serial = useRef(0);
  const sourceBox = useRef<HTMLElement>(null);
  const sourceTrigger = useRef<HTMLButtonElement | null>(null);
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const drag = useRef<{ x: number; y: number } | null>(null);
  const prefix = '/works/' + workId + '/story-graph';
  async function load() {
    const version = ++serial.current;
    try {
      const next = await request(prefix) as Graph;
      if (version !== serial.current) return;
      onReady?.(next.state === 'ready');
      setGraph(old => {
        if (old && old.version_id === next.version_id && old.canon_revision > next.canon_revision) return old;
        if (old?.state === 'ready' && old.version_id === next.version_id && old.canon_revision === next.canon_revision && next.state === 'ready') return old;
        const previous = new Map(old?.content?.entities.map(e => [e.id, JSON.stringify(e)]) ?? []);
        const previousEdges = new Map(old?.content?.relations.map(e => [e.id, JSON.stringify(e)]) ?? []);
        setChanged([...(next.content?.entities.filter(e => previous.get(e.id) !== JSON.stringify(e)).map(e => e.id) ?? []), ...(next.content?.relations.filter(e => previousEdges.get(e.id) !== JSON.stringify(e)).map(e => e.id) ?? [])]);
        setSource('');
        return next;
      });
      setError('');
    } catch { if (version === serial.current) setError('图谱读取失败，当前显示可能不是最新版本。'); }
  }
  useEffect(() => { if (source) { sourceBox.current?.focus(); sourceBox.current?.scrollIntoView?.({ block: 'nearest' }); } }, [source]);
  useEffect(() => { if (!changed.length) return; const timer = setTimeout(() => setChanged([]), 4000); return () => clearTimeout(timer); }, [changed]);
  useEffect(() => { void load(); const timer = setInterval(() => void load(), 2000); return () => { clearInterval(timer); ++serial.current; }; }, [workId]);
  async function build() {
    try { await request(prefix + '/builds', { method: 'POST', headers: { 'X-CSRF-Token': csrf } }); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : '构建失败'); }
  }
  const entities = graph?.content?.entities ?? [];
  const relations = graph?.content?.relations.filter(r => r.active) ?? [];
  for (const e of entities) if (!positions.current.has(e.id)) { const i = positions.current.size; positions.current.set(e.id, { x: 250 + Math.cos(i * 2.39996) * (100 + Math.min(90, i * 4)), y: 220 + Math.sin(i * 2.39996) * (100 + Math.min(75, i * 4)) }); }
  const points = positions.current;
  return <aside className="story-graph" aria-label="当前剧情知识图谱"><h3>剧情知识图谱</h3><p>只随已采纳剧情更新 · 主线版本 {graph?.canon_revision ?? 0}</p>
    {error && <p role="alert">{error}<button onClick={() => void load()}>重试读取</button></p>}
    {!graph && <p role="status">正在加载图谱…</p>}
    {graph && graph.state !== 'ready' && <p role="status">{['queued', 'running'].includes(graph.state) ? '初始图谱构建中…' : '初始图谱尚未就绪'}{graph.version_id && !['queued', 'running'].includes(graph.state) && <button onClick={() => void build()}>构建或重试</button>}</p>}
    {graph?.state === 'ready' && <><div className="actions"><button onClick={() => setZoom(z => Math.min(4, z + .2))}>放大</button><button onClick={() => setZoom(z => Math.max(.2, z - .2))}>缩小</button><button onClick={() => { setZoom(1); setOffset({ x: 0, y: 0 }); }}>适应画布</button></div>
      <p className="muted">圆形：人物 · 方形：地点 · 菱形：关键物品</p><svg viewBox="0 0 500 440" role="img" aria-label="人物、地点和关键物品关系图" tabIndex={0}
        onKeyDown={e => { const d: Record<string, [number, number]> = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] }; if (d[e.key]) { e.preventDefault(); const [x, y] = d[e.key]; setOffset(p => ({ x: p.x + x, y: p.y + y })); } }}
        onPointerDown={e => { drag.current = { x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId); }}
        onPointerMove={e => { if (drag.current) { const p = drag.current; setOffset(o => ({ x: o.x + e.clientX - p.x, y: o.y + e.clientY - p.y })); drag.current = { x: e.clientX, y: e.clientY }; } }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
        <g transform={`translate(${offset.x + 250} ${offset.y + 220}) scale(${zoom}) translate(-250 -220)`}>
          {relations.map(r => { const a = points.get(r.subject), b = points.get(r.object); return a && b ? <g key={r.id}><line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={changed.includes(r.id) ? '#b87920' : 'currentColor'} /><text className="graph-edge-label" x={(a.x+b.x)/2} y={(a.y+b.y)/2}><title>{r.predicate}</title>{r.predicate}</text></g> : null; })}
          {entities.map(e => { const p = points.get(e.id)!; return <g key={e.id}>{e.kind === 'character' ? <circle cx={p.x} cy={p.y} r="12" fill={changed.includes(e.id) ? '#b87920' : '#456a68'} /> : <rect x={p.x-11} y={p.y-11} width="22" height="22" transform={e.kind === 'object' ? `rotate(45 ${p.x} ${p.y})` : undefined} fill={changed.includes(e.id) ? '#b87920' : '#456a68'} />}<text x={p.x} y={p.y + 30} textAnchor="middle">{e.name}</text></g>; })}
        </g>
      </svg>
      {!entities.length && <p>尚无有来源支持的实体。</p>}
      <ul>{entities.map(e => <li key={e.id}>{kinds[e.kind]}：{e.name} <button onClick={event => { sourceTrigger.current = event.currentTarget; setSource(e.source); }}>查看来源</button></li>)}</ul>
      <ul>{relations.map(r => <li key={r.id}>{entities.find(e => e.id === r.subject)?.name} → {r.predicate} → {entities.find(e => e.id === r.object)?.name} <button onClick={event => { sourceTrigger.current = event.currentTarget; setSource(r.source); }}>查看关系来源</button></li>)}</ul>
      {source && <section aria-label="事实来源" tabIndex={0} ref={sourceBox}><h4>事实来源</h4><p>{graph.content?.sources[source]?.text ?? '来源不可用'}</p><button onClick={() => { setSource(''); sourceTrigger.current?.focus(); }}>关闭来源</button></section>}
    </>}
  </aside>;
}
