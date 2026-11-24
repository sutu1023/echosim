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
installEventSource()

const PROJECT = 'project-1'
const BUILD = 'build-1'
const SNAPSHOT = 'snapshot-1'
const PREP = 'prep-1'
const SELECTION = 'selection-1'
const RUN = 'run-1'
const OLD_RUN = 'run-old'

function frozenSelection() {
  return {
    selectionId: SELECTION,
    preparationId: PREP,
    projectId: PROJECT,
    buildId: BUILD,
    snapshotId: SNAPSHOT,
    question: '接驳班次是否应调整？',
    settingsRevision: 2,
    settings: {
      durationHours: 3,
      minutesPerRound: 60,
      roundsMode: 'manual',
      manualRounds: 3,
      rounds: 3,
      autoRounds: 72,
      settingsRevision: 2,
    },
    roles: [
      {
        roleId: 'role-1',
        displayName: '张同学',
        roleType: '个人',
        simulation: { activeHours: [13], platformKeys: ['platform_a', 'platform_b'] },
      },
      {
        roleId: 'role-2',
        displayName: '校友李某',
        roleType: '个人',
        simulation: { activeHours: [13], platformKeys: ['platform_a', 'platform_b'] },
      },
    ],
    platforms: [
      { platformKey: 'platform_a', nativePlatform: 'twitter' },
      { platformKey: 'platform_b', nativePlatform: 'reddit' },
    ],
    initialTopics: [{ topicId: 'q', text: '接驳班次是否应调整？', origin: 'question' }],
    activation: {
      firstSpeakerRoleIds: ['role-1'],
      firstSpeakerTopics: [{ roleId: 'role-1', topicId: 'q' }],
      order: ['role-1', 'role-2'],
    },
  }
}

const ACTIONS = [
  {
    actionId: 'action-1',
    platformKey: 'platform_a',
    nativePlatform: 'twitter',
    nativeAction: 'create_post',
    roundIndex: 1,
    actionSeq: 1,
    actorRoleId: 'role-1',
    actorRoleName: '张同学',
    content: '接驳班次是否应调整？｜张同学表达关注。',
    engineRecordId: 'post:1',
    targetEngineRecordId: null,
    targetActionId: null,
    targetNativeAction: null,
    targetResolved: false,
    target: null,
    simulatedAt: '2026-11-24T13:00:00Z',
  },
  {
    actionId: 'action-2',
    platformKey: 'platform_b',
    nativePlatform: 'reddit',
    nativeAction: 'create_comment',
    roundIndex: 1,
    actionSeq: 2,
    actorRoleId: 'role-2',
    actorRoleName: '校友李某',
    content: '回应主题：建议增加备用车。',
    engineRecordId: 'comment:1',
    targetEngineRecordId: 'post:1',
    targetActionId: 'action-1',
    targetNativeAction: 'create_post',
    targetResolved: true,
    target: {
      actionId: 'action-1',
      content: '接驳班次是否应调整？｜张同学表达关注。',
      actorRoleName: '张同学',
      roundIndex: 1,
      simulatedAt: '2026-11-24T13:00:00Z',
    },
    simulatedAt: '2026-11-24T13:00:00Z',
  },
]

