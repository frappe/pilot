<template>
  <Dialog v-model="open" :title="`Install ${appLabel}`" size="md">
    <template #default>
      <div class="space-y-5">
        <p v-if="presetSite" class="text-ink-gray-7 text-sm">
          Install <strong>{{ appLabel }}</strong> on <strong>{{ presetSite.name }}</strong>?
          <span v-if="presetInstalled" class="block mt-1 text-ink-gray-5">Already installed on this site.</span>
        </p>

        <div v-else class="gap-2 grid max-h-96 overflow-y-auto">
          <button type="button"
            class="flex items-center gap-3 p-3 border rounded-lg text-left transition duration-150 ease-[var(--ease-out)] active:scale-[0.98]"
            :class="rowClass('all')" :disabled="!installableSites.length" @click="selection = 'all'">
            <span class="place-items-center grid bg-surface-gray-2 rounded-md size-8 shrink-0">
              <span class="lucide-layout-grid size-4 text-ink-gray-6" />
            </span>
            <div class="flex-1 min-w-0">
              <p class="font-medium text-ink-gray-8 text-sm">All sites</p>
              <p class="text-ink-gray-5 text-p-sm truncate">
                Installs on {{ installableSites.length }} site{{ installableSites.length === 1 ? '' : 's' }}
              </p>
            </div>
          </button>

          <button v-for="s in sites" :key="s.name" type="button"
            class="flex items-center gap-3 p-3 border rounded-lg min-w-0 text-left transition duration-150 ease-[var(--ease-out)] active:scale-[0.98]"
            :class="isInstalled(s) ? 'border-outline-gray-2 opacity-60 cursor-not-allowed' : rowClass(s.name)"
            :disabled="isInstalled(s)" @click="selection = s.name">
            <span class="place-items-center grid bg-surface-gray-2 rounded-md size-8 shrink-0">
              <span class="size-4 text-ink-gray-6 lucide-globe" />
            </span>
            <div class="flex-1 min-w-0">
              <p class="font-medium text-ink-gray-8 text-sm truncate">{{ s.name }}</p>
              <p class="text-ink-gray-5 text-p-sm truncate">
                {{ s.name }} · {{ isInstalled(s) ? 'already installed' : siteVersion(s) || 'latest' }}
              </p>
            </div>
          </button>

          <p v-if="!sites.length" class="py-6 text-ink-gray-5 text-sm text-center">
            No sites available on this bench.
          </p>
        </div>

        <ErrorMessage v-if="error" :message="error" />

        <div class="flex justify-end gap-2">
          <Button variant="subtle" @click="open = false">Cancel</Button>
          <Button variant="solid" :disabled="!selection || presetInstalled" :loading="installing"
            @click="confirmInstall">
            Install
          </Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dialog, ErrorMessage } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { openTaskDetailPage } from '@/utils/taskRoute'

const props = defineProps({
  app: { type: Object, default: null },
  sites: { type: Array, default: () => [] },
  siteName: { type: String, default: '' },
})
const open = defineModel('open')
const router = useRouter()

const selection = ref(null)
const installing = ref(false)
const error = ref('')

const appLabel = computed(() => props.app?.title || props.app?.name || '')

const presetSite = computed(() => props.sites.find((s) => s.name === props.siteName) || null)
const presetInstalled = computed(() => Boolean(presetSite.value && isInstalled(presetSite.value)))

watch(open, (isOpen) => {
  if (!isOpen) return
  selection.value = props.siteName || null
  error.value = ''
})

const installableSites = computed(() => props.sites.filter((s) => !isInstalled(s)))

function isInstalled(site) {
  return Boolean(props.app && site.installed_apps?.includes(props.app.name))
}

function siteVersion(site) {
  const match = /^version-(\d+)/.exec(site.framework_branch || '')
  return match ? `Version ${match[1]}` : ''
}

function rowClass(value) {
  return selection.value === value
    ? 'border-outline-gray-4 bg-surface-gray-1'
    : 'border-outline-gray-2 hover:bg-surface-gray-1'
}

async function startInstall(site) {
  const result = await sitesApi.apps.install(site.name, {
    app: props.app.name,
  })
  if (!result.task_id) throw new Error(apiErrorMessage(result, `Could not install on ${site.name}.`))
  return result.task_id
}

async function installOnSite(name) {
  const site = props.sites.find((s) => s.name === name)
  if (!site) return
  const taskId = await startInstall(site)
  open.value = false
  openTaskDetailPage(router, taskId)
}

async function installOnAllSites() {
  const targets = installableSites.value
  if (!targets.length) return
  await Promise.all(targets.map((site) => startInstall(site)))
  open.value = false
  router.push({ name: 'Tasks' })
}

async function confirmInstall() {
  if (!selection.value || installing.value) return
  error.value = ''
  installing.value = true
  try {
    if (selection.value === 'all') await installOnAllSites()
    else await installOnSite(selection.value)
  } catch (caught) {
    error.value = caught.message || 'Could not start install.'
  } finally {
    installing.value = false
  }
}
</script>
