<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, ApiError, type Project } from '../api'
import JobPanel from './JobPanel.vue'
import MaterialsPanel from './MaterialsPanel.vue'
import { checkHealth, healthState } from '../health'
import { messagesFor } from '../i18n'
import { buildMaterialQuery } from '../materials/format'
import { markLoggedOut } from '../session'
import {
  getStage,
  previousStage,
  resolveStageContentState,
  stageDefinitions,
} from '../workbench/stages'
import {
  LAYOUT_MODES,
  normalizeLang,
  normalizeLayout,
  stageLocation,
  type LayoutMode,
  type Lang,
} from '../workbench/stage-links'

type ShellState = 'loading' | 'ready' | 'not-found' | 'service' | 'network'

const route = useRoute()
const router = useRouter()
const shellState = ref<ShellState>('loading')
const project = ref<Project | null>(null)
const logoutError = ref('')
const contextOpen = ref(false)
let loadToken = 0

const projectId = computed(() => String(route.params.id ?? ''))
const stageParam = computed(() => String(route.params.stage ?? ''))
const currentStage = computed(() => getStage(stageParam.value))
const previous = computed(() => previousStage(currentStage.value))
const layout = computed(() => normalizeLayout(route.query.layout))
const lang = computed(() => normalizeLang(route.query.lang))
const layoutModes = LAYOUT_MODES
const t = computed(() => messagesFor(lang.value))
const healthLabel = computed(() => t.value.health[healthState.value])
const documentId = computed(() =>
  typeof route.query.document === 'string' ? route.query.document : null,
)

// 当前变更没有阶段数据源；正常分支只由隔离测试数据证明，产品页面始终使用真实 API。
const stageHasData = false
const stageContentState = computed(() =>
  resolveStageContentState(stageHasData),
)

const missingTitle = computed(() =>
  currentStage.value?.id === 1
    ? t.value.empty.materialsTitle
    : t.value.empty.missingTitle,
)

const missingBody = computed(() => {
  if (!currentStage.value) {
    return ''
  }
  if (currentStage.value.id === 1) {
    return t.value.empty.materialsBody
  }
  return previous.value
    ? `${t.value.empty.missingBody} ${t.value.empty.goPrevious}：${previous.value.title[lang.value]}`
    : t.value.empty.missingBody
})

const continueLabel = computed(() =>
  currentStage.value?.id === 1
    ? t.value.empty.goMaterials
    : t.value.empty.goPrevious,
)

const errorTitle = computed(() => {
  if (shellState.value === 'not-found') {
    return t.value.errors.projectNotFound
  }
  if (shellState.value === 'service') {
    return t.value.errors.serviceTitle
  }
  return t.value.errors.networkTitle
})

const errorBody = computed(() => {
  if (shellState.value === 'not-found') {
    return t.value.errors.projectNotFoundBody
  }
  if (shellState.value === 'service') {
    return t.value.errors.serviceBody
  }
  return t.value.errors.networkBody
})

async function loadProject(): Promise<void> {
  const token = ++loadToken
  shellState.value = 'loading'
  project.value = null
  try {
    const data = await api.get<{ project: Project }>(
      `/api/projects/${encodeURIComponent(projectId.value)}`,
    )
    if (token !== loadToken) {
      return
    }
    project.value = data.project
    shellState.value = 'ready'
  } catch (caught) {
    if (token !== loadToken) {
      return
    }
    if (caught instanceof ApiError && caught.status === 401) {
      markLoggedOut()
      await router.replace({
        name: 'login',
        query: { next: route.fullPath },
      })
      return
    }
    if (caught instanceof ApiError && caught.status === 404) {
      shellState.value = 'not-found'
      return
    }
    if (caught instanceof ApiError && caught.status === 503) {
      shellState.value = 'service'
      return
    }
    shellState.value = 'network'
  }
}

function stageLink(slug: string) {
  return stageLocation(projectId.value, slug, route.query)
}

function goStage(slug: string): void {
  void router.push(stageLink(slug))
}

function continueStage(): void {
  if (!currentStage.value) {
    return
  }
  if (currentStage.value.id === 1) {
    void router.push({ path: '/', hash: '#materials' })
    return
  }
  if (previous.value) {
    goStage(previous.value.slug)
  }
}

function selectMaterial(id: string | null): void {
  void router.replace({
    path: route.path,
    query: buildMaterialQuery(route.query, id),
  })
}

