import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createRouter,
  flush,
  installDom,
  installEventSource,
  installFetch,
  jsonResponse,
  loadBuiltComponents,
  mountComponent,
} from './page-helpers.mjs'

installDom()
const components = await loadBuiltComponents()
const eventSources = installEventSource()

const PROJECT = 'project-1'
const BUILD = 'build-1'
const SNAPSHOT = 'snapshot-1'
const PREP = 'prep-old'
const NEW_PREP = 'prep-new'

function settings(revision = 1, rounds = 72, mode = 'auto', manual = null) {
  return {
    durationHours: 72,
    minutesPerRound: 60,
    roundsMode: mode,
    manualRounds: manual,
    rounds,
    autoRounds: 72,
    settingsRevision: revision,
  }
}

function source(kind, extra = {}) {
  return {
    documentId: `doc-${kind}`,
    documentName: `原创材料.${kind}`,
    kind,
    chunkIndex: 1,
    pageNo: kind === 'pdf' ? 3 : null,
    paragraphStart: 1,
    paragraphEnd: 1,
    sourceStart: 2,
    sourceEnd: 8,
    quote: '原创引文',
    locator:
      kind === 'pdf'
        ? {
            kind: 'pdf',
            pageNo: 3,
            url: '/api/projects/project-1/documents/doc-pdf/file#page=3',
          }
        : {
            kind: 'text',
            url: '/api/projects/project-1/documents/doc-text/source',
            sourceStart: 2,
            sourceEnd: 8,
          },
    ...extra,
  }
}

function role(id, overrides = {}) {
  return {
    roleId: id,
    nodeKey: `node-${id}`,
    displayName: `角色 ${id}`,
    roleType: '个人',
    batchIndex: 0,
    previewReady: true,
    published: false,
    mention: { text: '材料提及', sources: [source('text')] },
    background: { text: '', stated: false, sources: [] },
    expressedTopics: [],
    involvedTopics: [],
    simulation: {
      origin: 'simulation',
      motive: '参与讨论',
      stance: '审慎',
      activeHours: [9, 10],
      postingFrequency: 1,
      responseDelayMinutes: 10,
      influenceWeight: 1,
      platformKeys: ['platform_a'],
      topics: [{ text: '模拟关注点 A' }],
    },
    ...overrides,
  }
}

function preparation(overrides = {}) {
  const settingsValue = overrides.settings ?? settings()
  const roles = overrides.roles ?? []
  return {
    preparationId: PREP,
    projectId: PROJECT,
    buildId: BUILD,
    snapshotId: SNAPSHOT,
    runId: 'run-old',
    jobId: 'job-old',
    jobStatus: 'failed',
    question: '原创问题：是否调整安排？',
    phase: 'failed',
    settings: settingsValue,
    failure: null,
    counts: {
      previewRoleCount: 0,
      profileBatchCount: 0,
      providerCalls: { real: 0, double: 0 },
    },
    progress: {
      totalRoles: roles.length,
      profiledRoles: roles.length,
      publishedRoles: roles.length,
      previewRoleCount: 0,
      batchCount: 1,
      previewBatchCount: 1,
    },
    previewOnly: true,
    previewRoles: [],
    roles,
    platforms: [],
    initialTopics: [],
    activation: null,
    frozenSelection: null,
    canContinue: false,
    requiredAction: null,
    ...overrides,
    settings: settingsValue,
  }
}

