import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', component: () => import('./pages/Dashboard.vue'), meta: { title: 'Dashboard' } },
  { path: '/apps', component: () => import('./pages/Apps.vue'), meta: { title: 'Apps' } },
  { path: '/sites', component: () => import('./pages/Sites.vue'), meta: { title: 'Sites' } },
  { path: '/sites/:name', component: () => import('./pages/SiteDetail.vue'), meta: { title: 'Sites' } },
  { path: '/processes', component: () => import('./pages/Processes.vue'), meta: { title: 'Processes' } },
  { path: '/logs', component: () => import('./pages/LogList.vue'), meta: { title: 'Logs' } },
  { path: '/logs/:filename', component: () => import('./pages/LogViewer.vue'), meta: { title: 'Logs' } },
  { path: '/tasks', component: () => import('./pages/TaskList.vue'), meta: { title: 'Tasks' } },
  { path: '/tasks/:id', component: () => import('./pages/TaskDetail.vue'), meta: { title: 'Tasks' } },
  { path: '/database', component: () => import('./pages/Database.vue'), meta: { title: 'Database' } },
  { path: '/database/binlogs/:name', component: () => import('./pages/BinlogDetail.vue'), meta: { title: 'Binlogs' } },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta?.title
    ? `${to.meta.title} - Bench Admin`
    : 'Bench Admin'
})
