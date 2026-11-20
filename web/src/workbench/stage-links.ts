/** 工作台布局与语言选项，以及显式保留查询参数的阶段链接。 */

export type LayoutMode = 'graph' | 'split' | 'workbench'
export type Lang = 'zh' | 'en'

export const DEFAULT_LAYOUT: LayoutMode = 'split'
export const DEFAULT_LANG: Lang = 'zh'

export const LAYOUT_MODES: readonly LayoutMode[] = ['graph', 'split', 'workbench']
export const LANGS: readonly Lang[] = ['zh', 'en']

export type StageQuery = Record<
  string,
  string | (string | null)[] | null | undefined
>

export interface StageLocation {
  name: 'workbench-stage'
  params: { id: string; stage: string }
  query: StageQuery
}

export function normalizeLayout(value: unknown): LayoutMode {
  return typeof value === 'string' && LAYOUT_MODES.includes(value as LayoutMode)
    ? (value as LayoutMode)
    : DEFAULT_LAYOUT
}

export function normalizeLang(value: unknown): Lang {
  return value === 'en' ? 'en' : DEFAULT_LANG
}

/**
 * 阶段跳转必须显式复制 query；`router.push` 只传 params 时会丢掉 layout/lang。
 */
export function stageLocation(
  projectId: string,
  stage: string,
  query: StageQuery = {},
): StageLocation {
  return {
    name: 'workbench-stage',
    params: { id: projectId, stage },
    query: { ...query },
  }
}
