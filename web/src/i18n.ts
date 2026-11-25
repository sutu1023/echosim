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
    rolesTitle: string
    rolesBody: string
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
  environment: {
    title: string
    intro: string
    loading: string
    loadFailed: string
    retry: string
    serviceUnavailable: string
    question: string
    snapshot: string
    start: string
    starting: string
    retryPreparation: string
    graphNotReady: string
    graphNotReadyBody: string
    backToGraph: string
    notStarted: string
    startHint: string
    preparing: string
    preparingHint: string
    previewBatch: string
    profileProgress: string
    previewOnly: string
    failed: string
    canceled: string
    retryHint: string
    ready: string
    readyHint: string
    confirm: string
    confirming: string
    confirmed: string
    confirmedHint: string
    confirmNeeded: string
    confirmDirty: string
    confirmInvalid: string
    enterNextStage: string
    roles: string
    roleCount: string
    selectRole: string
    roleDetail: string
    pageNumber: string
    mention: string
    background: string
    backgroundMissing: string
    expressedTopics: string
    involvedTopics: string
    noExpressed: string
    simulation: string
    simulationTopics: string
    motive: string
    stance: string
    activeHours: string
    postingFrequency: string
    responseDelay: string
    influenceWeight: string
    platformPreference: string
    sources: string
    openSource: string
    openPdf: string
    backToMaterial: string
    sourceNatureUnknown: string
    environmentConfig: string
    platforms: string
    initialTopics: string
    firstSpeakers: string
    activationOrder: string
    roundsTitle: string
    durationHours: string
    minutesPerRound: string
    roundsMode: string
    auto: string
    manual: string
    manualRounds: string
    autoRounds: string
    effectiveRounds: string
    save: string
    saving: string
    saved: string
    conflictReload: string
    frozen: string
    frozenHint: string
    newerGraph: string
    newerGraphHint: string
    runPendingTitle: string
    runPendingBody: string
    runPendingMissing: string
    goPrepare: string
    frozenSelection: string
    frozenQuestion: string
    frozenRounds: string
    frozenRoles: string
    errorCodes: Record<string, string>
  }
  simulation: {
    title: string
    intro: string
    loading: string
    loadFailed: string
    retry: string
    serviceUnavailable: string
    missingSelection: string
    missingSelectionBody: string
    goPrepare: string
    start: string
    starting: string
    started: string
    notStarted: string
    startHint: string
    clock: string
    engine: string
    phase: string
    question: string
    selectionSummary: string
    roles: string
    platformMap: string
    rounds: string
    actions: string
    actionStream: string
    emptyActions: string
    logs: string
    emptyLogs: string
    runs: string
    currentRun: string
    stop: string
    stopping: string
    stopped: string
    originalPost: string
    closeTarget: string
    targetUnresolved: string
    role: string
    round: string
    partialResult: string
    failedPlatform: string
    countMismatch: string
    noRuns: string
    errorCodes: Record<string, string>
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
  graph: {
    title: string
    intro: string
    materialsTitle: string
    questionLabel: string
    questionPlaceholder: string
    start: string
    starting: string
    selected: string
    maxDocs: string
    emptyMaterials: string
    empty: string
    loading: string
    loadFailed: string
    retry: string
    buildId: string
    jobId: string
    runId: string
    phaseLabel: Record<'preparing' | 'waiting_index' | 'drawing' | 'ready' | 'failed' | 'canceled', string>
    waitingIndex: string
    accepted: string
    confirmed: string
    drawing: string
    ready: string
    failed: string
    canceled: string
    revision: string
    nodes: string
    edges: string
    legend: string
    material: string
    hypothesis: string
    zoomHint: string
    fit: string
    refresh: string
    maximize: string
    restore: string
    edgeLabels: string
    details: string
    node: string
    edge: string
    origin: string
    claimNatureLabel: string
    claimNature: Record<string, string>
    claimNatureUnknown: string
    nodeRelations: string
    noNodeRelations: string
    summary: string
    sources: string
    noSources: string
    openSource: string
    sourceTitle: string
    sourceClose: string
    noSelection: string
    next: string
    nextBlocked: string
    nextHint: string
    questionRequired: string
    tooMany: string
    stale: string
    serviceUnavailable: string
    sourceFailed: string
    failureCodes: Record<string, string>
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
      rolesTitle: '角色与环境尚未配置',
      rolesBody: '证据图谱已完成即可继续；角色、环境与约束将在下一轮变更中接入。',
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
    environment: {
      title: '准备角色与环境',
      intro: '从证据图谱的最终快照选择角色，核对材料事实与模拟设定，确认轮数后冻结本次选择。',
      loading: '正在读取准备状态…',
      loadFailed: '准备状态读取失败，请重试。',
      retry: '重试',
      serviceUnavailable: '服务暂时不可用，登录状态已保留。',
      question: '推演问题',
      snapshot: '快照',
      start: '开始准备',
      starting: '正在开始…',
      retryPreparation: '重试准备',
      graphNotReady: '证据图谱尚未完成',
      graphNotReadyBody: '该 build 还没有已发布的最终快照，请回到第一阶段完成建图。',
      backToGraph: '返回证据图谱',
      notStarted: '尚未准备角色与环境',
      startHint: '使用同一 build 的最终快照生成角色；准备过程由独立 worker 执行。',
      preparing: '准备中',
      preparingHint: '已完成批次可以展开查看；确认与进入第三阶段在发布完成前不可用。',
      previewBatch: '第 {index} 批（已完成预览）',
      profileProgress: '已生成 {profiled} / {total} 个角色',
      previewOnly: '未完成预览',
      failed: '准备失败',
      canceled: '准备已取消',
      retryHint: '已完成批次只读；重试会创建新的准备与任务，旧记录保留。',
      ready: '已就绪，待确认',
      readyHint: '核对角色、平台映射、初始话题与轮数后确认冻结。',
      confirm: '确认并冻结',
      confirming: '正在确认…',
      confirmed: '已确认',
      confirmedHint: '本次选择已冻结，第三阶段只读取这份冻结结果。',
      confirmNeeded: '需要确认后才能进入第三阶段。',
      confirmDirty: '有未保存的轮数修改，请先保存并从服务端读回后再确认。',
      confirmInvalid: '轮数配置不合法，无法确认；请修正后保存。',
      enterNextStage: '进入双平台运行',
      roles: '角色',
      roleCount: '{count} 个角色',
      selectRole: '查看角色详情',
      roleDetail: '角色详情',
      pageNumber: '第 {page} 页',
      mention: '材料提及',
      background: '背景',
      backgroundMissing: '材料未说明',
      expressedTopics: '该角色表达/主张',
      involvedTopics: '材料涉及的话题',
      noExpressed: '材料未说明关注话题',
      simulation: '模拟设定',
      simulationTopics: '模拟关注点',
      motive: '动机',
      stance: '立场',
      activeHours: '活跃时段',
      postingFrequency: '发言频率',
      responseDelay: '响应延迟（分钟）',
      influenceWeight: '影响力权重',
      platformPreference: '平台偏好',
      sources: '来源',
      openSource: '打开原文并高亮',
      openPdf: '打开原件对应页',
      backToMaterial: '回到材料',
      sourceNatureUnknown: '来源性质未知',
      environmentConfig: '环境配置',
      platforms: '平台映射',
      initialTopics: '初始话题',
      firstSpeakers: '首发角色与话题',
      activationOrder: '激活顺序',
      roundsTitle: '轮数配置',
      durationHours: '模拟总时长（小时）',
      minutesPerRound: '每轮分钟数',
      roundsMode: '轮数模式',
      auto: '自动',
      manual: '手动',
      manualRounds: '手动轮数',
      autoRounds: '自动轮数',
      effectiveRounds: '生效轮数',
      save: '保存轮数',
      saving: '正在保存…',
      saved: '轮数已保存。',
      conflictReload: '配置已被其他页面更新，已重新读取，请核对后再确认。',
      frozen: '已冻结，轮数不可再修改。',
      frozenHint: '冻结选择包含问题、快照、角色、轮数、平台映射与首发话题。',
      newerGraph: '已有更新的图谱',
      newerGraphHint: '本次准备仍绑定旧快照；为新图准备请回到第一阶段并在新 build 下开始。',
      runPendingTitle: '准备已确认，运行功能待接入',
      runPendingBody: '角色与环境已按服务端冻结选择确认；双平台实际运行将在后续变更接入。',
      runPendingMissing: '尚未确认角色与环境',
      goPrepare: '前往准备角色与环境',
      frozenSelection: '冻结选择',
      frozenQuestion: '冻结问题',
      frozenRounds: '冻结轮数',
      frozenRoles: '冻结角色',
      errorCodes: {
        graph_not_ready: '证据图谱尚未完成。',
        graph_snapshot_missing: '该 build 没有可用的最终快照。',
        no_role_candidates: '材料中没有可作为角色的主体。',
        preparation_retry_required: '失败或取消的准备需要显式重试。',
        preparation_not_retryable: '当前准备不需要重试。',
        preparation_not_ready: '准备尚未完成，不能确认。',
        preparation_not_editable: '失败或取消的准备不可编辑。',
        preparation_frozen: '准备已确认冻结，轮数不可再修改。',
        settings_conflict: '配置已被其他页面更新，请重新读取后再确认。',
        duration_invalid: '模拟总时长必须是 1–720 小时的整数。',
        rounds_invalid: '手动轮数必须是 1–1000 的整数。',
        rounds_not_integral: '自动轮数不能整除，请调整时长或每轮分钟数。',
        model_unavailable: '画像服务暂时不可用，请稍后重试。',
        invalid_model_output: '画像输出不合法，未发布任何角色。',
        role_source_missing: '角色缺少材料来源，未发布。',
        role_source_mismatch: '角色来源与快照不一致，未发布。',
        preparation_failed: '准备执行失败，请稍后重试。',
      },
    },
    simulation: {
      title: '双平台运行',
      intro: '从已确认的冻结选择启动运行，观察两个平台的模拟时钟、行动流与按平台统计。',
      loading: '正在读取运行状态…',
      loadFailed: '运行状态读取失败，请重试。',
      retry: '重试',
      serviceUnavailable: '服务暂时不可用，登录状态已保留。',
      missingSelection: '尚未确认角色与环境',
      missingSelectionBody: '第三阶段需要一份已确认的冻结选择；请先在第二阶段完成确认。',
      goPrepare: '前往准备角色与环境',
      start: '开始运行',
      starting: '正在开始…',
      started: '运行已开始。',
      notStarted: '暂无运行记录',
      startHint: '开始后由 worker 在两个本地平台推进，动作从引擎记录读回。',
      clock: '模拟时钟',
      engine: '引擎',
      phase: '状态',
      question: '推演问题',
      selectionSummary: '冻结选择',
      roles: '角色',
      platformMap: '平台',
      rounds: '轮次',
      actions: '动作',
      actionStream: '行动流',
      emptyActions: '还没有从引擎读回的动作。',
      logs: '运行日志',
      emptyLogs: '暂无运行日志。',
      runs: '运行记录',
      currentRun: '当前运行',
      stop: '停止运行',
      stopping: '停止已请求',
      stopped: '已停止',
      originalPost: '原帖',
      closeTarget: '收起原帖',
      targetUnresolved: '目标未在本运行中定位',
      role: '角色',
      round: '轮次',
      partialResult: '部分结果：仅一个平台完成',
      failedPlatform: '该平台失败',
      countMismatch: '页面计数与服务端记录不一致，已按服务端刷新。',
      noRuns: '该冻结选择还没有运行记录。',
      errorCodes: {
        selection_missing: '缺少已确认的冻结选择。',
        selection_invalid: '冻结选择数据不完整，无法开始运行。',
        engine_not_enabled: '真实本地 OASIS 运行未启用。',
        engine_unavailable: '本地 OASIS 引擎不可用，未创建替身动作。',
        action_unconfirmed: '引擎已产生副作用但无法确认，已保留证据并停止该平台。',
        platform_a_setup: '信息广场环境准备失败。',
        platform_b_setup: '话题社区环境准备失败。',
        platform_a_action: '信息广场动作执行失败。',
        platform_b_action: '话题社区动作执行失败。',
        platform_a_record: '信息广场引擎记录无法核对。',
        platform_b_record: '话题社区引擎记录无法核对。',
        simulation_timeout: '单轮执行超出预算。',
        simulation_too_large: '动作数量超过上限。',
        simulation_failed: '运行执行失败。',
        canceled: '运行已停止。',
      },
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
    graph: {
      title: '证据图谱',
      intro: '选择已就绪材料并输入情景问题；建图由后台 worker 执行，完成后可点开材料关系回到原文。',
      materialsTitle: '选择材料（最多 5 份）',
      questionLabel: '情景问题',
      questionPlaceholder: '例如：雨天是否应该增加一班校园接驳车？',
      start: '开始建图',
      starting: '正在开始…',
      selected: '已选 {count} / {max} 份',
      maxDocs: '单次最多选择 {max} 份材料',
      emptyMaterials: '还没有已就绪材料，请先在材料区上传并等待解析完成。',
      empty: '还没有建图记录。选择材料与问题后开始构建。',
      loading: '正在读取建图…',
      loadFailed: '建图状态读取失败，请重试。',
      retry: '重试',
      buildId: 'buildId',
      jobId: 'jobId',
      runId: 'runId',
      phaseLabel: {
        preparing: '准备中',
        waiting_index: '等待索引',
        drawing: '增量绘图',
        ready: '完成',
        failed: '失败',
        canceled: '已取消',
      },
      waitingIndex: '已受理 {accepted} / 已确认 {confirmed} / 计划 {planned}',
      accepted: '已受理',
      confirmed: '已确认',
      drawing: '正在读取节点与边…',
      ready: '图谱已完成，可以进入下一阶段。',
      failed: '建图失败',
      canceled: '建图已取消，已发布的部分快照仍可查看。',
      revision: '快照版本',
      nodes: '节点',
      edges: '关系',
      legend: '图例',
      material: '材料陈述',
      hypothesis: '用户假设',
      zoomHint: '滚轮缩放，拖动平移；可拖动节点。',
      fit: '适应画布',
      refresh: '刷新',
      maximize: '最大化',
      restore: '退出最大化',
      edgeLabels: '关系标签',
      details: '详情',
      node: '节点',
      edge: '关系',
      origin: '来源类型',
      claimNatureLabel: '材料性质',
      claimNature: {
        media_report: '媒体报道',
        party_claim: '当事方主张',
        author_recommendation: '报告建议',
        official_decision: '机构正式决定',
        other_material: '其他材料陈述',
      },
      claimNatureUnknown: '未标注',
      nodeRelations: '该主体参与的关系',
      noNodeRelations: '这个节点还没有可见的材料关系。',
      summary: '摘要',
      sources: '原文来源',
      noSources: '该假设没有原文来源。',
      openSource: '回到原文',
      sourceTitle: '原文（未清洗）',
      sourceClose: '关闭原文',
      noSelection: '点击节点或关系查看全部来源。',
      next: '进入角色与环境',
      nextBlocked: '图谱尚未完成或来源不完整，不能进入下一阶段。',
      nextHint: '下一阶段将配置角色与环境。',
      questionRequired: '请输入 1–500 字符的问题。',
      tooMany: '单次最多选择 5 份材料。',
      stale: '已忽略上一次建图的迟到响应。',
      serviceUnavailable: '服务暂时不可用，已保留当前画面。',
      sourceFailed: '原文读取失败，请重试。',
      failureCodes: {
        missing_config: '模型或图谱配置不完整，建图未发起外部调用。',
        too_many_documents: '材料数量超过上限。',
        document_not_ready: '所选材料尚未解析完成。',
        invalid_graph_scope: '所选材料或图谱范围不合法。',
        invalid_graph_response: '图谱服务返回了不可用的结果。',
        invalid_model_output: '模型输出未通过结构校验。',
        source_mismatch: '引文与原文不一致。',
        ambiguous_source: '引文在材料中多次出现，无法唯一定位。',
        source_unavailable: '材料原件不可读取，无法定位原文。',
        model_unavailable: '模型暂时不可用。',
        graph_unavailable: '图谱服务暂时不可用。',
        write_unconfirmed: 'episode 写入结果未能确认。',
        stale_or_foreign_hit: '检索只命中旧记录或其它记录。',
        index_timeout: '等待索引超时。',
        processing_timeout: '等待处理或读回超时。',
        graph_incomplete: '读回结果不足以构成完成图谱。',
        graph_too_large: '节点或关系超过规模上限，未发布截断结果。',
        too_many_sources: '单项来源超过上限。',
        insufficient_graph: '材料节点或材料关系不足。',
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
      rolesTitle: 'Roles and environment are not configured yet',
      rolesBody: 'The evidence graph is ready. Roles, environment and constraints arrive in a later change.',
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
    environment: {
      title: 'Prepare roles and environment',
      intro: 'Choose roles from the final graph snapshot, review material facts separately from simulated settings, and freeze the selection after confirming rounds.',
      loading: 'Loading preparation…',
      loadFailed: 'Could not load the preparation. Please retry.',
      retry: 'Retry',
      serviceUnavailable: 'Service temporarily unavailable. Your session is kept.',
      question: 'Scenario question',
      snapshot: 'Snapshot',
      start: 'Start preparation',
      starting: 'Starting…',
      retryPreparation: 'Retry preparation',
      graphNotReady: 'The evidence graph is not ready',
      graphNotReadyBody: 'This build has no published final snapshot. Complete the first stage first.',
      backToGraph: 'Back to evidence graph',
      notStarted: 'Roles and environment not prepared yet',
      startHint: 'Roles are generated from the final snapshot of this build by the background worker.',
      preparing: 'Preparing',
      preparingHint: 'Completed batches can be expanded. Confirmation and the next stage stay disabled until publishing completes.',
      previewBatch: 'Batch {index} (preview ready)',
      profileProgress: '{profiled} / {total} roles generated',
      previewOnly: 'Unfinished preview',
      failed: 'Preparation failed',
      canceled: 'Preparation canceled',
      retryHint: 'Completed batches stay read-only. Retry creates a new preparation and job; old records remain.',
      ready: 'Ready to confirm',
      readyHint: 'Review roles, platform mapping, initial topics and rounds, then freeze the selection.',
      confirm: 'Confirm and freeze',
      confirming: 'Confirming…',
      confirmed: 'Confirmed',
      confirmedHint: 'This selection is frozen; the next stage reads only this record.',
      confirmNeeded: 'Confirm before entering the next stage.',
      confirmDirty: 'Rounds changed but not saved. Save and read them back before confirming.',
      confirmInvalid: 'The rounds configuration is invalid; fix and save it before confirming.',
      enterNextStage: 'Go to dual-platform run',
      roles: 'Roles',
      roleCount: '{count} roles',
      selectRole: 'Open role detail',
      roleDetail: 'Role detail',
      pageNumber: 'Page {page}',
      mention: 'Mentioned in material',
      background: 'Background',
      backgroundMissing: 'Not stated in material',
      expressedTopics: 'Expressed by this role',
      involvedTopics: 'Topics involving this role',
      noExpressed: 'No expressed topics in material',
      simulation: 'Simulated setting',
      simulationTopics: 'Simulated focus',
      motive: 'Motive',
      stance: 'Stance',
      activeHours: 'Active hours',
      postingFrequency: 'Posting frequency',
      responseDelay: 'Response delay (minutes)',
      influenceWeight: 'Influence weight',
      platformPreference: 'Platform preference',
      sources: 'Sources',
      openSource: 'Open source and highlight',
      openPdf: 'Open original page',
      backToMaterial: 'Back to material',
      sourceNatureUnknown: 'Source nature unknown',
      environmentConfig: 'Environment',
      platforms: 'Platform mapping',
      initialTopics: 'Initial topics',
      firstSpeakers: 'First speakers and topics',
      activationOrder: 'Activation order',
      roundsTitle: 'Rounds',
      durationHours: 'Duration (hours)',
      minutesPerRound: 'Minutes per round',
      roundsMode: 'Rounds mode',
      auto: 'Auto',
      manual: 'Manual',
      manualRounds: 'Manual rounds',
      autoRounds: 'Auto rounds',
      effectiveRounds: 'Effective rounds',
      save: 'Save rounds',
      saving: 'Saving…',
      saved: 'Rounds saved.',
      conflictReload: 'The configuration changed in another tab. It has been reloaded; review before confirming.',
      frozen: 'Frozen; rounds can no longer change.',
      frozenHint: 'The frozen selection keeps the question, snapshot, roles, rounds, platform mapping and first topics.',
      newerGraph: 'A newer graph exists',
      newerGraphHint: 'This preparation stays bound to the older snapshot. Start again on the new build to use it.',
      runPendingTitle: 'Preparation confirmed; run is not connected yet',
      runPendingBody: 'Roles and environment are confirmed from the server-side frozen selection; the dual-platform run arrives in a later change.',
      runPendingMissing: 'Roles and environment are not confirmed yet',
      goPrepare: 'Go to prepare roles and environment',
      frozenSelection: 'Frozen selection',
      frozenQuestion: 'Frozen question',
      frozenRounds: 'Frozen rounds',
      frozenRoles: 'Frozen roles',
      errorCodes: {
        graph_not_ready: 'The evidence graph is not ready.',
        graph_snapshot_missing: 'This build has no usable final snapshot.',
        no_role_candidates: 'No material subject can act as a role.',
        preparation_retry_required: 'A failed or canceled preparation needs an explicit retry.',
        preparation_not_retryable: 'This preparation does not need a retry.',
        preparation_not_ready: 'The preparation is not ready to confirm.',
        preparation_not_editable: 'A failed or canceled preparation is read-only.',
        preparation_frozen: 'The preparation is frozen; rounds cannot change.',
        settings_conflict: 'The configuration changed in another tab. Reload before confirming.',
        duration_invalid: 'Duration must be an integer between 1 and 720 hours.',
        rounds_invalid: 'Manual rounds must be an integer between 1 and 1000.',
        rounds_not_integral: 'Auto rounds are not an integer; adjust duration or minutes per round.',
        model_unavailable: 'The profiling service is unavailable. Retry later.',
        invalid_model_output: 'Model output was invalid; no roles were published.',
        role_source_missing: 'A role had no material source and was not published.',
        role_source_mismatch: 'A role source did not match the snapshot; nothing was published.',
        preparation_failed: 'Preparation failed. Retry later.',
      },
    },
    simulation: {
      title: 'Dual-platform run',
      intro: 'Start a run from the confirmed frozen selection and watch both platforms, the simulated clock and per-platform counts.',
      loading: 'Loading the run…',
      loadFailed: 'Could not load the run. Retry to continue.',
      retry: 'Retry',
      serviceUnavailable: 'The service is temporarily unavailable; your session is kept.',
      missingSelection: 'Roles and environment are not confirmed yet',
      missingSelectionBody: 'Stage three needs a confirmed frozen selection. Confirm it in stage two first.',
      goPrepare: 'Go to roles and environment',
      start: 'Start run',
      starting: 'Starting…',
      started: 'The run has started.',
      notStarted: 'No runs yet',
      startHint: 'A worker advances both local platforms and reads actions back from engine records.',
      clock: 'Simulated clock',
      engine: 'Engine',
      phase: 'Status',
      question: 'Question',
      selectionSummary: 'Frozen selection',
      roles: 'Roles',
      platformMap: 'Platforms',
      rounds: 'Rounds',
      actions: 'Actions',
      actionStream: 'Action stream',
      emptyActions: 'No actions have been read back from the engine yet.',
      logs: 'Run log',
      emptyLogs: 'No run events yet.',
      runs: 'Runs',
      currentRun: 'Current run',
      stop: 'Stop run',
      stopping: 'Stop requested',
      stopped: 'Stopped',
      originalPost: 'Original post',
      closeTarget: 'Hide original post',
      targetUnresolved: 'Target not found in this run',
      role: 'Role',
      round: 'Round',
      partialResult: 'Partial result: only one platform finished',
      failedPlatform: 'This platform failed',
      countMismatch: 'Page counts disagreed with the server; refreshed from the server.',
      noRuns: 'This frozen selection has no runs yet.',
      errorCodes: {
        selection_missing: 'The confirmed frozen selection is missing.',
        selection_invalid: 'The frozen selection is incomplete; the run cannot start.',
        engine_not_enabled: 'Local OASIS runs are not enabled.',
        engine_unavailable: 'The local OASIS engine is unavailable; no double actions were created.',
        action_unconfirmed: 'The engine wrote a side effect that cannot be confirmed; evidence kept and the platform stopped.',
        platform_a_setup: 'Info Plaza environment setup failed.',
        platform_b_setup: 'Topic Community environment setup failed.',
        platform_a_action: 'Info Plaza action failed.',
        platform_b_action: 'Topic Community action failed.',
        platform_a_record: 'Info Plaza engine record could not be verified.',
        platform_b_record: 'Topic Community engine record could not be verified.',
        simulation_timeout: 'A round exceeded its budget.',
        simulation_too_large: 'Action count exceeded the limit.',
        simulation_failed: 'The run failed.',
        canceled: 'The run was stopped.',
      },
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
    graph: {
      title: 'Evidence graph',
      intro: 'Choose ready materials and a scenario question. The worker builds the graph in the background; open a material relation to return to its source.',
      materialsTitle: 'Choose materials (up to 5)',
      questionLabel: 'Scenario question',
      questionPlaceholder: 'For example: should the campus shuttle add a run on rainy days?',
      start: 'Build graph',
      starting: 'Starting…',
      selected: '{count} / {max} selected',
      maxDocs: 'Up to {max} materials per build',
      emptyMaterials: 'No ready materials yet. Upload in the materials area and wait for parsing.',
      empty: 'No graph yet. Choose materials and a question to start.',
      loading: 'Loading graph…',
      loadFailed: 'Could not read the graph. Please retry.',
      retry: 'Retry',
      buildId: 'buildId',
      jobId: 'jobId',
      runId: 'runId',
      phaseLabel: {
        preparing: 'Preparing',
        waiting_index: 'Waiting for index',
        drawing: 'Drawing incrementally',
        ready: 'Ready',
        failed: 'Failed',
        canceled: 'Canceled',
      },
      waitingIndex: 'Accepted {accepted} / confirmed {confirmed} / planned {planned}',
      accepted: 'Accepted',
      confirmed: 'Confirmed',
      drawing: 'Reading nodes and edges…',
      ready: 'Graph is ready. You can continue to the next stage.',
      failed: 'Graph build failed',
      canceled: 'Graph build was canceled. Published partial snapshots remain readable.',
      revision: 'Snapshot revision',
      nodes: 'Nodes',
      edges: 'Relations',
      legend: 'Legend',
      material: 'Material statement',
      hypothesis: 'User hypothesis',
      zoomHint: 'Scroll to zoom, drag to pan; nodes can be dragged.',
      fit: 'Fit canvas',
      refresh: 'Refresh',
      maximize: 'Maximize',
      restore: 'Restore',
      edgeLabels: 'Relation labels',
      details: 'Details',
      node: 'Node',
      edge: 'Relation',
      origin: 'Origin',
      claimNatureLabel: 'Material nature',
      claimNature: {
        media_report: 'Media report',
        party_claim: 'Party claim',
        author_recommendation: 'Report recommendation',
        official_decision: 'Official decision',
        other_material: 'Other material statement',
      },
      claimNatureUnknown: 'Not marked',
      nodeRelations: 'Relations for this subject',
      noNodeRelations: 'No visible material relation for this node yet.',
      summary: 'Summary',
      sources: 'Original sources',
      noSources: 'This hypothesis has no original source.',
      openSource: 'Open source',
      sourceTitle: 'Original text (uncleaned)',
      sourceClose: 'Close source',
      noSelection: 'Select a node or relation to see all sources.',
      next: 'Continue to roles and environment',
      nextBlocked: 'The graph is incomplete or sources are missing; the next stage is disabled.',
      nextHint: 'The next stage configures roles and environment.',
      questionRequired: 'Enter a question of 1–500 characters.',
      tooMany: 'Up to 5 materials per build.',
      stale: 'A late response from the previous build was ignored.',
      serviceUnavailable: 'Service temporarily unavailable; the current view is kept.',
      sourceFailed: 'Could not read the source text. Please retry.',
      failureCodes: {
        missing_config: 'Model or graph configuration is incomplete; no external call was made.',
        too_many_documents: 'Too many materials.',
        document_not_ready: 'A selected material is not ready.',
        invalid_graph_scope: 'The selected materials or graph scope are invalid.',
        invalid_graph_response: 'The graph service returned an unusable result.',
        invalid_model_output: 'Model output failed structural validation.',
        source_mismatch: 'A quote does not match the original text.',
        ambiguous_source: 'A quote appears multiple times and cannot be located uniquely.',
        source_unavailable: 'The original file is unreadable, so sources cannot be located.',
        model_unavailable: 'The model is temporarily unavailable.',
        graph_unavailable: 'The graph service is temporarily unavailable.',
        write_unconfirmed: 'The episode write could not be confirmed.',
        stale_or_foreign_hit: 'Search matched only stale or foreign records.',
        index_timeout: 'Timed out waiting for the index.',
        processing_timeout: 'Timed out waiting for processing or readback.',
        graph_incomplete: 'Readback is insufficient for a complete graph.',
        graph_too_large: 'Nodes or relations exceed the limit; no truncated graph was published.',
        too_many_sources: 'A single item has too many sources.',
        insufficient_graph: 'There are not enough material nodes or relations.',
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
