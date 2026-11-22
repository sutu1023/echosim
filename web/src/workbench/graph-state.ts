/** 证据图谱面板的纯状态：buildId 守卫、阶段合并与迟到响应丢弃。 */

export type GraphPhase =
  | 'preparing'
  | 'waiting_index'
  | 'drawing'
  | 'ready'
  | 'failed'
  | 'canceled'

export interface GraphLocator {
  kind: 'pdf' | 'text'
  url: string
  pageNo?: number
  sourceStart?: number
  sourceEnd?: number
}

export interface GraphSource {
  documentId: string
  documentName: string
  kind: string
  chunkIndex: number
  pageNo: number | null
  paragraphStart: number | null
  paragraphEnd: number | null
  sourceStart: number
  sourceEnd: number
  quote: string
  locator: GraphLocator
}

export interface GraphNodeData {
  key: string
  nodeType: string
  label: string
  origin: string
  summary: string | null
  sources: GraphSource[]
}

export interface GraphEdgeData {
  key: string
  edgeType: string
  label: string
  origin: string
  summary: string | null
  sourceKey: string
  targetKey: string
  sources: GraphSource[]
}

export interface GraphSnapshotData {
  snapshotId: string
  buildId: string
  revision: number
  complete: boolean
  published: boolean
  nodeCount: number
  edgeCount: number
  episodeCount: number
  nodes: GraphNodeData[]
  edges: GraphEdgeData[]
}

export interface GraphBuildData {
  buildId: string
  projectId: string
  runId: string
  jobId: string
  question: string
  phase: GraphPhase
  status: string
  plannedEpisodes: number
  acceptedEpisodes: number
  confirmedEpisodes: number
  canContinue: boolean
  failure: { code: string; message: string } | null
}

export interface GraphPayload {
  build: GraphBuildData
  snapshot: GraphSnapshotData | null
}

export interface GraphEventPayload {
  buildId?: string
  phase?: GraphPhase
  plannedEpisodes?: number
  acceptedEpisodes?: number
  confirmedEpisodes?: number
  snapshotRevision?: number
  nodeCount?: number
  edgeCount?: number
  canContinue?: boolean
  reason?: string
}

export interface GraphState {
  buildId: string | null
  runId: string | null
  jobId: string | null
  phase: GraphPhase | null
  status: string | null
  plannedEpisodes: number
  acceptedEpisodes: number
  confirmedEpisodes: number
  canContinue: boolean
  snapshotRevision: number | null
  nodeCount: number
  edgeCount: number
  failureCode: string | null
  failureMessage: string | null
  snapshot: GraphSnapshotData | null
  stale: boolean
}

export function createGraphState(): GraphState {
  return {
    buildId: null,
    runId: null,
    jobId: null,
    phase: null,
    status: null,
    plannedEpisodes: 0,
    acceptedEpisodes: 0,
    confirmedEpisodes: 0,
    canContinue: false,
    snapshotRevision: null,
    nodeCount: 0,
    edgeCount: 0,
    failureCode: null,
    failureMessage: null,
    snapshot: null,
    stale: false,
  }
}

function buildMatches(state: GraphState, buildId: string): boolean {
  return state.buildId === null || state.buildId === buildId
}

export function applyGraphPayload(
  state: GraphState,
  payload: GraphPayload,
  force = false,
): GraphState {
  const build = payload.build
  if (!force && !buildMatches(state, build.buildId)) {
    return { ...state, stale: true }
  }
  const sameBuild = state.buildId === build.buildId
  const snapshot =
    payload.snapshot && payload.snapshot.buildId === build.buildId
      ? payload.snapshot
      : null
  return {
    buildId: build.buildId,
    runId: build.runId,
    jobId: build.jobId,
    phase: build.phase,
    status: build.status,
    plannedEpisodes: build.plannedEpisodes,
    acceptedEpisodes: build.acceptedEpisodes,
    confirmedEpisodes: build.confirmedEpisodes,
    canContinue: build.canContinue,
    snapshotRevision: snapshot
      ? snapshot.revision
      : sameBuild
        ? state.snapshotRevision
        : null,
    nodeCount: snapshot ? snapshot.nodeCount : sameBuild ? state.nodeCount : 0,
    edgeCount: snapshot ? snapshot.edgeCount : sameBuild ? state.edgeCount : 0,
    failureCode: build.failure?.code ?? null,
    failureMessage: build.failure?.message ?? null,
    snapshot,
    stale: false,
  }
}

export function applyGraphSnapshot(
  state: GraphState,
  snapshot: GraphSnapshotData,
): GraphState {
  if (state.buildId !== null && snapshot.buildId !== state.buildId) {
    return { ...state, stale: true }
  }
  return {
    ...state,
    snapshot,
    snapshotRevision: snapshot.revision,
    nodeCount: snapshot.nodeCount,
    edgeCount: snapshot.edgeCount,
  }
}

export function applyGraphEvent(
  state: GraphState,
  payload: GraphEventPayload,
): GraphState {
  if (payload.buildId && !buildMatches(state, payload.buildId)) {
    return { ...state, stale: true }
  }
  return {
    ...state,
    buildId: state.buildId ?? payload.buildId ?? null,
    phase: payload.phase ?? state.phase,
    plannedEpisodes: payload.plannedEpisodes ?? state.plannedEpisodes,
    acceptedEpisodes: payload.acceptedEpisodes ?? state.acceptedEpisodes,
    confirmedEpisodes: payload.confirmedEpisodes ?? state.confirmedEpisodes,
    snapshotRevision: payload.snapshotRevision ?? state.snapshotRevision,
    nodeCount: payload.nodeCount ?? state.nodeCount,
    edgeCount: payload.edgeCount ?? state.edgeCount,
    canContinue: payload.canContinue ?? state.canContinue,
    failureCode: payload.phase === 'failed' ? payload.reason ?? 'graph_unavailable' : state.failureCode,
    stale: false,
  }
}

export function isGraphActive(state: GraphState): boolean {
  return state.phase === 'preparing' || state.phase === 'waiting_index' || state.phase === 'drawing'
}

export function graphViewMode(state: GraphState): 'waiting' | 'drawing' | 'ready' | 'failed' {
  if (state.phase === 'failed' || state.phase === 'canceled') {
    return 'failed'
  }
  if (state.phase === 'ready') {
    return 'ready'
  }
  if (state.snapshot && state.phase === 'drawing') {
    return 'drawing'
  }
  return 'waiting'
}

export function canOpenNextStage(state: GraphState): boolean {
  // 服务端 canContinue 与最终快照在同一发布事务写入；不额外用本地快照猜测。
  return state.phase === 'ready' && state.canContinue
}

/** 旧 build 查询地址：保留 layout/lang/document，并显式带上 build。 */
export function buildQuery(
  query: Record<string, unknown>,
  buildId: string | null,
): Record<string, string> {
  const next: Record<string, string> = {}
  for (const [key, value] of Object.entries(query)) {
    if (key === 'build' || typeof value !== 'string') {
      continue
    }
    next[key] = value
  }
  if (buildId) {
    next.build = buildId
  }
  return next
}
