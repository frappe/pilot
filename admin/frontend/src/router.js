import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', component: () => import('./pages/Sites.vue'), meta: { title: 'Sites' } },
  { path: '/sites/:name', component: () => import('./pages/SiteDetail.vue'), meta: { title: 'Sites' } },
  { path: '/marketplace', component: () => import('./pages/Marketplace.vue'), meta: { title: 'Marketplace' } },
  { path: '/monitor', component: () => import('./pages/Monitor.vue'), meta: { title: 'Monitor' } },
  { path: '/logs', component: () => import('./pages/Logs.vue'), meta: { title: 'Logs' } },
  { path: '/tasks', component: () => import('./pages/Tasks.vue'), meta: { title: 'Tasks' } },
  { path: '/tasks/:id', component: () => import('./pages/TaskDetail.vue'), meta: { title: 'Tasks' } },
  { path: '/database', component: () => import('./pages/Database.vue'), meta: { title: 'Database' } },
  { path: '/database/binlogs/:name', component: () => import('./pages/BinlogDetail.vue'), meta: { title: 'Binlogs' } },
  { path: '/snapshots', component: () => import('./pages/Snapshots.vue'), meta: { title: 'Snapshots' } },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta?.title
    ? `${to.meta.title} - Pilot`
    : 'Pilot'
})
