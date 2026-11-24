/** 角色与环境面板的纯状态：build/preparation 守卫、迟到响应与分批预览合并。 */

export type EnvironmentPhase =
  | 'preparing'
  | 'ready'
  | 'failed'
  | 'canceled'

export interface EnvironmentLocator {
  kind: 'pdf' | 'text'
  url: string
  pageNo?: number
  sourceStart?: number
  sourceEnd?: number
}

export interface EnvironmentSource {
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
  locator: EnvironmentLocator
}

export interface EnvironmentTopicItem {
  text: string
  claimNature?: string | null
  claimNatureKnown?: boolean
  sources?: EnvironmentSource[]
}

export interface EnvironmentSimulation {
  origin?: string
  motive: string
  stance: string
  activeHours: number[]
  postingFrequency: number
  responseDelayMinutes: number
  influenceWeight: number
  platformKeys: string[]
  topics: { text: string }[]
  orderIndex?: number
  firstSpeaker?: boolean
  firstTopic?: string | null
}

export interface EnvironmentRole {
  roleId: string
  nodeKey: string
  displayName: string
  roleType: string
  batchIndex?: number
  previewReady?: boolean
  published?: boolean
  mention: { text: string; sources: EnvironmentSource[] }
  background: { text: string; stated: boolean; sources: EnvironmentSource[] }
  expressedTopics: EnvironmentTopicItem[]
  involvedTopics: EnvironmentTopicItem[]
  simulation: EnvironmentSimulation | null
}

export interface EnvironmentSettings {
  durationHours: number
  minutesPerRound: number
  roundsMode: 'auto' | 'manual'
  manualRounds: number | null
  rounds: number
  autoRounds: number
  settingsRevision: number
}

export interface EnvironmentTopic {
  topicId: string
  text: string
  origin: 'question' | 'material' | 'simulation'
  claimNature: string | null
  claimNatureKnown: boolean
  sources: EnvironmentSource[]
}

export interface EnvironmentActivation {
  firstSpeakerRoleIds: string[]
  firstSpeakerTopics: { roleId: string; topicId: string }[]
  order: string[]
}

export interface EnvironmentPlatform {
  platformKey: 'platform_a' | 'platform_b'
  nativePlatform: 'twitter' | 'reddit'
  enabled?: boolean
}

export interface EnvironmentFrozenSelection {
  selectionId: string
  preparationId: string
  projectId: string
  buildId: string
  snapshotId: string
  question: string
  settingsRevision: number
  settings: EnvironmentSettings
  roles: EnvironmentRole[]
  platforms: EnvironmentPlatform[]
  initialTopics: EnvironmentTopic[]
  activation: EnvironmentActivation
}

export interface EnvironmentPreparationPayload {
  preparationId: string
  projectId: string
  buildId: string
  snapshotId: string
  runId: string
  jobId: string
  jobStatus: string
  question: string
  phase: EnvironmentPhase
  settings: EnvironmentSettings
  failure: { code: string; message: string } | null
  counts: {
    previewRoleCount: number
    profileBatchCount: number
    providerCalls: { real: number; double: number }
  }
  progress?: {
    totalRoles: number
    profiledRoles: number
    publishedRoles: number
    previewRoleCount: number
    batchCount: number
    previewBatchCount: number
  }
  previewOnly: boolean
  previewRoles: EnvironmentRole[]
  roles: EnvironmentRole[]
  platforms: EnvironmentPlatform[]
  initialTopics: EnvironmentTopic[]
  activation: EnvironmentActivation | null
  frozenSelection: EnvironmentFrozenSelection | null
  canContinue: boolean
  requiredAction: string | null
}

export interface EnvironmentContextPayload {
  build: {
    buildId: string
    projectId: string
    runId: string
    jobId: string
    question: string
    phase: string
    canContinue: boolean
    failure: { code: string; message: string } | null
  }
  snapshot: {
    snapshotId: string
    revision: number
    complete: boolean
    published: boolean
    nodeCount: number
    edgeCount: number
  } | null
  canPrepare: boolean
  reason: string | null
  preparation: {
    preparationId: string
    jobId: string
    runId: string
    phase: EnvironmentPhase
    settings: EnvironmentSettings
    failure: { code: string; message: string } | null
    frozen: boolean
    previewRoleCount: number
    profileBatchCount: number
  } | null
  newerBuildId: string | null
}

export interface EnvironmentEventPayload {
  preparationId?: string
  buildId?: string
  snapshotId?: string
  step?: number
  stepKey?: string
  phase?: EnvironmentPhase
  batchIndex?: number
  batchRoleIds?: string[]
  eligibleRoles?: number
  profiledRoles?: number
  totalRoles?: number
  previewRoleCount?: number
  rounds?: number
  reason?: string
}

