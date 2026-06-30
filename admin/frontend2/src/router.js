import { createRouter, createWebHistory } from 'vue-router'
import { navigationRoutes } from './navigation'

const routes = [
  {
    path: '/setup',
    name: 'Setup',
    component: () => import('./pages/Setup.vue'),
    meta: { title: 'Setup' },
  },
  { path: '/', redirect: '/sites' },
  ...navigationRoutes(),
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta?.title ? `${to.meta.title} - Bench Admin` : 'Bench Admin'
})
