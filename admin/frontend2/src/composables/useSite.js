import { ref, computed } from 'vue'
import { sitesApi } from '@/api/sites'

const cache = new Map()

function getStore(name) {
  if (!cache.has(name)) {
    cache.set(name, {
      site: ref(null),
      apps: ref([]),
      backups: ref([]),
      installable: ref([]),
      nginxEnabled: ref(false),
      adminTls: ref(false),
      loading: ref(false),
      error: ref(''),
      appsLoading: ref(false),
      backupsLoading: ref(false),
    })
  }
  return cache.get(name)
}

export function useSite(name) {
  const store = getStore(name)

  async function load() {
    store.loading.value = true
    store.error.value = ''
    try {
      const data = await sitesApi.detail(name)
      store.site.value = data.site
      store.installable.value = data.installable_apps || []
      store.nginxEnabled.value = data.nginx_enabled ?? false
      store.adminTls.value = data.admin_tls ?? false
    } catch (caught) {
      store.error.value = caught.message || 'Failed to load site'
      store.site.value = null
    } finally {
      store.loading.value = false
    }
  }

  async function reload() {
    try {
      const data = await sitesApi.detail(name)
      store.site.value = data.site
      store.installable.value = data.installable_apps || []
      store.nginxEnabled.value = data.nginx_enabled ?? false
      store.adminTls.value = data.admin_tls ?? false
    } catch { /* silent */ }
  }

  async function loadApps() {
    store.appsLoading.value = true
    try {
      const data = await sitesApi.apps.list(name)
      store.apps.value = data.apps || []
    } catch {
      store.apps.value = []
    } finally {
      store.appsLoading.value = false
    }
  }

  async function loadBackups() {
    store.backupsLoading.value = true
    try {
      store.backups.value = await sitesApi.backups.list(name)
    } catch {
      store.backups.value = []
    } finally {
      store.backupsLoading.value = false
    }
  }

  async function login() {
    const data = await sitesApi.login(name)
    if (data.url) window.open(data.url, '_blank')
    return data
  }

  async function backup() {
    return sitesApi.backups.create(name)
  }

  async function drop() {
    return sitesApi.drop(name)
  }

  async function reinstall() {
    return sitesApi.reinstall(name)
  }

  async function installApp(app) {
    return sitesApi.apps.install(name, app)
  }

  async function uninstallApp(app) {
    return sitesApi.apps.uninstall(name, app)
  }

  async function forceUninstallApp(app) {
    return sitesApi.apps.forceUninstall(name, app)
  }

  async function forceDrop() {
    return sitesApi.forceDrop(name)
  }

  async function saveConfig(config) {
    return sitesApi.config(name, config)
  }

  const installedApps = computed(() => store.site.value?.installed_apps || [])

  const status = computed(() => {
    if (!store.site.value) return 'unknown'
    if (!store.site.value.exists) return 'offline'
    if (store.site.value.broken) return 'broken'
    return 'online'
  })

  const version = computed(() => {
    const branch = store.site.value?.site_config?.frappe_branch
    if (!branch) return ''
    const match = /^version-(\d+)/.exec(branch)
    return match ? `Version ${match[1]}` : branch
  })

  return {
    site: store.site,
    apps: store.apps,
    backups: store.backups,
    installable: store.installable,
    nginxEnabled: store.nginxEnabled,
    adminTls: store.adminTls,
    loading: store.loading,
    error: store.error,
    appsLoading: store.appsLoading,
    backupsLoading: store.backupsLoading,
    installedApps,
    status,
    version,
    load,
    reload,
    loadApps,
    loadBackups,
    login,
    backup,
    drop,
    reinstall,
    installApp,
    uninstallApp,
    forceUninstallApp,
    forceDrop,
    saveConfig,
  }
}
