import { ref, computed } from 'vue'
import { appsApi } from '@/api/apps'
import { settingsApi } from '@/api/settings'
import { parseBranchVersion } from '@/utils/format'

const COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']

export function logoColor(name) {
  let hash = 0
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) | 0
  return COLORS[Math.abs(hash) % COLORS.length]
}

function parseVersion(branch) {
  const match = /^version-(\d+)/.exec(branch || '')
  return match ? Number(match[1]) : null
}

function parseBenchBranch(branch) {
  const version = parseVersion(branch)
  if (version !== null) return { version, label: `v${version}` }
  if (branch === 'develop') return { version: null, label: 'Nightly' }
  return { version: null, label: parseBranchVersion(branch) || null }
}

export function isFrappeApp(app) {
  return Boolean(app.repo?.includes('github.com/frappe/'))
}


function sortApps(a, b) {
  if (a.installed !== b.installed) return a.installed ? -1 : 1
  const as = a.stars ?? -1
  const bs = b.stars ?? -1
  if (as !== bs) return bs - as
  return (a.title || a.name).localeCompare(b.title || b.name)
}

// Maps an app's branches to how it relates to the current bench version.
function compatibility(app, benchVersion) {
  const versions = (app.branches ?? []).map(parseVersion).filter((v) => v !== null)
  if (benchVersion === null) return { compatible: true, label: parseBranchVersion(app.branch) }

  const supported = versions.filter((v) => v <= benchVersion)
  if (supported.length) return { compatible: true, label: `v${Math.max(...supported)}` }
  if (versions.length) return { compatible: false, needs: Math.min(...versions) }
  return { compatible: true, label: parseBranchVersion(app.branch) || 'latest' }
}

export function useMarketplace() {
  const registry = ref([])
  const installedNames = ref(new Set())
  const benchName = ref('')
  const benchVersion = ref(null)
  const benchVersionLabel = ref(null)
  const loading = ref(true)
  const error = ref('')

  const search = ref('')
  const selectedCategory = ref('All categories')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const [registryData, installed, settings] = await Promise.all([
        appsApi.registry(),
        appsApi.installed(),
        settingsApi.get(),
      ])
      registry.value = registryData
      installedNames.value = new Set(installed.map((app) => app.name))
      benchName.value = settings.bench?.name || 'this bench'
      const benchBranch =
        parseBenchBranch(settings.bench?.default_branch) ||
        parseBenchBranch(installed.find((app) => app.name === 'frappe')?.branch)
      benchVersion.value = benchBranch.version
      benchVersionLabel.value = benchBranch.label
    } catch (caught) {
      error.value = caught.message || 'Failed to load marketplace'
    } finally {
      loading.value = false
    }
  }

  const categories = computed(() => {
    const unique = [...new Set(registry.value.map((app) => app.category).filter(Boolean))].sort()
    return ['All categories', ...unique]
  })

  const matchingApps = computed(() => {
    const query = search.value.toLowerCase().trim()
    return registry.value
      .filter(
        (app) =>
          selectedCategory.value === 'All categories' || app.category === selectedCategory.value,
      )
      .filter(
        (app) =>
          !query ||
          app.title?.toLowerCase().includes(query) ||
          app.description?.toLowerCase().includes(query),
      )
      .map((app) => ({
        ...app,
        installed: installedNames.value.has(app.name),
        ...compatibility(app, benchVersion.value),
      }))
  })

  const frappeApps = computed(() => matchingApps.value.filter(isFrappeApp).sort(sortApps))
  const communityApps = computed(() =>
    matchingApps.value.filter((app) => !isFrappeApp(app)).sort(sortApps),
  )

  return {
    loading,
    error,
    search,
    selectedCategory,
    categories,
    benchName,
    benchVersion,
    benchVersionLabel,
    frappeApps,
    communityApps,
    load,
  }
}
