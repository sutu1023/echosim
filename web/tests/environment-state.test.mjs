import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const stateDir = new URL('../src/environment/', import.meta.url)

async function loadTypeScriptModule(name) {
  const source = fs.readFileSync(new URL(name, stateDir), 'utf8')
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: name,
  }).outputText
  const encoded = Buffer.from(output, 'utf8').toString('base64')
  return import(`data:text/javascript;base64,${encoded}`)
}

const env = await loadTypeScriptModule('state.ts')
const links = await loadTypeScriptModule('links.ts')

function role(id, batchIndex = 0, simulation = null) {
  return {
    roleId: id,
    nodeKey: `node-${id}`,
    displayName: `角色 ${id}`,
    roleType: '个人',
    batchIndex,
    mention: { text: '材料提及', sources: [] },
    background: { text: '', stated: false, sources: [] },
    expressedTopics: [],
    involvedTopics: [],
    simulation,
  }
}

function settings(revision = 1, rounds = 72) {
  return {
    durationHours: 72,
    minutesPerRound: 60,
    roundsMode: 'auto',
    manualRounds: null,
    rounds,
    autoRounds: 72,
    settingsRevision: revision,
  }
}

function preparation(overrides = {}) {
  return {
    preparationId: 'prep-1',
    projectId: 'project-1',
    buildId: 'build-1',
    snapshotId: 'snapshot-1',
    runId: 'run-1',
    jobId: 'job-1',
    jobStatus: 'running',
    question: '原创问题',
    phase: 'preparing',
    settings: settings(),
    failure: null,
    counts: { previewRoleCount: 0, profileBatchCount: 0, providerCalls: { real: 0, double: 0 } },
    progress: {
      totalRoles: 3,
      profiledRoles: 0,
      publishedRoles: 0,
      previewRoleCount: 0,
      batchCount: 2,
      previewBatchCount: 0,
    },
    previewOnly: true,
    previewRoles: [],
    roles: [],
    platforms: [],
    initialTopics: [],
    activation: null,
    frozenSelection: null,
    canContinue: false,
    requiredAction: null,
    ...overrides,
  }
}

test('切换 build 先清空旧角色与旧轮数', () => {
  let state = env.createEnvironmentState()
  state = env.applyContext(state, {
    build: {
      buildId: 'build-1',
      projectId: 'project-1',
      runId: 'run-1',
      jobId: 'job-1',
      question: '旧问题',
      phase: 'ready',
      canContinue: true,
      failure: null,
    },
    snapshot: { snapshotId: 'snapshot-1', revision: 1, complete: true, published: true, nodeCount: 3, edgeCount: 2 },
    canPrepare: true,
    reason: null,
    preparation: null,
    newerBuildId: null,
  })
  state = env.applyPreparation(
    state,
    preparation({ previewRoles: [role('a'), role('b')], counts: { previewRoleCount: 2, profileBatchCount: 1, providerCalls: { real: 0, double: 1 } } }),
  )
  assert.equal(state.previewRoles.length, 2)
  const reset = env.resetForBuild(state, 'build-2')
  assert.equal(reset.previewRoles.length, 0)
  assert.equal(reset.settings, null)
  assert.equal(reset.buildId, 'build-2')
})

test('迟到响应不能抹掉已看到的批次，也不能把终态退回准备中', () => {
  let state = env.createEnvironmentState()
  state = env.applyContext(state, {
    build: {
      buildId: 'build-1',
      projectId: 'project-1',
      runId: 'run-1',
      jobId: 'job-1',
      question: '原创问题',
      phase: 'ready',
      canContinue: true,
      failure: null,
    },
    snapshot: { snapshotId: 'snapshot-1', revision: 1, complete: true, published: true, nodeCount: 3, edgeCount: 2 },
    canPrepare: true,
    reason: null,
    preparation: null,
    newerBuildId: null,
  })
  state = env.applyPreparation(
    state,
    preparation({ previewRoles: [role('a'), role('b')] }),
  )
  state = env.applyPreparation(
    state,
    preparation({ previewRoles: [role('a')] }),
  )
  assert.deepEqual(state.previewRoles.map((item) => item.roleId), ['a', 'b'])

  state = env.applyPreparation(
    state,
    preparation({
      phase: 'ready',
      previewOnly: false,
      previewRoles: [],
      roles: [role('a', 0, { motive: 'x', stance: 'y', activeHours: [9], postingFrequency: 1, responseDelayMinutes: 1, influenceWeight: 1, platformKeys: ['platform_a'], topics: [] })],
      platforms: [
        { platformKey: 'platform_a', nativePlatform: 'twitter' },
        { platformKey: 'platform_b', nativePlatform: 'reddit' },
      ],
      activation: { firstSpeakerRoleIds: ['a'], firstSpeakerTopics: [{ roleId: 'a', topicId: 't1' }], order: ['a'] },
      initialTopics: [{ topicId: 't1', text: '原创问题', origin: 'question', claimNature: null, claimNatureKnown: false, sources: [] }],
      canContinue: false,
    }),
  )
  assert.equal(state.phase, 'ready')
  const regressed = env.applyPreparation(state, preparation({ previewRoles: [role('c')] }))
  assert.equal(regressed.phase, 'ready')
  assert.equal(regressed.roles.length, 1)
})

