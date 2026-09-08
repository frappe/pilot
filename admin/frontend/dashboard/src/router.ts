import { createRouter, createWebHistory } from 'vue-router'

import { useSession } from '@/composables/auth/useSession'
import { safeRedirect } from '@/utils/redirect'
import { authApi } from '@/api/auth'

const routes = [
  {
    path: '/setup',
    name: 'Setup',
    component: () => import('@/pages/setup/Setup.vue'),
    meta: { title: 'Setup', fullScreen: true },
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/pages/auth/Login.vue'),
    meta: { title: 'Login', fullScreen: true },
  },
  { path: '/', redirect: '/sites' },
  {
    path: '/home',
    name: 'Home',
    component: () => import('@/pages/home/Home.vue'),
    meta: { title: 'Home' },
  },
  {
    path: '/sites',
    name: 'Sites',
    component: () => import('@/pages/sites/Sites.vue'),
    meta: { title: 'Sites', showUpdateStatus: true },
  },
  {
    path: '/sites/:name/:tab?',
    name: 'SiteDetail',
    component: () => import('@/pages/sites/SiteDetail.vue'),
    meta: { group: 'Sites' },
  },
  { path: '/server', redirect: '/storage' },
  {
    path: '/storage',
    name: 'Storage',
    component: () => import('@/pages/storage/Storage.vue'),
    meta: { title: 'Storage' },
  },
  {
    path: '/marketplace',
    name: 'Marketplace',
    component: () => import('@/pages/marketplace/Marketplace.vue'),
    meta: { title: 'Marketplace', showUpdateStatus: true },
  },
  {
    path: '/insights/analytics',
    name: 'Analytics',
    component: () => import('@/pages/Analytics.vue'),
    meta: { title: 'Analytics', group: 'Insights' },
  },
  {
    path: '/insights/logs',
    name: 'Logs',
    component: () => import('@/pages/logs/Logs.vue'),
    meta: { title: 'Logs', group: 'Insights' },
  },
  {
    path: '/insights/activity',
    name: 'Activity',
    component: () => import('@/pages/Activities.vue'),
    meta: { title: 'Activity', group: 'Insights' },
  },
  {
    path: '/insights/processes',
    name: 'Processes',
    component: () => import('@/pages/processes/Processes.vue'),
    meta: { title: 'Processes', group: 'Insights' },
  },
  {
    path: '/insights/tasks',
    name: 'Tasks',
    component: () => import('@/pages/tasks/Tasks.vue'),
    meta: { title: 'Tasks', group: 'Insights' },
  },
  {
    path: '/insights/tasks/:taskId',
    name: 'TaskDetail',
    component: () => import('@/pages/tasks/TaskDetail.vue'),
    meta: { group: 'Insights' },
  },
  {
    path: '/updates',
    name: 'Updates',
    component: () => import('@/pages/updates/Updates.vue'),
    meta: { title: 'Updates', group: 'Insights', showUpdateStatus: true },
  },
  {
    path: '/updates/:operationId',
    name: 'UpdateDetail',
    component: () => import('@/pages/updates/UpdateDetail.vue'),
    props: true,
    meta: { title: 'Update', group: 'Insights' },
  },
  {
    path: '/database/analyzer',
    name: 'DB analyzer',
    component: () => import('@/pages/database/Analyzer.vue'),
    meta: { title: 'DB analyzer', group: 'Dev tools' },
  },
  {
    path: '/database/sql-playground',
    name: 'SQL playground',
    component: () => import('@/pages/database/SQLPlayground.vue'),
    meta: { title: 'SQL playground', group: 'Dev tools' },
  },
  {
    path: '/editor',
    name: 'Code editor',
    component: () => import('@/pages/editor/Launcher.vue'),
    meta: { title: 'Code editor', group: 'Dev tools' },
  },
  {
    path: '/mobile/settings',
    name: 'MobileSettings',
    component: () => import('@/pages/settings/Settings.vue'),
    meta: { title: 'Settings' },
  },
  {
    path: '/settings/:section?/:subSection?',
    name: 'Settings',
    component: () => import('@/pages/settings/SettingsRoute.vue'),
    meta: { title: 'Settings' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  // A `?sid=<token>` on the URL is a one-time sign-in link (see
  // `pilot generate-session`). Exchange it for a real session cookie and
  // re-navigate to the same place with the token stripped, instead of
  // dragging it along into a /login redirect. Errors (expired/already-used
  // link) are swallowed - the next pass through this guard will see no
  // session cookie and fall through to the normal /login redirect below.
  if (to.query.sid) {
    try {
      await authApi.loginWithSid(to.query.sid)
    } catch {
      /* fall through to the unauthenticated redirect below */
    }
    const { sid: _sid, ...query } = to.query
    return { path: to.path, query, replace: true }
  }

  const { session, ensureSession } = useSession()
  await ensureSession()
  // Setup authenticates like every other page. Without a session the sign-in page is
  // the only stop; the wizard's own link (printed by `pilot start`) carries a ?sid=.
  if (session.wizard && !session.authenticated)
    return to.name === 'Login' ? true : { name: 'Login' }
  if (session.wizard) return to.name === 'Setup' ? true : { name: 'Setup' }
  if (to.name === 'Setup') return { path: '/' }
  if (!session.authenticated && to.name !== 'Login')
    return { name: 'Login', query: { redirect: to.fullPath } }
  if (session.authenticated && to.name === 'Login') {
    const target = safeRedirect(to.query.redirect)
    if (!router.resolve(target).matched.length) {
      window.location.assign(target)
      return false
    }
    return { path: target }
  }
  return true
})

router.afterEach((to) => {
  if (to.name !== 'SiteDetail') {
    document.title = to.meta?.title ? `${to.meta.title} - Pilot` : 'Pilot'
  }
})
