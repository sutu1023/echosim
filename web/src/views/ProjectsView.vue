<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  api,
  ApiError,
  describeError,
  type Project,
} from '../api'
import { markLoggedOut } from '../session'

const router = useRouter()
const projects = ref<Project[]>([])
const loading = ref(true)
const error = ref('')
const name = ref('')
const description = ref('')
const creating = ref(false)
const createError = ref('')

async function handle(caught: unknown): Promise<void> {
  if (caught instanceof ApiError && caught.status === 401) {
    markLoggedOut()
    await router.replace({ name: 'login', query: { next: '/' } })
    return
  }
  error.value = describeError(caught)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get<{ projects: Project[] }>('/api/projects')
    projects.value = data.projects
  } catch (caught) {
    await handle(caught)
  } finally {
    loading.value = false
  }
}

async function create(): Promise<void> {
  creating.value = true
  createError.value = ''
  try {
    const data = await api.post<{ project: Project }>('/api/projects', {
      name: name.value,
      description: description.value || null,
    })
    name.value = ''
    description.value = ''
    projects.value = [data.project, ...projects.value]
    await load()
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 401) {
      await handle(caught)
      return
    }
    createError.value =
      caught instanceof ApiError && caught.status === 422
        ? '项目名称需为 1–80 字符，描述不超过 500 字符。'
        : describeError(caught)
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="card">
    <h2>我的项目</h2>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="loading" class="muted">正在加载项目…</p>
    <p v-else-if="projects.length === 0" class="muted">
      还没有项目，先在下方创建一个。
    </p>
    <ul v-else class="project-list">
      <li v-for="project in projects" :key="project.id">
        <RouterLink :to="`/projects/${project.id}`">
          <strong>{{ project.name }}</strong>
        </RouterLink>
        <p v-if="project.description" class="muted">
          {{ project.description }}
        </p>
        <time class="muted" :datetime="project.created_at">
          {{ new Date(project.created_at).toLocaleString() }}
        </time>
      </li>
    </ul>
  </section>

  <section class="card">
    <h2>新建项目</h2>
    <form class="form" @submit.prevent="create">
      <label for="project-name">项目名称</label>
      <input
        id="project-name"
        v-model="name"
        name="name"
        maxlength="80"
        required
      />
      <label for="project-description">描述（可选）</label>
      <textarea
        id="project-description"
        v-model="description"
        name="description"
        rows="3"
        maxlength="500"
      />
      <p v-if="createError" class="error" role="alert">{{ createError }}</p>
      <button type="submit" :disabled="creating">
        {{ creating ? '创建中…' : '创建项目' }}
      </button>
    </form>
  </section>
</template>
