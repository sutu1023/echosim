<script setup lang="ts">
import { onMounted, ref } from 'vue'

type HealthState = 'checking' | 'ok' | 'degraded' | 'unreachable'

const state = ref<HealthState>('checking')
const detail = ref('正在检查数据库可用性…')

const labels: Record<HealthState, string> = {
  checking: '检查中',
  ok: '可用',
  degraded: '降级',
  unreachable: '不可达',
}

async function checkHealth(): Promise<void> {
  state.value = 'checking'
  detail.value = '正在检查数据库可用性…'
  try {
    const response = await fetch('/health', {
      headers: { Accept: 'application/json' },
    })
    if (response.ok) {
      state.value = 'ok'
      detail.value = '数据库连接正常，服务可用。'
    } else if (response.status === 503) {
      state.value = 'degraded'
      detail.value = '数据库不可用，服务处于降级状态。'
    } else {
      state.value = 'unreachable'
      detail.value = `健康检查返回意外状态码 ${response.status}。`
    }
  } catch {
    state.value = 'unreachable'
    detail.value = '无法连接到同域健康检查接口。'
  }
}

onMounted(() => {
  void checkHealth()
})
</script>

<template>
  <main class="app">
    <header class="hero">
      <h1>EchoSim</h1>
      <p class="tagline">材料驱动的双平台推演工作台</p>
    </header>

    <section class="status" aria-live="polite">
      <h2>服务状态</h2>
      <p class="status-line">
        <span class="badge" :data-state="state">{{ labels[state] }}</span>
        <span class="detail">{{ detail }}</span>
      </p>
      <button type="button" @click="checkHealth">重新检查</button>
    </section>
  </main>
</template>
