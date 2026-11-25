/** 第三阶段运行状态：runId 守卫、详情优先、事件只唤醒与记日志。 */

export type SimulationPhase =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled'

export type SimulationEngine = 'double' | 'oasis'

export interface SimulationActionTarget {
  actionId: string
  content: string
  actorRoleName: string
  roundIndex: number
  simulatedAt: string | null
}

export interface SimulationAction {
  actionId: string
  platformKey: string
  nativePlatform: string
  nativeAction: string
  roundIndex: number
  actionSeq: number
  actorRoleId: string
  actorRoleName: string
  content: string
  engineRecordId: string
  targetEngineRecordId: string | null
  targetActionId: string | null
  targetNativeAction: string | null
  targetResolved: boolean
  target: SimulationActionTarget | null
  simulatedAt: string | null
}

export interface SimulationPlatformView {
  platformKey: string
  nativePlatform: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'canceled'
  roundsCompleted: number
  roundsTotal: number
  actionCount: number
  byAction: Record<string, number>
  failure: { code: string } | null
  simulatedAt: string | null
}

export interface SimulationDetail {
  runId: string
  jobId: string
  selectionId: string
  preparationId: string
  buildId: string
  snapshotId: string
  question: string
  engine: SimulationEngine
  phase: SimulationPhase
  terminal: boolean
  partial: boolean
  failedPlatforms: string[]
  failure: { code: string } | null
  rounds: number
  minutesPerRound: number
  simulatedStart: string
  simulatedAt: string
  platforms: SimulationPlatformView[]
  counts: { total: number; byAction: Record<string, number> }
  memory?: SimulationMemorySummary
  canStop: boolean
  stopRequested: boolean
  cleanup: { code: string } | null
  createdAt: string
  finishedAt: string | null
}

export interface SimulationSummary {
  runId: string
  selectionId: string
  engine: SimulationEngine
  phase: SimulationPhase
  rounds: number
  terminal: boolean
  failedPlatforms: string[]
  createdAt: string
  finishedAt: string | null
}

export interface SimulationActionPage {
  items: SimulationAction[]
  nextAfter: number
}

export interface SimulationEvent {
  runId: string
  seq: number
  type: 'progress' | 'completed' | 'failed' | 'canceled'
  simulatedAt: string
  payload: {
    simulationId?: string
    selectionId?: string
    step?: number
    stepKey?: string
    roundIndex?: number
    platformKey?: string
    nativePlatform?: string
    roundsCompleted?: number
    actionCount?: number
    nativeActionCounts?: Record<string, number>
    reason?: string | null
  }
}

export interface SimulationLogLine {
  seq: number
  type: string
  simulatedAt: string
  text: string
}

export interface SimulationState {
  selectionId: string | null
  runId: string | null
  detail: SimulationDetail | null
  actions: SimulationAction[]
  nextAfter: number
  logs: SimulationLogLine[]
  lastSeq: number
  stopRequested: boolean
  streamEnded: boolean
}

export function createSimulationState(selectionId: string | null): SimulationState {
  return {
    selectionId,
    runId: null,
    detail: null,
    actions: [],
    nextAfter: 0,
    logs: [],
    lastSeq: 0,
    stopRequested: false,
    streamEnded: false,
  }
}

/** 用户主动切换运行：清空上一条运行的动作、日志与游标，但保留选择身份。 */
export function switchRun(state: SimulationState, runId: string): SimulationState {
  return { ...createSimulationState(state.selectionId), runId }
}

/** 详情只接受同一 runId；首次加载允许为空 runId。 */
export function detailMatches(
  state: SimulationState,
  detail: SimulationDetail,
): boolean {
  if (state.selectionId && detail.selectionId !== state.selectionId) {
    return false
  }
  return state.runId === null || state.runId === detail.runId
}

export function applyDetail(
  state: SimulationState,
  detail: SimulationDetail,
  actions: SimulationAction[] = [],
  nextAfter = 0,
): SimulationState {
  if (!detailMatches(state, detail)) {
    return state
  }
  const merged = mergeActions(state.runId === detail.runId ? state.actions : [], actions)
  return {
    ...state,
    runId: detail.runId,
    detail,
    actions: merged,
    nextAfter: state.runId === detail.runId ? Math.max(state.nextAfter, nextAfter) : nextAfter,
    stopRequested:
      state.runId === detail.runId
        ? state.stopRequested || detail.stopRequested
        : detail.stopRequested,
    streamEnded: detail.terminal,
  }
}

