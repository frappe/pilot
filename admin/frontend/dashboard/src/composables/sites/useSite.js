import { ref, computed } from 'vue'
import { sitesApi } from '@/api/sites'
import { openSiteLogin } from '@/utils/siteLogin'

const cache = new Map()
const BACKUPS_PAGE_SIZE = 20

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
      backupsLimit: ref(BACKUPS_PAGE_SIZE),
      backupsHasMore: ref(false),
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
      const [data, configuration] = await Promise.all([
        sitesApi.detail(name),
        sitesApi.configuration.get(name),
      ])
      store.site.value = { ...data, site_config: configuration }
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
      const [data, configuration] = await Promise.all([
        sitesApi.detail(name),
        sitesApi.configuration.get(name),
      ])
      store.site.value = { ...data, site_config: configuration }
      store.installable.value = data.installable_apps || []
      store.nginxEnabled.value = data.nginx_enabled ?? false
      store.adminTls.value = data.admin_tls ?? false
    } catch {
      /* silent */
    }
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

  async function _fetchBackups() {
    store.backupsLoading.value = true
    try {
      const data = await sitesApi.backups.list(name, store.backupsLimit.value)
      store.backups.value = data
      // A full page suggests there may be more months of offsite history to fetch.
      store.backupsHasMore.value = data.length >= store.backupsLimit.value
    } catch {
      store.backups.value = []
      store.backupsHasMore.value = false
    } finally {
      store.backupsLoading.value = false
    }
  }

  async function loadBackups() {
    store.backupsLimit.value = BACKUPS_PAGE_SIZE
    await _fetchBackups()
  }

  /** Re-fetches with a larger `limit` - ListFooter's page-length control. */
  async function setBackupsPageLength(pageLength) {
    store.backupsLimit.value = pageLength
    await _fetchBackups()
  }

  /** ListFooter's "Load More" - grows the page by one more page-length step. */
  async function loadMoreBackups() {
    store.backupsLimit.value += BACKUPS_PAGE_SIZE
    await _fetchBackups()
  }

  async function login() {
    return openSiteLogin(() => sitesApi.loginLink(name))
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

  async function uninstallApp(app, options) {
    return sitesApi.apps.remove(name, app, options)
  }

  async function saveConfig(config) {
    return sitesApi.configuration.update(name, config)
  }

  const installedApps = computed(() => store.site.value?.installed_apps || [])

  const status = computed(() => {
    if (!store.site.value) return 'unknown'
    // Provisioning wins over "offline": the site dir/site_config.json may not
    // exist yet in the earliest moments of a new-site/reinstall task.
    if (store.site.value.provisioning) return 'provisioning'
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
    backupsHasMore: store.backupsHasMore,
    backupsLimit: store.backupsLimit,
    installedApps,
    status,
    version,
    load,
    reload,
    loadApps,
    loadBackups,
    loadMoreBackups,
    setBackupsPageLength,
    login,
    backup,
    drop,
    reinstall,
    uninstallApp,
    saveConfig,
  }
}
