/** 工作台外壳的中英文文案；阶段名称来自阶段注册表。 */

import type { Lang } from './workbench/stage-links'

export interface WorkbenchMessages {
  toolbar: {
    console: string
    detail: string
    history: string
    logout: string
    layout: string
    language: string
    contextShow: string
    contextHide: string
  }
  health: {
    checking: string
    ok: string
    degraded: string
    unreachable: string
  }
  layout: {
    graph: string
    split: string
    workbench: string
  }
  langName: {
    zh: string
    en: string
  }
  aria: {
    stages: string
    layout: string
    language: string
  }
  stage: {
    navTitle: string
    current: string
    prerequisite: string
    statusReady: string
    statusPending: string
    project: string
  }
  empty: {
    materialsTitle: string
    materialsBody: string
    missingTitle: string
    missingBody: string
    goMaterials: string
    goPrevious: string
  }
  canvas: {
    title: string
    body: string
    summary: string
  }
  context: {
    materialsTitle: string
    materialsBody: string
    materialsLink: string
    questionsTitle: string
    questions: string[]
    nextTitle: string
    nextBody: string
  }
  errors: {
    loading: string
    projectNotFound: string
    projectNotFoundBody: string
    serviceTitle: string
    serviceBody: string
    networkTitle: string
    networkBody: string
    stageNotFound: string
    stageNotFoundBody: string
    retry: string
    backProjects: string
    backConsole: string
  }
  statusbar: string
  logoutFailed: string
}

const messages: Record<Lang, WorkbenchMessages> = {
  zh: {
    toolbar: {
      console: '返回控制台',
      detail: '项目详情',
      history: '历史',
      logout: '退出登录',
      layout: '布局',
      language: '语言',
      contextShow: '显示上下文',
      contextHide: '收起上下文',
    },
    health: {
      checking: '检查中',
      ok: '可用',
      degraded: '降级',
      unreachable: '不可达',
    },
    layout: {
      graph: '图谱',
      split: '双栏',
      workbench: '工作台',
    },
    langName: {
      zh: '中文',
      en: 'English',
    },
    aria: {
      stages: '阶段导航',
      layout: '布局切换',
      language: '语言切换',
    },
    stage: {
      navTitle: '五个阶段',
      current: '当前阶段',
      prerequisite: '进入条件',
      statusReady: '工作台外壳就绪',
      statusPending: '阶段数据尚未接入',
      project: '项目',
    },
    empty: {
      materialsTitle: '还没有材料',
      materialsBody:
        '当前版本尚未开放材料入库，因此无法构建证据图谱。可以先去控制台查看材料入口说明。',
      missingTitle: '缺少前置步骤',
      missingBody: '请先完成前一阶段，再回到这里继续。',
      goMaterials: '查看材料入口',
      goPrevious: '前往上一阶段',
    },
    canvas: {
      title: '证据图谱画布',
      body: '材料入库与图谱数据将在后续变更接入，这里不会展示示例节点或虚假进度。',
      summary: '画布占位 · 无真实数据',
    },
    context: {
      materialsTitle: '材料入口',
      materialsBody: '材料入库尚未开放，当前不能上传或保存材料。',
      materialsLink: '查看控制台材料说明',
      questionsTitle: '推演问题',
      questions: [
        '哪些证据真正改变了判断？',
        '两个平台在哪些条件下产生分歧？',
        '结论在什么情况下会失效？',
      ],
      nextTitle: '下一步',
      nextBody: '当前版本可以创建并打开项目；材料、运行与报告将在后续变更接入。',
    },
    errors: {
      loading: '正在加载项目…',
      projectNotFound: '项目不存在',
      projectNotFoundBody: '该链接无效，或项目不属于当前账户。',
      serviceTitle: '服务暂时不可用',
      serviceBody: '无法读取项目数据，请确认服务与数据库状态后重试。',
      networkTitle: '无法连接服务',
      networkBody: '请检查网络或服务状态后重试。',
      stageNotFound: '阶段不存在',
      stageNotFoundBody: '该阶段地址无效，请选择下面五个阶段之一。',
      retry: '重试',
      backProjects: '返回项目列表',
      backConsole: '返回控制台',
    },
    statusbar: '工作台外壳就绪 · 阶段数据尚未接入',
    logoutFailed: '退出失败，请稍后重试。',
  },
  en: {
    toolbar: {
      console: 'Back to console',
      detail: 'Project details',
      history: 'History',
      logout: 'Sign out',
      layout: 'Layout',
      language: 'Language',
      contextShow: 'Show context',
      contextHide: 'Hide context',
    },
    health: {
      checking: 'Checking',
      ok: 'Available',
      degraded: 'Degraded',
      unreachable: 'Unreachable',
    },
    layout: {
      graph: 'Graph',
      split: 'Split',
      workbench: 'Workbench',
    },
    langName: {
      zh: '中文',
      en: 'English',
    },
    aria: {
      stages: 'Stage navigation',
      layout: 'Layout switch',
      language: 'Language switch',
    },
    stage: {
      navTitle: 'Five stages',
      current: 'Current stage',
      prerequisite: 'Entry requirement',
      statusReady: 'Workbench shell ready',
      statusPending: 'Stage data not connected',
      project: 'Project',
    },
    empty: {
      materialsTitle: 'No materials yet',
      materialsBody:
        'Material intake is not available in this version, so the evidence graph cannot be built yet. Open the console to read the material entry note.',
      missingTitle: 'A prerequisite is missing',
      missingBody: 'Complete the previous stage first, then return here.',
      goMaterials: 'View material entry',
      goPrevious: 'Go to previous stage',
    },
    canvas: {
      title: 'Evidence graph canvas',
      body: 'Material intake and graph data will arrive in a later change. No sample nodes or fake progress are shown here.',
      summary: 'Canvas placeholder · no real data',
    },
    context: {
      materialsTitle: 'Material entry',
      materialsBody:
        'Material intake is not available yet; nothing can be uploaded or saved here.',
      materialsLink: 'Read the console material note',
      questionsTitle: 'Simulation questions',
      questions: [
        'Which evidence actually changed the judgment?',
        'Where do the two platforms diverge?',
        'Under what conditions would the conclusion fail?',
      ],
      nextTitle: 'Next step',
      nextBody:
        'This version can create and open projects; materials, runs and reports arrive in later changes.',
    },
    errors: {
      loading: 'Loading project…',
      projectNotFound: 'Project not found',
      projectNotFoundBody: 'This link is invalid, or the project does not belong to this account.',
      serviceTitle: 'Service temporarily unavailable',
      serviceBody:
        'The project data could not be read. Check the service and database, then retry.',
      networkTitle: 'Cannot reach the service',
      networkBody: 'Check the network or service status, then retry.',
      stageNotFound: 'Stage not found',
      stageNotFoundBody: 'This stage address is invalid. Choose one of the five stages below.',
      retry: 'Retry',
      backProjects: 'Back to projects',
      backConsole: 'Back to console',
    },
    statusbar: 'Workbench shell ready · stage data not connected',
    logoutFailed: 'Sign out failed. Please try again.',
  },
}

export function messagesFor(lang: Lang): WorkbenchMessages {
  return messages[lang]
}
