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

const log = await loadTypeScriptModule('job-log.ts')

const RUN = 'run-1'
const OTHER = 'run-2'

function event(seq, type = 'progress', runId = RUN) {
  return {
    runId,
    seq,
    type,
    stage: 'dual-platform-run',
    simulatedAt: `2026-11-21T09:0${seq}:00Z`,
    payload: { step: seq },
  }
}

test('历史补发与实时推送重叠时不重复', () => {
  const state = log.applyJobEvents(
    log.createJobLog(RUN),
    [1, 2, 3, 4].map((seq) => event(seq)),
  )

  const merged = log.applyJobEvents(
    state,
    [event(3), event(4), event(5)],
  )

  assert.deepEqual(
    merged.events.map((item) => item.seq),
    [1, 2, 3, 4, 5],
  )
  assert.equal(merged.cursor, 5)
  assert.equal(merged.gap, false)
})

test('缺口时游标停在连续段，补齐后一起释放', () => {
  const four = log.applyJobEvents(
    log.createJobLog(RUN),
    [1, 2, 3, 4].map((seq) => event(seq)),
  )

  const six = log.applyJobEvent(four, event(6))

  assert.equal(six.cursor, 4)
  assert.equal(six.gap, true)
  assert.deepEqual(
    six.events.map((item) => item.seq),
    [1, 2, 3, 4],
  )
  assert.deepEqual(log.pendingSeqs(six), [6])

  const five = log.applyJobEvent(six, event(5))

  assert.equal(five.cursor, 6)
  assert.equal(five.gap, false)
  assert.deepEqual(
    five.events.map((item) => item.seq),
    [1, 2, 3, 4, 5, 6],
  )
})

test('切换任务后丢弃旧 run 的迟到事件', () => {
  const runOne = log.applyJobEvent(
    log.createJobLog(RUN),
    event(1),
  )
  const runTwo = log.applyJobEvent(
    log.createJobLog(OTHER),
    event(1, 'progress', OTHER),
  )

  const polluted = log.applyJobEvent(runTwo, event(2, 'progress', RUN))

  assert.equal(polluted.runId, OTHER)
  assert.deepEqual(
    polluted.events.map((item) => item.seq),
    [1],
  )
  assert.equal(runOne.runId, RUN)
})

test('终态事件计入并结束日志', () => {
  const state = log.applyJobEvents(
    log.createJobLog(RUN),
    [
      event(1),
      event(2),
      event(3),
      event(4),
      event(5),
      event(6),
      event(7),
      event(8),
      event(9),
      event(10, 'completed'),
    ],
  )

  assert.equal(state.ended, true)
  assert.equal(state.cursor, 10)
  assert.equal(state.events.length, 10)
  assert.equal(state.events.at(-1).type, 'completed')

  const afterEnd = log.applyJobEvent(state, event(11))
  assert.equal(afterEnd.cursor, 10)
  assert.equal(afterEnd.events.length, 10)
})

test('重复的 seq 与非法 seq 被忽略', () => {
  const state = log.applyJobEvent(log.createJobLog(RUN), event(1))
  const duplicate = log.applyJobEvent(state, event(1))
  const invalid = log.applyJobEvent(duplicate, event(0))

  assert.equal(invalid.cursor, 1)
  assert.equal(invalid.events.length, 1)
  assert.equal(invalid.gap, false)
})
