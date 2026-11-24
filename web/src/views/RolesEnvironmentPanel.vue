<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import { messagesFor } from '../i18n'
import { markLoggedOut } from '../session'
import type { Lang } from '../workbench/stage-links'
import { roleTypeLabel } from '../environment/format'
import { openJobStream, type JobStreamHandle } from '../workbench/job-stream'
import type { JobEvent } from '../workbench/job-log'
import {
  canOpenNextStage,
  createEnvironmentState,
  environmentViewMode,
  applyContext,
  applyEnvironmentEvent,
  applyPreparation,
  preparationMatches,
  previewBatches,
  resetForBuild,
  type EnvironmentContextPayload,
  type EnvironmentPreparationPayload,
  type EnvironmentRole,
  type EnvironmentSource,
  type EnvironmentState,
} from '../environment/state'
import {
  contextQuery,
  nextStageUrl,
  previousStageUrl,
  sourceDirectUrl,
  sourceWorkbenchUrl,
} from '../environment/links'

const props = defineProps<{ projectId: string; lang: Lang }>()

const route = useRoute()
const router = useRouter()
const t = computed(() => messagesFor(props.lang).environment)
const claimLabels = computed(() => messagesFor(props.lang).graph)
const state = ref<EnvironmentState>(createEnvironmentState())
const loading = ref(true)
const submitting = ref(false)
const errorText = ref('')
const errorCode = ref<string | null>(null)
const notice = ref('')
const selectedRoleId = ref<string | null>(null)
const expandedBatches = ref<Set<number>>(new Set())
const streamStatus = ref('')
const lastSeq = ref(0)
const form = reactive({
  durationHours: 72,
  minutesPerRound: 60,
  roundsMode: 'auto' as 'auto' | 'manual',
  manualRounds: 3,
  settingsRevision: 1,
})

let loadToken = 0
let preparationToken = 0
let stream: JobStreamHandle | null = null
let refreshTimer: number | undefined
let newerGraphTimer: number | undefined
let newerGraphInFlight = false
let pendingRefresh = false
const NEWER_GRAPH_POLL_MS = 2000

const buildParam = computed(() =>
  typeof route.query.build === 'string' ? route.query.build : null,
)
const viewMode = computed(() => environmentViewMode(state.value))
const batches = computed(() => previewBatches(state.value))
const shownRoles = computed(() =>
  state.value.phase === 'ready' ? state.value.roles : state.value.previewRoles,
)
const selectedRole = computed(() =>
  shownRoles.value.find((role) => role.roleId === selectedRoleId.value) ?? null,
)
const nextLink = computed(() =>
  nextStageUrl(
    props.projectId,
    route.query,
    state.value.buildId,
    state.value.preparationId,
  ),
)
const previousLink = computed(() =>
  previousStageUrl(props.projectId, route.query, state.value.buildId),
)
const frozen = computed(() => state.value.frozenSelection !== null)
const editable = computed(
  () => !frozen.value && state.value.phase === 'ready',
)
const formInvalid = computed(() => {
  const duration = Number(form.durationHours)
  const minutes = Number(form.minutesPerRound)
  if (!Number.isInteger(duration) || duration < 1 || duration > 720) {
    return true
  }
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
    return true
  }
  if (form.roundsMode === 'manual') {
    const manual = Number(form.manualRounds)
    if (!Number.isInteger(manual) || manual < 1 || manual > 1000) {
      return true
    }
  }
  return false
})
const formDirty = computed(() => {
  const saved = state.value.settings
  if (!saved) {
    return true
  }
  if (
    Number(form.durationHours) !== saved.durationHours ||
    Number(form.minutesPerRound) !== saved.minutesPerRound ||
    form.roundsMode !== saved.roundsMode
  ) {
    return true
  }
  if (form.roundsMode === 'manual') {
    return Number(form.manualRounds) !== (saved.manualRounds ?? null)
  }
  return false
})
const confirmBlockedReason = computed(() => {
  if (formInvalid.value) {
    return t.value.confirmInvalid
  }
  if (formDirty.value) {
    return t.value.confirmDirty
  }
  return ''
})
const canConfirm = computed(
  () =>
    state.value.phase === 'ready' &&
    !frozen.value &&
    !submitting.value &&
    !formInvalid.value &&
    !formDirty.value,
)
const roleName = computed(() => {
  const map: Record<string, string> = {}
  for (const role of state.value.roles) {
    map[role.roleId] = role.displayName
  }
  return map
})
const topicText = computed(() => {
  const map: Record<string, string> = {}
  for (const topic of state.value.initialTopics) {
    map[topic.topicId] = topic.text
  }
  return map
})