export function mergeActions(
  existing: SimulationAction[],
  incoming: SimulationAction[],
): SimulationAction[] {
  const byId = new Map<string, SimulationAction>()
  for (const action of existing) {
    byId.set(action.actionId, action)
  }
  for (const action of incoming) {
    byId.set(action.actionId, action)
  }
  return [...byId.values()].sort((left, right) => left.actionSeq - right.actionSeq)
}

export function appendActionPage(
  state: SimulationState,
  runId: string,
  page: SimulationActionPage,
): SimulationState {
  if (state.runId !== runId) {
    return state
  }
  return {
    ...state,
    actions: mergeActions(state.actions, page.items),
    nextAfter: Math.max(state.nextAfter, page.nextAfter),
  }
}

export function selectionOf(detail: SimulationDetail | null): string | null {
  return detail ? detail.selectionId : null
}

/** 事件只做唤醒与日志：返回是否需要重新读取运行详情。 */
export function eventBelongsTo(
  state: SimulationState,
  event: SimulationEvent,
): boolean {
  if (state.runId === null) {
    return false
  }
  if (event.runId !== state.runId) {
    return false
  }
  const selectionId = event.payload?.selectionId
  if (state.selectionId && selectionId && selectionId !== state.selectionId) {
    return false
  }
  return true
}

export function eventLogLine(event: SimulationEvent, lang: string): string {
  const payload = event.payload ?? {}
  const parts: string[] = []
  if (event.type === 'progress') {
    if (payload.roundIndex) {
      parts.push(lang === 'en' ? `Round ${payload.roundIndex}` : `第 ${payload.roundIndex} 轮`)
    }
    if (payload.platformKey) {
      parts.push(platformLabel(payload.platformKey, lang))
    }
    if (typeof payload.actionCount === 'number') {
      parts.push(
        lang === 'en'
          ? `${payload.actionCount} actions`
          : `${payload.actionCount} 个动作`,
      )
    }
    if (payload.stepKey && !payload.roundIndex) {
      parts.push(String(payload.stepKey))
    }
  } else {
    parts.push(
      event.type === 'completed'
        ? lang === 'en'
          ? 'Run finished'
          : '运行完成'
        : event.type === 'canceled'
          ? lang === 'en'
            ? 'Run stopped'
            : '运行已停止'
          : lang === 'en'
            ? 'Run failed'
            : '运行失败',
    )
    if (payload.reason) {
      parts.push(String(payload.reason))
    }
  }
  return parts.join(' · ')
}

export function applyEvent(
  state: SimulationState,
  event: SimulationEvent,
  lang: string,
): { state: SimulationState; needsRefresh: boolean } {
  if (!eventBelongsTo(state, event)) {
    return { state, needsRefresh: false }
  }
  const line: SimulationLogLine = {
    seq: event.seq,
    type: event.type,
    simulatedAt: event.simulatedAt,
    text: eventLogLine(event, lang),
  }
  const logs =
    event.seq > state.lastSeq
      ? [...state.logs.filter((item) => item.seq !== event.seq), line]
          .sort((left, right) => left.seq - right.seq)
          .slice(-200)
      : state.logs
  return {
    state: {
      ...state,
      logs,
      lastSeq: Math.max(state.lastSeq, event.seq),
      streamEnded: state.streamEnded || event.type !== 'progress',
    },
    needsRefresh: true,
  }
}

export function shouldStopStream(state: SimulationState): boolean {
  return state.detail?.terminal === true || state.streamEnded
}

export function markStopRequested(state: SimulationState): SimulationState {
  return { ...state, stopRequested: true }
}

export function canStart(detail: SimulationDetail | null): boolean {
  return detail === null
}

export function countMismatch(state: SimulationState): boolean {
  const detail = state.detail
  if (!detail) {
    return false
  }
  const total = detail.platforms.reduce(
    (sum, platform) => sum + platform.actionCount,
    0,
  )
  return total !== detail.counts.total
}

