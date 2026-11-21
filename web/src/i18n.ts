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
  materials: {
    title: string
    intro: string
    uploadHint: string
    chooseFile: string
    uploading: string
    emptyTitle: string
    emptyBody: string
    limitHint: string
    listTitle: string
    loading: string
    loadFailed: string
    retry: string
    open: string
    back: string
    status: {
      queued: string
      parsing: string
      ready: string
      failed: string
    }
    kind: {
      pdf: string
      markdown: string
      text: string
    }
    pageCount: string
    chunkCount: string
    excerpts: string
    excerptsEmpty: string
    excerptsLoading: string
    loadMore: string
    noMore: string
    pageNumber: string
    paragraphs: string
    viewOriginal: string
    viewSource: string
    sourceTitle: string
    sourceClose: string
    sourceFailed: string
    sourceOnlyText: string
    refreshFailed: string
    serviceUnavailable: string
    retryUpload: string
    dedupNotice: string
    failure: {
      emptyFile: string
      fileTooLarge: string
      unsupportedType: string
      decodeFailed: string
      noTextLayer: string
      originalMissing: string
      tooManyPages: string
      tooManyPagesDetail: string
      tooManyPagesNoLimit: string
      parseError: string
      canceled: string
    }
  }
  statusbar: string
  logoutFailed: string
  job: {
    title: string
    notice: string
    start: string
    cancel: string
    reconnect: string
    refreshEffects: string
    jobId: string
    runId: string
    status: string
    attempts: string
    progress: string
    effects: string
    log: string
    emptyLog: string
    waiting: string
    statusLabel: Record<'queued' | 'running' | 'succeeded' | 'failed' | 'canceled', string>
    streamInterrupted: string
    streamEnded: string
    awaitingTakeover: string
    failedBanner: string
    canceledBanner: string
    gap: string
    startFailed: string
    cancelFailed: string
    serviceUnavailable: string
    cancelNotApplied: string
    loadFailed: string
    retryLoad: string
  }
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
        '上传 PDF、Markdown 或 TXT 后即可阅读摘录；证据图谱的构建属于后续变更。',
      missingTitle: '缺少前置步骤',
      missingBody: '请先完成前一阶段，再回到这里继续。',
      goMaterials: '前往材料区',
      goPrevious: '前往上一阶段',
    },
    canvas: {
      title: '证据图谱画布',
      body: '材料入库已开放；证据图谱的构建属于后续变更，这里不会展示示例节点或虚假进度。',
      summary: '画布占位 · 无真实数据',
    },
    context: {
      materialsTitle: '材料入口',
      materialsBody:
        '材料在当前项目内上传与查看；就绪后可阅读摘录并回到原件。',
      materialsLink: '前往材料区',
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
    materials: {
      title: '项目材料',
      intro:
        '材料只属于当前项目。上传后由后台进程解析，就绪后可以阅读摘录并回到原件。',
      uploadHint: '选择 PDF、Markdown 或 TXT 文件后立即上传。',
      chooseFile: '选择文件',
      uploading: '正在上传…',
      emptyTitle: '还没有材料',
      emptyBody: '上传第一份材料后，证据图谱才能开始构建。',
      limitHint:
        '支持 PDF、Markdown、TXT，单文件不超过 {size}；当前 PDF 页数上限 {pages} 页。',
      listTitle: '材料列表',
      loading: '正在读取材料…',
      loadFailed: '材料列表读取失败。',
      retry: '重试读取',
      open: '查看摘录',
      back: '返回材料列表',
      status: {
        queued: '排队中',
        parsing: '解析中',
        ready: '就绪',
        failed: '失败',
      },
      kind: { pdf: 'PDF', markdown: 'Markdown', text: 'TXT' },
      pageCount: '总页数 {count}',
      chunkCount: '分块 {count}',
      excerpts: '阅读摘录',
      excerptsEmpty: '还没有可阅读的摘录。',
      excerptsLoading: '正在读取摘录…',
      loadMore: '加载更多摘录',
      noMore: '已经读到末尾。',
      pageNumber: '第 {page} 页',
      paragraphs: '第 {range} 段',
      viewOriginal: '查看原件',
      viewSource: '查看原文',
      sourceTitle: '原件原文（未清洗）',
      sourceClose: '关闭原文',
      sourceFailed: '原文读取失败，请重试。',
      sourceOnlyText: 'PDF 材料请在原件中查看对应页。',
      refreshFailed: '暂时无法刷新材料状态，已保留当前内容。',
      serviceUnavailable: '服务暂时不可用，请稍后重试；登录状态已保留。',
      retryUpload: '重新选择文件上传',
      dedupNotice: '该内容已在项目中，直接打开已有材料。',
      failure: {
        emptyFile: '文件为空，请选择有内容的文件。',
        fileTooLarge: '文件超过 20 MiB 上限。',
        unsupportedType: '仅支持 PDF、Markdown 与 TXT。',
        decodeFailed: '无法以 UTF-8 或可识别的中文编码解码。',
        noTextLayer: '该 PDF 没有文字层，请上传文本型 PDF。',
        originalMissing: '原件不可读取，请重新上传。',
        tooManyPages: '该 PDF 页数超过当前上限。',
        tooManyPagesDetail: '该 PDF 共 {pages} 页，超过解析时的上限 {limit} 页。',
        tooManyPagesNoLimit: '该 PDF 页数超过解析时的上限。',
        parseError: '解析失败，请重试或更换文件。',
        canceled: '入库已取消。',
      },
    },
    statusbar: '工作台外壳就绪 · 证据图谱待构建',
    logoutFailed: '退出失败，请稍后重试。',
    job: {
      title: '测试任务（连通性自检）',
      notice:
        '这是任务与事件链路的连通性自检，不产生推演结论，也不会记入阶段完成度。',
      start: '开始测试任务',
      cancel: '取消任务',
      reconnect: '重新连接',
      refreshEffects: '刷新副作用计数',
      jobId: 'jobId',
      runId: 'runId',
      status: '状态',
      attempts: '领取次数',
      progress: '已提交步骤',
      effects: '副作用',
      log: '事件日志',
      emptyLog: '还没有事件；开始任务后日志会从第 1 条出现。',
      waiting: '等待 worker 领取…',
      statusLabel: {
        queued: '排队中',
        running: '运行中',
        succeeded: '成功',
        failed: '失败',
        canceled: '已取消',
      },
      streamInterrupted: '连接已中断，正在从第 {seq} 条重连…',
      streamEnded: '事件流已结束。',
      awaitingTakeover: '执行进程已中断，任务等待接手（租约已到期 {seconds} 秒）。',
      failedBanner: '测试任务失败：{reason}',
      canceledBanner: '已取消（第 {step} 步之后）。',
      gap: '检测到事件缺口，正在从第 {seq} 条重新同步…',
      startFailed: '开始任务失败，请稍后重试。',
      cancelFailed: '取消任务失败，请稍后重试。',
      serviceUnavailable: '服务暂时不可用，请稍后重试；已保留登录态与日志。',
      cancelNotApplied: '任务已结束，取消未生效。',
      loadFailed: '暂时无法读取任务，请稍后重试。',
      retryLoad: '重试读取',
    },
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
        'Upload a PDF, Markdown or TXT file to read excerpts; building the evidence graph arrives in a later change.',
      missingTitle: 'A prerequisite is missing',
      missingBody: 'Complete the previous stage first, then return here.',
      goMaterials: 'Go to materials',
      goPrevious: 'Go to previous stage',
    },
    canvas: {
      title: 'Evidence graph canvas',
      body: 'Material intake is available; building the evidence graph arrives in a later change. No sample nodes or fake progress are shown here.',
      summary: 'Canvas placeholder · no real data',
    },
    context: {
      materialsTitle: 'Material entry',
      materialsBody:
        'Materials are uploaded and read inside this project; ready items can be traced back to the original.',
      materialsLink: 'Go to materials',
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
    materials: {
      title: 'Project materials',
      intro:
        'Materials belong to this project only. Uploads are parsed by the background worker; ready materials can be read as excerpts and traced back to the original.',
      uploadHint: 'Choose a PDF, Markdown or TXT file to upload.',
      chooseFile: 'Choose file',
      uploading: 'Uploading…',
      emptyTitle: 'No materials yet',
      emptyBody: 'Upload the first material before the evidence graph can be built.',
      limitHint:
        'PDF, Markdown and TXT are supported, up to {size} per file; the current PDF page limit is {pages} pages.',
      listTitle: 'Materials',
      loading: 'Loading materials…',
      loadFailed: 'Could not load the material list.',
      retry: 'Retry',
      open: 'Read excerpts',
      back: 'Back to materials',
      status: {
        queued: 'Queued',
        parsing: 'Parsing',
        ready: 'Ready',
        failed: 'Failed',
      },
      kind: { pdf: 'PDF', markdown: 'Markdown', text: 'TXT' },
      pageCount: 'Pages {count}',
      chunkCount: 'Chunks {count}',
      excerpts: 'Excerpts',
      excerptsEmpty: 'No excerpts to read yet.',
      excerptsLoading: 'Loading excerpts…',
      loadMore: 'Load more excerpts',
      noMore: 'End of excerpts.',
      pageNumber: 'Page {page}',
      paragraphs: 'Paragraphs {range}',
      viewOriginal: 'View original',
      viewSource: 'View source text',
      sourceTitle: 'Original text (uncleaned)',
      sourceClose: 'Close source',
      sourceFailed: 'Could not read the source text. Please retry.',
      sourceOnlyText: 'Open the original PDF to view the matching page.',
      refreshFailed: 'Material status could not be refreshed; the current content is kept.',
      serviceUnavailable: 'Service temporarily unavailable. Your session is kept; retry shortly.',
      retryUpload: 'Choose another file',
      dedupNotice: 'This content already exists in the project; opening the existing material.',
      failure: {
        emptyFile: 'The file is empty. Choose a file with content.',
        fileTooLarge: 'The file exceeds the 20 MiB limit.',
        unsupportedType: 'Only PDF, Markdown and TXT are supported.',
        decodeFailed: 'The file could not be decoded as UTF-8 or a recognizable Chinese encoding.',
        noTextLayer: 'This PDF has no text layer. Upload a text-based PDF.',
        originalMissing: 'The original file is unreadable. Upload it again.',
        tooManyPages: 'This PDF exceeds the current page limit.',
        tooManyPagesDetail: 'This PDF has {pages} pages, above the {limit}-page limit applied when it was parsed.',
        tooManyPagesNoLimit: 'This PDF exceeded the page limit applied when it was parsed.',
        parseError: 'Parsing failed. Retry or choose another file.',
        canceled: 'Intake was canceled.',
      },
    },
    statusbar: 'Workbench shell ready · evidence graph pending',
    logoutFailed: 'Sign out failed. Please try again.',
    job: {
      title: 'Test job (connectivity self-check)',
      notice:
        'This is a connectivity self-check for the job and event pipeline. It produces no simulation result and never marks the stage complete.',
      start: 'Start test job',
      cancel: 'Cancel job',
      reconnect: 'Reconnect',
      refreshEffects: 'Refresh effect count',
      jobId: 'jobId',
      runId: 'runId',
      status: 'Status',
      attempts: 'Attempts',
      progress: 'Steps applied',
      effects: 'Effects',
      log: 'Event log',
      emptyLog: 'No events yet. The log starts at seq 1 once the job runs.',
      waiting: 'Waiting for a worker…',
      statusLabel: {
        queued: 'Queued',
        running: 'Running',
        succeeded: 'Succeeded',
        failed: 'Failed',
        canceled: 'Canceled',
      },
      streamInterrupted: 'Stream interrupted, resuming from seq {seq}…',
      streamEnded: 'Event stream ended.',
      awaitingTakeover:
        'Worker interrupted; waiting for takeover (lease expired {seconds}s ago).',
      failedBanner: 'Test job failed: {reason}',
      canceledBanner: 'Canceled after step {step}.',
      gap: 'Event gap detected, resynchronizing from seq {seq}…',
      startFailed: 'Could not start the job. Please retry.',
      cancelFailed: 'Could not cancel the job. Please retry.',
      serviceUnavailable:
        'Service temporarily unavailable. Your session and log are kept; retry shortly.',
      cancelNotApplied: 'The job already finished, so cancel did not apply.',
      loadFailed: 'The job could not be read. Please retry.',
      retryLoad: 'Retry loading',
    },
  },
}

export function messagesFor(lang: Lang): WorkbenchMessages {
  return messages[lang]
}
