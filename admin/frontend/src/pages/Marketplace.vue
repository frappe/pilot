<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Badge, Dialog, FormControl, LoadingText, ErrorMessage, TextInput } from 'frappe-ui'

const router = useRouter()
const registry = ref([])
const installedNames = ref(new Set())
const loading = ref(true)
const error = ref('')
const search = ref('')

const filteredRegistry = computed(() => {
  const q = search.value.toLowerCase().trim()
  if (!q) return registry.value
  return registry.value.filter(a =>
    a.title.toLowerCase().includes(q) || a.description?.toLowerCase().includes(q)
  )
})

const COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']
function hashColor(name) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [regRes, appsRes] = await Promise.all([
      fetch('/api/apps/registry'),
      fetch('/api/apps/'),
    ])
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
const installBranch = ref('')
const installing = ref(false)
const installError = ref('')

function openInstall(app) {
  installApp.value = app
  installBranch.value = app.branches[0] ?? app.branch
  installing.value = false
  installError.value = ''
  showInstall.value = true
}

async function doInstall() {
  if (!installApp.value) return
  installing.value = true
  installError.value = ''
  try {
    const res = await fetch('/api/apps/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: installApp.value.name,
        repo: installApp.value.repo,
        branch: installBranch.value,
      }),
    })
    const d = await res.json()
    if (d.ok) { showInstall.value = false; router.push(`/tasks/${d.task_id}`) }
    else installError.value = d.error
  } catch (e) {
    installError.value = e.message
  } finally {
    installing.value = false
  }
}

const branchOptions = computed(() =>
  (installApp.value?.branches ?? []).map(b => ({ label: b, value: b }))
)

onMounted(load)
</script>

<template>
  <div class="mx-auto flex max-w-2xl flex-col gap-4">
    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="error" :message="error" />

    <template v-else>
      <TextInput v-model="search" placeholder="Search apps…" />
      <div
        v-for="app in filteredRegistry"
        :key="app.name"
        class="flex items-start gap-4 rounded-lg border border-outline-gray-1 bg-surface-white px-4 py-3 shadow-sm"
      >
        <!-- Logo -->
        <div
          class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg overflow-hidden"
          :style="app.logo_url ? {} : { background: hashColor(app.name) }"
        >
          <img v-if="app.logo_url" :src="app.logo_url" :alt="app.title" class="h-full w-full object-contain" />
          <span v-else class="text-sm font-bold text-white leading-none">{{ app.title[0].toUpperCase() }}</span>
        </div>

        <!-- Info -->
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <span class="font-medium text-ink-gray-9">{{ app.title }}</span>
            <div class="flex gap-1">
              <Badge
                v-for="b in app.branches"
                :key="b"
                :label="b"
                theme="gray"
                size="sm"
              />
            </div>
          </div>
          <p v-if="app.description" class="mt-0.5 text-sm leading-relaxed text-ink-gray-5 line-clamp-2">{{ app.description }}</p>
        </div>

        <!-- Action -->
        <div class="shrink-0">
          <Badge v-if="installedNames.has(app.name)" label="Installed" theme="green" />
          <Button v-else variant="outline" size="sm" @click="openInstall(app)">Add</Button>
        </div>
      </div>
    </template>

    <Dialog v-model="showInstall" :options="{ title: `Add ${installApp?.title}` }">
      <template #body-content>
        <div class="flex flex-col gap-4">
          <FormControl
            v-if="branchOptions.length > 1"
            label="Branch"
            type="select"
            v-model="installBranch"
            :options="branchOptions"
          />
          <p v-else class="text-sm text-ink-gray-6">Branch: <span class="font-medium text-ink-gray-9">{{ installBranch }}</span></p>
          <ErrorMessage v-if="installError" :message="installError" />
          <div class="flex justify-end gap-2">
            <Button variant="ghost" @click="showInstall = false">Cancel</Button>
            <Button variant="solid" :loading="installing" @click="doInstall">Add App</Button>
          </div>
        </div>
      </template>
    </Dialog>
  </div>
</template>