export function simulatedClock(
  iso: string | null,
  lang: string,
  startIso?: string | null,
): string {
  if (!iso) {
    return '—'
  }
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) {
    return '—'
  }
  const hours = String(parsed.getUTCHours()).padStart(2, '0')
  const minutes = String(parsed.getUTCMinutes()).padStart(2, '0')
  let day = 1
  if (startIso) {
    const start = new Date(startIso)
    if (!Number.isNaN(start.getTime())) {
      day = Math.floor((parsed.getTime() - start.getTime()) / 86_400_000) + 1
    }
  }
  return lang === 'en'
    ? `Day ${day} ${hours}:${minutes}`
    : `第 ${day} 天 ${hours}:${minutes}`
}

export function platformLabel(platformKey: string, lang: string): string {
  const zh: Record<string, string> = {
    platform_a: '信息广场',
    platform_b: '话题社区',
  }
  const en: Record<string, string> = {
    platform_a: 'Info Plaza',
    platform_b: 'Topic Community',
  }
  return (lang === 'en' ? en : zh)[platformKey] ?? platformKey
}

export function nativePlatformLabel(nativePlatform: string): string {
  return nativePlatform === 'twitter' ? 'twitter' : 'reddit'
}

const PLATFORM_ACTION_LABELS: Record<
  string,
  { zh: string; en: string }
> = {
  'platform_a:create_post': { zh: '发帖', en: 'Post' },
  'platform_a:repost': { zh: '转发', en: 'Repost' },
  'platform_a:like_post': { zh: '点赞', en: 'Like' },
  'platform_b:create_post': { zh: '发主题', en: 'New topic' },
  'platform_b:create_comment': { zh: '评论', en: 'Comment' },
  'platform_b:like_post': { zh: '投票赞成', en: 'Upvote' },
  'platform_b:dislike_post': { zh: '投票反对', en: 'Downvote' },
}

/** 动作名按平台显示：同一原生动作在两个平台的界面词不同。 */
export function actionLabel(
  nativeAction: string,
  lang: string,
  platformKey = 'platform_a',
): string {
  const entry =
    PLATFORM_ACTION_LABELS[`${platformKey}:${nativeAction}`] ??
    PLATFORM_ACTION_LABELS[`platform_a:${nativeAction}`]
  if (!entry) {
    return nativeAction
  }
  return lang === 'en' ? entry.en : entry.zh
}

export function engineLabel(engine: string, lang: string): string {
  if (engine === 'oasis') {
    return lang === 'en' ? 'Local OASIS run' : '真实本地 OASIS 运行'
  }
  return lang === 'en' ? 'Offline double run' : '离线替身运行'
}

export function phaseLabel(phase: string, lang: string): string {
  const labels: Record<string, { zh: string; en: string }> = {
    queued: { zh: '排队中', en: 'Queued' },
    running: { zh: '运行中', en: 'Running' },
    succeeded: { zh: '已完成', en: 'Completed' },
    failed: { zh: '已失败', en: 'Failed' },
    canceled: { zh: '已停止', en: 'Stopped' },
  }
  const entry = labels[phase]
  return entry ? (lang === 'en' ? entry.en : entry.zh) : phase
}

export function stopLabel(state: SimulationState, lang: string): string {
  if (state.detail?.terminal) {
    return lang === 'en' ? 'Stopped' : '已停止'
  }
  if (state.stopRequested) {
    return lang === 'en' ? 'Stop requested' : '停止已请求'
  }
  return lang === 'en' ? 'Stop run' : '停止运行'
}

/** 第三阶段时序记忆：按轮视图、来源与迟到响应守卫。 */
export type MemoryStatus =
  | 'unconfigured'
  | 'pending'
  | 'syncing'
  | 'complete'
  | 'partial'
  | 'unconfirmed'
  | 'unavailable'

export interface MemorySource {
  memoryId: string
  actionId: string
  facet: 'role_speech' | 'simulation_result'
  episodeId: string | null
  platformKey: string
  roundIndex: number
  simulatedAt: string | null
  sourceEventSeq: number
  actorRoleId?: string
  actorRoleName?: string
  nativeAction?: string
  targetActionId?: string | null
  content?: string
}

export interface MemoryNode {
  key: string
  nodeType: string
  label: string
  origin: 'material' | 'role' | 'simulation'
  summary?: string | null
  sources?: MemorySource[]
  sourceCount?: number
  firstRound?: number | null
  lastRound?: number | null
}

