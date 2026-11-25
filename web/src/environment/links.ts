/** 角色来源与阶段跳转链接：显式保留 layout/lang/build/preparation/document。 */

import type { EnvironmentLocator, EnvironmentSource } from './state'

export type QueryLike = Record<string, unknown>

function stringQuery(query: QueryLike): Record<string, string> {
  const next: Record<string, string> = {}
  for (const [key, value] of Object.entries(query)) {
    if (typeof value === 'string' && value !== '') {
      next[key] = value
    }
  }
  return next
}

export function environmentQuery(
  query: QueryLike,
  buildId: string | null,
  preparationId: string | null,
): Record<string, string> {
  const next = stringQuery(query)
  delete next.document
  delete next.page
  delete next.sourceStart
  delete next.sourceEnd
  delete next.source
  if (buildId) {
    next.build = buildId
  } else {
    delete next.build
  }
  if (preparationId) {
    next.preparation = preparationId
  } else {
    delete next.preparation
  }
  return next
}

export function contextQuery(
  query: QueryLike,
  buildId: string,
): Record<string, string> {
  return environmentQuery(query, buildId, null)
}

/**
 * 来源跳转回到第一阶段材料深链接：
 * PDF 带 `page`，文本带 UTF-16 `sourceStart`/`sourceEnd` 与 `source=1`。
 */
export function sourceWorkbenchUrl(
  projectId: string,
  source: EnvironmentSource,
  query: QueryLike,
  buildId: string | null,
  preparationId: string | null,
): string {
  const params = environmentQuery(query, buildId, preparationId)
  params.document = source.documentId
  if (source.locator.kind === 'pdf') {
    params.page = String(source.locator.pageNo ?? source.pageNo ?? 1)
  } else {
    params.source = '1'
    params.sourceStart = String(source.sourceStart)
    params.sourceEnd = String(source.sourceEnd)
  }
  return `/projects/${encodeURIComponent(projectId)}/workbench/evidence-graph?${new URLSearchParams(params).toString()}`
}

/** PDF 原件直链：物理页由 `#page=` 决定，不按字符偏移猜测。 */
export function sourceDirectUrl(
  source: EnvironmentSource,
  locator: EnvironmentLocator | undefined = source.locator,
): string {
  if (!locator) {
    return ''
  }
  if (locator.kind === 'pdf') {
    const page = locator.pageNo ?? source.pageNo ?? 1
    return `${locator.url.split('#')[0]}#page=${page}`
  }
  return locator.url
}

export function nextStageUrl(
  projectId: string,
  query: QueryLike,
  buildId: string | null,
  preparationId: string | null,
  selectionId: string | null = null,
): string {
  const params = environmentQuery(query, buildId, preparationId)
  if (selectionId) {
    params.selection = selectionId
  } else {
    delete params.selection
  }
  delete params.run
  return `/projects/${encodeURIComponent(projectId)}/workbench/dual-platform-run?${new URLSearchParams(params).toString()}`
}

export function previousStageUrl(
  projectId: string,
  query: QueryLike,
  buildId: string | null,
): string {
  const params = environmentQuery(query, buildId, null)
  return `/projects/${encodeURIComponent(projectId)}/workbench/evidence-graph?${new URLSearchParams(params).toString()}`
}
