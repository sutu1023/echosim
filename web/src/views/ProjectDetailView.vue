<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError, describeError, type Project } from '../api'
import { markLoggedOut } from '../session'

const route = useRoute()
const router = useRouter()
const project = ref<Project | null>(null)
const loading = ref(true)
const notFound = ref(false)
const error = ref('')

onMounted(async () => {
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
})
</script>

<template>
  <section class="card">
    <p v-if="loading" class="muted">正在加载项目…</p>
    <template v-else-if="notFound">
      <h2>项目不存在</h2>
      <p class="muted">该链接无效，或项目不属于当前账户。</p>
      <RouterLink to="/">返回项目列表</RouterLink>
    </template>
    <p v-else-if="error" class="error" role="alert">{{ error }}</p>
    <template v-else-if="project">
      <h2>{{ project.name }}</h2>
      <p v-if="project.description" class="detail">{{ project.description }}</p>
      <p class="muted">
        创建时间：{{ new Date(project.created_at).toLocaleString() }}
      </p>
      <nav class="inline-nav">
        <RouterLink to="/history">查看历史</RouterLink>
        <RouterLink to="/">返回项目列表</RouterLink>
      </nav>
    </template>
  </section>
</template>
