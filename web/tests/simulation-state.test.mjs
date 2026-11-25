import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = fs.readFileSync(new URL('../src/simulation/state.ts', import.meta.url), 'utf8')
const output = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  fileName: 'state.ts',
}).outputText
const state = await import(
  `data:text/javascript;base64,${Buffer.from(output, 'utf8').toString('base64')}`
)

function action(id, seq, extra = {}) {
  return {
    actionId: id,
    actionSeq: seq,
    platformKey: 'platform_a',
    nativeAction: 'create_post',
    content: id,
    roundIndex: 1,
    targetResolved: false,
    target: null,
    ...extra,
  }
}

function detail(overrides = {}) {
  return {
    runId: 'run-1',
    selectionId: 'sel-1',
    question: '问题',
    engine: 'double',
    phase: 'running',
    terminal: false,
    partial: false,
    failedPlatforms: [],
    failure: null,
    rounds: 3,
    minutesPerRound: 60,
    simulatedStart: '2026-11-24T13:00:00Z',
    simulatedAt: '2026-11-24T15:00:00Z',
    platforms: [
      { platformKey: 'platform_a', nativePlatform: 'twitter', status: 'running', roundsCompleted: 2, roundsTotal: 3, actionCount: 2, byAction: { create_post: 2 }, failure: null, simulatedAt: null },
      { platformKey: 'platform_b', nativePlatform: 'reddit', status: 'running', roundsCompleted: 1, roundsTotal: 3, actionCount: 1, byAction: { create_comment: 1 }, failure: null, simulatedAt: null },
    ],
    counts: { total: 3, byAction: { create_post: 2, create_comment: 1 } },
    canStop: true,
    stopRequested: false,
    cleanup: null,
    ...overrides,
  }
}

test('详情按 runId 与 selectionId 守卫', () => {
  let current = state.createSimulationState('sel-1')
  assert.equal(state.detailMatches(current, { runId: 'run-x', selectionId: 'sel-1' }), true)
  current = state.applyDetail(current, detail(), [], 0)
  assert.equal(current.runId, 'run-1')
  assert.equal(state.detailMatches(current, detail({ runId: 'run-2' })), false)
  assert.equal(state.detailMatches(current, detail({ selectionId: 'sel-2' })), false)
})

test('动作按 actionId 去重并按 actionSeq 排序', () => {
  const merged = state.mergeActions(
    [action('a2', 2), action('a1', 1)],
    [action('a2', 2, { content: 'updated' }), action('a3', 3)],
  )
  assert.deepEqual(merged.map((item) => item.actionId), ['a1', 'a2', 'a3'])
  assert.equal(merged[1].content, 'updated')
})

test('事件只唤醒、推进日志并终结流', () => {
  let current = state.applyDetail(
    state.createSimulationState('sel-1'),
    detail(),
    [action('a1', 1)],
    1,
  )
  const progress = {
    runId: 'run-1',
    seq: 3,
    type: 'progress',
    simulatedAt: '2026-11-24T14:00:00Z',
    payload: { simulationId: 'sim-1', selectionId: 'sel-1', roundIndex: 2, platformKey: 'platform_a', actionCount: 2 },
  }
  const applied = state.applyEvent(current, progress, 'zh')
  current = applied.state
  assert.equal(applied.needsRefresh, true)
  assert.equal(current.lastSeq, 3)
  assert.equal(current.logs.length, 1)
  assert.match(current.logs[0].text, /第 2 轮/)
  assert.equal(state.shouldStopStream(current), false)

  const terminal = state.applyEvent(
    current,
    { ...progress, seq: 4, type: 'completed', payload: { selectionId: 'sel-1', reason: null } },
    'zh',
  )
  assert.equal(terminal.state.streamEnded, true)
  assert.equal(state.shouldStopStream(terminal.state), true)
  assert.equal(terminal.state.logs.length, 2)

  const foreign = state.applyEvent(current, { ...progress, runId: 'run-2' }, 'zh')
  assert.equal(foreign.needsRefresh, false)
  assert.equal(foreign.state.lastSeq, 3)
})