export interface EnvironmentState {
  buildId: string | null
  snapshotId: string | null
  question: string
  graphPhase: string | null
  canPrepare: boolean
  reason: string | null
  newerBuildId: string | null
  preparationId: string | null
  jobId: string | null
  runId: string | null
  phase: EnvironmentPhase | null
  settings: EnvironmentSettings | null
  failureCode: string | null
  failureMessage: string | null
  previewRoles: EnvironmentRole[]
  roles: EnvironmentRole[]
  previewOnly: boolean
  canContinue: boolean
  platforms: EnvironmentPlatform[]
  initialTopics: EnvironmentTopic[]
  activation: EnvironmentActivation | null
  frozenSelection: EnvironmentFrozenSelection | null
  previewRoleCount: number
  profileBatchCount: number
  totalRoles: number
  stale: boolean
  needsRefresh: boolean
}

export function createEnvironmentState(): EnvironmentState {
  return {
    buildId: null,
    snapshotId: null,
    question: '',
    graphPhase: null,
    canPrepare: false,
    reason: null,
    newerBuildId: null,
    preparationId: null,
    jobId: null,
    runId: null,
    phase: null,
    settings: null,
    failureCode: null,
    failureMessage: null,
    previewRoles: [],
    roles: [],
    previewOnly: false,
    canContinue: false,
    platforms: [],
    initialTopics: [],
    activation: null,
    frozenSelection: null,
    previewRoleCount: 0,
    profileBatchCount: 0,
    totalRoles: 0,
    stale: false,
    needsRefresh: false,
  }
}

/** 切换 build 时先清空旧角色、旧轮数与旧来源。 */
export function resetForBuild(
  _state: EnvironmentState,
  buildId: string | null,
): EnvironmentState {
  return {
    ...createEnvironmentState(),
    buildId,
  }
}

function buildMatches(state: EnvironmentState, buildId: string): boolean {
  return state.buildId === null || state.buildId === buildId
}

export function preparationMatches(
  state: EnvironmentState,
  payload: { preparationId: string; buildId: string },
): boolean {
  if (!buildMatches(state, payload.buildId)) {
    return false
  }
  return (
    state.preparationId === null || state.preparationId === payload.preparationId
  )
}

function mergePreviewRoles(
  current: EnvironmentRole[],
  incoming: EnvironmentRole[],
): EnvironmentRole[] {
  // 迟到响应不能让已经看到的批次消失；只在同一准备内按 roleId 合并。
  const byId = new Map<string, EnvironmentRole>()
  for (const role of current) {
    byId.set(role.roleId, role)
  }
  for (const role of incoming) {
    const existing = byId.get(role.roleId)
    byId.set(
      role.roleId,
      existing && !role.simulation && existing.simulation
        ? { ...role, simulation: existing.simulation }
        : role,
    )
  }
  return [...byId.values()].sort((a, b) => {
    const batch = (a.batchIndex ?? 0) - (b.batchIndex ?? 0)
    return batch !== 0 ? batch : a.roleId.localeCompare(b.roleId)
  })
}

export function applyContext(
  state: EnvironmentState,
  context: EnvironmentContextPayload,
): EnvironmentState {
  if (!buildMatches(state, context.build.buildId)) {
    return { ...state, stale: true }
  }
  const sameBuild = state.buildId === context.build.buildId
  return {
    ...state,
    buildId: context.build.buildId,
    snapshotId: sameBuild
      ? state.snapshotId ?? context.snapshot?.snapshotId ?? null
      : context.snapshot?.snapshotId ?? null,
    question: context.build.question,
    graphPhase: context.build.phase,
    canPrepare: context.canPrepare,
    reason: context.reason,
    newerBuildId: context.newerBuildId,
    preparationId: sameBuild
      ? state.preparationId ?? context.preparation?.preparationId ?? null
      : context.preparation?.preparationId ?? null,
    jobId: sameBuild
      ? state.jobId ?? context.preparation?.jobId ?? null
      : context.preparation?.jobId ?? null,
    runId: sameBuild
      ? state.runId ?? context.preparation?.runId ?? null
      : context.preparation?.runId ?? null,
    phase: sameBuild
      ? state.phase ?? context.preparation?.phase ?? null
      : context.preparation?.phase ?? null,
    settings: sameBuild
      ? state.settings ?? context.preparation?.settings ?? null
      : context.preparation?.settings ?? null,
    failureCode: sameBuild
      ? state.failureCode ?? context.preparation?.failure?.code ?? null
      : context.preparation?.failure?.code ?? null,
    failureMessage: sameBuild
      ? state.failureMessage ?? context.preparation?.failure?.message ?? null
      : context.preparation?.failure?.message ?? null,
    stale: false,
  }
}