function apiBase(): string {
  return `/api/projects/${encodeURIComponent(props.projectId)}/environment`
}

function detailPath(id: string): string {
  return `${apiBase()}/preparations/${encodeURIComponent(id)}`
}

function reasonText(code: string | null, fallback = ''): string {
  if (!code) {
    return fallback
  }
  return t.value.errorCodes[code] ?? fallback
}

async function handleAuth(caught: unknown): Promise<boolean> {
  if (caught instanceof ApiError && caught.status === 401) {
    markLoggedOut()
    await router.replace({ name: 'login', query: { next: route.fullPath } })
    return true
  }
  return false
}

function captureError(caught: unknown): void {
  if (caught instanceof ApiError) {
    errorCode.value = caught.code
    errorText.value =
      caught.status === 503
        ? t.value.serviceUnavailable
        : reasonText(caught.code, caught.message)
    return
  }
  errorCode.value = null
  errorText.value = t.value.loadFailed
}

function syncForm(payload: EnvironmentPreparationPayload): void {
  form.durationHours = payload.settings.durationHours
  form.minutesPerRound = payload.settings.minutesPerRound
  form.roundsMode = payload.settings.roundsMode
  form.manualRounds = payload.settings.manualRounds ?? 3
  form.settingsRevision = payload.settings.settingsRevision
}

function stopStream(): void {
  stream?.close()
  stream = null
  if (refreshTimer !== undefined) {
    window.clearTimeout(refreshTimer)
    refreshTimer = undefined
  }
}

function scheduleRefresh(): void {
  if (refreshTimer !== undefined) {
    return
  }
  refreshTimer = window.setTimeout(() => {
    refreshTimer = undefined
    const id = state.value.preparationId
    if (id) {
      void loadPreparation(id)
    }
  }, 120)
}

async function pollNewerGraph(): Promise<void> {
  const buildId = state.value.buildId
  if (
    newerGraphInFlight ||
    !buildId ||
    !state.value.preparationId ||
    state.value.phase !== 'preparing'
  ) {
    return
  }
  newerGraphInFlight = true
  try {
    const context = await api.get<EnvironmentContextPayload>(
      `${apiBase()}?build=${encodeURIComponent(buildId)}`,
    )
    if (state.value.buildId !== buildId) {
      return
    }
    const next = applyContext(state.value, context)
    if (!next.stale) {
      state.value = next
    }
  } catch {
    // 轮询失败不改变当前准备；下一次定时器继续尝试。
  } finally {
    newerGraphInFlight = false
  }
}

function subscribe(): void {
  stopStream()
  const jobId = state.value.jobId
  if (!jobId || state.value.phase !== 'preparing') {
    return
  }
  stream = openJobStream({
    projectId: props.projectId,
    jobId,
    after: lastSeq.value,
    onStatus: (status) => {
      streamStatus.value = status
    },
    onEvent: (event: JobEvent) => {
      lastSeq.value = Math.max(lastSeq.value, event.seq)
      const payload = (event.payload ?? {}) as never
      const next = applyEnvironmentEvent(state.value, payload)
      if (next.stale) {
        return
      }
      state.value = next
      const isBatch = typeof (payload as { batchIndex?: unknown }).batchIndex === 'number'
      const isTerminal = ['completed', 'failed', 'canceled'].includes(event.type)
      if (isBatch || isTerminal) {
        if (loading.value || submitting.value) {
          pendingRefresh = true
        } else {
          scheduleRefresh()
        }
      }
    },
    onEnd: () => {
      if (loading.value || submitting.value) {
        pendingRefresh = true
      } else {
        scheduleRefresh()
      }
    },
  })
  streamStatus.value = 'connecting'
}