export interface MemoryEdge {
  key: string
  edgeType: string
  label: string
  origin: 'material' | 'role' | 'simulation'
  summary?: string | null
  sourceKey?: string
  targetKey?: string
  sources?: MemorySource[]
  sourceCount?: number
  firstRound?: number | null
  lastRound?: number | null
}

export interface MemoryEntry {
  memoryId: string
  actionId: string
  platformKey: string
  roundIndex: number
  actionSeq: number
  simulatedAt: string | null
  sourceEventSeq: number
  actorRoleId: string
  actorRoleName: string
  nativeAction: string
  speechText: string
  targetActionId: string | null
  targetEngineRecordId: string | null
  episodeId: string | null
  graphState: string
  writeOutcome: string
  facets: string[]
}

export interface SimulationMemorySummary {
  status: MemoryStatus
  pendingEpisodes: number
  unconfirmedEpisodes: number
  lastSyncedRound: number
  reason: string | null
  memoryJobId: string | null
}

export interface SimulationMemoryView {
  runId: string
  round: number
  platform: string | null
  cutoff: {
    round: number
    availableMaxRound: number
    platforms: Record<string, { completed: number }>
  }
  status: SimulationMemorySummary
  material: {
    snapshotId: string
    nodes: MemoryNode[]
    edges: MemoryEdge[]
  }
  runtime: {
    nodes: MemoryNode[]
    edges: MemoryEdge[]
  }
  entries: MemoryEntry[]
}

export interface MemoryViewState {
  memory: SimulationMemoryView | null
  loading: boolean
  error: string
}

export function createMemoryState(): MemoryViewState {
  return { memory: null, loading: false, error: '' }
}

export function memoryMatches(
  _state: MemoryViewState,
  view: SimulationMemoryView,
  expected: { runId: string | null; round: number | null; platform: string | null },
): boolean {
  if (expected.runId && view.runId !== expected.runId) {
    return false
  }
  if (expected.round !== null && view.round !== expected.round) {
    return false
  }
  if (expected.platform !== null && view.platform !== expected.platform) {
    return false
  }
  return true
}

export function applyMemoryView(
  state: MemoryViewState,
  view: SimulationMemoryView,
  expected: { runId: string | null; round: number | null; platform: string | null },
): MemoryViewState {
  if (!memoryMatches(state, view, expected)) {
    return state
  }
  return { memory: view, loading: false, error: '' }
}

export function switchMemoryView(
  state: MemoryViewState,
  expected: { runId: string | null; round: number | null; platform: string | null },
): MemoryViewState {
  if (
    state.memory &&
    !memoryMatches(state, state.memory, expected)
  ) {
    return { memory: null, loading: true, error: '' }
  }
  return { ...state, loading: true, error: '' }
}

export function memoryStatusLabel(status: string, lang: string): string {
  const labels: Record<string, { zh: string; en: string }> = {
    unconfigured: { zh: '未配置图谱', en: 'Graph not configured' },
    pending: { zh: '记忆待同步', en: 'Memory pending' },
    syncing: { zh: '记忆同步中', en: 'Syncing memory' },
    complete: { zh: '记忆已完成', en: 'Memory complete' },
    partial: { zh: '部分记忆可用', en: 'Partial memory' },
    unconfirmed: { zh: '写入待确认', en: 'Write unconfirmed' },
    unavailable: { zh: '记忆同步暂不可用', en: 'Memory unavailable' },
  }
  const entry = labels[status]
  return entry ? (lang === 'en' ? entry.en : entry.zh) : status
}

export function memoryOriginLabel(origin: string, lang: string): string {
  const labels: Record<string, { zh: string; en: string }> = {
    material: { zh: '材料事实', en: 'Material fact' },
    role: { zh: '角色发言', en: 'Role speech' },
    simulation: { zh: '推演结果', en: 'Simulation result' },
  }
  const entry = labels[origin]
  return entry ? (lang === 'en' ? entry.en : entry.zh) : origin
}

export function memorySourceLabel(source: MemorySource, lang: string): string {
  const facet =
    source.facet === 'role_speech'
      ? lang === 'en'
        ? 'role speech'
        : '角色发言'
      : lang === 'en'
        ? 'simulation result'
        : '推演结果'
  return `${facet} · R${source.roundIndex} · #${source.sourceEventSeq}`
}
