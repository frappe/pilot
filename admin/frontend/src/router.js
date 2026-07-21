import { createRouter, createWebHistory } from 'vue-router'
import { navigationRoutes } from './navigation'
import { useSession } from './composables/auth/useSession'
import { safeRedirect } from './utils/redirect'
import { authApi } from './api/auth'

const routes = [
  {
    path: '/setup',
    name: 'Setup',
    component: () => import('./pages/setup/Setup.vue'),
    meta: { title: 'Setup', fullScreen: true },
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('./pages/auth/Login.vue'),
    meta: { title: 'Login', fullScreen: true },
  },
  { path: '/', redirect: '/sites' },
  {
    path: '/sites/:name/:tab?',
    name: 'SiteDetail',
    component: () => import('./pages/sites/SiteDetail.vue'),
    meta: { group: 'Sites' },
  },
  {
    path: '/insights/tasks/:taskId',
    name: 'TaskDetail',
    component: () => import('./pages/tasks/TaskDetail.vue'),
    meta: { group: 'Insights' },
  },
  {
    path: '/migrations/:operationId',
    name: 'MigrationDetail',
    component: () => import('./pages/migrations/MigrationDetail.vue'),
    props: true,
    meta: { title: 'Migration', group: 'Insights' },
  },
  ...navigationRoutes(),
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
    } catch { /* fall through to the unauthenticated redirect below */ }
    const { sid: _sid, ...query } = to.query
    return { path: to.path, query, replace: true }
  }

  const { session, ensureSession } = useSession()
  await ensureSession()
  if (session.wizard) return to.name === 'Setup' ? true : { name: 'Setup' }
  if (to.name === 'Setup') return { path: '/' }
  if (!session.authenticated && to.name !== 'Login')
    return { name: 'Login', query: { redirect: to.fullPath } }
  if (session.authenticated && to.name === 'Login')
    return { path: safeRedirect(to.query.redirect) }
  return true
})

router.afterEach((to) => {
  if (to.name !== 'SiteDetail') {
    document.title = to.meta?.title ? `${to.meta.title} - Pilot` : 'Pilot'
  }
})