async function loadPreparation(preparationId: string): Promise<void> {
  const token = ++preparationToken
  try {
    const data = await api.get<{ preparation: EnvironmentPreparationPayload }>(
      detailPath(preparationId),
    )
    if (token !== preparationToken) {
      return
    }
    if (!preparationMatches(state.value, data.preparation)) {
      return
    }
    state.value = applyPreparation(state.value, data.preparation)
    syncForm(data.preparation)
    if (
      selectedRoleId.value &&
      !shownRoles.value.some((role) => role.roleId === selectedRoleId.value)
    ) {
      selectedRoleId.value = null
    }
    if (state.value.phase === 'preparing') {
      if (!stream) {
        subscribe()
      }
    } else {
      stopStream()
    }
    errorText.value = ''
    errorCode.value = null
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    captureError(caught)
  } finally {
    if (token === preparationToken && pendingRefresh) {
      pendingRefresh = false
      if (state.value.phase === 'preparing') {
        scheduleRefresh()
      }
    }
  }
}

async function load(): Promise<void> {
  const token = ++loadToken
  loading.value = true
  errorText.value = ''
  errorCode.value = null
  notice.value = ''
  const requestedBuild = buildParam.value
  if (requestedBuild && state.value.buildId !== requestedBuild) {
    state.value = resetForBuild(state.value, requestedBuild)
  }
  stopStream()
  try {
    const suffix = requestedBuild
      ? `?build=${encodeURIComponent(requestedBuild)}`
      : ''
    const context = await api.get<EnvironmentContextPayload>(
      `${apiBase()}${suffix}`,
    )
    if (token !== loadToken) {
      return
    }
    const next = applyContext(state.value, context)
    if (next.stale) {
      return
    }
    state.value = next
    if (!requestedBuild && context.build.buildId) {
      await router.replace({
        path: route.path,
        query: contextQuery(route.query, context.build.buildId),
      })
    }
    if (context.preparation) {
      await loadPreparation(context.preparation.preparationId)
    } else {
      state.value = {
        ...state.value,
        phase: null,
        preparationId: null,
        jobId: null,
        runId: null,
        settings: null,
        previewRoles: [],
        roles: [],
        platforms: [],
        initialTopics: [],
        activation: null,
        frozenSelection: null,
        canContinue: false,
      }
    }
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    captureError(caught)
  } finally {
    if (token === loadToken) {
      loading.value = false
    }
  }
}

async function startPreparation(): Promise<void> {
  if (submitting.value) {
    return
  }
  submitting.value = true
  errorText.value = ''
  errorCode.value = null
  try {
    const suffix = state.value.buildId
      ? `?build=${encodeURIComponent(state.value.buildId)}`
      : ''
    const data = await api.post<{ preparation: EnvironmentPreparationPayload }>(
      `${apiBase()}/preparations${suffix}`,
    )
    state.value = applyPreparation(state.value, data.preparation)
    syncForm(data.preparation)
    subscribe()
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    captureError(caught)
    if (caught instanceof ApiError && caught.code === 'preparation_retry_required') {
      const id = state.value.preparationId
      if (id) {
        await loadPreparation(id)
      }
    }
  } finally {
    submitting.value = false
    if (pendingRefresh) {
      pendingRefresh = false
      scheduleRefresh()
    }
  }
}