test('时钟、平台与引擎标签按语言展示', () => {
  assert.equal(
    state.simulatedClock('2026-11-24T15:00:00Z', 'zh', '2026-11-24T13:00:00Z'),
    '第 1 天 15:00',
  )
  assert.equal(
    state.simulatedClock('2026-11-25T14:00:00Z', 'en', '2026-11-24T13:00:00Z'),
    'Day 2 14:00',
  )
  assert.equal(state.platformLabel('platform_a', 'zh'), '信息广场')
  assert.equal(state.platformLabel('platform_b', 'en'), 'Topic Community')
  assert.equal(state.engineLabel('double', 'zh'), '离线替身运行')
  assert.equal(state.engineLabel('oasis', 'en'), 'Local OASIS run')
  assert.equal(state.phaseLabel('canceled', 'zh'), '已停止')
  assert.equal(state.actionLabel('create_post', 'zh', 'platform_a'), '发帖')
  assert.equal(state.actionLabel('create_post', 'zh', 'platform_b'), '发主题')
  assert.equal(state.actionLabel('create_post', 'en', 'platform_b'), 'New topic')
  assert.equal(state.actionLabel('like_post', 'zh', 'platform_a'), '点赞')
  assert.equal(state.actionLabel('like_post', 'zh', 'platform_b'), '投票赞成')
  assert.equal(state.actionLabel('like_post', 'en', 'platform_b'), 'Upvote')
  assert.equal(state.actionLabel('dislike_post', 'zh', 'platform_b'), '投票反对')
  assert.equal(state.actionLabel('repost', 'zh', 'platform_a'), '转发')
})

test('详情里的 stopRequested 会保留停止提示', () => {
  const current = state.applyDetail(
    state.createSimulationState('sel-1'),
    detail({ stopRequested: true, canStop: false }),
  )
  assert.equal(current.stopRequested, true)
  assert.equal(state.stopLabel(current, 'zh'), '停止已请求')
})

test('计数不一致可被发现，停止请求可标记', () => {
  const current = state.applyDetail(state.createSimulationState('sel-1'), detail())
  assert.equal(state.countMismatch(current), false)
  const broken = state.applyDetail(
    state.createSimulationState('sel-1'),
    detail({
      platforms: [
        { platformKey: 'platform_a', nativePlatform: 'twitter', status: 'running', roundsCompleted: 1, roundsTotal: 3, actionCount: 1, byAction: {}, failure: null, simulatedAt: null },
      ],
    }),
  )
  assert.equal(state.countMismatch(broken), true)
  assert.equal(state.markStopRequested(current).stopRequested, true)
})

test('记忆视图按 runId、轮次与平台守卫迟到响应', () => {
  let current = state.createMemoryState()
  const view = {
    runId: 'run-1',
    round: 2,
    platform: 'platform_b',
    cutoff: { round: 2, availableMaxRound: 2, platforms: {} },
    status: { status: 'complete', pendingEpisodes: 0, unconfirmedEpisodes: 0, lastSyncedRound: 2, reason: null, memoryJobId: null },
    material: { snapshotId: 'snap-1', nodes: [], edges: [] },
    runtime: { nodes: [], edges: [] },
    entries: [],
  }
  current = state.applyMemoryView(current, view, {
    runId: 'run-1',
    round: 2,
    platform: 'platform_b',
  })
  assert.equal(current.memory?.round, 2)
  const stale = state.applyMemoryView(current, { ...view, round: 3 }, {
    runId: 'run-1',
    round: 2,
    platform: 'platform_b',
  })
  assert.equal(stale.memory?.round, 2)
  const otherRun = state.applyMemoryView(current, { ...view, runId: 'run-2' }, {
    runId: 'run-1',
    round: 2,
    platform: 'platform_b',
  })
  assert.equal(otherRun, current)
  const switched = state.switchMemoryView(current, {
    runId: 'run-1',
    round: 3,
    platform: 'platform_b',
  })
  assert.equal(switched.memory, null)
  assert.equal(switched.loading, true)
})

test('记忆状态与来源按语言展示', () => {
  assert.equal(state.memoryStatusLabel('unconfirmed', 'zh'), '写入待确认')
  assert.equal(state.memoryStatusLabel('unavailable', 'en'), 'Memory unavailable')
  assert.equal(state.memoryOriginLabel('material', 'zh'), '材料事实')
  assert.equal(state.memoryOriginLabel('role', 'en'), 'Role speech')
  assert.equal(
    state.memorySourceLabel({ facet: 'simulation_result', roundIndex: 2, sourceEventSeq: 6 }, 'zh'),
    '推演结果 · R2 · #6',
  )
})
