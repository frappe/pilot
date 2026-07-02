import { createRouter, createWebHistory } from 'vue-router'
import { navigationRoutes } from './navigation'
import { useSession } from './composables/useSession'
import { safeRedirect } from './utils/redirect'

const routes = [
  {
    path: '/setup',
    name: 'Setup',
    component: () => import('./pages/Setup.vue'),
    meta: { title: 'Setup', fullScreen: true },
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('./pages/Login.vue'),
    meta: { title: 'Login', fullScreen: true },
  },
  { path: '/', redirect: '/sites' },
  {
    path: '/sites/:name/:tab?',
    name: 'SiteDetail',
    component: () => import('./pages/SiteDetail.vue'),
    meta: { group: 'Sites' },
  },
  {
    path: '/insights/tasks/:taskId',
    name: 'TaskDetail',
    component: () => import('./pages/TaskDetail.vue'),
    meta: { group: 'Insights' },
  },
  ...navigationRoutes(),
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const { session, ensureSession } = useSession()
  await ensureSession()
  if (session.wizard) return to.name === 'Setup' ? true : { name: 'Setup' }
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