function readyDetail(options = {}) {
  const settingsValue = options.settings ?? settings(2, 40, 'manual', 40)
  const roles = options.roles ?? [
    role('a', {
      published: true,
      previewReady: true,
      background: {
        text: '背景原句',
        stated: true,
        sources: [source('pdf')],
      },
      expressedTopics: [
        {
          text: '表达话题',
          claimNature: 'party_claim',
          claimNatureKnown: true,
          sources: [source('text', { sourceStart: 10, sourceEnd: 16 })],
        },
      ],
      involvedTopics: [
        {
          text: '关联话题',
          claimNature: null,
          claimNatureKnown: false,
          sources: [source('pdf')],
        },
      ],
    }),
  ]
  return preparation({
    phase: 'ready',
    jobStatus: 'succeeded',
    settings: settingsValue,
    roles,
    previewRoles: [],
    previewOnly: false,
    platforms: [
      { platformKey: 'platform_a', nativePlatform: 'twitter', enabled: true },
      { platformKey: 'platform_b', nativePlatform: 'reddit', enabled: true },
    ],
    initialTopics: [
      {
        topicId: 't1',
        text: '原创问题：是否调整安排？',
        origin: 'question',
        claimNature: null,
        claimNatureKnown: false,
        sources: [],
      },
    ],
    activation: {
      firstSpeakerRoleIds: [roles[0].roleId],
      firstSpeakerTopics: [{ roleId: roles[0].roleId, topicId: 't1' }],
      order: [roles[0].roleId],
    },
    counts: {
      previewRoleCount: roles.length,
      profileBatchCount: 1,
      providerCalls: { real: 0, double: 1 },
    },
    progress: {
      totalRoles: roles.length,
      profiledRoles: roles.length,
      publishedRoles: roles.length,
      previewRoleCount: roles.length,
      batchCount: 1,
      previewBatchCount: 1,
    },
    canContinue: false,
    requiredAction: 'confirm',
    ...options,
  })
}

function context(prep) {
  return {
    build: {
      buildId: BUILD,
      projectId: PROJECT,
      runId: 'run-build',
      jobId: 'job-build',
      question: '原创问题：是否调整安排？',
      phase: 'ready',
      canContinue: true,
      failure: null,
    },
    snapshot: {
      snapshotId: SNAPSHOT,
      revision: 1,
      complete: true,
      published: true,
      nodeCount: 3,
      edgeCount: 2,
    },
    canPrepare: true,
    reason: null,
    preparation: prep
      ? {
          preparationId: prep.preparationId,
          jobId: prep.jobId,
          runId: prep.runId,
          phase: prep.phase,
          settings: prep.settings,
          failure: prep.failure,
          frozen: Boolean(prep.frozenSelection),
          previewRoleCount: prep.counts.previewRoleCount,
          profileBatchCount: prep.counts.profileBatchCount,
        }
      : null,
    newerBuildId: null,
  }
}

function routePath(stage = 'roles-environment') {
  return `/projects/${PROJECT}/workbench/${stage}?layout=split&lang=zh&build=${BUILD}`
}

async function mountPanel(handler) {
  const calls = installFetch(handler)
  const router = await createRouter(routePath())
  const wrapper = await mountComponent(components.RolesEnvironmentPanel, {
    props: { projectId: PROJECT, lang: 'zh' },
    global: { plugins: [router] },
  })
  await flush(8)
  return { wrapper, calls, router }
}

test('显式重试成功后切换到新准备并订阅新 job，旧事件被丢弃', async () => {
  const old = preparation({
    previewRoles: [role('old-preview')],
    failure: { code: 'model_unavailable', message: '画像服务不可用' },
    counts: {
      previewRoleCount: 1,
      profileBatchCount: 1,
      providerCalls: { real: 0, double: 1 },
    },
  })
  const next = preparation({
    preparationId: NEW_PREP,
    jobId: 'job-new',
    runId: 'run-new',
    jobStatus: 'queued',
    phase: 'preparing',
    previewRoles: [],
    failure: null,
  })
  const { wrapper } = await mountPanel((url, call) => {
    if (url.endsWith(`/environment?build=${BUILD}`)) {
      return jsonResponse(context(old))
    }
    if (url.endsWith(`/preparations/${PREP}`)) {
      return jsonResponse({ preparation: old })
    }
    if (url.endsWith(`/preparations/${PREP}/retry`) && call.method === 'POST') {
      return jsonResponse({ preparation: next }, 202)
    }
    if (url.endsWith(`/preparations/${NEW_PREP}`)) {
      return jsonResponse({ preparation: next })
    }
    throw new Error(`unexpected fetch ${call.method} ${url}`)
  })

  assert.match(wrapper.text(), /准备失败/)
  const retry = wrapper
    .findAll('button')
    .find((button) => button.text().includes('重试准备'))
  assert.ok(retry)
  await retry.trigger('click')
  await flush(10)

  assert.match(wrapper.text(), /准备中/)
  assert.doesNotMatch(wrapper.text(), /角色 old-preview/)
  const current = eventSources.at(-1)
  assert.ok(current.url.includes('/jobs/job-new/events'))
  current.emit('run-event', {
    runId: 'run-new',
    seq: 8,
    type: 'failed',
    stage: 'roles-environment',
    simulatedAt: '2026-11-24T00:00:00Z',
    payload: {
      preparationId: PREP,
      buildId: BUILD,
      phase: 'failed',
      reason: 'stale',
    },
  })
  await flush(2)
  assert.match(wrapper.text(), /准备中/)
  assert.doesNotMatch(wrapper.text(), /准备失败/)
  wrapper.unmount()
})

