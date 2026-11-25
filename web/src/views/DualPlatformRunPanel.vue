<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import { messagesFor } from '../i18n'
import { markLoggedOut } from '../session'
import type { Lang } from '../workbench/stage-links'
import { openJobStream, type JobStreamHandle } from '../workbench/job-stream'
import type { JobEvent } from '../workbench/job-log'
import { previousStageUrl } from '../environment/links'
import type {
  EnvironmentContextPayload,
  EnvironmentPreparationPayload,
} from '../environment/state'
import {
  actionLabel,
  applyDetail,
  applyEvent,
  applyMemoryView,
  appendActionPage,
  countMismatch,
  createMemoryState,
  createSimulationState,
  engineLabel,
  markStopRequested,
  memoryOriginLabel,
  memorySourceLabel,
  memoryStatusLabel,
  nativePlatformLabel,
  switchMemoryView,
  switchRun,
  phaseLabel,
  platformLabel,
  simulatedClock,
  stopLabel,
  type MemorySource,
  type MemoryViewState,
  type SimulationAction,
  type SimulationMemoryView,
  type SimulationActionPage,
  type SimulationDetail,
  type SimulationEvent,
  type SimulationState,
  type SimulationSummary,
} from '../simulation/state'

const props = defineProps<{ projectId: string; lang: Lang }>()
const route = useRoute()
const router = useRouter()
const t = computed(() => messagesFor(props.lang).simulation)
const loading = ref(true)
const errorText = ref('')
const loadFailed = ref(false)
const selectionMissing = ref(false)
const starting = ref(false)
const startError = ref('')
const actionError = ref('')
const runs = ref<SimulationSummary[]>([])
const state = ref<SimulationState>(createSimulationState(null))
const memoryState = ref<MemoryViewState>(createMemoryState())
const selectedSource = ref<MemorySource | null>(null)
const selectedMemoryAction = ref<string | null>(null)
const memoryRetrying = ref(false)
const expanded = ref<string | null>(null)
const stream = ref<JobStreamHandle | null>(null)
const streamStatus = ref<string>('idle')
const streamRunId = ref<string | null>(null)
const frozen = ref<EnvironmentPreparationPayload['frozenSelection']>(null)
const preparationId = ref<string | null>(null)
const buildId = computed(() =>
  typeof route.query.build === 'string' ? route.query.build : null,
)
const querySelection = computed(() =>
  typeof route.query.selection === 'string' ? route.query.selection : null,
)
const queryRun = computed(() =>
  typeof route.query.run === 'string' ? route.query.run : null,
)
const detail = computed<SimulationDetail | null>(() => state.value.detail)
const selectionId = computed(
  () => querySelection.value ?? frozen.value?.selectionId ?? null,
)
const prepareLink = computed(() =>
  previousStageUrl(props.projectId, route.query, buildId.value),
)
const clock = computed(() =>
  simulatedClock(
    detail.value?.simulatedAt ?? null,
    props.lang,
    detail.value?.simulatedStart ?? null,
  ),
)
const platforms = computed(() => detail.value?.platforms ?? [])
const totalRounds = computed(() => detail.value?.rounds ?? 0)
const actions = computed(() => state.value.actions)
const memory = computed(() => memoryState.value.memory)
const memoryRound = computed(() => {
  if (typeof route.query.round === 'string') {
    const parsed = Number(route.query.round)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
})
const memoryPlatform = computed(() =>
  typeof route.query.platform === 'string' ? route.query.platform : null,
)
const memoryMaxRound = computed(() => {
  if (!detail.value) {
    return 0
  }
  const completed = detail.value.platforms
    .filter((item) => !memoryPlatform.value || item.platformKey === memoryPlatform.value)
    .map((item) => item.roundsCompleted)
  return Math.max(0, ...completed)
})
const memoryRounds = computed(() =>
  Array.from({ length: memoryMaxRound.value }, (_, index) => index + 1),
)
const memoryStatusText = computed(() => {
  const status = memory.value?.status
  if (!status) {
    return detail.value?.memory
      ? memoryStatusLabel(detail.value.memory.status, props.lang)
      : ''
  }
  return memoryStatusLabel(status.status, props.lang)
})
const canLoadMore = computed(
  () =>
    detail.value !== null &&
    detail.value.counts.total > actions.value.length &&
    !state.value.streamEnded,
)

function apiBase(): string {
  return `/api/projects/${encodeURIComponent(props.projectId)}/simulations`
}

async function handleAuth(caught: unknown): Promise<boolean> {
  if (caught instanceof ApiError && caught.status === 401) {
    markLoggedOut()
    await router.replace({ name: 'login', query: { next: route.fullPath } })
    return true
  }
  return false
}

function closeStream(): void {
  stream.value?.close()
  stream.value = null
  streamRunId.value = null
  streamStatus.value = 'idle'
}

function subscribe(runId: string, jobId: string): void {
  closeStream()
  if (state.value.streamEnded) {
    return
  }
  stream.value = openJobStream({
    projectId: props.projectId,
    jobId,
    after: state.value.lastSeq,
    onStatus: (status) => {
      if (streamRunId.value !== runId) {
        return
      }
      streamStatus.value = status
      if (status === 'ended') {
        void loadDetail(runId, true)
      }
    },
    onEvent: (event: JobEvent) => {
      if (streamRunId.value !== runId) {
        return
      }
      const applied = applyEvent(
        state.value,
        event as unknown as SimulationEvent,
        props.lang,
      )
      state.value = applied.state
      if (applied.needsRefresh) {
        void loadDetail(runId, true)
      }
    },
    onEnd: () => {
      if (streamRunId.value === runId) {
        void loadDetail(runId, true)
      }
    },
  })
  streamRunId.value = runId
}

async function loadPreparation(): Promise<void> {
  try {
    const suffix = buildId.value
      ? `?build=${encodeURIComponent(buildId.value)}`
      : ''
    const base = `/api/projects/${encodeURIComponent(props.projectId)}/environment`
    let id = typeof route.query.preparation === 'string' ? route.query.preparation : null
    if (!id) {
      const context = await api.get<EnvironmentContextPayload>(
        `${base}${suffix}`,
      )
      id = context.preparation?.preparationId ?? null
    }
    if (!id) {
      frozen.value = null
      return
    }
    preparationId.value = id
    const data = await api.get<{ preparation: EnvironmentPreparationPayload }>(
      `${base}/preparations/${encodeURIComponent(id)}`,
    )
    frozen.value = data.preparation.frozenSelection
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    if (caught instanceof ApiError && caught.status === 404) {
      frozen.value = null
      return
    }
    errorText.value =
      caught instanceof ApiError && caught.status === 503
        ? t.value.serviceUnavailable
        : t.value.loadFailed
    loadFailed.value = true
  }
}

async function loadDetail(runId: string, quiet = false): Promise<void> {
  if (!quiet && state.value.runId !== null && state.value.runId !== runId) {
    // 用户主动切换运行：先解绑旧运行，再加载新运行；迟到响应仍按 runId 丢弃。
    closeStream()
    expanded.value = null
    actionError.value = ''
    selectedSource.value = null
    selectedMemoryAction.value = null
    memoryState.value = createMemoryState()
    state.value = switchRun(state.value, runId)
  }
  try {
    const data = await api.get<{ simulation: SimulationDetail }>(
      `${apiBase()}/${encodeURIComponent(runId)}`,
    )
    const page = await api.get<SimulationActionPage & { runId: string }>(
      `${apiBase()}/${encodeURIComponent(runId)}/actions?after=0&limit=200`,
    )
    const applied = applyDetail(
      state.value,
      data.simulation,
      page.items,
      page.nextAfter,
    )
    if (applied === state.value && state.value.runId !== data.simulation.runId) {
      return
    }
    state.value = applied
    if (countMismatch(state.value)) {
      actionError.value = t.value.countMismatch
    }
    if (streamRunId.value !== data.simulation.runId) {
      subscribe(data.simulation.runId, data.simulation.jobId)
    }
    if (data.simulation.memory) {
      await loadMemory(data.simulation.runId)
    } else {
      memoryState.value = createMemoryState()
    }
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    if (!quiet) {
      errorText.value =
        caught instanceof ApiError && caught.status === 503
          ? t.value.serviceUnavailable
          : t.value.loadFailed
      loadFailed.value = true
    }
  }
}

async function loadRuns(): Promise<void> {
  if (!selectionId.value) {
    selectionMissing.value = true
    runs.value = []
    return
  }
  selectionMissing.value = false
  const query = new URLSearchParams()
  query.set('selection', selectionId.value)
  const data = await api.get<{
    simulations: SimulationSummary[]
    simulation: SimulationDetail | null
  }>(`${apiBase()}?${query.toString()}`)
  runs.value = data.simulations
  const target =
    queryRun.value ??
    data.simulation?.runId ??
    data.simulations[0]?.runId ??
    null
  if (target) {
    await loadDetail(target)
  } else {
    closeStream()
    state.value = createSimulationState(selectionId.value)
  }
}

async function load(): Promise<void> {
  loading.value = true
  errorText.value = ''
  loadFailed.value = false
  starting.value = false
  startError.value = ''
  actionError.value = ''
  closeStream()
  try {
    await loadPreparation()
    await loadRuns()
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    if (!errorText.value) {
      errorText.value =
        caught instanceof ApiError && caught.status === 503
          ? t.value.serviceUnavailable
          : t.value.loadFailed
    }
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

async function startRun(engineMode?: 'double' | 'oasis'): Promise<void> {
  if (!selectionId.value || starting.value) {
    return
  }
  starting.value = true
  startError.value = ''
  try {
    const data = await api.post<{ simulation: SimulationDetail }>(apiBase(), {
      selectionId: selectionId.value,
      engineMode: engineMode ?? null,
    })
    await router.replace({
      name: route.name ?? undefined,
      params: route.params,
      query: { ...route.query, selection: selectionId.value, run: data.simulation.runId },
    })
    await loadRuns()
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    startError.value =
      caught instanceof ApiError && caught.status === 503
        ? t.value.serviceUnavailable
        : caught instanceof ApiError
          ? errorCodeMessage(caught.code) ?? caught.message
          : t.value.loadFailed
  } finally {
    starting.value = false
  }
}

async function stopRun(): Promise<void> {
  const runId = state.value.runId
  if (!runId || state.value.stopRequested) {
    return
  }
  state.value = markStopRequested(state.value)
  try {
    await api.post(`${apiBase()}/${encodeURIComponent(runId)}/stop`)
    await loadDetail(runId)
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    startError.value =
      caught instanceof ApiError && caught.status === 503
        ? t.value.serviceUnavailable
        : t.value.loadFailed
  }
}

async function loadMoreActions(): Promise<void> {
  const runId = state.value.runId
  if (!runId) {
    return
  }
  try {
    const page = await api.get<SimulationActionPage & { runId: string }>(
      `${apiBase()}/${encodeURIComponent(runId)}/actions?after=${state.value.nextAfter}&limit=200`,
    )
    state.value = appendActionPage(state.value, runId, page)
  } catch (caught) {
    if (!(await handleAuth(caught))) {
      actionError.value = t.value.loadFailed
    }
  }
}

function selectRun(runId: string): void {
  if (state.value.runId !== null && state.value.runId !== runId) {
    closeStream()
    expanded.value = null
    actionError.value = ''
    selectedSource.value = null
    selectedMemoryAction.value = null
    memoryState.value = createMemoryState()
    state.value = switchRun(state.value, runId)
  }
  void router.replace({
    name: route.name ?? undefined,
    params: route.params,
    query: { ...route.query, run: runId, selection: selectionId.value ?? undefined },
  })
}

function errorCodeMessage(code: string | null): string | null {
  if (!code) {
    return null
  }
  const messages: Record<string, string> = t.value.errorCodes
  return messages[code] ?? null
}

function platformActions(platformKey: string): SimulationAction[] {
  return actions.value.filter((action) => action.platformKey === platformKey)
}

function failureText(platformKey: string): string {
  const platform = platforms.value.find((item) => item.platformKey === platformKey)
  if (!platform?.failure) {
    return ''
  }
  return errorCodeMessage(platform.failure.code) ?? platform.failure.code
}

function toggleTarget(actionId: string): void {
  expanded.value = expanded.value === actionId ? null : actionId
}

function actionClock(action: SimulationAction): string {
  return simulatedClock(action.simulatedAt, props.lang, detail.value?.simulatedStart ?? null)
}

function actionTarget(action: SimulationAction): string {
  return actionLabel(action.nativeAction, props.lang, action.platformKey)
}

function memoryQuery(): { round: number | null; platform: string | null } {
  return { round: memoryRound.value, platform: memoryPlatform.value }
}

function memoryRequestIsCurrent(
  runId: string,
  expected: { round: number | null; platform: string | null },
): boolean {
  return (
    state.value.runId === runId &&
    memoryRound.value === expected.round &&
    memoryPlatform.value === expected.platform
  )
}

async function loadMemory(runId: string): Promise<void> {
  const expected = { runId, ...memoryQuery() }
  memoryState.value = switchMemoryView(memoryState.value, expected)
  try {
    const params = new URLSearchParams()
    if (expected.round !== null) {
      params.set('round', String(expected.round))
    }
    if (expected.platform) {
      params.set('platform', expected.platform)
    }
    const suffix = params.toString() ? `?${params.toString()}` : ''
    const view = await api.get<SimulationMemoryView>(
      `${apiBase()}/${encodeURIComponent(runId)}/memory${suffix}`,
    )
    if (!memoryRequestIsCurrent(runId, expected)) {
      return
    }
    memoryState.value = applyMemoryView(memoryState.value, view, expected)
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    if (!memoryRequestIsCurrent(runId, expected)) {
      return
    }
    memoryState.value = {
      memory: memoryState.value.memory,
      loading: false,
      error:
        caught instanceof ApiError && caught.status === 503
          ? t.value.serviceUnavailable
          : t.value.memoryUnavailable,
    }
  }
}

function selectMemoryRound(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  void router.replace({
    path: route.path,
    query: {
      ...route.query,
      round: value || undefined,
      platform: memoryPlatform.value ?? undefined,
    },
  })
}

function selectMemoryPlatform(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  void router.replace({
    path: route.path,
    query: {
      ...route.query,
      round: memoryRound.value ?? undefined,
      platform: value || undefined,
    },
  })
}

async function retryMemory(): Promise<void> {
  const runId = state.value.runId
  if (!runId || memoryRetrying.value) {
    return
  }
  memoryRetrying.value = true
  try {
    await api.post(`${apiBase()}/${encodeURIComponent(runId)}/memory/retry`)
    await loadDetail(runId, true)
    await loadMemory(runId)
  } catch (caught) {
    if (!(await handleAuth(caught))) {
      memoryState.value = {
        ...memoryState.value,
        error: t.value.memoryUnavailable,
      }
    }
  } finally {
    memoryRetrying.value = false
  }
}

function openMemorySource(source: MemorySource): void {
  selectedSource.value = source
  selectedMemoryAction.value = source.actionId
  if (actions.value.some((action) => action.actionId === source.actionId)) {
    expanded.value = source.actionId
  }
}

function actionIsMemorySource(actionId: string): boolean {
  return selectedMemoryAction.value === actionId
}

watch(
  () => [route.query.run, route.query.selection, route.params.id, route.query.round, route.query.platform],
  () => {
    if (!loading.value) {
      if (route.query.run && state.value.runId) {
        void loadMemory(state.value.runId)
      }
      void loadRuns().catch(() => {
        errorText.value = t.value.loadFailed
      })
    }
  },
)

onMounted(() => {
  void load()
})

onBeforeUnmount(() => {
  closeStream()
})
</script>

<template>
  <section class="run-panel card" data-view="dual-platform-run">
    <p v-if="loading" class="muted" aria-live="polite">{{ t.loading }}</p>
    <div v-else-if="errorText" class="error-block" role="alert">
      <p>{{ errorText }}</p>
      <button type="button" class="primary" @click="load">{{ t.retry }}</button>
    </div>
    <template v-else>
      <header class="run-head">
        <div>
          <p class="eyebrow">{{ t.title }}</p>
          <h2>{{ detail?.question ?? frozen?.question ?? t.title }}</h2>
          <p class="muted">{{ t.intro }}</p>
        </div>
        <div class="run-badges">
          <span v-if="detail" class="badge" data-field="engine">
            {{ engineLabel(detail.engine, props.lang) }}
          </span>
          <span v-if="detail" class="badge" data-field="phase">
            {{ phaseLabel(detail.phase, props.lang) }}
          </span>
          <span v-if="detail?.partial" class="badge badge-warn" data-field="partial">
            {{ t.partialResult }}
          </span>
        </div>
      </header>

      <div v-if="selectionMissing" class="empty-block">
        <h3>{{ t.missingSelection }}</h3>
        <p class="muted">{{ t.missingSelectionBody }}</p>
        <RouterLink class="button-link" :to="prepareLink">
          {{ t.goPrepare }}
        </RouterLink>
      </div>

      <template v-else>
        <dl class="run-summary">
          <dt>{{ t.selectionSummary }}</dt>
          <dd data-field="selection">
            {{ selectionId }} · {{ frozen?.roles.length ?? 0 }} {{ t.roles }} ·
            {{ frozen?.settings.rounds ?? totalRounds }} {{ t.rounds }}
          </dd>
          <dt>{{ t.platformMap }}</dt>
          <dd>
            <span v-for="platform in frozen?.platforms ?? []" :key="platform.platformKey">
              {{ platformLabel(platform.platformKey, props.lang) }}
              → {{ nativePlatformLabel(platform.nativePlatform) }}
            </span>
          </dd>
          <dt>{{ t.clock }}</dt>
          <dd data-field="clock">{{ clock }}</dd>
        </dl>

        <div class="run-actions">
          <button
            type="button"
            class="primary"
            data-action="start"
            :disabled="starting || (detail !== null && !detail.terminal && detail.phase !== 'queued')"
            @click="startRun()"
          >
            {{ starting ? t.starting : t.start }}
          </button>
          <button
            v-if="detail && !detail.terminal"
            type="button"
            class="secondary"
            data-action="stop"
            :disabled="state.stopRequested"
            @click="stopRun"
          >
            {{ stopLabel(state, props.lang) }}
          </button>
          <span class="muted">{{ t.startHint }}</span>
        </div>
        <p v-if="startError" class="error-text" role="alert">{{ startError }}</p>
        <p v-if="actionError" class="error-text" role="alert">{{ actionError }}</p>

        <div v-if="runs.length > 1" class="run-list">
          <p class="eyebrow">{{ t.runs }}</p>
          <button
            v-for="item in runs"
            :key="item.runId"
            type="button"
            class="run-chip"
            :class="{ active: item.runId === state.runId }"
            data-action="select-run"
            @click="selectRun(item.runId)"
          >
            {{ item.runId.slice(0, 8) }} · {{ phaseLabel(item.phase, props.lang) }} ·
            {{ engineLabel(item.engine, props.lang) }}
          </button>
        </div>

        <div v-if="!detail" class="empty-block">
          <h3>{{ t.notStarted }}</h3>
          <p class="muted">{{ t.noRuns }}</p>
        </div>

        <template v-else>
          <section class="memory-block" data-view="temporal-memory">
            <header class="memory-head">
              <div>
                <p class="eyebrow">{{ t.memoryTitle }}</p>
                <p class="muted">{{ t.memoryIntro }}</p>
              </div>
              <span
                v-if="memoryStatusText"
                class="badge"
                :class="{ 'badge-warn': memory?.status.status !== 'complete' }"
                data-field="memory-status"
              >
                {{ memoryStatusText }}
              </span>
            </header>
            <div class="memory-controls">
              <label>
                {{ t.memoryRound }}
                <select
                  data-action="memory-round"
                  :value="memoryRound ?? memoryMaxRound"
                  @change="selectMemoryRound"
                >
                  <option v-for="round in memoryRounds" :key="round" :value="round">
                    R{{ round }}
                  </option>
                </select>
              </label>
              <label>
                {{ t.memoryPlatform }}
                <select
                  data-action="memory-platform"
                  :value="memoryPlatform ?? ''"
                  @change="selectMemoryPlatform"
                >
                  <option value="">{{ t.memoryAllPlatforms }}</option>
                  <option value="platform_a">{{ platformLabel('platform_a', props.lang) }}</option>
                  <option value="platform_b">{{ platformLabel('platform_b', props.lang) }}</option>
                </select>
              </label>
              <span class="muted" data-field="memory-pending">
                {{ t.memoryPending }}：{{ memory?.status.pendingEpisodes ?? detail.memory?.pendingEpisodes ?? 0 }}
              </span>
              <button
                v-if="memory && ['pending', 'partial', 'unavailable', 'unconfirmed'].includes(memory.status.status)"
                type="button"
                class="secondary"
                data-action="memory-retry"
                :disabled="memoryRetrying"
                @click="retryMemory"
              >
                {{ memoryRetrying ? t.memoryRetrying : t.memoryRetry }}
              </button>
            </div>
            <p v-if="memoryState.error" class="error-text" role="alert">{{ memoryState.error }}</p>
            <p v-else-if="memory?.status.status === 'unavailable'" class="error-text" role="alert">
              {{ t.memoryUnavailable }}
            </p>
            <p v-else-if="memory?.status.status === 'unconfigured'" class="muted">
              {{ t.memoryUnconfigured }}
            </p>
            <p v-else-if="memory?.status.status === 'unconfirmed'" class="error-text" role="alert">
              {{ t.memoryUnconfirmed }}
            </p>
            <p v-if="memoryState.loading" class="muted">{{ t.loading }}</p>
            <p v-else-if="memory && !memory.runtime.nodes.length && !memory.runtime.edges.length" class="muted">
              {{ t.memoryEmpty }}
            </p>
            <div v-if="memory" class="memory-legend" data-field="memory-legend">
              <span class="badge">{{ t.memoryLegend }}</span>
              <span class="muted">{{ memoryOriginLabel('material', props.lang) }}</span>
              <span class="muted">{{ memoryOriginLabel('role', props.lang) }}</span>
              <span class="muted">{{ memoryOriginLabel('simulation', props.lang) }}</span>
            </div>
            <div v-if="memory && (memory.runtime.nodes.length || memory.runtime.edges.length)" class="memory-grid">
              <div>
                <h4>{{ t.memoryNodes }}</h4>
                <ul class="memory-list" data-field="memory-nodes">
                  <li
                    v-for="node in memory.runtime.nodes"
                    :key="node.key"
                    :data-memory-key="node.key"
                  >
                    <strong>{{ node.label }}</strong>
                    <span class="muted"> · {{ memoryOriginLabel(node.origin, props.lang) }} · R{{ node.firstRound }}–{{ node.lastRound }}</span>
                    <div class="memory-sources">
                      <button
                        v-for="source in node.sources"
                        :key="`${node.key}-${source.memoryId}-${source.facet}`"
                        type="button"
                        class="link-button"
                        data-action="memory-source"
                        @click="openMemorySource(source)"
                      >
                        {{ memorySourceLabel(source, props.lang) }}
                      </button>
                    </div>
                  </li>
                </ul>
              </div>
              <div>
                <h4>{{ t.memoryEdges }}</h4>
                <ul class="memory-list" data-field="memory-edges">
                  <li
                    v-for="edge in memory.runtime.edges"
                    :key="edge.key"
                    :data-memory-key="edge.key"
                  >
                    <strong>{{ edge.label }}</strong>
                    <span class="muted"> · {{ memoryOriginLabel(edge.origin, props.lang) }}</span>
                    <div class="memory-sources">
                      <button
                        v-for="source in edge.sources"
                        :key="`${edge.key}-${source.memoryId}-${source.facet}`"
                        type="button"
                        class="link-button"
                        data-action="memory-source"
                        @click="openMemorySource(source)"
                      >
                        {{ memorySourceLabel(source, props.lang) }}
                      </button>
                    </div>
                  </li>
                </ul>
              </div>
            </div>
            <div v-if="selectedSource" class="memory-source-block" data-field="memory-source">
              <p class="eyebrow">{{ t.memorySourceAction }}</p>
              <p>
                {{ selectedSource.actorRoleName ?? selectedSource.actorRoleId }} ·
                R{{ selectedSource.roundIndex }} ·
                {{ simulatedClock(selectedSource.simulatedAt, props.lang, detail.simulatedStart) }}
              </p>
              <p v-if="selectedSource.content">{{ selectedSource.content }}</p>
              <p class="muted">
                actionId {{ selectedSource.actionId }} · event #{{ selectedSource.sourceEventSeq }}
              </p>
              <button
                type="button"
                class="link-button"
                data-action="memory-back-action"
                @click="selectedMemoryAction = selectedSource.actionId"
              >
                {{ t.memoryAction }}
              </button>
            </div>
          </section>
          <div class="platform-grid" data-field="platforms">
            <article
              v-for="platform in platforms"
              :key="platform.platformKey"
              class="platform-card"
              :data-platform="platform.platformKey"
            >
              <header>
                <h3>{{ platformLabel(platform.platformKey, props.lang) }}</h3>
                <span class="muted">{{ nativePlatformLabel(platform.nativePlatform) }}</span>
              </header>
              <p class="stats">
                <span data-field="rounds">
                  {{ platform.roundsCompleted }}/{{ platform.roundsTotal }} {{ t.rounds }}
                </span>
                <span data-field="actions">
                  {{ platform.actionCount }} {{ t.actions }}
                </span>
                <span
                  v-for="(count, nativeAction) in platform.byAction"
                  :key="nativeAction"
                  class="stat-type"
                >
                  {{ actionLabel(nativeAction, props.lang, platform.platformKey) }} {{ count }}
                </span>
              </p>
              <p v-if="platform.failure" class="error-text" role="alert">
                {{ t.failedPlatform }}：{{ failureText(platform.platformKey) }}
              </p>
              <p v-if="platformActions(platform.platformKey).length === 0" class="muted">
                {{ t.emptyActions }}
              </p>
              <ol class="action-list" data-field="action-stream">
                <li
                  v-for="action in platformActions(platform.platformKey)"
                  :key="action.actionId"
                  class="action-row"
                  :class="{ 'action-row-active': actionIsMemorySource(action.actionId) }"
                  :data-action-id="action.actionId"
                >
                  <div class="action-head">
                    <span class="badge">{{ actionTarget(action) }}</span>
                    <strong>{{ action.actorRoleName }}</strong>
                    <span class="muted">
                      R{{ action.roundIndex }} · {{ actionClock(action) }}
                    </span>
                  </div>
                  <p v-if="action.content">{{ action.content }}</p>
                  <button
                    v-if="action.targetEngineRecordId"
                    type="button"
                    class="link-button"
                    data-action="open-target"
                    @click="toggleTarget(action.actionId)"
                  >
                    {{ expanded === action.actionId ? t.closeTarget : t.originalPost }}
                  </button>
                  <div
                    v-if="expanded === action.actionId"
                    class="target-block"
                    data-field="target"
                  >
                    <template v-if="action.target">
                      <p>{{ action.target.content }}</p>
                      <p class="muted">
                        {{ t.role }}：{{ action.target.actorRoleName }} · R{{ action.target.roundIndex }} ·
                        {{ simulatedClock(action.target.simulatedAt, props.lang, detail.simulatedStart) }}
                      </p>
                    </template>
                    <p v-else class="muted">{{ t.targetUnresolved }}（{{ action.targetEngineRecordId }}）</p>
                  </div>
                </li>
              </ol>
            </article>
          </div>

          <div class="run-actions">
            <button
              v-if="canLoadMore"
              type="button"
              class="secondary"
              data-action="load-more"
              @click="loadMoreActions"
            >
              {{ t.actions }}
            </button>
          </div>

          <section class="log-block">
            <h3>{{ t.logs }}</h3>
            <p v-if="state.logs.length === 0" class="muted">{{ t.emptyLogs }}</p>
            <ol class="log-list" data-field="log">
              <li v-for="line in state.logs" :key="line.seq">
                <span class="muted">#{{ line.seq }}</span> {{ line.text }}
              </li>
            </ol>
          </section>
        </template>
      </template>
    </template>
  </section>
</template>

<style scoped>
.run-panel {
  display: grid;
  gap: 1rem;
}
.run-head {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}
.run-badges {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  border: 1px solid var(--border, #d0d7de);
  font-size: 0.8rem;
}
.badge-warn {
  border-color: #d4a72c;
}
.run-summary {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.35rem 1rem;
}
.run-summary dt {
  font-weight: 600;
}
.run-summary dd {
  margin: 0;
}
.run-actions {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  flex-wrap: wrap;
}
.run-list {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}
.run-chip {
  border: 1px solid var(--border, #d0d7de);
  background: transparent;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  cursor: pointer;
}
.run-chip.active {
  border-color: #0969da;
}
.platform-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  gap: 1rem;
}
.platform-card {
  border: 1px solid var(--border, #d0d7de);
  border-radius: 8px;
  padding: 0.75rem;
  display: grid;
  gap: 0.5rem;
}
.stats {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  font-size: 0.9rem;
}
.action-list,
.log-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.5rem;
  max-height: 26rem;
  overflow: auto;
}
.action-row {
  border-top: 1px solid var(--border, #d0d7de);
  padding-top: 0.5rem;
}
.action-head {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  flex-wrap: wrap;
}
.target-block {
  border-left: 3px solid #0969da;
  padding-left: 0.6rem;
  margin-top: 0.4rem;
}
.link-button {
  background: none;
  border: none;
  padding: 0;
  color: #0969da;
  cursor: pointer;
  font-size: 0.85rem;
}
.error-text {
  color: #cf222e;
}
.empty-block {
  display: grid;
  gap: 0.4rem;
}
.memory-block {
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 1rem;
  margin: 1rem 0;
  background: var(--surface);
}
.memory-head {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}
.memory-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: center;
  margin: 0.75rem 0;
}
.memory-controls label {
  display: flex;
  gap: 0.35rem;
  align-items: center;
}
.memory-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 0.5rem 0;
}
.memory-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: 1rem;
}
.memory-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 0.5rem;
}
.memory-list li {
  padding: 0.5rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
}
.memory-sources {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.35rem;
}
.memory-source-block {
  margin-top: 0.75rem;
  padding: 0.75rem;
  border-left: 3px solid var(--accent);
  background: var(--surface-muted);
}
.action-row-active {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>
