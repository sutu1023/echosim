/**
 * 测试任务面板的纯状态选择：把“当前应该显示哪条提示”与 DOM 解耦。
 *
 * 关键规则：SSE 重连提示优先于后台轮询错误，避免一次 503 把“正在从第 N 条重连”
 * 覆盖掉；恢复读取成功后由调用方清空对应错误。
 */

import type { JobEventType } from './job-log'
import type { JobStreamStatus } from './job-stream'

export type BannerSource =
  | 'failed'
  | 'canceled'
  | 'stream'
  | 'error'
  | 'poll'
  | 'takeover'
  | 'gap'
  | null

export interface BannerFlags {
  terminalType: JobEventType | null
  streamStatus: JobStreamStatus
  error: string
  pollError: string
  awaitingTakeover: boolean
  gap: boolean
}

const STREAM_PENDING: readonly JobStreamStatus[] = ['reconnecting', 'interrupted']

export function pickBannerSource(flags: BannerFlags): BannerSource {
  if (flags.terminalType === 'failed') {
    return 'failed'
  }
  if (flags.terminalType === 'canceled') {
    return 'canceled'
  }
  if (STREAM_PENDING.includes(flags.streamStatus)) {
    return 'stream'
  }
  if (flags.error) {
    return 'error'
  }
  if (flags.pollError) {
    return 'poll'
  }
  if (flags.awaitingTakeover) {
    return 'takeover'
  }
  if (flags.gap) {
    return 'gap'
  }
  return null
}

/** 读取成功后清空的错误：列表读取、状态轮询与动作错误都在此收敛。 */
export function clearedOnReadSuccess(): { error: string; pollError: string } {
  return { error: '', pollError: '' }
}
