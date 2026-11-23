import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const moduleDir = new URL('../src/workbench/', import.meta.url)

async function loadTypeScriptModule(name) {
  const source = fs.readFileSync(new URL(name, moduleDir), 'utf8')
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

const graph = await loadTypeScriptModule('graph-state.ts')
const { graphEdgeLabel, graphEdgeSentence } = graph

function build(id, phase = 'drawing', canContinue = false) {
  return {
    buildId: id,
    projectId: 'project-1',
    runId: `run-${id}`,
    jobId: `job-${id}`,
    question: '原创问题',
    phase,
    status: 'running',
    plannedEpisodes: 2,
    acceptedEpisodes: 2,
    confirmedEpisodes: 1,
    canContinue,
    failure: null,
  }
}

function snapshot(id, revision) {
  return {
    snapshotId: `snapshot-${id}`,
    buildId: id,
    revision,
    complete: false,
    published: true,
    nodeCount: 2,
    edgeCount: 1,
    episodeCount: 2,
    nodes: [],
    edges: [],
  }
}

test('同 build 快照与计数同源更新', () => {
  let state = graph.createGraphState()
  state = graph.applyGraphPayload(state, {
    build: build('a'),
    snapshot: snapshot('a', 1),
  })
  assert.equal(state.buildId, 'a')
  assert.equal(state.snapshot.revision, 1)
  assert.equal(state.nodeCount, 2)
  assert.equal(state.edgeCount, 1)
})

test('快照保留关系的来源性质', () => {
  const value = graph.applyGraphSnapshot(graph.createGraphState(), {
    ...snapshot('a', 1),
    edges: [
      {
        key: 'e1',
        edgeType: 'official_decision',
        label: '处分',
        origin: 'material',
        summary: null,
        sourceKey: 'n1',
        targetKey: 'n2',
        claimNature: 'official_decision',
        sources: [],
      },
    ],
  })
  assert.equal(value.snapshot.edges[0].claimNature, 'official_decision')
})

test('旧 build 响应不覆盖当前 build', () => {
  let state = graph.applyGraphPayload(graph.createGraphState(), {
    build: build('b'),
    snapshot: snapshot('b', 1),
  })
  const late = graph.applyGraphPayload(state, {
    build: build('a', 'ready', true),
    snapshot: snapshot('a', 2),
  })
  assert.equal(late.buildId, 'b')
  assert.equal(late.stale, true)
})

test('开始新 build 时显式切换并清空旧快照', () => {
  let state = graph.applyGraphPayload(graph.createGraphState(), {
    build: build('a'),
    snapshot: snapshot('a', 1),
  })
  state = graph.applyGraphPayload(
    state,
    { build: build('b', 'preparing'), snapshot: null },
    true,
  )
  assert.equal(state.buildId, 'b')
  assert.equal(state.snapshot, null)
  assert.equal(state.nodeCount, 0)
  assert.equal(state.snapshotRevision, null)
})

test('迟到事件被丢弃，当前事件推进阶段与计数', () => {
  let state = graph.applyGraphPayload(graph.createGraphState(), {
    build: build('b'),
    snapshot: null,
  })
  const stale = graph.applyGraphEvent(state, {
    buildId: 'a',
    phase: 'ready',
    canContinue: true,
  })
  assert.equal(stale.buildId, 'b')
  assert.equal(stale.phase, 'drawing')
  assert.equal(stale.stale, true)
  state = graph.applyGraphEvent(state, {
    buildId: 'b',
    phase: 'ready',
    snapshotRevision: 2,
    nodeCount: 9,
    edgeCount: 4,
    canContinue: true,
  })
  assert.equal(state.phase, 'ready')
  assert.equal(state.snapshotRevision, 2)
  assert.equal(graph.canOpenNextStage(state), true)
})

test('部分快照按版本合并且不改变当前阶段', () => {
  let state = graph.applyGraphPayload(graph.createGraphState(), {
    build: build('b'),
    snapshot: snapshot('b', 1),
  })
  state = graph.applyGraphSnapshot(state, {
    ...snapshot('b', 2),
    nodeCount: 4,
    edgeCount: 2,
  })
  assert.equal(state.phase, 'drawing')
  assert.equal(state.snapshot.revision, 2)
  assert.equal(state.nodeCount, 4)
  assert.equal(state.edgeCount, 2)
})

test('查询参数保留 layout/lang/document 并替换 build', () => {
  assert.deepEqual(
    graph.buildQuery(
      { layout: 'graph', lang: 'en', document: 'doc-1', build: 'old' },
      'new',
    ),
    { layout: 'graph', lang: 'en', document: 'doc-1', build: 'new' },
  )
})

test('画布关系文字按平台行动、转述与建议语义生成', () => {
  const labels = {
    user: '女性用户',
    platform: '小红书',
    actor: '部分账号',
    claim: '武大副校长子女驾车逼停学生（报告所称，未证实）',
    event: '杨景媛论文事件',
    decision: '第三方匿名评审机制',
  }
  assert.equal(
    graphEdgeSentence(
      {
        key: 'e1',
        edgeType: 'platform_action',
        label: '维权',
        sourceKey: 'user',
        targetKey: 'platform',
      },
      labels,
    ),
    '女性用户在小红书上维权',
  )
  assert.equal(
    graphEdgeLabel(
      {
        key: 'e1',
        edgeType: 'platform_action',
        label: '维权',
        sourceKey: 'user',
        targetKey: 'platform',
      },
      labels,
    ),
    '在小红书上维权',
  )
  assert.equal(
    graphEdgeSentence(
      {
        key: 'e2',
        edgeType: 'attributed_claim',
        label: '传播不实信息',
        sourceKey: 'actor',
        targetKey: 'claim',
      },
      labels,
    ),
    '部分账号 传播不实信息 武大副校长子女驾车逼停学生（报告所称，未证实）',
  )
  assert.equal(
    graphEdgeSentence(
      {
        key: 'e3',
        edgeType: 'recommendation',
        label: '建议',
        sourceKey: 'event',
        targetKey: 'decision',
      },
      labels,
    ),
    '杨景媛论文事件 —建议→ 第三方匿名评审机制',
  )
})
