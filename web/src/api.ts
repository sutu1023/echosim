/** 同域 API 客户端：统一 JSON、错误与凭据处理。 */

export interface User {
  id: string
  email: string
  display_name: string | null
}

export interface Project {
  id: string
  name: string
  description: string | null
  created_at: string
}

export interface HistoryEvent {
  id: string
  project_id: string
  project_name: string
  type: string
  occurred_at: string
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
  }
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 503) {
      return '服务暂时不可用，请稍后重试。'
    }
    return error.message
  }
  return '网络请求失败，请检查连接后重试。'
}

function isJsonBody(body: unknown): boolean {
  return body !== undefined
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (isJsonBody(init.body)) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, {
    ...init,
    headers,
    credentials: 'same-origin',
  })

  if (response.status === 204) {
    return undefined as T
  }

  const data = (await response.json().catch(() => null)) as
    | { detail?: unknown }
    | null

  if (!response.ok) {
    const detail =
      typeof data?.detail === 'string'
        ? data.detail
        : `请求失败（HTTP ${response.status}）`
    throw new ApiError(response.status, detail)
  }

  return data as T
}

export const api = {
  get<T>(path: string): Promise<T> {
    return request<T>(path)
  },
  post<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  },
}
