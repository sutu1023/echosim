/** 前端会话状态：只缓存当前用户，不保存任何令牌。 */

import { ref } from 'vue'
import { api, ApiError, type User } from './api'

export const currentUser = ref<User | null>(null)
let loaded = false

export function markLoggedIn(user: User): void {
  currentUser.value = user
  loaded = true
}

export function markLoggedOut(): void {
  currentUser.value = null
  loaded = true
}

export async function ensureSession(force = false): Promise<User | null> {
  if (loaded && !force) {
    return currentUser.value
  }
  try {
    const data = await api.get<{ user: User }>('/api/auth/session')
    markLoggedIn(data.user)
    return data.user
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      markLoggedOut()
      return null
    }
    throw error
  }
}
