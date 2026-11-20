/** 路由：公开控制台与私有项目/工作台；私有页面进入前确认会话并记住原路径。 */

import { createRouter, createWebHistory } from 'vue-router'
import { ApiError } from './api'
import { ensureSession } from './session'
import { defaultStage } from './workbench/stages'
import { stageLocation } from './workbench/stage-links'
import ConsoleView from './views/ConsoleView.vue'
import HistoryView from './views/HistoryView.vue'
import LoginView from './views/LoginView.vue'
import ProjectDetailView from './views/ProjectDetailView.vue'
import ProjectsView from './views/ProjectsView.vue'
import WorkbenchView from './views/WorkbenchView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'console',
      component: ConsoleView,
      meta: { public: true },
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { public: true },
    },
    { path: '/projects', name: 'projects', component: ProjectsView },
    {
      path: '/projects/:id',
      name: 'project-detail',
      component: ProjectDetailView,
    },
    {
      path: '/projects/:id/workbench',
      name: 'workbench-root',
      redirect: (to) =>
        stageLocation(
          String(to.params.id),
          defaultStage.slug,
          to.query,
        ),
    },
    {
      path: '/projects/:id/workbench/:stage',
      name: 'workbench-stage',
      component: WorkbenchView,
      meta: { hideChrome: true },
    },
    { path: '/history', name: 'history', component: HistoryView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  const isPublic = to.meta.public === true
  try {
    const user = await ensureSession()
    if (isPublic) {
      if (to.name === 'login' && user) {
        return { name: 'projects' }
      }
      return true
    }
    if (!user) {
      return { name: 'login', query: { next: to.fullPath } }
    }
    return true
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return isPublic ? true : { name: 'login', query: { next: to.fullPath } }
    }
    // 503 等暂时性故障：允许进入页面，由页面展示不可用提示并保留登录态。
    return true
  }
})
