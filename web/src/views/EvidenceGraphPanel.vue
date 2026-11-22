<script setup lang="ts">
/**
 * 证据图谱面板：开始建图、三态进度、D3 力图、来源定位与下一阶段入口。
 * 只读取真实 API；测试任务与材料面板各自维护状态，互不改写。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as d3 from 'd3'
import { api, ApiError } from '../api'
import { messagesFor } from '../i18n'
import type { MaterialDocument } from '../materials/format'
import { markLoggedOut } from '../session'
import type { Lang } from '../workbench/stage-links'
import { stageLocation } from '../workbench/stage-links'
import {
  applyGraphEvent,
  applyGraphPayload,
  applyGraphSnapshot,
  buildQuery,
  canOpenNextStage,
  createGraphState,
  graphViewMode,
  isGraphActive,
  type GraphBuildData,
  type GraphEdgeData,
  type GraphEventPayload,
  type GraphNodeData,
  type GraphPayload,
  type GraphSource,
} from '../workbench/graph-state'
import { openJobStream, type JobStreamHandle } from '../workbench/job-stream'
import type { JobEvent } from '../workbench/job-log'

const props = defineProps<{ projectId: string; lang: Lang }>()
const route = useRoute()
const router = useRouter()
const t = computed(() => messagesFor(props.lang).graph)

interface SimNode extends GraphNodeData {
  x?: number
  y?: number
  vx?: number
  vy?: number
  fx?: number | null
  fy?: number | null
}
interface SimLink {
  source: string | SimNode
  target: string | SimNode
  label: string
  origin: string
  key: string
}

const state = ref(createGraphState())
const documents = ref<MaterialDocument[]>([])
const selectedIds = ref<string[]>([])
const question = ref('')
const loading = ref(false)
const starting = ref(false)
const error = ref('')
const note = ref('')
const showEdgeLabels = ref(true)
const maximized = ref(false)
const selection = ref<{ kind: 'node' | 'edge'; item: GraphNodeData | GraphEdgeData } | null>(null)
const sourceOpen = ref(false)
const sourceText = ref('')
const sourceError = ref('')
const sourceRef = ref<HTMLElement | null>(null)
const svgRef = ref<SVGSVGElement | null>(null)
const canvasRef = ref<HTMLElement | null>(null)
const stream = ref<JobStreamHandle | null>(null)
let requestSeq = 0
let pollTimer: number | null = null
let renderQueue: Promise<void> = Promise.resolve()
let simulation: d3.Simulation<SimNode, undefined> | null = null
let zoomBehavior: d3.ZoomBehavior<SVGSVGElement, unknown> | null = null
let svgSelection: d3.Selection<SVGSVGElement, unknown, null, undefined> | null = null

const readyDocuments = computed(() =>
  documents.value.filter((item) => item.status === 'ready'),
)
const viewMode = computed(() => graphViewMode(state.value))
const active = computed(() => isGraphActive(state.value))
const canContinue = computed(() => canOpenNextStage(state.value))
const phaseLabel = computed(() => {
  const phase = state.value.phase
  return phase ? t.value.phaseLabel[phase] : ''
})
const failureText = computed(() => {
  const code = state.value.failureCode
  if (!code) {
    return ''
  }
  return t.value.failureCodes[code] ?? state.value.failureMessage ?? code
})
const selectedCountHint = computed(() =>
  t.value.selected
    .replace('{count}', String(selectedIds.value.length))
    .replace('{max}', '5'),
)

function graphPath(buildId?: string | null): string {
  const base = `/api/projects/${encodeURIComponent(props.projectId)}/evidence-graph`
  return buildId ? `${base}?build=${encodeURIComponent(buildId)}` : base
}

async function handleAuth(caught: unknown): Promise<boolean> {
  if (caught instanceof ApiError && caught.status === 401) {
    markLoggedOut()
    await router.replace({ name: 'login', query: { next: route.fullPath } })
    return true
  }
  return false
}

function describeError(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 503) {
      return t.value.serviceUnavailable
    }
    return caught.message
  }
  return t.value.loadFailed
}

async function loadDocuments(): Promise<void> {
  try {
    const data = await api.get<{ documents: MaterialDocument[] }>(
      `/api/projects/${encodeURIComponent(props.projectId)}/documents`,
    )
    documents.value = data.documents
    selectedIds.value = selectedIds.value.filter((id) =>
      data.documents.some((item) => item.documentId === id && item.status === 'ready'),
    )
  } catch (caught) {
    if (!(await handleAuth(caught))) {
      error.value = describeError(caught)
    }
  }
}

function detach(): void {
  stream.value?.close()
  stream.value = null
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
    if (isGraphActive(state.value)) {
      void loadGraph(state.value.buildId)
    } else {
      stopPolling()
    }
  }, 1200)
}

function attach(build: GraphBuildData): void {
  detach()
  stream.value = openJobStream({
    projectId: props.projectId,
    jobId: build.jobId,
    after: 0,
    onStatus: (status) => {
      if (status === 'ended') {
        stopPolling()
        enqueueRender(() => loadGraph(build.buildId))
      } else if (status === 'interrupted' || status === 'reconnecting') {
        // SSE 不可用时回退到读取已发布快照，不制造任何本地进度。
        startPolling()
      }
    },
    onEvent: (event: JobEvent) => {
      state.value = applyGraphEvent(
        state.value,
        event.payload as GraphEventPayload,
      )
      const revision = event.payload.snapshotRevision
      const partial = event.payload.partial === true
      if (event.type === 'progress' && partial && typeof revision === 'number') {
        // 按真实发布顺序读取对应版本，避免快速完成时只看到最终图。
        stopPolling()
        enqueueRender(() => loadSnapshotRevision(build.buildId, revision))
        return
      }
      if (event.type === 'completed' || event.type === 'failed' || event.type === 'canceled') {
        stopPolling()
        enqueueRender(() => loadGraph(build.buildId))
      }
    },
    onEnd: () => {
      stopPolling()
      enqueueRender(() => loadGraph(build.buildId))
    },
  })
  if (isGraphActive(state.value)) {
    startPolling()
  }
}

async function loadGraph(buildId?: string | null): Promise<void> {
  const request = ++requestSeq
  const expected = buildId ?? state.value.buildId
  loading.value = !state.value.buildId
  error.value = ''
  try {
    const data = await api.get<GraphPayload>(graphPath(buildId))
    if (request !== requestSeq) {
      return
    }
    if (expected && data.build.buildId !== expected) {
      state.value = { ...state.value, stale: true }
      return
    }
    const targetChanged =
      state.value.buildId !== null && state.value.buildId !== data.build.buildId
    state.value = applyGraphPayload(
      state.value,
      data,
      Boolean(buildId) || state.value.buildId === null || targetChanged,
    )
    if (isGraphActive(state.value)) {
      startPolling()
    } else {
      stopPolling()
    }
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    if (caught instanceof ApiError && caught.status === 404 && !state.value.buildId) {
      state.value = createGraphState()
      return
    }
    error.value = describeError(caught)
  } finally {
    if (request === requestSeq) {
      loading.value = false
    }
  }
}

function enqueueRender(task: () => Promise<void>): void {
  renderQueue = renderQueue.then(task).catch(() => {})
}

async function loadSnapshotRevision(
  buildId: string,
  revision: number,
): Promise<void> {
  const request = ++requestSeq
  try {
    const data = await api.get<GraphPayload>(
      `/api/projects/${encodeURIComponent(props.projectId)}/evidence-graph/builds/${encodeURIComponent(buildId)}/snapshots/${revision}`,
    )
    if (request !== requestSeq || !data.snapshot) {
      return
    }
    state.value = applyGraphSnapshot(state.value, data.snapshot)
  } catch (caught) {
    if (!(await handleAuth(caught))) {
      // 部分快照读取失败时保留当前画面，由实时事件或最终读取继续推进。
    }
  }
}

function toggleMaterial(id: string): void {
  if (selectedIds.value.includes(id)) {
    selectedIds.value = selectedIds.value.filter((item) => item !== id)
    return
  }
  if (selectedIds.value.length >= 5) {
    note.value = t.value.tooMany
    return
  }
  note.value = ''
  selectedIds.value = [...selectedIds.value, id]
}

async function startBuild(): Promise<void> {
  note.value = ''
  error.value = ''
  const trimmed = question.value.trim()
  if (!trimmed) {
    error.value = t.value.questionRequired
    return
  }
  if (selectedIds.value.length === 0) {
    error.value = t.value.emptyMaterials
    return
  }
  if (selectedIds.value.length > 5) {
    error.value = t.value.tooMany
    return
  }
  starting.value = true
  try {
    const data = await api.post<{ build: GraphBuildData }>(
      `/api/projects/${encodeURIComponent(props.projectId)}/evidence-graph/builds`,
      { documentIds: selectedIds.value, question: trimmed },
    )
    requestSeq += 1
    state.value = createGraphState()
    state.value = applyGraphPayload(state.value, {
      build: data.build,
      snapshot: null,
    }, true)
    await router.replace({
      path: route.path,
      query: buildQuery(route.query, data.build.buildId),
    })
    attach(data.build)
    await loadGraph(data.build.buildId)
  } catch (caught) {
    if (!(await handleAuth(caught))) {
      error.value = describeError(caught)
    }
  } finally {
    starting.value = false
  }
}

function closeSource(): void {
  sourceOpen.value = false
  window.getSelection()?.removeAllRanges()
}

function highlight(start: number, end: number): void {
  const element = sourceRef.value
  if (!element) {
    return
  }
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
  const node = walker.nextNode()
  if (!node) {
    return
  }
  const text = node as Text
  const length = text.data.length
  const range = document.createRange()
  range.setStart(text, Math.min(Math.max(start, 0), length))
  range.setEnd(text, Math.min(Math.max(end, 0), length))
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)
  const rect = range.getBoundingClientRect()
  if (rect && rect.top) {
    window.scrollTo({
      top: window.scrollY + rect.top - 140,
      behavior: 'smooth',
    })
  }
}

async function openSource(source: GraphSource): Promise<void> {
  if (source.locator.kind === 'pdf') {
    window.open(source.locator.url, '_blank', 'noopener')
    return
  }
  sourceOpen.value = true
  sourceError.value = ''
  sourceText.value = ''
  try {
    sourceText.value = await api.text(source.locator.url)
    await nextTick()
    highlight(
      source.locator.sourceStart ?? source.sourceStart,
      source.locator.sourceEnd ?? source.sourceEnd,
    )
  } catch (caught) {
    if (!(await handleAuth(caught))) {
      sourceError.value = t.value.sourceFailed
    }
  }
}

function selectNode(node: GraphNodeData): void {
  selection.value = { kind: 'node', item: node }
}

function selectEdge(edge: GraphEdgeData): void {
  selection.value = { kind: 'edge', item: edge }
}

function clearSelection(): void {
  selection.value = null
}

function renderGraph(): void {
  const element = svgRef.value
  if (!element) {
    return
  }
  simulation?.stop()
  const svg = d3.select(element)
  svg.selectAll('*').remove()
  svgSelection = svg
  const width = element.clientWidth || 900
  const height = element.clientHeight || 520
  svg.attr('viewBox', `0 0 ${width} ${height}`)
  const root = svg.append('g')
  const snapshot = state.value.snapshot
  if (!snapshot || snapshot.nodes.length === 0) {
    return
  }
  const nodes: SimNode[] = snapshot.nodes.map((node) => ({ ...node }))
  const links: SimLink[] = snapshot.edges.map((edge) => ({
    source: edge.sourceKey,
    target: edge.targetKey,
    label: edge.label,
    origin: edge.origin,
    key: edge.key,
  }))
  for (const link of links) {
    link.source = nodes.find((node) => node.key === link.source) ?? link.source
    link.target = nodes.find((node) => node.key === link.target) ?? link.target
  }
  const linkSelection = root
    .append('g')
    .attr('class', 'graph-links')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('class', (link) => `graph-link graph-link--${link.origin}`)
    .style('cursor', 'pointer')
    .on('click', (_, link) => {
      const edge = snapshot.edges.find((item) => item.key === link.key)
      if (edge) {
        selectEdge(edge)
      }
    })
  const linkLabels = root
    .append('g')
    .attr('class', 'graph-link-labels')
    .selectAll('text')
    .data(links)
    .join('text')
    .attr('class', 'graph-link-label')
    .text((link) => link.label)
  const nodeSelection = root
    .append('g')
    .attr('class', 'graph-nodes')
    .selectAll('g')
    .data(nodes)
    .join('g')
    .attr('class', (node) => `graph-node graph-node--${node.origin}`)
    .style('cursor', 'pointer')
    .on('click', (_, node) => {
      const value = snapshot.nodes.find((item) => item.key === node.key)
      if (value) {
        selectNode(value)
      }
    })
  nodeSelection
    .append('circle')
    .attr('r', (node) => (node.origin === 'hypothesis' ? 13 : 17))
  nodeSelection
    .append('text')
    .attr('class', 'graph-node-label')
    .attr('text-anchor', 'middle')
    .attr('dy', '0.35em')
    .text((node) => node.label.slice(0, 10))
  nodeSelection.append('title').text((node) => node.label)

  const drag = d3
    .drag<SVGGElement, SimNode>()
    .on('start', (event, node) => {
      if (!event.active) {
        simulation?.alphaTarget(0.25).restart()
      }
      node.fx = node.x
      node.fy = node.y
    })
    .on('drag', (event, node) => {
      node.fx = event.x
      node.fy = event.y
    })
    .on('end', (event, node) => {
      if (!event.active) {
        simulation?.alphaTarget(0)
      }
      node.fx = null
      node.fy = null
    })
  ;(nodeSelection as unknown as d3.Selection<SVGGElement, SimNode, SVGGElement, unknown>).call(drag)

  const zoom = d3
    .zoom<SVGSVGElement, unknown>()
    .scaleExtent([0.2, 3])
    .on('zoom', (event) => {
      root.attr('transform', event.transform)
    })
  zoomBehavior = zoom
  svg.call(zoom)

  simulation = d3
    .forceSimulation<SimNode>(nodes)
    .force(
      'link',
      d3
        .forceLink<SimNode, SimLink>(links)
        .id((node) => node.key)
        .distance(120)
        .strength(0.7),
    )
    .force('charge', d3.forceManyBody().strength(-320))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide(38))
    .on('tick', () => {
      linkSelection
        .attr('x1', (link) => (link.source as SimNode).x ?? 0)
        .attr('y1', (link) => (link.source as SimNode).y ?? 0)
        .attr('x2', (link) => (link.target as SimNode).x ?? 0)
        .attr('y2', (link) => (link.target as SimNode).y ?? 0)
      linkLabels
        .attr('x', (link) =>
          (((link.source as SimNode).x ?? 0) + ((link.target as SimNode).x ?? 0)) / 2,
        )
        .attr('y', (link) =>
          (((link.source as SimNode).y ?? 0) + ((link.target as SimNode).y ?? 0)) / 2 - 4,
        )
      nodeSelection.attr(
        'transform',
        (node) => `translate(${node.x ?? 0},${node.y ?? 0})`,
      )
    })
  linkLabels.style('display', showEdgeLabels.value ? '' : 'none')
}

function fitCanvas(): void {
  if (svgSelection && zoomBehavior) {
    svgSelection.transition().duration(250).call(zoomBehavior.transform, d3.zoomIdentity)
  }
}

async function refresh(): Promise<void> {
  await loadGraph(state.value.buildId)
}

async function toggleMaximize(): Promise<void> {
  maximized.value = !maximized.value
  await nextTick()
  renderGraph()
}

function goNextStage(): void {
  if (!canContinue.value || !state.value.buildId) {
    return
  }
  void router.push(
    stageLocation(props.projectId, 'roles-environment', {
      ...route.query,
      build: state.value.buildId,
    }),
  )
}

watch(
  () => state.value.snapshot,
  async (snapshot) => {
    if (snapshot) {
      await nextTick()
      renderGraph()
    }
  },
)

watch(showEdgeLabels, async () => {
  await nextTick()
  renderGraph()
})

watch(
  () => route.query.build,
  (value) => {
    const buildId = typeof value === 'string' ? value : null
    if (buildId && buildId !== state.value.buildId) {
      state.value = createGraphState()
      void loadGraph(buildId)
    }
  },
)

watch(
  () => props.projectId,
  async () => {
    detach()
    stopPolling()
    state.value = createGraphState()
    selectedIds.value = []
    await loadDocuments()
    await loadGraph(
      typeof route.query.build === 'string' ? route.query.build : null,
    )
  },
)

onMounted(async () => {
  await loadDocuments()
  await loadGraph(
    typeof route.query.build === 'string' ? route.query.build : null,
  )
  window.addEventListener('resize', renderGraph)
})

onBeforeUnmount(() => {
  detach()
  stopPolling()
  simulation?.stop()
  window.removeEventListener('resize', renderGraph)
})
</script>

<template>
  <section
    class="evidence-graph card"
    :class="{ 'evidence-graph--max': maximized }"
    data-view="evidence-graph"
    :data-phase="state.phase ?? 'none'"
    :data-build-id="state.buildId ?? ''"
    :data-view-mode="viewMode"
    :data-revision="state.snapshotRevision ?? ''"
  >
    <div class="card-heading">
      <div>
        <h2>{{ t.title }}</h2>
        <p class="muted">{{ t.intro }}</p>
      </div>
      <span class="pill" :data-phase="state.phase ?? 'none'">
        {{ phaseLabel || t.empty }}
      </span>
    </div>

    <div class="graph-controls">
      <button
        type="button"
        class="ghost"
        data-action="graph-refresh"
        :disabled="loading"
        @click="refresh"
      >
        {{ t.refresh }}
      </button>
      <button type="button" class="ghost" data-action="graph-fit" @click="fitCanvas">
        {{ t.fit }}
      </button>
      <label class="graph-toggle">
        <input v-model="showEdgeLabels" type="checkbox" />
        {{ t.edgeLabels }}
      </label>
      <button type="button" class="ghost" data-action="graph-maximize" @click="toggleMaximize">
        {{ maximized ? t.restore : t.maximize }}
      </button>
    </div>

    <div class="graph-form">
      <fieldset class="graph-materials">
        <legend>{{ t.materialsTitle }}</legend>
        <p v-if="readyDocuments.length === 0" class="muted">{{ t.emptyMaterials }}</p>
        <label v-for="item in readyDocuments" :key="item.documentId" class="graph-material">
          <input
            type="checkbox"
            :value="item.documentId"
            :checked="selectedIds.includes(item.documentId)"
            @change="toggleMaterial(item.documentId)"
          />
          <span>{{ item.name }}</span>
        </label>
        <p class="fine-print">{{ selectedCountHint }} · {{ t.maxDocs.replace('{max}', '5') }}</p>
      </fieldset>
      <label class="graph-question">
        <span>{{ t.questionLabel }}</span>
        <textarea
          v-model="question"
          rows="2"
          maxlength="500"
          :placeholder="t.questionPlaceholder"
        />
      </label>
      <button
        type="button"
        class="primary"
        data-action="graph-start"
        :disabled="starting || active"
        @click="startBuild"
      >
        {{ starting ? t.starting : t.start }}
      </button>
    </div>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="note" class="notice" role="status">{{ note }}</p>
    <p v-if="state.stale" class="notice" role="status">{{ t.stale }}</p>

    <div v-if="state.buildId" class="graph-progress" :data-phase="state.phase ?? 'none'">
      <span class="badge" :data-state="state.phase ?? ''">{{ phaseLabel }}</span>
      <span>{{ t.waitingIndex
        .replace('{accepted}', String(state.acceptedEpisodes))
        .replace('{confirmed}', String(state.confirmedEpisodes))
        .replace('{planned}', String(state.plannedEpisodes)) }}</span>
      <span v-if="state.snapshotRevision !== null">
        {{ t.revision }} {{ state.snapshotRevision }}
      </span>
      <span>{{ t.nodes }} {{ state.nodeCount }} · {{ t.edges }} {{ state.edgeCount }}</span>
    </div>

    <p v-if="viewMode === 'waiting'" class="muted graph-state-note">
      {{ state.buildId ? t.drawing : t.empty }}
    </p>
    <p v-if="viewMode === 'drawing'" class="muted graph-state-note">
      {{ t.drawing }}
    </p>
    <p v-if="viewMode === 'ready'" class="notice graph-state-note">{{ t.ready }}</p>
    <p v-if="viewMode === 'failed'" class="error graph-state-note">
      {{ state.phase === 'canceled' ? t.canceled : `${t.failed}：${failureText}` }}
    </p>

    <div ref="canvasRef" class="graph-canvas-wrap">
      <svg ref="svgRef" class="graph-canvas" role="img" :aria-label="t.title" />
      <p v-if="!state.snapshot" class="graph-placeholder muted">
        {{ state.buildId ? t.drawing : t.empty }}
      </p>
    </div>
    <p class="fine-print">{{ t.zoomHint }}</p>

    <div class="graph-legend">
      <strong>{{ t.legend }}</strong>
      <span><i class="legend-dot legend-dot--material" /> {{ t.material }}</span>
      <span><i class="legend-dot legend-dot--hypothesis" /> {{ t.hypothesis }}</span>
    </div>

    <div class="graph-detail">
      <h3>{{ t.details }}</h3>
      <p v-if="!selection" class="muted">{{ t.noSelection }}</p>
      <template v-else>
        <p class="eyebrow">{{ selection.kind === 'node' ? t.node : t.edge }}</p>
        <h4>{{ selection.item.label }}</h4>
        <p class="muted">
          {{ t.origin }}：{{ selection.item.origin === 'material' ? t.material : t.hypothesis }}
        </p>
        <p v-if="selection.item.summary">{{ t.summary }}：{{ selection.item.summary }}</p>
        <h5>{{ t.sources }}</h5>
        <p v-if="selection.item.sources.length === 0" class="muted">{{ t.noSources }}</p>
        <ul class="graph-source-list">
          <li v-for="(source, index) in selection.item.sources" :key="index">
            <p class="fine-print">
              {{ source.documentName }} ·
              <template v-if="source.pageNo !== null">p.{{ source.pageNo }}</template>
              <template v-else-if="source.paragraphStart !== null">P.{{ source.paragraphStart }}</template>
            </p>
            <blockquote>{{ source.quote }}</blockquote>
            <a
              v-if="source.locator.kind === 'pdf'"
              class="quiet-link"
              :href="source.locator.url"
              target="_blank"
              rel="noopener"
              data-action="open-graph-source"
            >
              {{ t.openSource }}
            </a>
            <button
              v-else
              type="button"
              class="quiet-link"
              data-action="open-graph-source"
              @click="openSource(source)"
            >
              {{ t.openSource }}
            </button>
          </li>
        </ul>
        <button type="button" class="ghost" @click="clearSelection">×</button>
      </template>
    </div>

    <section v-if="sourceOpen" class="source-panel" aria-live="polite">
      <div class="card-heading">
        <h4>{{ t.sourceTitle }}</h4>
        <button type="button" class="quiet-link" @click="closeSource">
          {{ t.sourceClose }}
        </button>
      </div>
      <p v-if="sourceError" class="error" role="alert">{{ sourceError }}</p>
      <pre ref="sourceRef" class="source-text">{{ sourceText }}</pre>
    </section>

    <div class="graph-next">
      <button
        type="button"
        class="primary"
        data-action="graph-next-stage"
        :disabled="!canContinue"
        @click="goNextStage"
      >
        {{ t.next }}
      </button>
      <p class="fine-print">{{ canContinue ? t.nextHint : t.nextBlocked }}</p>
    </div>
  </section>
</template>
