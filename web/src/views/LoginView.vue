<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError, type User } from '../api'
import { markLoggedIn } from '../session'

const route = useRoute()
const router = useRouter()
const email = ref('')
const password = ref('')
const error = ref('')
const submitting = ref(false)

function nextPath(): string {
  const next = route.query.next
  return typeof next === 'string' && next.startsWith('/') ? next : '/'
}

async function submit(): Promise<void> {
  error.value = ''
  submitting.value = true
  try {
    const data = await api.post<{ user: User }>('/api/auth/login', {
      email: email.value,
      password: password.value,
    })
    markLoggedIn(data.user)
    await router.replace(nextPath())
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 401) {
      error.value = '邮箱或密码不正确。'
    } else if (caught instanceof ApiError && caught.status === 422) {
      error.value = '请检查邮箱与密码格式。'
    } else if (caught instanceof ApiError && caught.status === 503) {
      error.value = '服务暂时不可用，请稍后重试。'
    } else {
      error.value = '无法登录，请检查网络后重试。'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="card auth-card">
    <h2>登录</h2>
    <p class="muted">账户由管理员在本机创建，暂不提供自助注册。</p>
    <form class="form" @submit.prevent="submit">
      <label for="email">邮箱</label>
      <input
        id="email"
        v-model="email"
        type="email"
        name="email"
        autocomplete="username"
        required
      />
      <label for="password">密码</label>
      <input
        id="password"
        v-model="password"
        type="password"
        name="password"
        autocomplete="current-password"
        required
      />
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button type="submit" :disabled="submitting">
        {{ submitting ? '登录中…' : '登录' }}
      </button>
    </form>
  </section>
</template>