function setLayout(next: LayoutMode): void {
  void router.replace({
    path: route.path,
    query: { ...route.query, layout: next },
  })
}

function setLang(next: Lang): void {
  void router.replace({
    path: route.path,
    query: { ...route.query, lang: next },
  })
}

async function logout(): Promise<void> {
  logoutError.value = ''
  try {
    await api.post('/api/auth/logout')
    markLoggedOut()
    await router.replace({ name: 'login' })
  } catch {
    logoutError.value = t.value.logoutFailed
  }
}

watch(
  projectId,
  () => {
    void loadProject()
  },
  { immediate: true },
)

watch(
  lang,
  (value) => {
    document.documentElement.lang = value === 'en' ? 'en' : 'zh'
  },
  { immediate: true },
)

watch(layout, (value) => {
  if (value !== 'graph') {
    contextOpen.value = false
  }
})

onMounted(() => {
  void checkHealth()
})

onBeforeUnmount(() => {
  document.documentElement.lang = 'zh'
})
</script>

<template>
  <div
    class="workbench"
    data-view="workbench"
    :data-layout="layout"
    :data-stage="stageParam"
    :data-content-state="stageContentState"
  >
    <template v-if="shellState === 'loading'">
      <div class="wb-minimal-bar">
        <RouterLink class="wb-brand" to="/">EchoSim</RouterLink>
      </div>
      <section class="wb-loading card" aria-live="polite">
        <p class="muted">{{ t.errors.loading }}</p>
      </section>
    </template>

    <template v-else-if="shellState !== 'ready'">
      <div class="wb-minimal-bar">
        <RouterLink class="wb-brand" to="/">EchoSim</RouterLink>
      </div>
      <section class="wb-error-page card">
        <p class="eyebrow">EchoSim</p>
        <h1>{{ errorTitle }}</h1>
        <p class="muted">{{ errorBody }}</p>
        <div class="error-actions">
          <button
            v-if="shellState === 'service' || shellState === 'network'"
            type="button"
            class="primary"
            @click="loadProject"
          >
            {{ t.errors.retry }}
          </button>
          <RouterLink class="button-link" to="/projects">
            {{ t.errors.backProjects }}
          </RouterLink>
          <RouterLink class="button-link" to="/">
            {{ t.errors.backConsole }}
          </RouterLink>
        </div>
      </section>
    </template>

    <template v-else-if="project">
      <header class="wb-toolbar">
        <div class="wb-toolbar-top">
          <RouterLink class="wb-brand" to="/">EchoSim</RouterLink>
          <RouterLink class="wb-back" to="/">{{ t.toolbar.console }}</RouterLink>
          <div class="wb-project">
            <span class="eyebrow">{{ t.stage.project }}</span>
            <strong>{{ project.name }}</strong>
            <RouterLink :to="`/projects/${project.id}`">
              {{ t.toolbar.detail }}
            </RouterLink>
            <RouterLink to="/history">{{ t.toolbar.history }}</RouterLink>
          </div>
          <div class="wb-toolbar-actions">
            <span class="badge" :data-state="healthState">{{ healthLabel }}</span>
            <button type="button" class="ghost" @click="logout">
              {{ t.toolbar.logout }}
            </button>
          </div>
        </div>

        <nav class="wb-stage-nav" :aria-label="t.aria.stages">
          <button
            v-for="stage in stageDefinitions"
            :key="stage.slug"
            type="button"
            :class="{ active: stage.slug === currentStage?.slug }"
            :aria-current="stage.slug === currentStage?.slug ? 'step' : undefined"
            @click="goStage(stage.slug)"
          >
            <span class="stage-index">{{ stage.id }}</span>
            <span>{{ stage.title[lang] }}</span>
          </button>
        </nav>

        <div class="wb-view-controls">
          <div class="control-group" role="group" :aria-label="t.aria.layout">
            <span class="control-label">{{ t.toolbar.layout }}</span>
            <button
              v-for="mode in layoutModes"
              :key="mode"
              type="button"
              :aria-pressed="layout === mode"
              @click="setLayout(mode)"
            >
              {{ t.layout[mode] }}
            </button>
          </div>
          <div class="control-group" role="group" :aria-label="t.aria.language">
            <span class="control-label">{{ t.toolbar.language }}</span>
            <button
              type="button"
              :aria-pressed="lang === 'zh'"
              @click="setLang('zh')"
            >
              中文
            </button>
            <button
              type="button"
              :aria-pressed="lang === 'en'"
              @click="setLang('en')"
            >
              English
            </button>
          </div>
          <button
            v-if="layout === 'graph'"
            type="button"
            class="ghost context-toggle"
            :aria-pressed="contextOpen"
            @click="contextOpen = !contextOpen"
          >
            {{ contextOpen ? t.toolbar.contextHide : t.toolbar.contextShow }}
          </button>
        </div>
        <p v-if="logoutError" class="error" role="alert">{{ logoutError }}</p>
      </header>

      <section v-if="!currentStage" class="wb-invalid card">
        <p class="eyebrow">{{ t.errors.stageNotFound }}</p>
        <h1>{{ t.errors.stageNotFound }}</h1>
        <p class="muted">{{ t.errors.stageNotFoundBody }}</p>
        <nav class="valid-stages" :aria-label="t.aria.stages">
          <RouterLink
            v-for="stage in stageDefinitions"
            :key="stage.slug"
            :to="stageLink(stage.slug)"
          >
            <span class="stage-index">{{ stage.id }}</span>
            {{ stage.title[lang] }}
          </RouterLink>
        </nav>
      </section>

      <div v-else class="wb-body" :class="`wb-body--${layout}`">
        <aside v-if="layout === 'workbench'" class="wb-rail card">
          <h2>{{ t.stage.navTitle }}</h2>
          <button
            v-for="stage in stageDefinitions"
            :key="stage.slug"
            type="button"
            class="rail-stage"
            :class="{ active: stage.slug === currentStage.slug }"
            @click="goStage(stage.slug)"
          >
            <span class="stage-index">{{ stage.id }}</span>
            <span>
              <strong>{{ stage.title[lang] }}</strong>
              <small>{{ stage.prerequisite[lang] }}</small>
            </span>
          </button>
        </aside>

        <main class="wb-main">
          <section class="wb-stage-header card">
            <div>
              <p class="eyebrow">
                {{ t.stage.current }} · {{ currentStage.id }}
              </p>
              <h1>{{ currentStage.title[lang] }}</h1>
              <p class="muted">{{ currentStage.purpose[lang] }}</p>
            </div>
            <div class="wb-prerequisite">
              <span>{{ t.stage.prerequisite }}</span>
              <strong>{{ currentStage.prerequisite[lang] }}</strong>
            </div>
          </section>

          <MaterialsPanel
            v-if="currentStage.id === 1"
            :project-id="project.id"
            :lang="lang"
            :document-id="documentId"
            @select="selectMaterial"
          />

          <section v-else class="wb-empty card">
            <h2>{{ missingTitle }}</h2>
            <p class="muted">{{ missingBody }}</p>
            <button type="button" class="primary" @click="continueStage">
              {{ continueLabel }}
            </button>
          </section>

          <section v-if="layout !== 'workbench'" class="wb-summary card">
            <strong>{{ t.stage.statusReady }}</strong>
            <span class="muted">{{ t.stage.statusPending }}</span>
          </section>

          <section class="wb-graph-canvas" :aria-label="t.canvas.title">
            <div class="graph-grid-lines" aria-hidden="true" />
            <div class="graph-copy">
              <p class="eyebrow">{{ t.canvas.summary }}</p>
              <h2>{{ t.canvas.title }}</h2>
              <p>{{ t.canvas.body }}</p>
            </div>
          </section>
        </main>

        <aside
          v-show="layout !== 'graph' || contextOpen"
          class="wb-context"
          :class="{ 'wb-context--drawer': layout === 'graph' }"
        >
          <JobPanel
            :project-id="project.id"
            :stage="currentStage.slug"
            :lang="lang"
          />
          <section class="context-card card">
            <h2>{{ t.context.materialsTitle }}</h2>
            <p class="muted">{{ t.context.materialsBody }}</p>
            <a class="quiet-link" href="#materials">
              {{ t.context.materialsLink }}
            </a>
          </section>
          <section class="context-card card">
            <h2>{{ t.context.questionsTitle }}</h2>
            <ul class="question-list">
              <li v-for="question in t.context.questions" :key="question">
                {{ question }}
              </li>
            </ul>
          </section>
          <section class="context-card card">
            <h2>{{ t.context.nextTitle }}</h2>
            <p class="muted">{{ t.context.nextBody }}</p>
          </section>
        </aside>

        <footer v-if="layout === 'workbench'" class="wb-statusbar card">
          <span class="status-dot" aria-hidden="true" />
          {{ t.statusbar }}
        </footer>
      </div>
    </template>
  </div>
</template>