async function retryPreparation(): Promise<void> {
  const id = state.value.preparationId
  if (!id || submitting.value) {
    return
  }
  submitting.value = true
  errorText.value = ''
  errorCode.value = null
  preparationToken += 1
  stopStream()
  lastSeq.value = 0
  selectedRoleId.value = null
  expandedBatches.value = new Set()
  try {
    const data = await api.post<{ preparation: EnvironmentPreparationPayload }>(
      `${detailPath(id)}/retry`,
    )
    // 重试会返回新的 preparationId；必须显式切换，不能按旧 ID 当迟到响应丢弃。
    state.value = applyPreparation(state.value, data.preparation, true)
    syncForm(data.preparation)
    subscribe()
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    captureError(caught)
  } finally {
    submitting.value = false
  }
}

async function saveRounds(): Promise<void> {
  const id = state.value.preparationId
  if (!id || submitting.value || frozen.value) {
    return
  }
  submitting.value = true
  notice.value = ''
  errorText.value = ''
  errorCode.value = null
  try {
    const data = await api.patch<{ preparation: EnvironmentPreparationPayload }>(
      `${detailPath(id)}/configuration`,
      {
        settingsRevision: form.settingsRevision,
        durationHours: Number(form.durationHours),
        minutesPerRound: Number(form.minutesPerRound),
        roundsMode: form.roundsMode,
        manualRounds:
          form.roundsMode === 'manual' ? Number(form.manualRounds) : null,
      },
    )
    state.value = applyPreparation(state.value, data.preparation)
    syncForm(data.preparation)
    await loadPreparation(id)
    notice.value = t.value.saved
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    captureError(caught)
    if (caught instanceof ApiError && caught.code === 'settings_conflict') {
      await loadPreparation(id)
      notice.value = t.value.conflictReload
    }
  } finally {
    submitting.value = false
  }
}

async function confirmSelection(): Promise<void> {
  const id = state.value.preparationId
  if (!id || submitting.value || frozen.value || !canConfirm.value) {
    return
  }
  submitting.value = true
  errorText.value = ''
  errorCode.value = null
  try {
    const data = await api.post<{ preparation: EnvironmentPreparationPayload }>(
      `${detailPath(id)}/confirm`,
      { settingsRevision: form.settingsRevision },
    )
    state.value = applyPreparation(state.value, data.preparation)
    syncForm(data.preparation)
    stopStream()
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    captureError(caught)
    if (caught instanceof ApiError && caught.code === 'settings_conflict') {
      await loadPreparation(id)
      notice.value = t.value.conflictReload
    }
  } finally {
    submitting.value = false
  }
}

function toggleBatch(index: number): void {
  const next = new Set(expandedBatches.value)
  if (next.has(index)) {
    next.delete(index)
  } else {
    next.add(index)
  }
  expandedBatches.value = next
}

function selectRole(role: EnvironmentRole): void {
  selectedRoleId.value =
    selectedRoleId.value === role.roleId ? null : role.roleId
  void nextTick(() => {
    document
      .querySelector('[data-role-detail]')
      ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  })
}

function natureLabel(
  value: string | null | undefined,
  known: boolean | undefined,
): string {
  if (!known || !value) {
    return claimLabels.value.claimNatureUnknown
  }
  return claimLabels.value.claimNature[value] ?? claimLabels.value.claimNatureUnknown
}

function originLabel(origin: string): string {
  if (origin === 'question') {
    return t.value.question
  }
  if (origin === 'simulation') {
    return t.value.simulation
  }
  return t.value.initialTopics
}

function formatHours(hours: number[]): string {
  return hours.map((hour) => `${String(hour).padStart(2, '0')}:00`).join(' · ')
}

function workbenchSource(source: EnvironmentSource): string {
  return sourceWorkbenchUrl(
    props.projectId,
    source,
    route.query,
    state.value.buildId,
    state.value.preparationId,
  )
}

function directSource(source: EnvironmentSource): string {
  return sourceDirectUrl(source)
}