export function applyPreparation(
  state: EnvironmentState,
  payload: EnvironmentPreparationPayload,
  force = false,
): EnvironmentState {
  if (!force && !preparationMatches(state, payload)) {
    return { ...state, stale: true }
  }
  const samePreparation =
    !force && state.preparationId === payload.preparationId
  if (samePreparation) {
    if (
      (state.phase === 'ready' ||
        state.phase === 'failed' ||
        state.phase === 'canceled') &&
      payload.phase === 'preparing'
    ) {
      // 终态不能被迟到的“准备中”响应退回。
      return { ...state, stale: false, needsRefresh: false }
    }
    if (
      state.settings &&
      payload.settings.settingsRevision < state.settings.settingsRevision
    ) {
      // 旧版本的配置与正文不能覆盖新版本。
      return { ...state, stale: false, needsRefresh: false }
    }
  }
  const keepFinal =
    samePreparation &&
    state.frozenSelection !== null &&
    payload.frozenSelection === null
  const previewRoles = force
    ? payload.previewRoles
    : payload.phase === 'preparing'
      ? mergePreviewRoles(state.previewRoles, payload.previewRoles)
      : payload.previewRoles
  return {
    ...state,
    buildId: payload.buildId,
    snapshotId: payload.snapshotId,
    question: payload.question,
    preparationId: payload.preparationId,
    jobId: payload.jobId,
    runId: payload.runId,
    phase: payload.phase,
    settings: payload.settings,
    failureCode: payload.failure?.code ?? null,
    failureMessage: payload.failure?.message ?? null,
    previewRoles,
    roles: payload.roles,
    previewOnly: payload.previewOnly,
    canContinue: payload.canContinue,
    platforms: payload.platforms,
    initialTopics: payload.initialTopics,
    activation: payload.activation,
    frozenSelection: keepFinal ? state.frozenSelection : payload.frozenSelection,
    previewRoleCount:
      payload.progress?.previewRoleCount ?? payload.counts.previewRoleCount,
    profileBatchCount: payload.counts.profileBatchCount,
    totalRoles: payload.progress?.totalRoles ?? state.totalRoles,
    stale: false,
    needsRefresh: false,
  }
}

export function applyEnvironmentEvent(
  state: EnvironmentState,
  payload: EnvironmentEventPayload,
): EnvironmentState {
  if (payload.buildId && !buildMatches(state, payload.buildId)) {
    return { ...state, stale: true }
  }
  if (
    payload.preparationId &&
    state.preparationId &&
    payload.preparationId !== state.preparationId
  ) {
    return { ...state, stale: true }
  }
  if (
    state.phase === 'ready' ||
    state.phase === 'failed' ||
    state.phase === 'canceled'
  ) {
    if (payload.phase === 'preparing' || payload.phase === undefined) {
      return state
    }
  }
  return {
    ...state,
    preparationId: state.preparationId ?? payload.preparationId ?? null,
    buildId: state.buildId ?? payload.buildId ?? null,
    snapshotId: state.snapshotId ?? payload.snapshotId ?? null,
    phase: payload.phase ?? state.phase,
    previewRoleCount: payload.previewRoleCount ?? state.previewRoleCount,
    totalRoles: payload.totalRoles ?? state.totalRoles,
    failureCode:
      payload.phase === 'failed'
        ? payload.reason ?? 'preparation_failed'
        : state.failureCode,
    needsRefresh: true,
    stale: false,
  }
}

export type EnvironmentViewMode =
  | 'graph-not-ready'
  | 'not-started'
  | 'preparing'
  | 'ready-unconfirmed'
  | 'confirmed'
  | 'failed'
  | 'canceled'

export function environmentViewMode(
  state: EnvironmentState,
): EnvironmentViewMode {
  if (!state.canPrepare) {
    return 'graph-not-ready'
  }
  if (state.phase === null) {
    return 'not-started'
  }
  if (state.phase === 'preparing') {
    return 'preparing'
  }
  if (state.phase === 'failed') {
    return 'failed'
  }
  if (state.phase === 'canceled') {
    return 'canceled'
  }
  return state.frozenSelection ? 'confirmed' : 'ready-unconfirmed'
}

/** 第三阶段只使用服务端确认后的 canContinue；不按角色数量猜测。 */
export function canOpenNextStage(state: EnvironmentState): boolean {
  return state.phase === 'ready' && state.canContinue
}

/** 激活顺序必须覆盖全部角色；首发是前缀且每个首发有话题。 */
export function activationIsValid(state: EnvironmentState): boolean {
  if (!state.activation) {
    return false
  }
  const roleIds = state.roles.map((role) => role.roleId)
  const order = state.activation.order
  if (order.length !== roleIds.length || new Set(order).size !== order.length) {
    return false
  }
  if (!roleIds.every((id) => order.includes(id))) {
    return false
  }
  const first = state.activation.firstSpeakerRoleIds
  if (!first.length || order.slice(0, first.length).join(',') !== first.join(',')) {
    return false
  }
  const mapped = new Map(
    state.activation.firstSpeakerTopics.map((item) => [item.roleId, item.topicId]),
  )
  return first.every((id) => Boolean(mapped.get(id)))
}

export function previewBatches(state: EnvironmentState): Map<number, EnvironmentRole[]> {
  const batches = new Map<number, EnvironmentRole[]>()
  for (const role of state.previewRoles) {
    const index = role.batchIndex ?? 0
    const items = batches.get(index) ?? []
    items.push(role)
    batches.set(index, items)
  }
  return new Map([...batches.entries()].sort((a, b) => a[0] - b[0]))
}
