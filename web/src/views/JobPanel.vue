<script setup lang="ts">
/**
 * 测试任务面板：开始、取消、状态、事件日志与副作用计数。
 *
 * 只读取真实 API；不写阶段内容状态，也不改动路由（项目/阶段/布局/语言保持不变）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, ApiError } from '../api'
import { messagesFor } from '../i18n'
import type { Lang } from '../workbench/stage-links'
import {
  applyJobEvent,
  applyJobEvents,
  createJobLog,
  type JobEvent,
  type JobLogState,
} from '../workbench/job-log'
import {
  openJobStream,
  type JobStreamHandle,
  type JobStreamStatus,
} from '../workbench/job-stream'
import {
  clearedOnReadSuccess,
  pickBannerSource,
} from '../workbench/job-panel-state'

const props = defineProps<{
  projectId: string
  stage: string
  lang: Lang
}>()

interface JobBody {
  jobId: string
  runId: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'
  stage: string
  stepsApplied: number
  totalSteps: number
  attempts: number
  awaitingTakeover: boolean
  leaseExpiredSeconds: number
  terminalReason: string | null
}

interface EffectsBody {
  runId: string
  total: number
  distinctSteps: number
  steps: number[]
  totalSteps: number
  duplicated: boolean
}

const t = computed(() => messagesFor(props.lang))

const job = ref<JobBody | null>(null)
const log = ref<JobLogState>(createJobLog())
const streamStatus = ref<JobStreamStatus>('connecting')
const effects = ref<EffectsBody | null>(null)
const error = ref('')
const pollError = ref('')
const listFailed = ref(false)
const note = ref('')
const busy = ref(false)
let handle: JobStreamHandle | null = null
let pollTimer: number | null = null

const listUrl = computed(
  () => `/api/projects/${encodeURIComponent(props.projectId)}/jobs`,
)
const detailUrl = computed(() =>
  job.value ? `${listUrl.value}/${encodeURIComponent(job.value.jobId)}` : '',
)

const isActive = (status: JobBody['status']): boolean =>
  status === 'queued' || status === 'running'

const terminalEvent = computed<JobEvent | null>(() => {
  for (let index = log.value.events.length - 1; index >= 0; index -= 1) {
    const item = log.value.events[index]
    if (item.type === 'failed' || item.type === 'canceled' || item.type === 'completed') {
      return item
    }
  }
  return null
})

const banner = computed<{ kind: string; text: string } | null>(() => {
  const terminal = terminalEvent.value
  const source = pickBannerSource({
    terminalType: terminal?.type ?? null,
    streamStatus: streamStatus.value,
    error: error.value,
    pollError: pollError.value,
    awaitingTakeover: Boolean(job.value?.awaitingTakeover),
    gap: log.value.gap,
  })
  if (source === null) {
    return null
  }
  if (source === 'failed') {
    const reason = String(
      (terminal?.payload.reason as string | undefined) ?? 'controlled_failure',
    )
    return {
      kind: 'failed',
      text: t.value.job.failedBanner.replace('{reason}', reason),
    }
  }
  if (source === 'canceled') {
    const step = String((terminal?.payload.stepsApplied as number | undefined) ?? 0)
    return {
      kind: 'canceled',
      text: t.value.job.canceledBanner.replace('{step}', step),
    }
  }
  if (source === 'stream') {
    return {
      kind: 'stream',
      text: t.value.job.streamInterrupted.replace(
        '{seq}',
        String(log.value.cursor),
      ),
    }
  }
  if (source === 'error') {
    return { kind: 'service', text: error.value }
  }
  if (source === 'poll') {
    return { kind: 'service', text: pollError.value }
  }
  if (source === 'takeover') {
    return {
      kind: 'takeover',
      text: t.value.job.awaitingTakeover.replace(
        '{seconds}',
        String(Math.round(job.value?.leaseExpiredSeconds ?? 0)),
      ),
    }
  }
  return {
    kind: 'gap',
    text: t.value.job.gap.replace('{seq}', String(log.value.cursor)),
  }
})

function describeError(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 503) {
      return t.value.job.serviceUnavailable
    }
    return caught.message
  }
  return t.value.job.loadFailed
}

function detach(): void {
  handle?.close()
  handle = null
}

function stopPolling(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling(): void {
  stopPolling()
  pollTimer = window.setInterval(() => {
    void refreshJob()
  }, 1000)
}

function attach(current: JobBody, after: number): void {
  detach()
  if (after === 0) {
    log.value = createJobLog(current.runId)
  }
  handle = openJobStream({
    projectId: props.projectId,
    jobId: current.jobId,
    after,
    onStatus: (status) => {
      streamStatus.value = status
      if (status === 'live' || status === 'ended') {
        // 连接恢复即代表读取路径恢复：清掉轮询错误，避免旧提示压住新状态。
        pollError.value = ''
      }
    },
    onEvent: (event) => {
      log.value = applyJobEvent(log.value, event)
      if (log.value.ended) {
        stopPolling()
        void loadEffects()
      }
    },
    onEnd: () => {
      streamStatus.value = 'ended'
      stopPolling()
      void refreshJob()
    },
  })
  if (isActive(current.status)) {
    startPolling()
  }
}

async function loadEffects(): Promise<void> {
  if (!job.value) {
    return
  }
  try {
    effects.value = await api.get<EffectsBody>(
      `${listUrl.value}/${encodeURIComponent(job.value.jobId)}/effects`,
    )
  } catch {
    effects.value = null
  }
}

async function refreshJob(): Promise<void> {
  if (!job.value) {
    return
  }
  try {
    const data = await api.get<{ job: JobBody }>(detailUrl.value)
    job.value = data.job
    const cleared = clearedOnReadSuccess()
    error.value = cleared.error
    pollError.value = cleared.pollError
    listFailed.value = false
    if (!isActive(data.job.status)) {
      stopPolling()
    }
  } catch (caught) {
    // 轮询失败单独记录，不覆盖 SSE 重连提示；成功读取时自动清空。
    pollError.value = describeError(caught)
  }
}

async function loadJobs(): Promise<void> {
  error.value = ''
  pollError.value = ''
  note.value = ''
  detach()
  stopPolling()
  streamStatus.value = 'ended'
  try {
    const data = await api.get<{ jobs: JobBody[] }>(listUrl.value)
    const cleared = clearedOnReadSuccess()
    error.value = cleared.error
    pollError.value = cleared.pollError
    listFailed.value = false
    const latest = data.jobs[0] ?? null
    job.value = latest
    streamStatus.value = 'ended'
    if (latest) {
      attach(latest, 0)
      void loadEffects()
    } else {
      log.value = createJobLog()
      effects.value = null
    }
  } catch (caught) {
    // 首次列表读取失败：保留上下文并给出“重试读取”，只发 GET。
    listFailed.value = true
    error.value = describeError(caught)
  }
}

async function start(): Promise<void> {
  busy.value = true
  error.value = ''
  note.value = ''
  try {
    const data = await api.post<{ job: JobBody }>(listUrl.value, {
      operation: 'test-simulation',
      stage: props.stage,
    })
    job.value = data.job
    error.value = ''
    pollError.value = ''
    listFailed.value = false
    attach(data.job, 0)
    void loadEffects()
  } catch (caught) {
    error.value = describeError(caught)
  } finally {
    busy.value = false
  }
}

async function cancel(): Promise<void> {
  if (!job.value) {
    return
  }
  busy.value = true
  note.value = ''
  try {
    const data = await api.post<{
      job: JobBody
      cancelApplied: boolean
    }>(`${listUrl.value}/${encodeURIComponent(job.value.jobId)}/cancel`)
    job.value = data.job
    error.value = ''
    pollError.value = ''
    if (!data.cancelApplied) {
      note.value = t.value.job.cancelNotApplied
    }
    void refreshJob()
  } catch (caught) {
    error.value = describeError(caught)
  } finally {
    busy.value = false
  }
}

async function reconnect(): Promise<void> {
  if (!job.value) {
    return
  }
  error.value = ''
  pollError.value = ''
  attach(job.value, log.value.cursor)
}

async function resync(): Promise<void> {
  if (!job.value) {
    return
  }
  error.value = ''
  try {
    const data = await api.get<{
      events: JobEvent[]
      lastSeq: number
      terminal: boolean
    }>(
      `${listUrl.value}/${encodeURIComponent(job.value.jobId)}/events?format=json&after=${log.value.cursor}`,
    )
    log.value = applyJobEvents(log.value, data.events)
    if (!data.terminal) {
      attach(job.value, log.value.cursor)
    }
  } catch (caught) {
    error.value = describeError(caught)
  }
}

function eventLabel(event: JobEvent): string {
  const label = event.payload.labels
  if (typeof label === 'string') {
    return label
  }
  return `${event.type} · ${event.stage}`
}

watch(
  () => props.projectId,
  () => {
    void loadJobs()
  },
)

onMounted(() => {
  void loadJobs()
})

onBeforeUnmount(() => {
  detach()
  stopPolling()
})
</script>

<template>
  <section
    class="context-card card job-panel"
    data-view="job-panel"
    :data-status="job?.status ?? 'none'"
    :data-stream="streamStatus"
    :data-awaiting-takeover="job?.awaitingTakeover ? 'true' : 'false'"
  >
    <h2>{{ t.job.title }}</h2>
    <p class="muted">{{ t.job.notice }}</p>

    <div class="job-actions">
      <button
        v-if="listFailed"
        type="button"
        class="primary"
        data-action="retry-list"
        :disabled="busy"
        @click="loadJobs"
      >
        {{ t.job.retryLoad }}
      </button>
      <button
        type="button"
        class="primary"
        :disabled="busy || listFailed || (job ? isActive(job.status) : false)"
        @click="start"
      >
        {{ t.job.start }}
      </button>
      <button
        v-if="job && isActive(job.status)"
        type="button"
        class="ghost"
        :disabled="busy"
        @click="cancel"
      >
        {{ t.job.cancel }}
      </button>
      <button
        v-if="job && (streamStatus === 'interrupted' || streamStatus === 'reconnecting')"
        type="button"
        class="ghost"
        @click="reconnect"
      >
        {{ t.job.reconnect }}
      </button>
      <button
        v-if="log.gap"
        type="button"
        class="ghost"
        @click="resync"
      >
        {{ t.job.reconnect }}
      </button>
    </div>

    <p v-if="banner" class="job-banner" :data-kind="banner.kind" role="status">
      {{ banner.text }}
    </p>
    <p v-if="note" class="muted" role="status">{{ note }}</p>

    <dl v-if="job" class="job-meta">
      <div>
        <dt>{{ t.job.status }}</dt>
        <dd>
          <span class="badge" :data-state="job.status">
            {{ t.job.statusLabel[job.status] }}
          </span>
        </dd>
      </div>
      <div>
        <dt>{{ t.job.progress }}</dt>
        <dd>{{ job.stepsApplied }} / {{ job.totalSteps }}</dd>
      </div>
      <div>
        <dt>{{ t.job.attempts }}</dt>
        <dd>{{ job.attempts }}</dd>
      </div>
      <div>
        <dt>{{ t.job.effects }}</dt>
        <dd>
          <template v-if="effects">
            {{ effects.total }} / {{ effects.totalSteps }}
            ({{ effects.distinctSteps }})
          </template>
          <template v-else>—</template>
        </dd>
      </div>
      <div>
        <dt>{{ t.job.jobId }}</dt>
        <dd><code>{{ job.jobId }}</code></dd>
      </div>
      <div>
        <dt>{{ t.job.runId }}</dt>
        <dd><code>{{ job.runId }}</code></dd>
      </div>
    </dl>

    <p v-else class="muted">{{ t.job.emptyLog }}</p>

    <div v-if="job" class="job-log">
      <h3>{{ t.job.log }} ({{ log.events.length }})</h3>
      <ol>
        <li
          v-for="event in log.events"
          :key="event.seq"
          :data-seq="event.seq"
          :data-type="event.type"
        >
          <span class="job-seq">#{{ event.seq }}</span>
          <span class="job-type" :data-type="event.type">{{ event.type }}</span>
          <span class="muted">{{ eventLabel(event) }}</span>
          <time class="muted">{{ event.simulatedAt }}</time>
        </li>
      </ol>
      <button
        type="button"
        class="ghost"
        :disabled="busy"
        @click="loadEffects"
      >
        {{ t.job.refreshEffects }}
      </button>
    </div>
  </section>
</template>
