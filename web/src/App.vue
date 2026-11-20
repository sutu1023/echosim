<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, describeError } from './api'
import { currentUser, markLoggedOut } from './session'

type HealthState = 'checking' | 'ok' | 'degraded' | 'unreachable'

const router = useRouter()
const healthState = ref<HealthState>('checking')
const healthDetail = ref('')
const logoutError = ref('')

const healthLabels: Record<HealthState, string> = {
  checking: '检查中',
  ok: '可用',
  degraded: '降级',
  unreachable: '不可达',
}

async function checkHealth(): Promise<void> {
  healthState.value = 'checking'
  healthDetail.value = ''
  try {
    const response = await fetch('/health', {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    })
    if (response.ok) {
      healthState.value = 'ok'
    } else if (response.status === 503) {
      healthState.value = 'degraded'
      healthDetail.value = '数据库不可用，服务处于降级状态。'
    } else {
      healthState.value = 'unreachable'
      healthDetail.value = `健康检查返回意外状态码 ${response.status}。`
    }
  } catch {
    healthState.value = 'unreachable'
    healthDetail.value = '无法连接到同域健康检查接口。'
  }
}

async function logout(): Promise<void> {
  logoutError.value = ''
  try {
    await api.post('/api/auth/logout')
    markLoggedOut()
    await router.replace({ name: 'login' })
  } catch (caught) {
    logoutError.value = describeError(caught)
  }
}

onMounted(() => {
  void checkHealth()
})
</script>

<template>
  <main class="app">
    <header class="topbar">
      <div class="brand">
        <h1>EchoSim</h1>
        <p class="tagline">材料驱动的双平台推演工作台</p>
      </div>
      <div class="topbar-actions">
        <button
          type="button"
          class="badge badge-button"
          :data-state="healthState"
          title="重新检查服务状态"
          @click="checkHealth"
        >
          {{ healthLabels[healthState] }}
        </button>
        <template v-if="currentUser">
          <nav class="nav" aria-label="主导航">
            <RouterLink to="/">项目</RouterLink>
            <RouterLink to="/history">历史</RouterLink>
          </nav>
          <button type="button" class="ghost" @click="logout">退出登录</button>
        </template>
      </div>
    </header>
    <p v-if="healthDetail" class="muted" aria-live="polite">{{ healthDetail }}</p>
    <p v-if="logoutError" class="error" role="alert">{{ logoutError }}</p>

    <RouterView />
  </main>
</template>
