import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const moduleDir = new URL('../src/materials/', import.meta.url)

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

test('buildMaterialQuery 保留 layout/lang 并切换 document', async () => {
  const { buildMaterialQuery } = await loadTypeScriptModule('format.ts')

  assert.deepEqual(
    buildMaterialQuery({ layout: 'graph', lang: 'en', document: 'old', x: 1 }, 'new'),
    { layout: 'graph', lang: 'en', document: 'new' },
  )
  assert.deepEqual(
    buildMaterialQuery({ layout: 'graph', lang: 'en', document: 'old' }, null),
    { layout: 'graph', lang: 'en' },
  )
})

test('sliceSource 用 UTF-16 下标切出含 emoji 的原文', async () => {
  const { sliceSource } = await loadTypeScriptModule('format.ts')
  const text = '😀开头。\n\n第二段。'

  assert.equal(sliceSource(text, 0, 2), '😀')
  assert.equal(sliceSource(text, 7, 11), '第二段。')
})

test('状态与失败码映射到稳定的文案键', async () => {
  const { statusKey, isProcessing, failureKey, formatBytes } =
    await loadTypeScriptModule('format.ts')

  assert.equal(statusKey('parsing'), 'parsing')
  assert.equal(statusKey('weird'), 'queued')
  assert.equal(isProcessing('queued'), true)
  assert.equal(isProcessing('ready'), false)
  assert.equal(failureKey('too_many_pages'), 'tooManyPages')
  assert.equal(failureKey('unknown_code'), 'parseError')
  assert.equal(formatBytes(20971520), '20.0 MiB')
})

test('页数超限文案带上实际页数与配置上限', async () => {
  const { formatPageLimit } = await loadTypeScriptModule('format.ts')

  assert.equal(
    formatPageLimit('该 PDF 共 {pages} 页，超过当前上限 {limit} 页。', 4, 3),
    '该 PDF 共 4 页，超过当前上限 3 页。',
  )
  assert.equal(
    formatPageLimit('This PDF has {pages} pages, above limit {limit}.', null, 3),
    'This PDF has ? pages, above limit 3.',
  )
})

test('段落标签覆盖单段与区间', async () => {
  const { paragraphLabel } = await loadTypeScriptModule('format.ts')
  const base = {
    index: 1,
    pageNo: null,
    sourceStart: 0,
    sourceEnd: 1,
    text: 'x',
    sourceUrl: null,
  }

  assert.equal(paragraphLabel({ ...base, paragraphStart: 3, paragraphEnd: 3 }), '3')
  assert.equal(paragraphLabel({ ...base, paragraphStart: 3, paragraphEnd: 5 }), '3–5')
  assert.equal(paragraphLabel({ ...base, paragraphStart: null, paragraphEnd: null }), null)
})
