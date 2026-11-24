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

function documentBody() {
  return {
    documentId: 'doc-1',
    name: '原创材料.txt',
    kind: 'text',
    contentType: 'text/plain',
    sizeBytes: 100,
    sha256: 'a'.repeat(64),
    status: 'ready',
    pageCount: null,
    chunkCount: 1,
    failure: null,
    createdAt: '2026-11-24T00:00:00Z',
    readyAt: '2026-11-24T00:00:01Z',
    jobId: null,
    runId: null,
  }
}

function graphPayload() {
  return {
    build: {
      buildId: 'build-1',
      projectId: PROJECT,
      runId: 'run-1',
      jobId: 'job-1',
      question: '原创问题',
      phase: 'ready',
      status: 'succeeded',
      plannedEpisodes: 1,
      acceptedEpisodes: 1,
      confirmedEpisodes: 1,
      canContinue: true,
      failure: null,
    },
    snapshot: {
      snapshotId: 'snapshot-1',
      buildId: 'build-1',
      revision: 1,
      complete: true,
      published: true,
      nodeCount: 0,
      edgeCount: 0,
      episodeCount: 1,
      nodes: [],
      edges: [],
    },
  }
}

test('graph 布局从角色来源进入时自动展开材料抽屉并显示原文高亮', async () => {
  const selection = installSelectionSpy()
  installFetch((url) => {
    if (url === '/health') {
      return jsonResponse({ status: 'ok', database: 'ok' })
    }
    if (url.endsWith(`/api/projects/${PROJECT}`)) {
      return jsonResponse({
        project: {
          id: PROJECT,
          name: '原创项目',
          description: null,
          created_at: '2026-11-24T00:00:00Z',
        },
      })
    }
    if (url.endsWith(`/api/projects/${PROJECT}/documents`)) {
      return jsonResponse({ documents: [documentBody()], limits: {} })
    }
    if (url.endsWith(`/api/projects/${PROJECT}/documents/doc-1`)) {
      return jsonResponse({ document: documentBody() })
    }
    if (url.includes(`/api/projects/${PROJECT}/documents/doc-1/chunks`)) {
      return jsonResponse({
        chunks: [
          {
            index: 1,
            pageNo: null,
            paragraphStart: 1,
            paragraphEnd: 1,
            sourceStart: 2,
            sourceEnd: 5,
            text: '原文摘录',
            sourceUrl: null,
          },
        ],
        total: 1,
        ready: true,
      })
    }
    if (url.endsWith(`/api/projects/${PROJECT}/documents/doc-1/source`)) {
      return new Response(TEXT, { status: 200 })
    }
    if (url.endsWith(`/api/projects/${PROJECT}/evidence-graph`)) {
      return jsonResponse(graphPayload())
    }
    if (url.endsWith(`/api/projects/${PROJECT}/jobs`)) {
      return jsonResponse({ jobs: [] })
    }
    throw new Error(`unexpected fetch ${url}`)
  })
  const router = await createRouter(
    `/projects/${PROJECT}/workbench/evidence-graph?layout=graph&lang=zh&document=doc-1&source=1&sourceStart=2&sourceEnd=5`,
  )
  const wrapper = await mountComponent(components.WorkbenchView, {
    global: { plugins: [router] },
  })
  await flush(20)
  await new Promise((resolve) => setTimeout(resolve, 20))
  await flush(10)
  const context = wrapper.find('.wb-context')
  assert.ok(context.exists())
  assert.notEqual(context.element.style.display, 'none')
  assert.equal(wrapper.find('.source-text').text(), TEXT)
  assert.ok(wrapper.find('.materials').exists())
  assert.deepEqual(selection.at(-1), { start: 2, end: 5 })
  wrapper.unmount()
})