function simulation(overrides = {}) {
  return {
    runId: RUN,
    jobId: 'job-1',
    selectionId: SELECTION,
    preparationId: PREP,
    buildId: BUILD,
    snapshotId: SNAPSHOT,
    question: '接驳班次是否应调整？',
    engine: 'double',
    phase: 'running',
    terminal: false,
    partial: false,
    failedPlatforms: [],
    failure: null,
    rounds: 3,
    minutesPerRound: 60,
    durationHours: 3,
    simulatedStart: '2026-11-24T13:00:00Z',
    simulatedAt: '2026-11-24T15:00:00Z',
    platforms: [
      {
        platformKey: 'platform_a',
        nativePlatform: 'twitter',
        status: 'running',
        roundsCompleted: 2,
        roundsTotal: 3,
        actionCount: 3,
        byAction: { create_post: 2, repost: 1 },
        failure: null,
        simulatedAt: '2026-11-24T15:00:00Z',
      },
      {
        platformKey: 'platform_b',
        nativePlatform: 'reddit',
        status: 'running',
        roundsCompleted: 1,
        roundsTotal: 3,
        actionCount: 1,
        byAction: { create_comment: 1 },
        failure: null,
        simulatedAt: '2026-11-24T14:00:00Z',
      },
    ],
    counts: { total: 4, byAction: { create_post: 2, repost: 1, create_comment: 1 } },
    actions: { items: ACTIONS, nextAfter: 2 },
    canStop: true,
    createdAt: '2026-11-24T13:00:00Z',
    updatedAt: '2026-11-24T15:00:00Z',
    finishedAt: null,
    ...overrides,
  }
}

function preparation() {
  return {
    preparationId: PREP,
    phase: 'ready',
    frozenSelection: frozenSelection(),
  }
}

async function mount(handler, path = `/projects/${PROJECT}/workbench/dual-platform-run?layout=split&lang=zh&build=${BUILD}&preparation=${PREP}&selection=${SELECTION}`) {
  const calls = installFetch(handler)
  const router = await createRouter(path)
  const wrapper = await mountComponent(components.DualPlatformRunPanel, {
    props: { projectId: PROJECT, lang: 'zh' },
    global: { plugins: [router] },
  })
  await flush(10)
  return { wrapper, calls, router }
}

function defaultHandler(overrides = {}) {
  return (url, call) => {
    if (url.endsWith(`/preparations/${PREP}`)) {
      return jsonResponse({ preparation: preparation() })
    }
    if (url.includes('/actions')) {
      const runId = url.split('/simulations/')[1].split('/')[0]
      return jsonResponse({
        runId,
        items: runId === OLD_RUN ? [] : ACTIONS,
        nextAfter: 2,
        ...(overrides.actions || {}),
      })
    }
    if (url.endsWith(`/simulations/${RUN}/stop`)) {
      return jsonResponse({
        cancelApplied: true,
        simulation: simulation({ phase: 'canceled', terminal: true, canStop: false }),
      })
    }
    if (url.match(/\/simulations\/[^/?]+$/)) {
      const runId = url.split('/').pop()
      return jsonResponse({
        simulation: runId === OLD_RUN
          ? simulation({ runId: OLD_RUN, phase: 'canceled', terminal: true, canStop: false })
          : simulation(),
      })
    }
    if (url.includes('/simulations?')) {
      return jsonResponse({
        simulations: [
          { runId: RUN, selectionId: SELECTION, engine: 'double', phase: 'running', rounds: 3, terminal: false, failedPlatforms: [], createdAt: '', finishedAt: null },
          { runId: OLD_RUN, selectionId: SELECTION, engine: 'double', phase: 'canceled', rounds: 3, terminal: true, failedPlatforms: [], createdAt: '', finishedAt: null },
        ],
        simulation: simulation(),
      })
    }
    if (call.method === 'POST' && url.endsWith('/simulations')) {
      return jsonResponse({ simulation: simulation() }, 202)
    }
    throw new Error(`unexpected fetch ${call.method} ${url}`)
  }
}

test('第三阶段显示模拟时钟、两平台行动流与统计', async () => {
  const { wrapper } = await mount(defaultHandler())
  const text = wrapper.text()
  assert.match(text, /模拟时钟/)
  assert.match(text, /第 1 天 15:00/)
  assert.match(text, /离线替身运行/)
  assert.match(text, /信息广场/)
  assert.match(text, /话题社区/)
  assert.match(text, /2\/3/)
  assert.match(text, /3 动作/)
  assert.match(text, /发帖 2/)
  assert.match(text, /评论 1/)
  assert.match(text, /评论 1/)
  assert.match(text, /停止运行/)
  wrapper.unmount()
})

