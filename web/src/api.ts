/** 同域 API 客户端：统一 JSON、表单上传、文本读取、错误与凭据处理。 */

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
  readonly code: string | null

  constructor(status: number, detail: string, code: string | null = null) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.code = code
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

async function failFrom(response: Response): Promise<never> {
  const data = (await response.json().catch(() => null)) as
    | { detail?: unknown; code?: unknown }
    | null
  const detail =
    typeof data?.detail === 'string'
      ? data.detail
      : `请求失败（HTTP ${response.status}）`
  const code = typeof data?.code === 'string' ? data.code : null
  throw new ApiError(response.status, detail, code)
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
  if (!response.ok) {
    await failFrom(response)
  }
  return (await response.json()) as T
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
  /** multipart 上传；不设置 Content-Type，交给浏览器生成边界。 */
  postForm<T>(path: string, form: FormData): Promise<T> {
    return request<T>(path, { method: 'POST', body: form })
  },
  /** 读取同源文本（材料原文视图），失败时抛出结构化错误。 */
  async text(path: string): Promise<string> {
    const response = await fetch(path, {
      headers: { Accept: 'text/plain' },
      credentials: 'same-origin',
    })
    if (!response.ok) {
      await failFrom(response)
    }
    return response.text()
  },
}
