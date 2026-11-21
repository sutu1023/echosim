/** 同域 SSE 客户端：把帧交给 job-log 的同一 reducer，并在终态后停止重连。 */

import type { JobEvent } from './job-log'

export type JobStreamStatus =
  | 'connecting'
  | 'live'
  | 'reconnecting'
  | 'interrupted'
  | 'ended'

export interface JobStreamOptions {
  projectId: string
  jobId: string
  after: number
  onStatus: (status: JobStreamStatus) => void
  onEvent: (event: JobEvent) => void
  onEnd: (payload: { runId: string; lastSeq: number }) => void
}

export interface JobStreamHandle {
  close: () => void
}

export function eventsUrl(
  projectId: string,
  jobId: string,
  after: number,
): string {
  const base = `/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobId)}/events`
  return `${base}?after=${Math.max(0, Math.trunc(after))}`
}

export function openJobStream(options: JobStreamOptions): JobStreamHandle {
  const source = new EventSource(
    eventsUrl(options.projectId, options.jobId, options.after),
    { withCredentials: true },
  )
  let ended = false
  let closed = false

  options.onStatus('connecting')

  source.addEventListener('open', () => {
    if (!ended && !closed) {
      options.onStatus('live')
    }
  })

  source.addEventListener('run-event', (event) => {
    if (closed || ended) {
      return
    }
    try {
      options.onEvent(JSON.parse((event as MessageEvent).data) as JobEvent)
    } catch {
      options.onStatus('interrupted')
    }
  })

  source.addEventListener('stream-end', (event) => {
    ended = true
    let payload = { runId: '', lastSeq: 0 }
    try {
      payload = JSON.parse((event as MessageEvent).data) as {
        runId: string
        lastSeq: number
      }
    } catch {
      // 结束帧解析失败不影响关闭连接
    }
    options.onStatus('ended')
    options.onEnd(payload)
    source.close()
  })

  source.addEventListener('stream-error', () => {
    if (!ended) {
      options.onStatus('interrupted')
    }
    source.close()
  })

  source.onerror = () => {
    if (closed || ended) {
      return
    }
    options.onStatus(
      source.readyState === EventSource.CONNECTING
        ? 'reconnecting'
        : 'interrupted',
    )
  }

  return {
    close() {
      closed = true
      source.close()
    },
  }
}
