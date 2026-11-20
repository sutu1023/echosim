/** 路由：私有页面在进入前确认会话，未登录回到登录页并记住原路径。 */

import { createRouter, createWebHistory } from 'vue-router'
import { ApiError } from './api'
import { ensureSession } from './session'
import HistoryView from './views/HistoryView.vue'
import LoginView from './views/LoginView.vue'
import ProjectDetailView from './views/ProjectDetailView.vue'
import ProjectsView from './views/ProjectsView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/', name: 'projects', component: ProjectsView },
    { path: '/projects/:id', name: 'project-detail', component: ProjectDetailView },
    { path: '/history', name: 'history', component: HistoryView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  const isPublic = to.meta.public === true
  try {
    const user = await ensureSession()
    if (isPublic && user) {
      return { name: 'projects' }
    }
    if (!isPublic && !user) {
      return { name: 'login', query: { next: to.fullPath } }
    }
    return true
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return isPublic ? true : { name: 'login', query: { next: to.fullPath } }
    }
    // 503 等暂时性故障：允许进入页面，由页面展示不可用提示。
    return true
  }
})
