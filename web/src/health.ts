/** 同域健康检查状态：控制台、顶部工具条与工作台共用。 */

import { ref } from 'vue'

export type HealthState = 'checking' | 'ok' | 'degraded' | 'unreachable'

export const healthState = ref<HealthState>('checking')
export const healthDetail = ref('')

export async function checkHealth(): Promise<void> {
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
