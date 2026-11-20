<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api, describeError } from './api'
import { checkHealth, healthDetail, healthState } from './health'
import { currentUser, markLoggedOut } from './session'

const route = useRoute()
const router = useRouter()
const logoutError = ref('')
const hideChrome = computed(() => route.meta.hideChrome === true)

const healthLabels = {
  checking: '检查中',
  ok: '可用',
  degraded: '降级',
  unreachable: '不可达',
} as const

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
  <main class="app" :class="{ 'app--workbench': hideChrome }">
    <template v-if="!hideChrome">
      <header class="topbar">
        <div class="brand">
          <h1><RouterLink to="/">EchoSim</RouterLink></h1>
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
              <RouterLink to="/">控制台</RouterLink>
              <RouterLink to="/projects">我的项目</RouterLink>
              <RouterLink to="/history">历史</RouterLink>
            </nav>
            <button type="button" class="ghost" @click="logout">退出登录</button>
          </template>
        </div>
      </header>
      <p v-if="healthDetail" class="muted" aria-live="polite">{{ healthDetail }}</p>
      <p v-if="logoutError" class="error" role="alert">{{ logoutError }}</p>
    </template>

    <RouterView />
  </main>
</template>
