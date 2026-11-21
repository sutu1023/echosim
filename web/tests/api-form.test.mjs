import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const moduleDir = new URL('../src/', import.meta.url)

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

function stubFetch() {
  const calls = []
  globalThis.fetch = async (path, init) => {
    calls.push({ path, init })
    return {
      status: 201,
      ok: true,
      json: async () => ({ ok: true }),
      text: async () => 'ok',
    }
  }
  return calls
}

test('FormData 上传不设置 JSON 请求头，交由浏览器生成 multipart 边界', async () => {
  const calls = stubFetch()
  const { api } = await loadTypeScriptModule('api.ts')
  const form = new FormData()
  form.append('file', new Blob(['hello']), 'a.txt')

  await api.postForm('/api/projects/p/documents', form)

  const headers = new Headers(calls[0].init.headers)
  assert.equal(headers.get('content-type'), null)
  assert.equal(headers.get('accept'), 'application/json')
  assert.ok(calls[0].init.body instanceof FormData)
})

test('JSON 请求仍然显式设置 application/json', async () => {
  const calls = stubFetch()
  const { api } = await loadTypeScriptModule('api.ts')

  await api.post('/api/projects', { name: 'x' })

  const headers = new Headers(calls[0].init.headers)
  assert.equal(headers.get('content-type'), 'application/json')
})
