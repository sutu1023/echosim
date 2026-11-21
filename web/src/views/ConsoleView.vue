<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import {
  api,
  ApiError,
  describeError,
  type Project,
} from '../api'
import { checkHealth, healthDetail, healthState } from '../health'
import { ensureSession, markLoggedOut } from '../session'
import { defaultStage, stageDefinitions } from '../workbench/stages'
import { stageLocation } from '../workbench/stage-links'

type SessionState = 'checking' | 'guest' | 'authenticated' | 'unknown'
type ProjectsState = 'idle' | 'loading' | 'ready' | 'error'

const router = useRouter()
const sessionState = ref<SessionState>('checking')
const projectsState = ref<ProjectsState>('idle')
const projects = ref<Project[]>([])
const sessionMessage = ref('')
const projectsMessage = ref('')

const healthLabels = {
  checking: '检查中',
  ok: '可用',
  degraded: '降级',
  unreachable: '不可达',
} as const

const accountLabel = computed(() => {
  if (sessionState.value === 'authenticated') {
    return '已登录'
  }
  if (sessionState.value === 'guest') {
    return '访客'
  }
  if (sessionState.value === 'unknown') {
    return '暂时无法确认'
  }
  return '正在确认'
})

const startDisabled = computed(() => {
  if (sessionState.value === 'checking' || sessionState.value === 'unknown') {
    return true
  }
  return (
    sessionState.value === 'authenticated' && projectsState.value === 'loading'
  )
})

const startLabel = computed(() => {
  if (sessionState.value === 'checking') {
    return '准备中…'
  }
  if (sessionState.value === 'unknown') {
    return '暂时无法确认身份'
  }
  if (sessionState.value === 'authenticated' && projectsState.value === 'loading') {
    return '准备中…'
  }
  return '开始'
})

const startHint = computed(() => {
  if (sessionState.value === 'unknown') {
    return '会话查询暂时不可用，请先重试确认身份；这不会清除登录 Cookie。'
  }
  if (sessionState.value === 'authenticated' && projectsState.value === 'error') {
    return '项目列表暂时读取失败。点击“开始”会前往项目页，由项目页提供重试。'
  }
  if (sessionState.value === 'authenticated' && projectsState.value === 'ready') {
    return projects.value.length === 0
      ? '你将前往创建第一个项目。'
      : '你将进入最近创建项目的第一阶段。'
  }
  if (sessionState.value === 'guest') {
    return '登录后进入“我的项目”，不会自动创建项目。'
  }
  return '正在确认身份与项目状态。'
})

async function loadProjects(): Promise<void> {
  projectsState.value = 'loading'
  projectsMessage.value = ''
  try {
    const data = await api.get<{ projects: Project[] }>('/api/projects')
    projects.value = data.projects
    projectsState.value = 'ready'
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 401) {
      markLoggedOut()
      sessionState.value = 'guest'
      projectsState.value = 'idle'
      projects.value = []
      return
    }
    projectsState.value = 'error'
    projectsMessage.value = describeError(caught)
  }
}

async function loadIdentity(): Promise<void> {
  sessionState.value = 'checking'
  sessionMessage.value = ''
  projectsState.value = 'idle'
  projects.value = []
  try {
    const user = await ensureSession(true)
    if (!user) {
      sessionState.value = 'guest'
      return
    }
    sessionState.value = 'authenticated'
    await loadProjects()
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 401) {
      markLoggedOut()
      sessionState.value = 'guest'
      return
    }
    sessionState.value = 'unknown'
    sessionMessage.value = describeError(caught)
  }
}

function start(): void {
  if (startDisabled.value) {
    return
  }
  if (sessionState.value === 'guest') {
    void router.push({ name: 'login', query: { next: '/projects' } })
    return
  }
  if (sessionState.value !== 'authenticated') {
    return
  }
  if (projectsState.value === 'ready' && projects.value.length > 0) {
    void router.push(
      stageLocation(projects.value[0].id, defaultStage.slug),
    )
    return
  }
  if (projectsState.value === 'ready') {
    void router.push({ path: '/projects', query: { intent: 'create' } })
    return
  }
  void router.push('/projects')
}