test('从评论跳回同一运行的原帖', async () => {
  const { wrapper } = await mount(defaultHandler())
  const button = wrapper
    .findAll('button')
    .find((item) => item.text() === '原帖')
  assert.ok(button, '应存在原帖入口')
  await button.trigger('click')
  await flush(2)
  const target = wrapper.find('[data-field="target"]')
  assert.ok(target.exists())
  assert.match(target.text(), /张同学表达关注/)
  assert.match(target.text(), /张同学/)
  wrapper.unmount()
})

test('停止运行请求后按钮显示已请求并冻结', async () => {
  const { wrapper, calls } = await mount(defaultHandler())
  const stop = wrapper.find('[data-action="stop"]')
  await stop.trigger('click')
  await flush(6)
  assert.ok(
    calls.some(
      (call) => call.method === 'POST' && call.url.endsWith(`/simulations/${RUN}/stop`),
    ),
  )
  assert.match(wrapper.text(), /已停止/)
  wrapper.unmount()
})

test('切换到旧运行时以 runId 守卫丢弃迟到响应', async () => {
  const { wrapper, router, calls } = await mount(defaultHandler())
  const chips = wrapper.findAll('[data-action="select-run"]')
  assert.ok(chips.length >= 2)
  await chips[1].trigger('click')
  await flush(10)
  assert.match(router.currentRoute.value.query.run, new RegExp(OLD_RUN))
  assert.ok(
    calls.some((call) => call.url.endsWith(`/simulations/${OLD_RUN}`)),
  )
  assert.match(wrapper.text(), /已停止/)
  wrapper.unmount()
})

test('没有冻结选择时提示前往准备并禁止开始', async () => {
  const handler = (url) => {
    if (url.endsWith(`/preparations/${PREP}`)) {
      return jsonResponse({ preparation: { preparationId: PREP, phase: 'preparing', frozenSelection: null } })
    }
    throw new Error(`unexpected fetch ${url}`)
  }
  const { wrapper } = await mount(
    handler,
    `/projects/${PROJECT}/workbench/dual-platform-run?layout=split&lang=zh&build=${BUILD}&preparation=${PREP}`,
  )
  assert.match(wrapper.text(), /尚未确认角色与环境/)
  assert.match(wrapper.text(), /前往准备角色与环境/)
  wrapper.unmount()
})

test('服务不可用时显示 503 文案而不是空态', async () => {
  const handler = (url) => {
    if (url.endsWith(`/preparations/${PREP}`)) {
      return jsonResponse({ detail: '服务暂时不可用' }, 503)
    }
    throw new Error(`unexpected fetch ${url}`)
  }
  const { wrapper } = await mount(
    handler,
    `/projects/${PROJECT}/workbench/dual-platform-run?layout=split&lang=zh&build=${BUILD}&preparation=${PREP}`,
  )
  assert.match(wrapper.text(), /服务暂时不可用/)
  assert.doesNotMatch(wrapper.text(), /暂无运行记录/)
  wrapper.unmount()
})


