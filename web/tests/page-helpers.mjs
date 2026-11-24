import { JSDOM } from 'jsdom'
import { fileURLToPath } from 'node:url'

const WEB_ROOT = fileURLToPath(new URL('../', import.meta.url))

export function installDom() {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    url: 'http://127.0.0.1:8080/',
    pretendToBeVisual: true,
  })
  const { window } = dom
  for (const key of [
    'window',
    'document',
    'navigator',
    'HTMLElement',
    'Element',
    'Node',
    'Text',
    'Range',
    'NodeFilter',
    'Event',
    'CustomEvent',
    'getComputedStyle',
    'MutationObserver',
    'history',
    'location',
    'localStorage',
    'sessionStorage',
    'EventTarget',
    'HTMLAnchorElement',
    'HTMLInputElement',
    'HTMLButtonElement',
    'HTMLSelectElement',
    'SVGElement',
  ]) {
    Object.defineProperty(globalThis, key, {
      value: window[key],
      configurable: true,
      writable: true,
    })
  }
  globalThis.window.scrollTo = () => {}
  globalThis.window.HTMLElement.prototype.scrollIntoView = () => {}
  globalThis.window.document.querySelector = globalThis.window.document.querySelector.bind(
    globalThis.window.document,
  )
  return dom
}

export function installSelectionSpy() {
  const captured = []
  globalThis.window.getSelection = () => ({
    removeAllRanges() {},
    addRange(range) {
      captured.push({
        start: range.startOffset,
        end: range.endOffset,
      })
    },
  })
  globalThis.getSelection = globalThis.window.getSelection
  return captured
}

export async function loadBuiltComponents() {
  return import(new URL('../.page-build/page-entry.js', import.meta.url).href)
}

export async function mountComponent(component, options = {}) {
  const { mount } = await import('@vue/test-utils')
  return mount(component, {
    attachTo: document.body,
    ...options,
  })
}

export async function createRouter(initialPath) {
  const { createRouter, createMemoryHistory } = await import('vue-router')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/projects/:id/workbench/:stage',
        name: 'workbench-stage',
        component: { template: '<div />' },
      },
      { path: '/login', name: 'login', component: { template: '<div />' } },
    ],
  })
  await router.push(initialPath)
  await router.isReady()
  return router
}

export function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

export function installFetch(handler) {
  const calls = []
  globalThis.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url
    let body = null
    if (typeof init.body === 'string') {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }
    const call = { url, method: init.method || 'GET', body }
    calls.push(call)
    return handler(url, call)
  }
  return calls
}

export function installEventSource() {
  const instances = []
  class FakeEventSource {
    constructor(url) {
      this.url = url
      this.listeners = new Map()
      this.closed = false
      instances.push(this)
    }
    addEventListener(name, handler) {
      this.listeners.set(name, handler)
    }
    emit(name, payload) {
      const handler = this.listeners.get(name)
      if (handler) {
        handler({ data: JSON.stringify(payload) })
      }
    }
    close() {
      this.closed = true
    }
  }
  globalThis.EventSource = FakeEventSource
  globalThis.window.EventSource = FakeEventSource
  return instances
}

export async function flush(times = 3) {
  for (let index = 0; index < times; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
}