function simulationValue(role: EnvironmentRole): string[] {
  if (!role.simulation) {
    return []
  }
  return [
    `${t.value.motive}：${role.simulation.motive}`,
    `${t.value.stance}：${role.simulation.stance}`,
    `${t.value.activeHours}：${formatHours(role.simulation.activeHours)}`,
    `${t.value.postingFrequency}：${role.simulation.postingFrequency}`,
    `${t.value.responseDelay}：${role.simulation.responseDelayMinutes}`,
    `${t.value.influenceWeight}：${role.simulation.influenceWeight}`,
    `${t.value.platformPreference}：${role.simulation.platformKeys.join(' / ')}`,
    ...(role.simulation.topics ?? []).map(
      (topic) => `${t.value.simulationTopics}：${topic.text}`,
    ),
  ]
}

watch(
  () => [props.projectId, buildParam.value],
  () => {
    void load()
  },
  { immediate: true },
)

watch(
  () => props.lang,
  () => {
    if (errorCode.value) {
      errorText.value = reasonText(errorCode.value, errorText.value)
    }
  },
)

onMounted(() => {
  // 首次加载由上面的 immediate watcher 触发。
  newerGraphTimer = window.setInterval(() => {
    void pollNewerGraph()
  }, NEWER_GRAPH_POLL_MS)
})

onBeforeUnmount(() => {
  stopStream()
  if (newerGraphTimer !== undefined) {
    window.clearInterval(newerGraphTimer)
    newerGraphTimer = undefined
  }
  loadToken += 1
  preparationToken += 1
})
</script>