onMounted(() => {
  void checkHealth()
  void loadIdentity()
})
</script>

<template>
  <div class="console" data-view="console">
    <section class="console-hero">
      <p class="eyebrow">EchoSim 控制台</p>
      <h2>从材料出发，沿五个阶段完成一次双平台推演。</h2>
      <p class="muted">
        控制台对所有访客开放；进入真实项目仍需要登录。材料入库已开放，
        运行与报告能力将在后续变更接入。
      </p>
    </section>

    <div class="console-grid">
      <section class="console-left" aria-label="系统状态与阶段">
        <div class="card status-card">
          <div class="status-card-head">
            <h3>系统状态</h3>
            <button
              type="button"
              class="badge badge-button"
              :data-state="healthState"
              title="重新检查服务状态"
              @click="checkHealth"
            >
              {{ healthLabels[healthState] }}
            </button>
          </div>
          <p class="status-account">
            账户状态：
            <strong>{{ accountLabel }}</strong>
          </p>
          <p v-if="healthDetail" class="muted" aria-live="polite">
            {{ healthDetail }}
          </p>
          <p
            v-if="sessionState === 'unknown'"
            class="error"
            role="status"
            aria-live="polite"
          >
            {{ sessionMessage || '会话查询暂时不可用，登录 Cookie 已保留。' }}
          </p>
          <button
            v-if="sessionState === 'unknown'"
            type="button"
            class="ghost retry-button"
            @click="loadIdentity"
          >
            重试确认身份
          </button>
          <p
            v-if="projectsState === 'error'"
            class="error"
            role="status"
            aria-live="polite"
          >
            项目列表读取失败：{{ projectsMessage }}
          </p>
        </div>

        <div class="card stage-overview">
          <h3>五个阶段</h3>
          <ol class="stage-list">
            <li v-for="stage in stageDefinitions" :key="stage.slug" class="stage-item">
              <span class="stage-number">{{ stage.id }}</span>
              <div>
                <strong>{{ stage.title.zh }}</strong>
                <p class="muted">{{ stage.purpose.zh }}</p>
              </div>
            </li>
          </ol>
        </div>
      </section>

      <aside class="console-right" aria-label="材料、问题与开始">
        <nav class="jump-links" aria-label="控制台快捷入口">
          <a class="jump-link" href="#materials">材料入口</a>
          <a class="jump-link" href="#questions">推演问题</a>
        </nav>

        <section id="materials" class="card material-card">
          <div class="card-heading">
            <h3>材料入口</h3>
            <span class="pill">已开放</span>
          </div>
          <p class="muted">
            支持 PDF、Markdown 与 TXT，单文件不超过 20 MiB。材料在项目内上传，
            就绪后可阅读摘录并回到原件。
          </p>
          <RouterLink class="primary material-cta" to="/projects">
            进入我的项目上传材料
          </RouterLink>
          <p class="fine-print">
            材料入库后，证据图谱会从这里开始构建。
          </p>
        </section>

        <section id="questions" class="card question-card">
          <h3>推演问题</h3>
          <ul class="question-list">
            <li>哪些证据真正改变了判断？</li>
            <li>两个平台在哪些条件下产生分歧？</li>
            <li>结论在什么情况下会失效？</li>
          </ul>
        </section>

        <section class="card console-cta">
          <p class="cta-hint" aria-live="polite">{{ startHint }}</p>
          <button
            type="button"
            class="primary start-button"
            :disabled="startDisabled"
            @click="start"
          >
            {{ startLabel }}
          </button>
        </section>
      </aside>
    </div>

    <p class="console-boundary">
      当前版本支持登录、项目、材料入库与摘录来源定位。证据图谱、真实推演、报告与案例回放不在本版本范围内。
    </p>
    <p v-if="sessionState === 'guest'" class="muted">
      已有账户？
      <RouterLink to="/login">前往登录</RouterLink>
    </p>
  </div>
</template>