test('旧 preparationId 的事件与响应被丢弃', () => {
  let state = env.createEnvironmentState()
  state = env.applyPreparation(state, preparation())
  const event = env.applyEnvironmentEvent(state, {
    preparationId: 'prep-2',
    buildId: 'build-1',
    phase: 'preparing',
  })
  assert.equal(event.stale, true)
  const otherBuild = env.applyPreparation(
    state,
    preparation({ preparationId: 'prep-3', buildId: 'build-2' }),
  )
  assert.equal(otherBuild.stale, true)
})

test('六态与第三阶段守卫', () => {
  let state = env.createEnvironmentState()
  assert.equal(env.environmentViewMode(state), 'graph-not-ready')
  state = { ...state, canPrepare: true }
  assert.equal(env.environmentViewMode(state), 'not-started')
  state = env.applyPreparation(state, preparation())
  assert.equal(env.environmentViewMode(state), 'preparing')
  assert.equal(env.canOpenNextStage(state), false)
  state = env.applyPreparation(
    state,
    preparation({ phase: 'failed', previewOnly: true }),
  )
  assert.equal(env.environmentViewMode(state), 'failed')
  state = env.applyPreparation(
    state,
    preparation({
      phase: 'ready',
      canContinue: true,
      frozenSelection: { selectionId: 's1' },
      roles: [role('a')],
      activation: {
        firstSpeakerRoleIds: ['a'],
        firstSpeakerTopics: [{ roleId: 'a', topicId: 't1' }],
        order: ['a'],
      },
    }),
  )
  assert.equal(env.environmentViewMode(state), 'confirmed')
  assert.equal(env.canOpenNextStage(state), true)
  assert.equal(env.activationIsValid(state), true)
})

test('PDF 与文本来源链接保留上下文与定位', () => {
  const textSource = {
    documentId: 'doc-1',
    documentName: '原创材料.txt',
    kind: 'text',
    chunkIndex: 1,
    pageNo: null,
    paragraphStart: 1,
    paragraphEnd: 1,
    sourceStart: 12,
    sourceEnd: 30,
    quote: '原文引文',
    locator: { kind: 'text', url: '/api/projects/p/documents/d/source', sourceStart: 12, sourceEnd: 30 },
  }
  const textUrl = links.sourceWorkbenchUrl(
    'project-1',
    textSource,
    { layout: 'graph', lang: 'en' },
    'build-1',
    'prep-1',
  )
  const params = new URL(textUrl, 'http://localhost').searchParams
  assert.equal(params.get('layout'), 'graph')
  assert.equal(params.get('lang'), 'en')
  assert.equal(params.get('build'), 'build-1')
  assert.equal(params.get('preparation'), 'prep-1')
  assert.equal(params.get('document'), 'doc-1')
  assert.equal(params.get('source'), '1')
  assert.equal(params.get('sourceStart'), '12')
  assert.equal(params.get('sourceEnd'), '30')

  const pdfSource = {
    ...textSource,
    kind: 'pdf',
    pageNo: 3,
    locator: { kind: 'pdf', pageNo: 3, url: '/api/projects/p/documents/d/file#page=3' },
  }
  const pdfUrl = links.sourceWorkbenchUrl(
    'project-1',
    pdfSource,
    { layout: 'split', lang: 'zh' },
    'build-1',
    'prep-1',
  )
  assert.equal(new URL(pdfUrl, 'http://localhost').searchParams.get('page'), '3')
  assert.equal(links.sourceDirectUrl(pdfSource), '/api/projects/p/documents/d/file#page=3')
})

test('进入第三阶段显式保留 build 与 preparation', () => {
  const url = links.nextStageUrl(
    'project-1',
    { layout: 'workbench', lang: 'en', build: 'old-build' },
    'build-1',
    'prep-1',
  )
  const params = new URL(url, 'http://localhost').searchParams
  assert.equal(params.get('layout'), 'workbench')
  assert.equal(params.get('lang'), 'en')
  assert.equal(params.get('build'), 'build-1')
  assert.equal(params.get('preparation'), 'prep-1')
})