<template>
  <section class="environment-panel card" data-view="roles-environment">
    <header class="environment-head">
      <div>
        <p class="eyebrow">{{ t.title }}</p>
        <h2>{{ t.title }}</h2>
        <p class="muted">{{ t.intro }}</p>
      </div>
      <div v-if="state.buildId" class="environment-ids">
        <span class="badge">build {{ state.buildId.slice(0, 8) }}</span>
        <span v-if="state.snapshotId" class="badge">
          snapshot {{ state.snapshotId.slice(0, 8) }}
        </span>
      </div>
    </header>

    <p v-if="loading" class="muted" aria-live="polite">{{ t.loading }}</p>

    <div v-else-if="errorText" class="error-block" role="alert">
      <p>{{ errorText }}</p>
      <p v-if="errorCode" class="muted" data-error-code>{{ errorCode }}</p>
      <button type="button" class="primary" @click="load">{{ t.retry }}</button>
      <RouterLink
        v-if="state.buildId"
        class="button-link"
        :to="previousLink"
      >
        {{ t.backToGraph }}
      </RouterLink>
    </div>

    <template v-else>
      <section v-if="viewMode === 'graph-not-ready'" class="state-block">
        <h3>{{ t.graphNotReady }}</h3>
        <p class="muted">{{ t.graphNotReadyBody }}</p>
        <p v-if="state.reason" class="muted" data-reason>{{ state.reason }}</p>
        <RouterLink class="button-link" :to="previousLink">
          {{ t.backToGraph }}
        </RouterLink>
      </section>

      <template v-else>
        <section class="environment-question">
          <div>
            <span class="eyebrow">{{ t.question }}</span>
            <p><strong>{{ state.question }}</strong></p>
          </div>
          <div v-if="state.newerBuildId" class="notice">
            <strong>{{ t.newerGraph }}</strong>
            <span class="muted">{{ t.newerGraphHint }}</span>
          </div>
        </section>

        <section v-if="viewMode === 'not-started'" class="state-block">
          <h3>{{ t.notStarted }}</h3>
          <p class="muted">{{ t.startHint }}</p>
          <button
            type="button"
            class="primary"
            :disabled="submitting"
            @click="startPreparation"
          >
            {{ submitting ? t.starting : t.start }}
          </button>
        </section>

        <section
          v-else-if="viewMode === 'preparing'"
          class="state-block"
          data-phase="preparing"
        >
          <h3>{{ t.preparing }}</h3>
          <p class="muted">{{ t.preparingHint }}</p>
          <p class="muted">
            {{ t.profileProgress
              .replace('{profiled}', String(state.previewRoleCount))
              .replace('{total}', String(state.totalRoles || '?')) }}
          </p>
          <p v-if="streamStatus" class="muted" data-stream-status>
            {{ streamStatus }}
          </p>
          <div v-for="[index, roles] in batches" :key="index" class="preview-batch">
            <button type="button" class="ghost" @click="toggleBatch(index)">
              {{ t.previewBatch.replace('{index}', String(index + 1)) }}
            </button>
            <ul v-if="expandedBatches.has(index)" class="role-list">
              <li v-for="role in roles" :key="role.roleId">
                <button type="button" class="role-chip" @click="selectRole(role)">
                  <strong>{{ role.displayName }}</strong>
                  <span class="muted">{{ role.roleType }}</span>
                </button>
              </li>
            </ul>
          </div>
          <p class="muted">{{ t.previewOnly }}</p>
        </section>

        <section
          v-else-if="viewMode === 'failed' || viewMode === 'canceled'"
          class="state-block"
          :data-phase="viewMode"
        >
          <h3>{{ viewMode === 'failed' ? t.failed : t.canceled }}</h3>
          <p class="error">
            {{ reasonText(state.failureCode, state.failureMessage ?? '') }}
          </p>
          <p class="muted">{{ t.retryHint }}</p>
          <button
            type="button"
            class="primary"
            :disabled="submitting"
            @click="retryPreparation"
          >
            {{ t.retryPreparation }}
          </button>
        </section>

        <section
          v-else
          class="state-block"
          :data-phase="viewMode"
        >
          <h3>{{ viewMode === 'confirmed' ? t.confirmed : t.ready }}</h3>
          <p class="muted">
            {{ viewMode === 'confirmed' ? t.confirmedHint : t.readyHint }}
          </p>
          <p v-if="viewMode === 'confirmed'" class="notice">{{ t.frozenHint }}</p>
        </section>

        <div v-if="shownRoles.length" class="role-section">
          <h3>{{ t.roles }} · {{ t.roleCount.replace('{count}', String(shownRoles.length)) }}</h3>
          <ul class="role-list">
            <li v-for="role in shownRoles" :key="role.roleId">
              <button
                type="button"
                class="role-chip"
                :aria-pressed="selectedRoleId === role.roleId"
                @click="selectRole(role)"
              >
                <strong>{{ role.displayName }}</strong>
                <span class="badge">{{ roleTypeLabel(role.roleType, lang) }}</span>
              </button>
            </li>
          </ul>

          <article v-if="selectedRole" class="role-detail" data-role-detail>
            <h4>{{ t.roleDetail }}：{{ selectedRole.displayName }}</h4>
            <p class="muted">{{ roleTypeLabel(selectedRole.roleType, lang) }}</p>

            <section>
              <h5>{{ t.mention }}</h5>
              <p>{{ selectedRole.mention.text }}</p>
              <ul class="source-list">
                <li v-for="(source, index) in selectedRole.mention.sources" :key="index">
                  <span class="muted">{{ source.documentName }}</span>
                  <span v-if="source.pageNo">
                    · {{ t.pageNumber.replace('{page}', String(source.pageNo)) }}
                  </span>
                  <a :href="directSource(source)" target="_blank" rel="noopener">
                    {{ source.locator.kind === 'pdf' ? t.openPdf : t.openSource }}
                  </a>
                  <RouterLink :to="workbenchSource(source)">{{ t.backToMaterial }}</RouterLink>
                </li>
              </ul>
            </section>

            <section>
              <h5>{{ t.background }}</h5>
              <template v-if="selectedRole.background.stated">
                <p>{{ selectedRole.background.text }}</p>
                <ul class="source-list">
                  <li
                    v-for="(source, index) in selectedRole.background.sources"
                    :key="index"
                  >
                    <span class="muted">{{ source.documentName }}</span>
                    <RouterLink :to="workbenchSource(source)">{{ t.openSource }}</RouterLink>
                  </li>
                </ul>
              </template>
              <p v-else class="muted">{{ t.backgroundMissing }}</p>
            </section>

            <section>
              <h5>{{ t.expressedTopics }}</h5>
              <p v-if="!selectedRole.expressedTopics.length" class="muted">
                {{ t.noExpressed }}
              </p>
              <ul v-else class="topic-list">
                <li
                  v-for="(topic, index) in selectedRole.expressedTopics"
                  :key="index"
                >
                  <strong>{{ topic.text }}</strong>
                  <span class="badge">
                    {{ natureLabel(topic.claimNature, topic.claimNatureKnown) }}
                  </span>
                  <ul class="source-list">
                    <li
                      v-for="(source, sourceIndex) in topic.sources ?? []"
                      :key="sourceIndex"
                    >
                      <span class="muted">{{ source.documentName }}</span>
                      <RouterLink :to="workbenchSource(source)">
                        {{ t.openSource }}
                      </RouterLink>
                    </li>
                  </ul>
                </li>
              </ul>
            </section>

            <section>
              <h5>{{ t.involvedTopics }}</h5>
              <p v-if="!selectedRole.involvedTopics.length" class="muted">
                {{ t.backgroundMissing }}
              </p>
              <ul v-else class="topic-list">
                <li
                  v-for="(topic, index) in selectedRole.involvedTopics"
                  :key="index"
                >
                  <strong>{{ topic.text }}</strong>
                  <span class="badge">
                    {{ natureLabel(topic.claimNature, topic.claimNatureKnown) }}
                  </span>
                  <ul class="source-list">
                    <li
                      v-for="(source, sourceIndex) in topic.sources ?? []"
                      :key="sourceIndex"
                    >
                      <span class="muted">{{ source.documentName }}</span>
                      <RouterLink :to="workbenchSource(source)">
                        {{ t.openSource }}
                      </RouterLink>
                    </li>
                  </ul>
                </li>
              </ul>
            </section>

            <section v-if="selectedRole.simulation" class="simulation-block">
              <h5>{{ t.simulation }}</h5>
              <ul>
                <li v-for="line in simulationValue(selectedRole)" :key="line">
                  {{ line }}
                </li>
              </ul>
            </section>
          </article>
        </div>

        <section
          v-if="state.phase === 'ready'"
          class="environment-config"
        >
          <h3>{{ t.environmentConfig }}</h3>
          <div class="config-grid">
            <article>
              <h4>{{ t.platforms }}</h4>
              <ul>
                <li v-for="platform in state.platforms" :key="platform.platformKey">
                  <code>{{ platform.platformKey }}</code>
                  → {{ platform.nativePlatform }}
                </li>
              </ul>
            </article>
            <article>
              <h4>{{ t.initialTopics }}</h4>
              <ul class="topic-list">
                <li v-for="topic in state.initialTopics" :key="topic.topicId">
                  <strong>{{ topic.text }}</strong>
                  <span class="badge">{{ originLabel(topic.origin) }}</span>
                  <span
                    v-if="topic.origin === 'material'"
                    class="badge"
                  >
                    {{ natureLabel(topic.claimNature, topic.claimNatureKnown) }}
                  </span>
                </li>
              </ul>
            </article>
            <article>
              <h4>{{ t.firstSpeakers }}</h4>
              <ul>
                <li
                  v-for="item in state.activation?.firstSpeakerTopics ?? []"
                  :key="item.roleId"
                >
                  <strong>{{ roleName[item.roleId] }}</strong>
                  → {{ topicText[item.topicId] }}
                </li>
              </ul>
            </article>
            <article>
              <h4>{{ t.activationOrder }}</h4>
              <ol>
                <li v-for="(roleId, index) in state.activation?.order ?? []" :key="roleId">
                  {{ index + 1 }}. {{ roleName[roleId] }}
                  <span
                    v-if="state.activation?.firstSpeakerRoleIds.includes(roleId)"
                    class="badge"
                  >
                    {{ t.firstSpeakers }}
                  </span>
                </li>
              </ol>
            </article>
          </div>
        </section>

        <section v-if="state.phase === 'ready'" class="rounds-form">
          <h3>{{ t.roundsTitle }}</h3>
          <p v-if="frozen" class="notice">{{ t.frozen }}</p>
          <div class="rounds-grid">
            <label>
              {{ t.durationHours }}
              <input
                v-model.number="form.durationHours"
                type="number"
                min="1"
                max="720"
                :disabled="!editable || submitting"
              />
            </label>
            <label>
              {{ t.minutesPerRound }}
              <input
                v-model.number="form.minutesPerRound"
                type="number"
                min="1"
                max="1440"
                :disabled="!editable || submitting"
              />
            </label>
            <label>
              {{ t.roundsMode }}
              <select v-model="form.roundsMode" :disabled="!editable || submitting">
                <option value="auto">{{ t.auto }}</option>
                <option value="manual">{{ t.manual }}</option>
              </select>
            </label>
            <label v-if="form.roundsMode === 'manual'">
              {{ t.manualRounds }}
              <input
                v-model.number="form.manualRounds"
                type="number"
                min="1"
                max="1000"
                :disabled="!editable || submitting"
              />
            </label>
          </div>
          <p class="muted">
            {{ t.autoRounds }}：{{ state.settings?.autoRounds }} ·
            {{ t.effectiveRounds }}：<strong>{{ state.settings?.rounds }}</strong>
          </p>
          <button
            v-if="editable"
            type="button"
            class="primary"
            :disabled="submitting"
            @click="saveRounds"
          >
            {{ submitting ? t.saving : t.save }}
          </button>
          <p v-if="notice" class="notice">{{ notice }}</p>
          <button
            v-if="!frozen"
            type="button"
            class="primary"
            :disabled="!canConfirm"
            @click="confirmSelection"
          >
            {{ submitting ? t.confirming : t.confirm }}
          </button>
          <p v-if="!frozen" class="muted">
            {{ confirmBlockedReason || t.confirmNeeded }}
          </p>
          <p v-if="frozen" class="muted">
            {{ t.effectiveRounds }}：{{ state.frozenSelection?.settings.rounds }}
          </p>
          <RouterLink
            v-if="canOpenNextStage(state)"
            class="button-link"
            :to="nextLink"
          >
            {{ t.enterNextStage }}
          </RouterLink>
          <RouterLink v-else class="button-link" :to="previousLink">
            {{ t.backToGraph }}
          </RouterLink>
        </section>
      </template>
    </template>
  </section>
