<template>
  <Dialog v-model="open" title="New Site" size="lg">
    <template #default>

      <div v-if="loading" class="flex justify-center items-center h-80">
        <span class="size-6 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
      </div>
      <div v-else @pointerdown.stop class="space-y-5">

        <!-- Site name -->
        <div>
          <!-- Without wildcard site name -->
          <FormControl v-if="!wildcardDomains.length" v-model="newSiteName" label="Site name" type="text"
            placeholder="mysite.localhost" @keyup.enter="submit" />
          <div v-else class="space-y-1.5">
            <span class="text-ink-gray-7 text-p-sm-medium">Site name</span>
            <div class="flex items-stretch gap-2">
              <!-- Site prefix -->
              <FormControl v-model="sitePrefix" class="flex-1 min-w-0" type="text" placeholder="mysite"
                @keyup.enter="submit" />
              <!-- Multiple wildcards available -->
              <FormControl v-if="wildcardDomains.length > 1" v-model="selectedSuffix" class="w-48 shrink-0"
                type="select" :options="wildcardDomains.map((d) => ({ label: d, value: d }))" />
              <span v-else class="flex items-center text-ink-gray-6 text-sm whitespace-nowrap shrink-0">
                {{ wildcardDomains[0] }}
              </span>
            </div>
            <!-- Example site name -->
            <p class="mt-1.5 text-ink-gray-5 text-p-sm"> Web address:
              <span class="font-medium text-ink-gray-7">{{ newSiteName || `mysite${selectedSuffix}` }}</span>
            </p>
          </div>
        </div>

        <!-- DB type -->
        <div v-if="benchDbType" class="space-y-1.5">
          <span class="text-ink-gray-7 text-p-sm-medium">Database</span>
          <div class="gap-2 grid grid-cols-2">
            <!-- Benche's default production DB -->
            <button type="button" class="flex items-center gap-3 p-3 border rounded-lg text-left transition-all" :class="dbType !== 'sqlite'
              ? 'border-outline-gray-4 bg-surface-gray-1 ring-1 ring-outline-gray-4'
              : 'border-outline-gray-2 hover:bg-surface-gray-1'"
              @click="dbType = benchDbType !== 'sqlite' ? benchDbType : 'mariadb'">
              <div>
                <span class="block font-medium text-ink-gray-9 text-sm">
                  {{ benchDbType === 'postgres' ? 'PostgreSQL' : 'MariaDB' }}
                </span>
                <span class="text-ink-gray-5 text-xs">Recommended</span>
              </div>
            </button>
            <!-- SQite experimental -->
            <button type="button" class="flex items-center gap-3 p-3 border rounded-lg text-left transition-all" :class="dbType === 'sqlite'
              ? 'border-outline-gray-4 bg-surface-gray-1 ring-1 ring-outline-gray-4'
              : 'border-outline-gray-2 hover:bg-surface-gray-1'" @click="dbType = 'sqlite'">
              <div>
                <span class="block font-medium text-ink-gray-9 text-sm">SQLite</span>
                <span class="text-ink-gray-5 text-xs">Experimental</span>
              </div>
            </button>
          </div>
        </div>

        <!-- Choose apps -->
        <div v-if="!loading && registry.length">
          <div class="flex justify-between items-center mb-2">
            <span class="text-ink-gray-7 text-p-sm-medium">Choose apps</span>
            <span class="text-ink-gray-5 text-xs">
              {{ selectedApps.length }} selected
            </span>
          </div>
          <FormControl v-model="appSearch" type="text" placeholder="Search apps..." class="mb-2" />
          <div class="gap-2 grid grid-cols-1 sm:grid-cols-2 p-1 max-h-72 overflow-y-auto">
            <button v-for="app in filteredRegistry" :key="app.name" type="button"
              class="flex items-center gap-3 p-3 border rounded-lg min-w-0 overflow-hidden text-left transition-colors"
              :class="selectedApps.includes(app.name)
                ? 'border-outline-gray-4 bg-surface-gray-1 ring-1 ring-outline-gray-4'
                : 'border-outline-gray-2 hover:bg-surface-gray-1'" @click="toggleApp(app.name)">
              <AppIcon :name="app.name" class="size-8 shrink-0" />
              <span class="flex-1 min-w-0 font-medium text-ink-gray-9 text-sm truncate">
                {{ app.title || app.name }}
              </span>
              <Checkbox :model-value="selectedApps.includes(app.name)" class="pointer-events-none shrink-0" />
            </button>
            <p v-if="!filteredRegistry.length" class="col-span-2 py-4 text-ink-gray-5 text-sm text-center">
              No apps match "{{ appSearch }}"
            </p>
          </div>
        </div>

        <!-- Just a note -->
        <p class="flex items-start gap-1.5 text-ink-gray-5 text-p-sm">
          <span class="mt-0.5 size-3.5 lucide-info shrink-0"></span>
          Runs on My Server - no extra cost; sites share its compute and storage.
        </p>

        <!-- Error message -->
        <ErrorMessage v-if="error" class="mt-1" :message="error" />

        <div class="flex justify-end gap-2">
          <Button variant="subtle" @click="open = false">Cancel</Button>
          <Button variant="solid" :loading="creating" @click="submit" :disabled="!newSiteName">Create Site</Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { Button, Checkbox, Dialog, ErrorMessage, FormControl } from 'frappe-ui'