test('未保存/非法轮数禁用确认，保存读回后才确认冻结 3 轮', async () => {
  let detail = readyDetail()
  let patched = false
  const { wrapper, calls } = await mountPanel((url, call) => {
    if (url.endsWith(`/environment?build=${BUILD}`)) {
      return jsonResponse(context(detail))
    }
    if (url.endsWith(`/preparations/${PREP}`) && call.method === 'GET') {
      return jsonResponse({ preparation: detail })
    }
    if (url.endsWith(`/preparations/${PREP}/configuration`)) {
      patched = true
      detail = readyDetail({
        settings: settings(3, 3, 'manual', 3),
      })
      return jsonResponse({ preparation: detail })
    }
    if (url.endsWith(`/preparations/${PREP}/confirm`)) {
      return jsonResponse({
        preparation: {
          ...detail,
          frozenSelection: { selectionId: 'selection-1', settings: detail.settings },
          canContinue: true,
        },
        frozenSelection: { selectionId: 'selection-1', settings: detail.settings },
      })
    }
    throw new Error(`unexpected fetch ${call.method} ${url}`)
  })

  const confirm = () =>
    wrapper
      .findAll('button')
      .find((button) => button.text().includes('确认并冻结'))
  assert.equal(await confirm().attributes('disabled'), undefined)

  const manual = wrapper
    .findAll('label')
    .find((label) => label.text().includes('手动轮数'))
    .find('input')
  await manual.setValue('3')
  await flush(2)
  assert.notEqual(await confirm().attributes('disabled'), undefined)
  assert.match(wrapper.text(), /未保存的轮数修改/)
  const beforeConfirmCalls = calls.filter(
    (call) => call.method === 'POST' && call.url.endsWith('/confirm'),
  ).length
  await confirm().trigger('click')
  await flush(2)
  assert.equal(
    calls.filter(
      (call) => call.method === 'POST' && call.url.endsWith('/confirm'),
    ).length,
    beforeConfirmCalls,
  )

  await manual.setValue('0')
  await flush(2)
  assert.notEqual(await confirm().attributes('disabled'), undefined)
  assert.match(wrapper.text(), /轮数配置不合法/)

  await manual.setValue('3')
  const save = wrapper
    .findAll('button')
    .find((button) => button.text().includes('保存轮数'))
  await save.trigger('click')
  await flush(12)
  assert.ok(patched)
  assert.match(wrapper.text(), /轮数已保存/)
  assert.equal(await confirm().attributes('disabled'), undefined)

  await confirm().trigger('click')
  await flush(8)
  const confirmCall = calls.find(
    (call) => call.method === 'POST' && call.url.endsWith('/confirm'),
  )
  assert.deepEqual(confirmCall.body, { settingsRevision: 3 })
  assert.match(wrapper.text(), /已确认/)
  const next = wrapper.find('a[href*="dual-platform-run"]')
  assert.ok(next)
  assert.match(next.attributes('href'), /preparation=prep-old/)
  wrapper.unmount()
})

