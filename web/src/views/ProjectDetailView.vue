<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError, describeError, type Project } from '../api'
import { buildMaterialQuery } from '../materials/format'
import { markLoggedOut } from '../session'
import { normalizeLang } from '../workbench/stage-links'
import { defaultStage } from '../workbench/stages'
import MaterialsPanel from './MaterialsPanel.vue'

const route = useRoute()
const router = useRouter()
const project = ref<Project | null>(null)
const loading = ref(true)
const notFound = ref(false)
const error = ref('')
const lang = computed(() => normalizeLang(route.query.lang))
const documentId = computed(() =>
  typeof route.query.document === 'string' ? route.query.document : null,
)

function selectMaterial(id: string | null): void {
  void router.replace({
    path: route.path,
    query: buildMaterialQuery(route.query, id),
  })
}

async function load(): Promise<void> {
  loading.value = true
  notFound.value = false
  error.value = ''
  try {
    const data = await api.get<{ project: Project }>(
      `/api/projects/${encodeURIComponent(String(route.params.id))}`,
    )
    project.value = data.project
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 401) {
      markLoggedOut()
      await router.replace({
        name: 'login',
        query: { next: route.fullPath },
      })
      return
    }
    if (caught instanceof ApiError && caught.status === 404) {
      notFound.value = true
    } else {
      error.value = describeError(caught)
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="card">
    <p v-if="loading" class="muted">正在加载项目…</p>
    <template v-else-if="notFound">
      <h2>项目不存在</h2>
      <p class="muted">该链接无效，或项目不属于当前账户。</p>
      <nav class="inline-nav">
        <RouterLink to="/projects">返回项目列表</RouterLink>
        <RouterLink to="/">返回控制台</RouterLink>
      </nav>
    </template>
    <template v-else-if="error">
      <p class="error" role="alert">{{ error }}</p>
      <button type="button" class="ghost" @click="load">重试</button>
      <nav class="inline-nav">
        <RouterLink to="/projects">返回项目列表</RouterLink>
      </nav>
    </template>
    <template v-else-if="project">
      <div class="card-heading">
        <h2>{{ project.name }}</h2>
        <span class="pill">工作台外壳</span>
      </div>
      <p v-if="project.description" class="detail">{{ project.description }}</p>
      <p class="muted">
        创建时间：{{ new Date(project.created_at).toLocaleString() }}
      </p>
      <nav class="inline-nav">
        <RouterLink :to="`/projects/${project.id}/workbench/${defaultStage.slug}`">
          进入工作台
        </RouterLink>
        <RouterLink to="/history">查看历史</RouterLink>
        <RouterLink to="/projects">返回项目列表</RouterLink>
        <RouterLink to="/">返回控制台</RouterLink>
      </nav>
    </template>
  </section>

  <MaterialsPanel
    v-if="project && !loading && !notFound && !error"
    :project-id="project.id"
    :lang="lang"
    :document-id="documentId"
    @select="selectMaterial"
  />
</template>
