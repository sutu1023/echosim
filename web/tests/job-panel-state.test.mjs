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

const state = await loadTypeScriptModule('job-panel-state.ts')

function flags(overrides = {}) {
  return {
    terminalType: null,
    streamStatus: 'live',
    error: '',
    pollError: '',
    awaitingTakeover: false,
    gap: false,
    ...overrides,
  }
}

test('断线重连提示不被轮询 503 覆盖', () => {
  const source = state.pickBannerSource(
    flags({
      streamStatus: 'reconnecting',
      pollError: '暂时无法读取任务，请稍后重试。',
    }),
  )

  assert.equal(source, 'stream')
})

test('网络恢复读取成功后不再显示错误', () => {
  const recovered = state.pickBannerSource(
    flags({ streamStatus: 'live', pollError: '' }),
  )

  assert.equal(recovered, null)
})

test('终态失败与取消优先于连接提示', () => {
  assert.equal(
    state.pickBannerSource(
      flags({ terminalType: 'failed', streamStatus: 'reconnecting', pollError: 'x' }),
    ),
    'failed',
  )
  assert.equal(
    state.pickBannerSource(
      flags({ terminalType: 'canceled', streamStatus: 'interrupted' }),
    ),
    'canceled',
  )
})

test('成功完成不显示任何提示', () => {
  assert.equal(
    state.pickBannerSource(flags({ terminalType: 'completed', streamStatus: 'ended' })),
    null,
  )
})

test('读取成功会同时清空动作错误与轮询错误', () => {
  const cleared = state.clearedOnReadSuccess()

  assert.deepEqual(cleared, { error: '', pollError: '' })
})

test('等待接手与缺口提示仍可出现', () => {
  assert.equal(
    state.pickBannerSource(flags({ awaitingTakeover: true })),
    'takeover',
  )
  assert.equal(state.pickBannerSource(flags({ gap: true })), 'gap')
  assert.equal(
    state.pickBannerSource(flags({ error: '服务暂时不可用' })),
    'error',
  )
})
