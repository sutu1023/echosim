import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createRouter,
  flush,
  installDom,
  installFetch,
  installSelectionSpy,
  jsonResponse,
  loadBuiltComponents,
  mountComponent,
} from './page-helpers.mjs'

installDom()
const components = await loadBuiltComponents()
const PROJECT = 'project-1'
const TEXT = '零一二三四五六七八九十🌱原句结束'

function documentBody(id, kind = 'text') {
  return {
    documentId: id,
    name: `原创材料-${id}.${kind === 'pdf' ? 'pdf' : 'txt'}`,
    kind,
    contentType: kind === 'pdf' ? 'application/pdf' : 'text/plain',
    sizeBytes: 100,
    sha256: 'a'.repeat(64),
    status: 'ready',
    pageCount: kind === 'pdf' ? 3 : null,
    chunkCount: 1,
    failure: null,
    createdAt: '2026-11-24T00:00:00Z',
    readyAt: '2026-11-24T00:00:01Z',
    jobId: null,
    runId: null,
  }
}

function chunkBody(id, kind = 'text') {
  return {
    index: 1,
    pageNo: kind === 'pdf' ? 3 : null,
    paragraphStart: kind === 'pdf' ? null : 1,
    paragraphEnd: kind === 'pdf' ? null : 1,
    sourceStart: kind === 'pdf' ? 0 : 2,
    sourceEnd: kind === 'pdf' ? 10 : 5,
    text: kind === 'pdf' ? 'PDF 第三页原文' : '原文摘录',
    sourceUrl:
      kind === 'pdf'
        ? `/api/projects/${PROJECT}/documents/${id}/file#page=3`
        : null,
  }
}

async function mountMaterials(id, handler) {
  const calls = installFetch(handler)
  const router = await createRouter(
    `/projects/${PROJECT}/workbench/evidence-graph?layout=split&lang=zh&document=${id}&source=1&sourceStart=2&sourceEnd=5`,
  )
  const wrapper = await mountComponent(components.MaterialsPanel, {
    props: { projectId: PROJECT, lang: 'zh', documentId: id },
    global: { plugins: [router] },
  })
  await flush(12)
  return { wrapper, calls }
}

test('首次打开文本来源深链接会加载原文并按 UTF-16 区间高亮', async () => {
  const selection = installSelectionSpy()
  const { wrapper, calls } = await mountMaterials('doc-1', (url) => {
    if (url.endsWith('/documents')) {
      return jsonResponse({ documents: [documentBody('doc-1')], limits: {} })
    }
    if (url.endsWith('/documents/doc-1')) {
      return jsonResponse({ document: documentBody('doc-1') })
    }
    if (url.includes('/documents/doc-1/chunks')) {
      return jsonResponse({ chunks: [chunkBody('doc-1')], total: 1, ready: true })
    }
    if (url.endsWith('/documents/doc-1/source')) {
      return new Response(TEXT, { status: 200 })
    }
    throw new Error(`unexpected fetch ${url}`)
  })
  assert.ok(calls.some((call) => call.url.endsWith('/documents/doc-1/source')))
  assert.equal(wrapper.find('.source-text').text(), TEXT)
  assert.deepEqual(selection.at(-1), { start: 2, end: 5 })
  wrapper.unmount()
})

test('切换材料后旧原文迟到响应不得覆盖新材料', async () => {
  let releaseOld
  const oldSource = new Promise((resolve) => {
    releaseOld = resolve
  })
  let oldRequested = false
  const selection = installSelectionSpy()
  const { wrapper } = await mountMaterials('doc-1', async (url) => {
    if (url.endsWith('/documents')) {
      return jsonResponse({
        documents: [documentBody('doc-1'), documentBody('doc-2')],
        limits: {},
      })
    }
    if (url.endsWith('/documents/doc-1')) {
      return jsonResponse({ document: documentBody('doc-1') })
    }
    if (url.endsWith('/documents/doc-2')) {
      return jsonResponse({ document: documentBody('doc-2') })
    }
    if (url.includes('/documents/doc-1/chunks')) {
      return jsonResponse({ chunks: [chunkBody('doc-1')], total: 1, ready: true })
    }
    if (url.includes('/documents/doc-2/chunks')) {
      return jsonResponse({ chunks: [chunkBody('doc-2')], total: 1, ready: true })
    }
    if (url.endsWith('/documents/doc-1/source')) {
      oldRequested = true
      const text = await oldSource
      return new Response(text, { status: 200 })
    }
    if (url.endsWith('/documents/doc-2/source')) {
      return new Response('新材料原文🌱', { status: 200 })
    }
    throw new Error(`unexpected fetch ${url}`)
  })
  assert.ok(oldRequested)
  await wrapper.setProps({ documentId: 'doc-2' })
  await flush(12)
  assert.equal(wrapper.find('.source-text').text(), '新材料原文🌱')
  releaseOld('旧材料原文')
  await flush(6)
  assert.equal(wrapper.find('.source-text').text(), '新材料原文🌱')
  assert.ok(selection.length >= 1)
  wrapper.unmount()
})

test('已加载原文后切换材料会读取新材料原文而不是复用旧文本', async () => {
  installSelectionSpy()
  let loadedDoc2 = false
  const { wrapper, calls } = await mountMaterials('doc-1', (url) => {
    if (url.endsWith('/documents')) {
      return jsonResponse({
        documents: [documentBody('doc-1'), documentBody('doc-2')],
        limits: {},
      })
    }
    if (url.endsWith('/documents/doc-1')) {
      return jsonResponse({ document: documentBody('doc-1') })
    }
    if (url.endsWith('/documents/doc-2')) {
      return jsonResponse({ document: documentBody('doc-2') })
    }
    if (url.includes('/documents/doc-1/chunks')) {
      return jsonResponse({ chunks: [chunkBody('doc-1')], total: 1, ready: true })
    }
    if (url.includes('/documents/doc-2/chunks')) {
      return jsonResponse({ chunks: [chunkBody('doc-2')], total: 1, ready: true })
    }
    if (url.endsWith('/documents/doc-1/source')) {
      return new Response('旧材料原文', { status: 200 })
    }
    if (url.endsWith('/documents/doc-2/source')) {
      loadedDoc2 = true
      return new Response('新材料原文🌱', { status: 200 })
    }
    throw new Error(`unexpected fetch ${url}`)
  })
  await wrapper.setProps({ documentId: 'doc-2' })
  await flush(12)
  assert.ok(loadedDoc2, calls.map((call) => call.url).join('\n'))
  assert.equal(wrapper.find('.source-text').text(), '新材料原文🌱')
  wrapper.unmount()
})

test('PDF 摘录保留物理页原件链接', async () => {
  const { wrapper } = await mountMaterials('doc-pdf', (url) => {
    if (url.endsWith('/documents')) {
      return jsonResponse({
        documents: [documentBody('doc-pdf', 'pdf')],
        limits: {},
      })
    }
    if (url.endsWith('/documents/doc-pdf')) {
      return jsonResponse({ document: documentBody('doc-pdf', 'pdf') })
    }
    if (url.includes('/documents/doc-pdf/chunks')) {
      return jsonResponse({
        chunks: [chunkBody('doc-pdf', 'pdf')],
        total: 1,
        ready: true,
      })
    }
    throw new Error(`unexpected fetch ${url}`)
  })
  const link = wrapper.find('a[data-action="open-original"]')
  assert.ok(link)
  assert.match(link.attributes('href'), /#page=3$/)
  wrapper.unmount()
})
