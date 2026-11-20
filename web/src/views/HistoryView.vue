<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  api,
  ApiError,
  describeError,
  type HistoryEvent,
} from '../api'
import { markLoggedOut } from '../session'

const router = useRouter()
const events = ref<HistoryEvent[]>([])
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    const data = await api.get<{ events: HistoryEvent[] }>('/api/history')
    events.value = data.events
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 401) {
      markLoggedOut()
      await router.replace({
        name: 'login',
        query: { next: '/history' },
      })
      return
    }
    error.value = describeError(caught)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="card">
    <h2>历史</h2>
    <p v-if="loading" class="muted">正在加载历史…</p>
    <p v-else-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="events.length === 0" class="muted">
      暂无历史记录，创建项目后会出现在这里。
    </p>
    <ul v-else class="history-list">
      <li v-for="event in events" :key="event.id">
        <RouterLink :to="`/projects/${event.project_id}`">
          {{ event.project_name }}
        </RouterLink>
        <span class="muted">{{ event.type }}</span>
        <time class="muted" :datetime="event.occurred_at">
          {{ new Date(event.occurred_at).toLocaleString() }}
        </time>
      </li>
    </ul>
  </section>
</template>
