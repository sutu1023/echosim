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

const links = await loadTypeScriptModule('stage-links.ts')
const stages = await loadTypeScriptModule('stages.ts')

test('阶段链接显式保留 layout 与 lang', () => {
  const report = links.stageLocation('project-1', 'report', {
    layout: 'graph',
    lang: 'en',
  })
  const interaction = links.stageLocation('project-1', 'interaction', report.query)

  assert.equal(report.name, 'workbench-stage')
  assert.equal(report.params.id, 'project-1')
  assert.equal(report.params.stage, 'report')
  assert.deepEqual(report.query, { layout: 'graph', lang: 'en' })
  assert.equal(interaction.params.stage, 'interaction')
  assert.deepEqual(interaction.query, { layout: 'graph', lang: 'en' })
})

test('报告 graph/en → 深度互动 → 后退回报告不丢查询参数', () => {
  const report = links.stageLocation('project-1', 'report', {
    layout: 'graph',
    lang: 'en',
  })
  const interaction = links.stageLocation('project-1', 'interaction', report.query)
  const backToReport = links.stageLocation('project-1', 'report', interaction.query)

  assert.deepEqual(backToReport, report)
  assert.deepEqual(backToReport.query, { layout: 'graph', lang: 'en' })
})

test('非法布局和语言回退到稳定默认值', () => {
  assert.equal(links.normalizeLayout('unknown'), 'split')
  assert.equal(links.normalizeLayout(undefined), 'split')
  assert.equal(links.normalizeLayout('workbench'), 'workbench')
  assert.equal(links.normalizeLang('fr'), 'zh')
  assert.equal(links.normalizeLang(undefined), 'zh')
  assert.equal(links.normalizeLang('en'), 'en')
})

test('阶段顺序稳定，隔离数据状态可证明正常分支', () => {
  assert.deepEqual(
    stages.stageDefinitions.map((stage) => stage.slug),
    [
      'evidence-graph',
      'roles-environment',
      'dual-platform-run',
      'report',
      'interaction',
    ],
  )
  assert.equal(stages.resolveStageContentState(false), 'empty')
  assert.equal(stages.resolveStageContentState(true), 'normal')
})
