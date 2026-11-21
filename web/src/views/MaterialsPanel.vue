<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import { messagesFor } from '../i18n'
import {
  failureKey,
  formatBytes,
  isProcessing,
  kindLabelKey,
  paragraphLabel,
  statusKey,
  type MaterialChunk,
  type MaterialDocument,
} from '../materials/format'
import { markLoggedOut } from '../session'
import type { Lang } from '../workbench/stage-links'

const props = defineProps<{
  projectId: string
  lang: Lang
  documentId: string | null
}>()
const emit = defineEmits<{ (event: 'select', documentId: string | null): void }>()

const route = useRoute()
const router = useRouter()
const t = computed(() => messagesFor(props.lang).materials)

const documents = ref<MaterialDocument[]>([])
const listState = ref<'loading' | 'ready' | 'error'>('loading')
const listError = ref('')
const selected = ref<MaterialDocument | null>(null)
const detailLoading = ref(false)
const chunks = ref<MaterialChunk[]>([])
const chunksLoading = ref(false)
const chunkError = ref('')
const uploading = ref(false)
const uploadError = ref('')
const notice = ref('')
const refreshError = ref('')
const sourceOpen = ref(false)
const sourceText = ref('')
const sourceError = ref('')
const sourceRef = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

let pollTimer: number | undefined

function basePath(): string {
  return `/api/projects/${encodeURIComponent(props.projectId)}/documents`
}

function documentPath(id: string): string {
  return `${basePath()}/${encodeURIComponent(id)}`
}

async function handleAuth(caught: unknown): Promise<boolean> {
  if (caught instanceof ApiError && caught.status === 401) {
    markLoggedOut()
    await router.replace({ name: 'login', query: { next: route.fullPath } })
    return true
  }
  return false
}

function errorText(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 503) {
      return t.value.serviceUnavailable
    }
    if (caught.code) {
      const key = failureKey(caught.code) as keyof typeof t.value.failure
      return t.value.failure[key] ?? caught.message
    }
    return caught.message
  }
  return t.value.refreshFailed
}

async function loadList(showLoading = true): Promise<void> {
  if (showLoading) {
    listState.value = 'loading'
  }
  listError.value = ''
  try {
    const data = await api.get<{ documents: MaterialDocument[] }>(basePath())
    documents.value = data.documents
    listState.value = 'ready'
    refreshError.value = ''
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    if (showLoading) {
      listState.value = 'error'
      listError.value = errorText(caught)
    } else {
      refreshError.value = t.value.refreshFailed
    }
  }
}

async function loadFirstChunks(id: string): Promise<void> {
  chunkError.value = ''
  chunksLoading.value = true
  try {
    const data = await api.get<{
      chunks: MaterialChunk[]
      total: number
      ready: boolean
    }>(`${documentPath(id)}/chunks?after=0&limit=20`)
    chunks.value = data.chunks
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    chunkError.value = errorText(caught)
  } finally {
    chunksLoading.value = false
  }
}

async function loadDetail(id: string): Promise<void> {
  detailLoading.value = true
  try {
    const data = await api.get<{ document: MaterialDocument }>(documentPath(id))
    selected.value = data.document
    const entry = documents.value.find((item) => item.documentId === id)
    if (entry) {
      Object.assign(entry, data.document)
    }
    if (data.document.status === 'ready') {
      await loadFirstChunks(id)
    } else {
      chunks.value = []
    }
    refreshError.value = ''
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    if (caught instanceof ApiError && caught.status === 404) {
      selected.value = null
      emit('select', null)
      return
    }
    refreshError.value = t.value.refreshFailed
  } finally {
    detailLoading.value = false
  }
}

async function selectDocument(id: string): Promise<void> {
  sourceOpen.value = false
  sourceText.value = ''
  selected.value =
    documents.value.find((item) => item.documentId === id) ?? null
  emit('select', id)
  await loadDetail(id)
}

function backToList(): void {
  sourceOpen.value = false
  selected.value = null
  chunks.value = []
  emit('select', null)
}

async function loadMore(): Promise<void> {
  if (!selected.value || chunksLoading.value) {
    return
  }
  const last = chunks.value.at(-1)
  if (!last) {
    return
  }
  chunksLoading.value = true
  try {
    const data = await api.get<{ chunks: MaterialChunk[] }>(
      `${documentPath(selected.value.documentId)}/chunks?after=${last.index}&limit=20`,
    )
    chunks.value = [...chunks.value, ...data.chunks]
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    chunkError.value = errorText(caught)
  } finally {
    chunksLoading.value = false
  }
}