</template>

<style scoped>
.environment-panel {
  display: grid;
  gap: 1rem;
}
.environment-head,
.environment-question {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}
.environment-ids {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  flex-wrap: wrap;
}
.state-block {
  display: grid;
  gap: 0.5rem;
  padding: 0.75rem;
  border-left: 3px solid var(--accent, #3b82f6);
  background: rgba(148, 163, 184, 0.08);
}
.role-list,
.topic-list,
.source-list {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.35rem;
}
.role-list {
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
}
.role-chip {
  width: 100%;
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  text-align: left;
}
.role-detail,
.environment-config,
.rounds-form,
.preview-batch {
  display: grid;
  gap: 0.6rem;
  margin-top: 0.75rem;
  padding: 0.75rem;
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: 8px;
}
.role-detail section {
  display: grid;
  gap: 0.25rem;
}
.simulation-block {
  border-left: 3px solid #f59e0b;
  padding-left: 0.75rem;
}
.config-grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}
.rounds-grid {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
}
.rounds-grid label {
  display: grid;
  gap: 0.25rem;
}
.notice {
  padding: 0.5rem;
  border-radius: 6px;
  background: rgba(59, 130, 246, 0.12);
}
.source-list li {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.error-block {
  display: grid;
  gap: 0.5rem;
}
</style>
