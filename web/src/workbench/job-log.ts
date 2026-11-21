/**
 * run 日志的唯一处理逻辑：历史补发与实时推送都经过 `applyJobEvent`。
 *
 * `cursor` 始终是“已完整处理的最后一个连续 seq”，重连与重新同步都用它作为
 * `after`，而不是已收到的最大 seq；乱序帧先进入 `pending`，等缺口补齐后再连续释放。
 */

export type JobEventType = 'progress' | 'failed' | 'canceled' | 'completed'

export interface JobEvent {
  runId: string
  seq: number
  type: JobEventType
  stage: string
  simulatedAt: string
  payload: Record<string, unknown>
}

export interface JobLogState {
  runId: string | null
  cursor: number
  events: JobEvent[]
  pending: Map<number, JobEvent>
  gap: boolean
  ended: boolean
}

const TERMINAL_TYPES: readonly JobEventType[] = [
  'failed',
  'canceled',
  'completed',
]

export function isTerminalType(type: JobEventType): boolean {
  return TERMINAL_TYPES.includes(type)
}

export function createJobLog(runId: string | null = null): JobLogState {
  return {
    runId,
    cursor: 0,
    events: [],
    pending: new Map<number, JobEvent>(),
    gap: false,
    ended: false,
  }
}

export function applyJobEvent(
  state: JobLogState,
  incoming: JobEvent,
): JobLogState {
  if (state.runId !== null && incoming.runId !== state.runId) {
    return state
  }
  if (state.ended) {
    // 终态已处理：后续帧（迟到或重复）不得再追加日志。
    return state
  }
  if (!Number.isInteger(incoming.seq) || incoming.seq < 1) {
    return state
  }
  if (incoming.seq <= state.cursor) {
    return state
  }
  if (state.pending.has(incoming.seq)) {
    return state
  }

  const pending = new Map(state.pending)
  pending.set(incoming.seq, incoming)
  const events = state.events.slice()
  let cursor = state.cursor
  let ended: boolean = state.ended

  while (pending.has(cursor + 1)) {
    const next = pending.get(cursor + 1) as JobEvent
    pending.delete(cursor + 1)
    events.push(next)
    cursor += 1
    if (isTerminalType(next.type)) {
      ended = true
    }
  }

  return {
    runId: state.runId ?? incoming.runId,
    cursor,
    events,
    pending,
    gap: pending.size > 0,
    ended,
  }
}

export function applyJobEvents(
  state: JobLogState,
  incoming: readonly JobEvent[],
): JobLogState {
  return incoming.reduce(applyJobEvent, state)
}

export function pendingSeqs(state: JobLogState): number[] {
  return [...state.pending.keys()].sort((a, b) => a - b)
}