import AppIcon from '@/components/AppIcon.vue'
import { authApi } from '@/api/auth'
import { sitesApi } from '@/api/sites'
import { useAppRegistry } from '@/composables/useAppRegistry'
import { isFrappeApp } from '@/composables/useMarketplace'

defineProps({
  sites: { type: Array, default: () => [] },
})

const emit = defineEmits(['started'])
const open = defineModel()

const { registry, load: loadRegistry } = useAppRegistry()

const newSiteName = ref('')
const sitePrefix = ref('')
const wildcardDomains = ref([])
const selectedSuffix = ref('')
const loading = ref(false)
const creating = ref(false)
const error = ref('')

const benchDbType = ref('')
const dbType = ref('mariadb')

const selectedApps = ref([])
const appSearch = ref('')

const filteredRegistry = computed(() => {
  const query = appSearch.value.toLowerCase().trim()
  return registry.value
    .filter((app) => app.name !== 'frappe')
    .filter(
      (app) =>
        !query ||
        app.name.toLowerCase().includes(query) ||
        (app.title || '').toLowerCase().includes(query),
    )
    .sort((a, b) => {
      const aFrappe = isFrappeApp(a)
      const bFrappe = isFrappeApp(b)
      if (aFrappe !== bFrappe) return aFrappe ? -1 : 1
      return (b.stars ?? -1) - (a.stars ?? -1)
    })
})

watch([sitePrefix, selectedSuffix], () => {
  if (wildcardDomains.value.length && sitePrefix.value) {
    newSiteName.value = `${sitePrefix.value.trim()}${selectedSuffix.value}`
  } else {
    newSiteName.value = ''
  }
})

watch(open, (visible) => {
  if (!visible) return
  reset()
})

async function reset() {
  newSiteName.value = ''
  sitePrefix.value = ''
  error.value = ''
  benchDbType.value = ''
  dbType.value = ''
  selectedApps.value = []
  appSearch.value = ''
  loading.value = true
  await Promise.all([loadWildcardDomains(), loadBenchDbType(), loadRegistry()])
  loading.value = false
}

function toggleApp(name) {
  const index = selectedApps.value.indexOf(name)
  if (index === -1) selectedApps.value.push(name)
  else selectedApps.value.splice(index, 1)
}

async function loadWildcardDomains() {
  try {
    const { domains } = await sitesApi.domains.wildcardList()
    wildcardDomains.value = domains || []
    selectedSuffix.value = wildcardDomains.value[0] || ''
  } catch {
    wildcardDomains.value = []
  }
}

async function loadBenchDbType() {
  try {
    const { db_type } = await authApi.status()
    benchDbType.value = db_type || 'mariadb'
    dbType.value = benchDbType.value
  } catch {
    benchDbType.value = ''
  }
}

function validate(name) {
  if (!name) return 'Site name is required.'
  if (!/^[a-zA-Z0-9][a-zA-Z0-9\-.]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$/.test(name))
    return 'Site name must be a valid hostname.'
  return null
}

async function submit() {
  const name = newSiteName.value.trim()
  const validationError = validate(name)
  if (validationError) {
    error.value = validationError
    return
  }

  creating.value = true
  error.value = ''
  try {
    const result = await sitesApi.create({
      name,
      db_type: dbType.value,
      apps: selectedApps.value,
    })
    if (result.ok) {
      open.value = false
      emit('started', result.task_id)
    } else {
      error.value = result.error || 'Could not create site.'
    }
  } catch (caught) {
    error.value = caught.message || 'Could not create site.'
  } finally {
    creating.value = false
  }
}
</script>