<script setup>
import { ref, computed, onMounted } from 'vue'
import { Button, Badge, Checkbox, Dialog, LoadingText, ErrorMessage, TextInput } from 'frappe-ui'
import { useTaskProgress } from '../composables/useTaskProgress.js'

const { watchTask } = useTaskProgress()
const registry = ref([])
const installedNames = ref(new Set())
const loading = ref(true)
const error = ref('')
const search = ref('')
const selectedCategory = ref('All')

const CATEGORIES = [
  'All',
  'Applications',
  'Extensions',
  'Integrations',
  'Compliance',
  'Developer Tools',
  'Utilities',
]

const COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']
function hashColor(name) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

function isFrappe(app) {
  return Boolean(app.repo?.includes('github.com/frappe/'))
}

const sortedRegistry = computed(() =>
  [...registry.value].sort((a, b) => {
    if (a.is_installable !== b.is_installable) return a.is_installable ? -1 : 1
    const af = isFrappe(a), bf = isFrappe(b)
    if (af !== bf) return af ? -1 : 1
    const as = a.stars ?? -1, bs = b.stars ?? -1
    if (as !== bs) return bs - as
    return (a.title || a.name).localeCompare(b.title || b.name)
  })
)

const categoryCounts = computed(() => {
  const counts = { All: registry.value.length }
  for (const cat of CATEGORIES.slice(1)) {
    counts[cat] = registry.value.filter(a => a.category === cat).length
  }
  return counts
})

const filteredRegistry = computed(() => {
  let apps = sortedRegistry.value
  if (selectedCategory.value !== 'All') {
    apps = apps.filter(a => a.category === selectedCategory.value)
  }
  const q = search.value.toLowerCase().trim()
  if (q) {
    apps = apps.filter(a =>
      a.title?.toLowerCase().includes(q) || a.description?.toLowerCase().includes(q)
    )
  }
  return apps
})

