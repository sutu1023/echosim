import { useEffect, useState, useRef } from 'react';
import { request } from './Workspace';
import { states, taskErrors, type Task } from './StoryPreparation';

type Draft = { title: string; body: string; version: number };
type Job = Task & {
  version: number;
  text?: string;
  conflict?: string;
  changes?: string[];
  candidate?: Draft;
};
type Chapter = { draft: Draft | null; decision: string; job: Job | null };

export function Chapters({ workId, csrf }: { workId: string; csrf: string }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [id, setId] = useState(
    new URLSearchParams(location.search).get('simulation') || '',
  );
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let live = true;
    request('/works/' + workId + '/preparations')
      .then((data) => {
        if (!live) return;
        const rows = (data.tasks as Task[]).filter(
          (t) =>
            t.kind === 'simulation' &&
            ['pending', 'checking', 'adopted', 'discarded'].includes(
              t.decision || '',
            ),
        );
        setTasks(rows);
        setId((old) => old || rows[0]?.id || '');
        setLoaded(true);
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [workId]);
  return (
    <section className="panel">
      <label>
        推演记录
        <select value={id} onChange={(e) => setId(e.target.value)}>
          {tasks.map((t, i) => (
            <option key={t.id} value={t.id}>
              记录 {tasks.length - i} · {states[t.state]}
            </option>
          ))}
        </select>
      </label>
      {error && <p role="alert">{error}</p>}
      {id ? (
        <Editor key={id} sim={id} work={workId} csrf={csrf} />
      ) : (
        <p>{loaded ? '暂无可成章的推演。' : '正在加载…'}</p>
      )}
    </section>
  );
}

function Editor({
  sim,
  work,
  csrf,
}: {
  sim: string;
  work: string;
  csrf: string;
}) {
  const prefix = '/works/' + work + '/simulations/' + sim;
  const [state, setState] = useState<Chapter | null>(null);
  const [title, setTitle] = useState(''),
    [body, setBody] = useState('');
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const titleRef = useRef(title),
    bodyRef = useRef(body);
  titleRef.current = title;
  bodyRef.current = body;
  const [dismissed, setDismissed] = useState(''),
    [ack, setAck] = useState(false);
  const version = useRef(0),
    dirty = useRef(false);
  const [epoch, reload] = useState(0);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const next: Chapter = await request(prefix + '/chapters');
        if (!live) return;
        if (
          next.draft &&
          version.current !== next.draft.version &&
          !dirty.current
        ) {
          setTitle(next.draft.title);
          setBody(next.draft.body);
          version.current = next.draft.version;
        }
        if (
          next.job?.kind === 'chapter_polish' &&
          next.job.state === 'failed' &&
          next.draft?.version === version.current
        ) {
          dirty.current =
            titleRef.current !== next.draft.title ||
            bodyRef.current !== next.draft.body;
        }
        setState(next);
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : '读取失败');
      }
      if (live) timer = setTimeout(load, 750);
    }
    void load();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [prefix, epoch]);
  const job = state?.job;
  const locked =
    busy ||
    ['queued', 'running'].includes(job?.state || '') ||
    state?.decision === 'checking';
  const draft = state?.draft;
  const readonly = state?.decision === 'discarded';
  const review =
    job?.state === 'succeeded' &&
    job.kind === 'chapter_review' &&
    job.version === draft?.version;
  const candidate =
    job?.state === 'succeeded' &&
    job.version === draft?.version &&
    dismissed !== job.id &&
    job.candidate;
  async function act(action: string) {
    if (locked || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError('');
    const adopt = action === 'adopt';
    try {
      await request(prefix + (adopt ? '/adoptions' : '/chapters'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrf,
          'Idempotency-Key': crypto.randomUUID(),
        },
        body: JSON.stringify(
          adopt
            ? {
                expected_canon_revision: (await request(prefix)).canon_revision,
                acknowledge_irreversible: ack,
                review_id: job?.id,
              }
            : {
                action,
                version: version.current,
                ...(action === 'save' ? { title, body } : {}),
                ...(action === 'replace' ? { candidate: job?.id } : {}),
              },
        ),
      });
      if (action === 'save' || action === 'replace') dirty.current = false;
      setAck(false);
      reload((e) => e + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  if (!state) return <p role="status">{error || '正在读取章节…'}</p>;
  return (
    <section>
      <a href={'/works/' + work + '/simulation'}>返回推演现场</a>
      {error && <p role="alert">{error}</p>}
      {job && (
        <p role="status">
          {states[job.state]}{' '}
          {job.error_code &&
            (job.conflict || taskErrors[job.error_code] || '操作未完成')}
        </p>
      )}
      {job?.state === 'running' && job.text && (
        <article style={{ whiteSpace: 'pre-wrap' }}>{job.text}</article>
      )}
      {!readonly && (
        <button
          disabled={locked || dirty.current}
          onClick={() => void act('generate')}
        >
          {draft ? '重新生成' : '生成章节'}
        </button>
      )}
      {candidate && !readonly && (
        <article>
          <h3>新稿预览 · {candidate.title}</h3>
          <p style={{ whiteSpace: 'pre-wrap' }}>{candidate.body}</p>
          <button
            disabled={locked || dirty.current}
            onClick={() => void act('replace')}
          >
            替换原稿
          </button>
          <button onClick={() => setDismissed(job!.id)}>关闭预览</button>
        </article>
      )}
      {dirty.current && draft && version.current !== draft.version && (
        <p role="alert">
          已有较新的保存稿。
          <button
            onClick={() => {
              dirty.current = false;
              setTitle(draft.title);
              setBody(draft.body);
              version.current = draft.version;
              setError('');
            }}
          >
            载入保存稿
          </button>
        </p>
      )}
      {draft && (
        <>
          <label>
            章节标题
            <input
              value={title}
              readOnly={readonly || locked}
              onChange={(e) => {
                dirty.current = true;
                setTitle(e.target.value);
              }}
            />
          </label>
          <label>
            章节正文
            <textarea
              rows={16}
              value={body}
              readOnly={readonly || locked}
              onChange={(e) => {
                dirty.current = true;
                setBody(e.target.value);
              }}
            />
          </label>
          {!readonly && (
            <button
              disabled={
                locked || !dirty.current || !title.trim() || !body.trim()
              }
              onClick={() => void act('save')}
            >
              保存草稿
            </button>
          )}
          <a href={'/api/v1' + prefix + '/chapters/markdown'} download>
            下载 Markdown
          </a>
          {state.decision === 'pending' && (
            <button
              disabled={locked || dirty.current}
              onClick={() => void act('review')}
            >
              核对修改稿
            </button>
          )}
          {review && state.decision === 'pending' && (
            <section>
              <h3>将采纳的事件</h3>
              <ol>
                {job.changes?.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ol>
              <label>
                <input
                  type="checkbox"
                  checked={ack}
                  onChange={(e) => setAck(e.target.checked)}
                />
                确认整次采纳且不可撤销
              </label>
              <button
                disabled={locked || dirty.current || !ack}
                onClick={() => void act('adopt')}
              >
                采纳修改稿
              </button>
            </section>
          )}
        </>
      )}
      {readonly && <p>本次推演已放弃，章节只读。</p>}
      {state.decision === 'adopted' && <p>剧情已采纳，可润色章节措辞。</p>}
      {state.decision === 'checking' && <p>正在提交采纳。</p>}
    </section>
  );
}