test('保存遇到 settings_conflict 时重新读取最新 revision 再确认', async () => {
  let detail = readyDetail({
    settings: settings(1, 72, 'manual', 72),
  })
  let current = detail
  const { wrapper, calls } = await mountPanel((url, call) => {
    if (url.endsWith(`/environment?build=${BUILD}`)) {
      return jsonResponse(context(detail))
    }
    if (url.endsWith(`/preparations/${PREP}`) && call.method === 'GET') {
      return jsonResponse({ preparation: current })
    }
    if (url.endsWith(`/preparations/${PREP}/configuration`)) {
      current = readyDetail({
        settings: settings(3, 3, 'manual', 3),
      })
      return jsonResponse({ detail: '配置已被其他页面更新', code: 'settings_conflict' }, 409)
    }
    if (url.endsWith(`/preparations/${PREP}/confirm`)) {
      return jsonResponse({
        preparation: {
          ...current,
          frozenSelection: { selectionId: 'selection-2', settings: current.settings },
          canContinue: true,
        },
        frozenSelection: { selectionId: 'selection-2', settings: current.settings },
      })
    }
    throw new Error(`unexpected fetch ${call.method} ${url}`)
  })

  const manual = wrapper
    .findAll('label')
    .find((label) => label.text().includes('手动轮数'))
    .find('input')
  await manual.setValue('3')
  const save = wrapper
    .findAll('button')
    .find((button) => button.text().includes('保存轮数'))
  await save.trigger('click')
  await flush(12)
  assert.match(wrapper.text(), /配置已被其他页面更新/)
  assert.equal(await manual.element.value, '3')
  const confirm = wrapper
    .findAll('button')
    .find((button) => button.text().includes('确认并冻结'))
  assert.equal(await confirm.attributes('disabled'), undefined)
  await confirm.trigger('click')
  await flush(6)
  const confirmCall = calls.find(
    (call) => call.method === 'POST' && call.url.endsWith('/confirm'),
  )
  assert.deepEqual(confirmCall.body, { settingsRevision: 3 })
  wrapper.unmount()
})

test('角色详情逐个渲染 PDF 物理页与文本 UTF-16 来源，并显示模拟关注点', async () => {
  const detail = readyDetail()
  const { wrapper } = await mountPanel((url, call) => {
    if (url.endsWith(`/environment?build=${BUILD}`)) {
      return jsonResponse(context(detail))
    }
    if (url.endsWith(`/preparations/${PREP}`)) {
      return jsonResponse({ preparation: detail })
    }
    throw new Error(`unexpected fetch ${call.method} ${url}`)
  })
  await wrapper
    .findAll('button.role-chip')[0]
    .trigger('click')
  await flush(2)
  const directPdf = wrapper.find('a[href*="#page=3"]')
  assert.ok(directPdf, 'PDF 来源缺少 #page=3')
  const textLinks = wrapper.findAll('a[href*="source=1"]')
  assert.ok(textLinks.length >= 2, '文本提及/表达来源缺少原文深链接')
  assert.match(textLinks[0].attributes('href'), /sourceStart=\d+&sourceEnd=\d+/)
  assert.match(wrapper.text(), /模拟关注点：模拟关注点 A/)
  wrapper.unmount()
})

test('冻结后第三阶段显示准备已确认，刷新同一地址保持一致', async () => {
  const frozen = {
    selectionId: 'selection-1',
    preparationId: PREP,
    projectId: PROJECT,
    buildId: BUILD,
    snapshotId: SNAPSHOT,
    question: '原创问题：是否调整安排？',
    settingsRevision: 3,
    settings: settings(3, 3, 'manual', 3),
    roles: [role('a')],
    platforms: [
      { platformKey: 'platform_a', nativePlatform: 'twitter' },
      { platformKey: 'platform_b', nativePlatform: 'reddit' },
    ],
    initialTopics: [],
    activation: {
      firstSpeakerRoleIds: ['a'],
      firstSpeakerTopics: [{ roleId: 'a', topicId: 't1' }],
      order: ['a'],
    },
  }
  const detail = readyDetail({
    frozenSelection: frozen,
    canContinue: true,
  })
  const handler = (url) => {
    if (url.endsWith(`/preparations/${PREP}`)) {
      return jsonResponse({ preparation: detail })
    }
    throw new Error(`unexpected fetch ${url}`)
  }
  for (const attempt of [1, 2]) {
    installFetch(handler)
    const router = await createRouter(
      `/projects/${PROJECT}/workbench/dual-platform-run?layout=split&lang=zh&build=${BUILD}&preparation=${PREP}`,
    )
    const wrapper = await mountComponent(components.DualPlatformRunPanel, {
      props: { projectId: PROJECT, lang: 'zh' },
      global: { plugins: [router] },
    })
    await flush(6)
    assert.match(wrapper.text(), /准备已确认，运行功能待接入/)
    assert.match(wrapper.text(), /原创问题：是否调整安排？/)
    assert.match(wrapper.text(), /3/)
    wrapper.unmount()
  }
})