test('开始 B、停止 B 后点回 A，无需刷新即恢复 A 的计数与行动流', async () => {
  const actionA = {
    ...ACTIONS[0],
    actionId: 'action-a',
    actionSeq: 1,
    content: 'A 运行的帖子',
  }
  const actionB = {
    ...ACTIONS[0],
    actionId: 'action-b',
    actionSeq: 1,
    content: 'B 运行的帖子',
  }
  const detailA = simulation({
    runId: 'run-a',
    phase: 'succeeded',
    terminal: true,
    canStop: false,
    counts: { total: 4, byAction: { create_post: 3, repost: 1 } },
    platforms: [
      {
        platformKey: 'platform_a',
        nativePlatform: 'twitter',
        status: 'succeeded',
        roundsCompleted: 3,
        roundsTotal: 3,
        actionCount: 3,
        byAction: { create_post: 2, repost: 1 },
        failure: null,
        simulatedAt: '2026-11-24T16:00:00Z',
      },
      {
        platformKey: 'platform_b',
        nativePlatform: 'reddit',
        status: 'succeeded',
        roundsCompleted: 3,
        roundsTotal: 3,
        actionCount: 1,
        byAction: { create_comment: 1 },
        failure: null,
        simulatedAt: '2026-11-24T16:00:00Z',
      },
    ],
  })
  const detailB = simulation({ runId: 'run-b', phase: 'running', terminal: false })
  let runs = ['run-a']
  let bStopped = false
  const handler = (url, call) => {
    if (url.endsWith(`/preparations/${PREP}`)) {
      return jsonResponse({ preparation: preparation() })
    }
    if (url.includes('/actions')) {
      const runId = url.split('/simulations/')[1].split('/')[0]
      return jsonResponse({
        runId,
        items: runId === 'run-a' ? [actionA] : [actionB],
        nextAfter: 1,
      })
    }
    if (call.method === 'POST' && url.endsWith('/simulations')) {
      runs = ['run-b', 'run-a']
      return jsonResponse({ simulation: detailB }, 202)
    }
    if (url.endsWith('/simulations/run-b/stop')) {
      bStopped = true
      return jsonResponse({
        cancelApplied: true,
        simulation: {
          ...detailB,
          phase: 'canceled',
          terminal: true,
          canStop: false,
          stopRequested: true,
        },
      })
    }
    if (url.match(/\/simulations\/[^/?]+$/)) {
      const runId = url.split('/').pop()
      if (runId === 'run-a') {
        return jsonResponse({ simulation: detailA })
      }
      return jsonResponse({
        simulation: bStopped
          ? { ...detailB, phase: 'canceled', terminal: true, canStop: false }
          : detailB,
      })
    }
    if (url.includes('/simulations?')) {
      return jsonResponse({
        simulations: runs.map((runId) => ({
          runId,
          selectionId: SELECTION,
          engine: 'double',
          phase: runId === 'run-a' ? 'succeeded' : bStopped ? 'canceled' : 'running',
          rounds: 3,
          terminal: runId === 'run-a' || bStopped,
          failedPlatforms: [],
          createdAt: '',
          finishedAt: null,
        })),
        simulation: runs[0] === 'run-b' ? detailB : detailA,
      })
    }
    throw new Error(`unexpected fetch ${call.method} ${url}`)
  }

  const { wrapper, router, calls } = await mount(
    handler,
    `/projects/${PROJECT}/workbench/dual-platform-run?layout=split&lang=zh&build=${BUILD}&preparation=${PREP}&selection=${SELECTION}`,
  )
  assert.match(wrapper.text(), /A 运行的帖子/)
  assert.match(wrapper.text(), /3 动作/)

  await wrapper.find('[data-action="start"]').trigger('click')
  await flush(12)
  assert.equal(router.currentRoute.value.query.run, 'run-b')
  assert.match(wrapper.text(), /B 运行的帖子/)

  const stop = wrapper.find('[data-action="stop"]')
  assert.ok(stop.exists())
  await stop.trigger('click')
  await flush(12)
  assert.ok(
    calls.some(
      (call) =>
        call.method === 'POST' &&
        call.url.endsWith('/simulations/run-b/stop'),
    ),
    '停止请求必须发给 B',
  )
  assert.ok(!calls.some((call) => call.url.endsWith('/simulations/run-a/stop')))

  const chip = wrapper
    .findAll('[data-action="select-run"]')
    .find((item) => item.text().startsWith('run-a'))
  assert.ok(chip, '运行记录里应有 A')
  await chip.trigger('click')
  await flush(14)
  assert.equal(router.currentRoute.value.query.run, 'run-a')
  assert.match(wrapper.text(), /A 运行的帖子/)
  assert.match(wrapper.text(), /3 动作/)
  assert.doesNotMatch(wrapper.text(), /B 运行的帖子/)
  wrapper.unmount()
})