const hasMixedGroups = computed(() =>
  filteredRegistry.value.some(isFrappe) && filteredRegistry.value.some(a => !isFrappe(a))
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [regRes, appsRes] = await Promise.all([
      fetch('/api/apps/marketplace'),
      fetch('/api/apps/'),
    ])
    if (!regRes.ok) throw new Error(`Marketplace unavailable (${regRes.status})`)
    if (!appsRes.ok) throw new Error(`Apps unavailable (${appsRes.status})`)
    registry.value = await regRes.json()
    const apps = await appsRes.json()
    installedNames.value = new Set(apps.map(a => a.name))
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// Install dialog
const showInstall = ref(false)
const installApp = ref(null)
const installing = ref(false)
const installError = ref('')
const sites = ref([])
const sitesLoading = ref(false)
const selectedSites = ref([])

async function openInstall(app) {
  installApp.value = app
  installing.value = false
  installError.value = ''
  selectedSites.value = []
  showInstall.value = true
  sitesLoading.value = true
  try {
    const res = await fetch('/api/sites/')
    const all = await res.json()
    sites.value = all.filter(s => s.exists && !s.broken && !s.installed_apps?.includes(app.name))
  } catch { sites.value = [] }
  finally { sitesLoading.value = false }
}

async function doInstall() {
  if (!installApp.value) return
  installing.value = true
  installError.value = ''
  try {
    const endpoint = selectedSites.value.length ? '/api/apps/get-and-install' : '/api/apps/add'
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: installApp.value.name,
        repo: installApp.value.repo,
        target: installApp.value.target,
        sites: selectedSites.value,
      }),
    })
    const d = await res.json()
    if (d.ok) { showInstall.value = false; watchTask(d.task_id) }
    else installError.value = d.error
  } catch (e) {
    installError.value = e.message
  } finally {
    installing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-4 md:flex-row md:gap-6">
    <!-- Category sidebar: horizontal scroll on mobile, fixed column from md up -->
    <nav class="flex gap-1 overflow-x-auto pb-1 md:w-44 md:shrink-0 md:sticky md:top-0 md:h-fit md:flex-col md:gap-0.5 md:overflow-visible md:pb-0 md:pt-0.5">
      <button
        v-for="cat in CATEGORIES"
        :key="cat"
        @click="selectedCategory = cat"
        :class="[
          'flex shrink-0 items-center gap-1.5 rounded px-2.5 py-1.5 text-left text-sm transition-colors md:w-full md:justify-between',
          selectedCategory === cat
            ? 'bg-surface-gray-3 font-medium text-ink-gray-9'
            : 'text-ink-gray-6 hover:bg-surface-gray-2 hover:text-ink-gray-8',
        ]"
      >
        <span>{{ cat }}</span>
        <span v-if="!search" class="text-xs text-ink-gray-4">{{ categoryCounts[cat] }}</span>
      </button>
    </nav>

    <!-- Main content -->
    <div class="flex-1 flex flex-col gap-2.5 min-w-0">
      <TextInput v-model="search" placeholder="Search apps…" class="mb-1" />

      <LoadingText v-if="loading" />
      <div v-else-if="error" class="flex flex-col items-center gap-3 py-10 text-center">
        <p class="text-sm font-medium text-ink-red-3">Failed to load marketplace</p>
        <p class="text-xs text-ink-gray-5">{{ error }}</p>
        <Button variant="outline" size="sm" @click="load">Retry</Button>
      </div>

      <template v-else>
        <template v-for="(app, i) in filteredRegistry" :key="app.name">
          <!-- Section labels (only shown when both frappe + community apps are present) -->
          <p
            v-if="hasMixedGroups && i === 0 && isFrappe(app)"
            class="text-xs font-medium uppercase tracking-wide text-ink-gray-4 mt-1 mb-0.5"
          >
            From Frappe
          </p>
          <p
            v-if="hasMixedGroups && !isFrappe(app) && (i === 0 || isFrappe(filteredRegistry[i - 1]))"
            class="text-xs font-medium uppercase tracking-wide text-ink-gray-4 mt-2 mb-0.5"
          >
            Community
          </p>

          <!-- App card -->
          <div
            class="flex items-center gap-4 rounded-lg border px-4 py-3 shadow-sm"
            :class="app.is_installable
              ? 'border-outline-gray-1 bg-surface-white'
              : 'border-outline-gray-1 bg-surface-gray-1 opacity-60'"
          >
            <!-- Logo -->
            <div
              class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg overflow-hidden"
              :style="app.logo_url ? {} : { background: hashColor(app.name) }"
            >
              <img v-if="app.logo_url" :src="app.logo_url" :alt="app.title" class="h-full w-full object-contain" />
              <span v-else class="text-sm font-bold text-white leading-none">{{ app.title?.[0]?.toUpperCase() }}</span>
            </div>

            <!-- Info -->
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-medium text-ink-gray-9">{{ app.title }}</span>
                <Badge v-if="isFrappe(app)" label="Frappe" theme="gray" size="sm" />
                <Badge v-if="app.target" :label="app.target" theme="gray" size="sm" />
                <Badge v-if="app.version" :label="`v${app.version}`" theme="blue" size="sm" />
              </div>
              <p v-if="app.description" class="mt-0.5 text-sm leading-relaxed text-ink-gray-5 line-clamp-2">
                {{ app.description }}
              </p>
              <p v-if="!app.is_installable && app.required_version" class="mt-0.5 text-xs text-ink-gray-4">
                Requires Frappe {{ app.required_version }}
              </p>
            </div>

            <!-- Action -->
            <div class="flex shrink-0 items-center gap-2">
              <Badge v-if="installedNames.has(app.name)" label="Installed" theme="green" />
              <Button v-else-if="app.is_installable && app.repo" variant="outline" size="sm" @click="openInstall(app)">Add</Button>
            </div>
          </div>
        </template>

        <p v-if="filteredRegistry.length === 0" class="text-sm text-ink-gray-5 py-4 text-center">
          No apps found.
        </p>
      </template>
    </div>

    <!-- Install dialog -->
    <Dialog v-model="showInstall" :options="{ title: `Add ${installApp?.title}` }">
      <template #body-content>
        <div class="flex flex-col gap-4">
          <div class="flex gap-4 text-sm text-ink-gray-6">
            <span>Version: <span class="font-medium text-ink-gray-9">{{ installApp?.version }}</span></span>
            <span>{{ installApp?.target_type }}: <span class="font-medium text-ink-gray-9">{{ installApp?.target }}</span></span>
          </div>

          <!-- Site selection -->
          <div class="flex flex-col gap-2">
            <p class="text-sm font-medium text-ink-gray-7">Also install on sites</p>
            <LoadingText v-if="sitesLoading" />
            <p v-else-if="!sites.length" class="text-sm text-ink-gray-4">No sites available.</p>
            <div v-else class="flex flex-col">
              <label
                v-for="s in sites"
                :key="s.name"
                class="flex cursor-pointer items-center gap-2.5 rounded px-2.5 py-1.5 hover:bg-surface-gray-3 transition-colors"
              >
                <Checkbox
                  :modelValue="selectedSites.includes(s.name)"
                  @update:modelValue="val => val
                    ? (selectedSites.includes(s.name) || selectedSites.push(s.name))
                    : selectedSites.splice(selectedSites.indexOf(s.name), 1)"
                />
                <span class="text-sm font-medium select-none text-ink-gray-8">{{ s.name }}</span>
              </label>
            </div>
          </div>

          <div v-if="installError" class="rounded-md border border-outline-red-1 bg-surface-red-1 px-3 py-2.5">
            <p v-for="(line, i) in installError.split('\n')" :key="i" class="text-sm text-ink-red-3">{{ line }}</p>
          </div>
          <div class="flex justify-end gap-2">
            <Button variant="ghost" @click="showInstall = false">Cancel</Button>
            <Button variant="solid" :loading="installing" @click="doInstall">Add App</Button>
          </div>
        </div>
      </template>
    </Dialog>
  </div>
</template>
