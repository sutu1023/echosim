/** 工作台五阶段的唯一注册表：顺序、双语名称、进入条件与继续目标。 */

export type StageSlug =
  | 'evidence-graph'
  | 'roles-environment'
  | 'dual-platform-run'
  | 'report'
  | 'interaction'

export interface StageDefinition {
  id: number
  slug: StageSlug
  title: { zh: string; en: string }
  purpose: { zh: string; en: string }
  prerequisite: { zh: string; en: string }
  hasDataInThisChange: boolean
}

export const stageDefinitions: readonly StageDefinition[] = [
  {
    id: 1,
    slug: 'evidence-graph',
    title: { zh: '构建证据图谱', en: 'Build evidence graph' },
    purpose: {
      zh: '把材料整理成可追溯的证据与关系。',
      en: 'Turn materials into traceable evidence and relationships.',
    },
    prerequisite: { zh: '至少一份材料', en: 'At least one material' },
    hasDataInThisChange: false,
  },
  {
    id: 2,
    slug: 'roles-environment',
    title: { zh: '准备角色与环境', en: 'Prepare roles and environment' },
    purpose: {
      zh: '为双平台推演配置角色、环境与约束。',
      en: 'Configure roles, environments and constraints for the dual-platform run.',
    },
    prerequisite: { zh: '证据图谱已构建', en: 'Evidence graph is ready' },
    hasDataInThisChange: false,
  },
  {
    id: 3,
    slug: 'dual-platform-run',
    title: { zh: '双平台运行', en: 'Dual-platform run' },
    purpose: {
      zh: '在两个平台上运行推演并观察分歧。',
      en: 'Run the simulation on both platforms and observe divergence.',
    },
    prerequisite: { zh: '角色与环境已准备', en: 'Roles and environment are ready' },
    hasDataInThisChange: false,
  },
  {
    id: 4,
    slug: 'report',
    title: { zh: '生成报告', en: 'Generate report' },
    purpose: {
      zh: '把运行结果整理成可审阅的报告。',
      en: 'Turn run results into a reviewable report.',
    },
    prerequisite: { zh: '双平台运行已完成', en: 'Dual-platform run is complete' },
    hasDataInThisChange: false,
  },
  {
    id: 5,
    slug: 'interaction',
    title: { zh: '深度互动', en: 'Deep interaction' },
    purpose: {
      zh: '围绕报告追问、对照与继续推演。',
      en: 'Question, compare and continue the simulation around the report.',
    },
    prerequisite: { zh: '报告已生成', en: 'Report is ready' },
    hasDataInThisChange: false,
  },
]

export const defaultStage = stageDefinitions[0]

export function getStage(value: unknown): StageDefinition | undefined {
  if (typeof value !== 'string') {
    return undefined
  }
  return stageDefinitions.find((stage) => stage.slug === value)
}

export function previousStage(
  stage: StageDefinition | undefined,
): StageDefinition | undefined {
  if (!stage) {
    return undefined
  }
  return stageDefinitions.find((item) => item.id === stage.id - 1)
}

export type StageContentState = 'empty' | 'normal'

/**
 * 当前变更没有阶段数据源；normal 分支只由隔离测试数据证明。
 * 产品页面不得把测试夹具传给这个函数。
 */
export function resolveStageContentState(hasStageData: boolean): StageContentState {
  return hasStageData ? 'normal' : 'empty'
}