async function onFileChosen(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) {
    return
  }
  uploading.value = true
  uploadError.value = ''
  notice.value = ''
  const form = new FormData()
  form.append('file', file)
  try {
    const data = await api.postForm<{
      document: MaterialDocument
      deduplicated: boolean
    }>(basePath(), form)
    if (data.deduplicated) {
      notice.value = t.value.dedupNotice
    }
    await loadList(false)
    await selectDocument(data.document.documentId)
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    uploadError.value = errorText(caught)
  } finally {
    uploading.value = false
  }
}

function pickFile(): void {
  fileInput.value?.click()
}

function highlight(start: number, end: number): void {
  const element = sourceRef.value
  const node = element?.firstChild
  if (!node || node.nodeType !== Node.TEXT_NODE) {
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

async function viewSource(chunk: MaterialChunk): Promise<void> {
  if (!selected.value) {
    return
  }
  if (selected.value.kind === 'pdf') {
    return
  }
  sourceOpen.value = true
  sourceError.value = ''
  try {
    if (!sourceText.value) {
      sourceText.value = await api.text(
        `${documentPath(selected.value.documentId)}/source`,
      )
    }
    await nextTick()
    highlight(chunk.sourceStart, chunk.sourceEnd)
  } catch (caught) {
    if (await handleAuth(caught)) {
      return
    }
    sourceError.value = t.value.sourceFailed
  }
}

function closeSource(): void {
  sourceOpen.value = false
  window.getSelection()?.removeAllRanges()
}

function chunkLabel(chunk: MaterialChunk): string {
  if (chunk.pageNo !== null) {
    return t.value.pageNumber.replace('{page}', String(chunk.pageNo))
  }
  const range = paragraphLabel(chunk)
  return range
    ? t.value.paragraphs.replace('{range}', range)
    : t.value.excerpts
}

function kindLabel(document: MaterialDocument): string {
  const key = kindLabelKey(document.kind) as keyof typeof t.value.kind
  return t.value.kind[key]
}

function statusLabel(document: MaterialDocument): string {
  return t.value.status[statusKey(document.status)]
}

function failureText(document: MaterialDocument): string {
  const code = document.failure?.code ?? 'parse_error'
  const key = failureKey(code) as keyof typeof t.value.failure
  return t.value.failure[key] ?? document.failure?.message ?? ''
}

function hasMore(): boolean {
  if (!selected.value) {
    return false
  }
  return chunks.value.length < selected.value.chunkCount
}

async function tick(): Promise<void> {
  if (selected.value && isProcessing(selected.value.status)) {
    await loadDetail(selected.value.documentId)
    return
  }
  if (documents.value.some((item) => isProcessing(item.status))) {
    await loadList(false)
  }
}

onMounted(async () => {
  await loadList()
  if (props.documentId) {
    await loadDetail(props.documentId)
  }
  pollTimer = window.setInterval(() => {
    void tick()
  }, 2000)
})

onBeforeUnmount(() => {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
  }
})

watch(
  () => props.documentId,
  async (value) => {
    if (value === null) {
      selected.value = null
      chunks.value = []
      return
    }
    if (selected.value?.documentId === value) {
      return
    }
    await loadDetail(value)
  },
)

watch(
  () => props.projectId,
  async () => {
    selected.value = null
    chunks.value = []
    documents.value = []
    await loadList()
    if (props.documentId) {
      await loadDetail(props.documentId)
    }
  },
)
</script>

<template>
  <section id="materials" class="materials card" data-view="materials">
    <div class="card-heading">
      <h2>{{ t.title }}</h2>
      <span class="pill">{{ t.listTitle }}</span>
    </div>
    <p class="muted">{{ t.intro }}</p>

    <div class="material-upload">
      <input
        ref="fileInput"
        class="visually-hidden"
        type="file"
        accept=".pdf,.md,.markdown,.txt,application/pdf,text/markdown,text/plain"
        data-action="choose-material"
        @change="onFileChosen"
      />
      <button
        type="button"
        class="primary"
        :disabled="uploading"
        data-action="upload-material"
        @click="pickFile"
      >
        {{ uploading ? t.uploading : t.chooseFile }}
      </button>
      <span class="fine-print">{{ t.uploadHint }}</span>
    </div>
    <p class="fine-print">{{ t.limitHint }}</p>
    <p v-if="uploadError" class="error" role="alert">{{ uploadError }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="refreshError" class="error" role="status">{{ refreshError }}</p>

    <p v-if="listState === 'loading'" class="muted">{{ t.loading }}</p>
    <template v-else-if="listState === 'error'">
      <p class="error" role="alert">{{ listError }}</p>
      <button type="button" class="ghost" @click="loadList()">{{ t.retry }}</button>
    </template>
    <template v-else-if="documents.length === 0">
      <div class="material-empty">
        <h3>{{ t.emptyTitle }}</h3>
        <p class="muted">{{ t.emptyBody }}</p>
      </div>
    </template>

    <ul v-else-if="!selected" class="material-list">
      <li v-for="item in documents" :key="item.documentId">
        <button
          type="button"
          class="material-row"
          :data-status="item.status"
          @click="selectDocument(item.documentId)"
        >
          <span class="material-name">
            <strong>{{ item.name }}</strong>
            <small class="muted">{{ kindLabel(item) }} · {{ formatBytes(item.sizeBytes) }}</small>
          </span>
          <span class="material-meta">
            <span class="status-pill" :data-status="item.status">
              {{ statusLabel(item) }}
            </span>
            <small v-if="item.kind === 'pdf' && item.pageCount !== null" class="muted">
              {{ t.pageCount.replace('{count}', String(item.pageCount)) }}
            </small>
            <small v-if="item.status === 'ready'" class="muted">
              {{ t.chunkCount.replace('{count}', String(item.chunkCount)) }}
            </small>
          </span>
          <span v-if="item.status === 'failed'" class="material-failure">
            {{ failureText(item) }}
          </span>
        </button>
      </li>
    </ul>

    <template v-else>
      <div class="material-detail-head">
        <button type="button" class="quiet-link" @click="backToList">
          ← {{ t.back }}
        </button>
        <h3>{{ selected.name }}</h3>
        <p class="muted">
          {{ kindLabel(selected) }} · {{ formatBytes(selected.sizeBytes) }} ·
          <span class="status-pill" :data-status="selected.status">
            {{ statusLabel(selected) }}
          </span>
        </p>
        <p v-if="selected.kind === 'pdf' && selected.pageCount !== null" class="fine-print">
          {{ t.pageCount.replace('{count}', String(selected.pageCount)) }} ·
          {{ t.chunkCount.replace('{count}', String(selected.chunkCount)) }}
        </p>
      </div>

      <p v-if="selected.failure" class="error" role="alert">
        {{ failureText(selected) }}
      </p>
      <p v-if="detailLoading && !chunks.length" class="muted">
        {{ t.excerptsLoading }}
      </p>

      <template v-if="selected.status === 'ready'">
        <h4>{{ t.excerpts }}</h4>
        <p v-if="chunks.length === 0 && !chunksLoading" class="muted">
          {{ t.excerptsEmpty }}
        </p>
        <ol class="excerpt-list">
          <li v-for="chunk in chunks" :key="chunk.index" class="excerpt">
            <p class="excerpt-locator">{{ chunkLabel(chunk) }}</p>
            <p class="excerpt-text">{{ chunk.text }}</p>
            <a
              v-if="chunk.sourceUrl"
              class="source-link"
              :href="chunk.sourceUrl"
              target="_blank"
              rel="noopener"
              data-action="open-original"
            >
              {{ t.viewOriginal }}
            </a>
            <button
              v-else
              type="button"
              class="quiet-link"
              data-action="view-source"
              @click="viewSource(chunk)"
            >
              {{ t.viewSource }}
            </button>
          </li>
        </ol>
        <p v-if="chunkError" class="error" role="alert">{{ chunkError }}</p>
        <button
          v-if="hasMore()"
          type="button"
          class="ghost"
          :disabled="chunksLoading"
          @click="loadMore"
        >
          {{ chunksLoading ? t.excerptsLoading : t.loadMore }}
        </button>
        <p v-else-if="chunks.length" class="fine-print">{{ t.noMore }}</p>

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
      </template>
      <p
        v-else-if="selected.status === 'queued' || selected.status === 'parsing'"
        class="muted"
      >
        {{ statusLabel(selected) }}
      </p>
    </template>
  </section>
</template>
