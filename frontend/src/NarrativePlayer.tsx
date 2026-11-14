import { useEffect, useRef, useState } from 'react';
export type Segment = { segment_id: string; kind: string; text: string; speaker_id?: string };
export type NarrativeEvent = { id: string; sequence: number; content: { actor_id: string; segments?: Segment[]; action?: string; dialogue?: string; result?: string } };
function parts(event: NarrativeEvent): Segment[] {
  return event.content.segments ?? ['action', 'dialogue', 'result'].flatMap((key, i) => {
    const text = event.content[key as 'action' | 'dialogue' | 'result'];
    return text ? [{ segment_id: String(i), kind: key === 'dialogue' ? 'dialogue' : 'narration', speaker_id: event.content.actor_id, text }] : [];
  });
}
export function NarrativePlayer({ events, names }: { events: NarrativeEvent[]; names: Record<string, string> }) {
  const ordered = [...new Map(events.map(e => [e.id, e])).values()].sort((a, b) => a.sequence - b.sequence);
  const [shown, setShown] = useState<Record<string, number>>(() => Object.fromEntries(events.map(e => [e.id, parts(e).reduce((n, p) => n + Array.from(p.text).length, 0)])));
  const [following, setFollowing] = useState(true);
  const box = useRef<HTMLDivElement>(null);
  const seen = useRef({ events: ordered.length, chars: Object.values(shown).reduce((a, b) => a + b, 0) });
  const visibleChars = Object.values(shown).reduce((a, b) => a + b, 0);
  const unread = ordered.length > seen.current.events || visibleChars > seen.current.chars;
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
  const pending = ordered.find(e => (shown[e.id] ?? 0) < parts(e).reduce((n, p) => n + Array.from(p.text).length, 0));
  useEffect(() => {
    if (!pending) return;
    const length = parts(pending).reduce((n, p) => n + Array.from(p.text).length, 0);
    const timer = setInterval(() => setShown(old => ({ ...old, [pending.id]: reduced ? length : Math.min(length, (old[pending.id] ?? 0) + 1) })), 33);
    return () => clearInterval(timer);
  }, [pending?.id, reduced]);
  useEffect(() => { if (following && box.current) box.current.scrollTop = box.current.scrollHeight; }, [shown, following]);
  function reveal(all = false) {
    setShown(old => ({ ...old, ...Object.fromEntries((all ? ordered : pending ? [pending] : []).map(e => [e.id, parts(e).reduce((n, p) => n + Array.from(p.text).length, 0)])) }));
  }
  return <div className="narrative-player">
    <div className="story-flow" tabIndex={0} role="region" aria-label="剧情事件" ref={box} onScroll={() => { const el = box.current; if (el) { const next = el.scrollHeight - el.scrollTop - el.clientHeight < 48; if (following || next) seen.current = { events: ordered.length, chars: visibleChars }; setFollowing(next); } }}>
      {ordered.map(e => { let remaining = shown[e.id] ?? 0; return <article key={e.id} aria-label={`事件 ${e.sequence}`}>
        {parts(e).map(p => { const chars = Array.from(p.text); const count = Math.min(remaining, chars.length); remaining = Math.max(0, remaining - chars.length); return count > 0 ? <div key={p.segment_id} className={'story-segment ' + p.kind}>
          {p.kind === 'dialogue' && <strong>{names[p.speaker_id ?? ''] ?? '角色'}</strong>}
          <p aria-hidden={count < chars.length}>{chars.slice(0, count).join('')}</p>
        </div> : null; })}
      </article>; })}
    </div>
    <div className="actions playback-controls">
      {pending && <><button className="secondary" onClick={() => reveal()}>立即显示本事件</button><button className="quiet" onClick={() => reveal(true)}>显示剩余剧情</button></>}
      {!following && <button className="secondary" onClick={() => { seen.current = { events: ordered.length, chars: visibleChars }; setFollowing(true); }}>{unread ? '新内容 · 回到最新剧情' : '回到最新剧情'}</button>}
    </div>
    <span className="sr-only" role="status" aria-live="polite">{ordered.flatMap(e => { let left = shown[e.id] ?? 0; return parts(e).filter(p => { left -= Array.from(p.text).length; return left >= 0; }).map(p => p.text); }).slice(-1)}</span>
  </div>;
}
