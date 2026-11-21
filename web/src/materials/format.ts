/** 材料区的纯逻辑：状态、大小、地址查询参数与原文切片。 */

export type DocumentStatus = 'queued' | 'parsing' | 'ready' | 'failed'
export type DocumentKind = 'pdf' | 'markdown' | 'text'

export interface FailureDetail {
  pages?: number | null
  limit?: number | null
}

export interface MaterialFailure {
  code: string
  message: string
  detail?: FailureDetail | null
}

export interface MaterialDocument {
  documentId: string
  name: string
  kind: DocumentKind
  contentType: string
  sizeBytes: number
  sha256: string
  status: DocumentStatus
  pageCount: number | null
  chunkCount: number
  failure: MaterialFailure | null
  createdAt: string
  readyAt: string | null
  jobId: string | null
  runId: string | null
}

export interface MaterialChunk {
  index: number
  pageNo: number | null
  paragraphStart: number | null
  paragraphEnd: number | null
  sourceStart: number
  sourceEnd: number
  text: string
  sourceUrl: string | null
}

const STATUSES: DocumentStatus[] = ['queued', 'parsing', 'ready', 'failed']
const FAILURE_KEYS: Record<string, string> = {
  empty_file: 'emptyFile',
  file_too_large: 'fileTooLarge',
  unsupported_type: 'unsupportedType',
  decode_failed: 'decodeFailed',
  no_text_layer: 'noTextLayer',
  original_missing: 'originalMissing',
  too_many_pages: 'tooManyPages',
  parse_error: 'parseError',
  canceled: 'canceled',
}

export function statusKey(status: string): DocumentStatus {
  return STATUSES.includes(status as DocumentStatus)
    ? (status as DocumentStatus)
    : 'queued'
}

export function isProcessing(status: string): boolean {
  return status === 'queued' || status === 'parsing'
}

export function failureKey(code: string | null | undefined): string {
  if (!code) {
    return 'parseError'
  }
  return FAILURE_KEYS[code] ?? 'parseError'
}

export function kindLabelKey(kind: string): string {
  if (kind === 'pdf') {
    return 'pdf'
  }
  if (kind === 'markdown') {
    return 'markdown'
  }
  return 'text'
}

export function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`
}

/** 保留现有查询参数（layout/lang），并设置或移除 document。 */
export function buildMaterialQuery(
  query: Record<string, unknown>,
  documentId: string | null,
): Record<string, string> {
  const next: Record<string, string> = {}
  for (const [key, value] of Object.entries(query)) {
    if (key === 'document' || typeof value !== 'string') {
      continue
    }
    next[key] = value
  }
  if (documentId) {
    next.document = documentId
  }
  return next
}

/** 偏移是 UTF-16 代码单元，直接与 JS 字符串下标一致。 */
export function sliceSource(text: string, start: number, end: number): string {
  return text.slice(start, end)
}

export function paragraphLabel(chunk: MaterialChunk): string | null {
  if (chunk.paragraphStart === null || chunk.paragraphEnd === null) {
    return null
  }
  if (chunk.paragraphStart === chunk.paragraphEnd) {
    return `${chunk.paragraphStart}`
  }
  return `${chunk.paragraphStart}–${chunk.paragraphEnd}`
}

/** 用模板填充“实际页数 + 配置上限”，两种语言共用。 */
export function formatPageLimit(
  template: string,
  pages: number | null,
  limit: number,
): string {
  return template
    .replace('{pages}', pages === null ? '?' : String(pages))
    .replace('{limit}', String(limit))
}

/**
 * 已失败材料的页数超限文案：只使用解析时记录的实际页数与上限。
 * 修复前没有结构化详情的旧记录返回不含上限数字的文案。
 */
export function pageLimitFailureText(
  failure: MaterialFailure | null | undefined,
  templates: { detail: string; unknown: string },
): string {
  const pages = failure?.detail?.pages
  const limit = failure?.detail?.limit
  if (typeof pages === 'number' && typeof limit === 'number') {
    return formatPageLimit(templates.detail, pages, limit)
  }
  return templates.unknown
}
