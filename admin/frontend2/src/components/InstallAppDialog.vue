<template>
  <Dialog v-model="open" :options="{ title: `Install ${app?.title || ''}`, size: 'md' }">
    <template #body-content>
      <div class="gap-2 grid max-h-96 overflow-y-auto">
        <button type="button"
          class="flex items-center gap-3 hover:bg-surface-gray-1 p-3 border rounded-lg border-outline-gray-2 text-left transition-colors"
          :disabled="!installableSites.length" @click="installOnAllSites">
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
          class="flex items-center gap-3 p-3 border rounded-lg min-w-0 text-left transition-colors" :class="isInstalled(s)
            ? 'border-outline-gray-2 opacity-60 cursor-not-allowed'
            : 'border-outline-gray-2 hover:bg-surface-gray-1'" :disabled="isInstalled(s)" @click="installOnSite(s)">
          <span class="place-items-center grid bg-surface-gray-2 rounded-md size-8 shrink-0">
            <span class="size-4 text-ink-gray-6 lucide-globe" />
          </span>
          <div class="flex-1 min-w-0">
            <p class="font-medium text-ink-gray-8 text-sm truncate">{{ s.name }}</p>
            <p class="text-ink-gray-5 text-p-sm truncate">
              {{ s.name }} · {{ isInstalled(s) ? 'already installed' : siteVersion(s) || 'latest' }}
            </p>
          </div>
          <span v-if="installingNames.has(s.name)"
            class="size-4 text-ink-gray-5 animate-spin shrink-0 lucide-loader-circle" />
        </button>

        <p v-if="!sites.length" class="py-6 text-ink-gray-5 text-sm text-center">
          No sites available on this bench.
        </p>
      </div>

      <ErrorMessage v-if="error" :message="error" class="mt-3" />
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, Dialog, ErrorMessage } from 'frappe-ui'
import { sitesApi } from '@/api/sites'
import { tasksApi } from '@/api/tasks'

const props = defineProps({
  app: { type: Object, default: null },
  sites: { type: Array, default: () => [] },
})
const open = defineModel('open')

const installingNames = ref(new Set())
const error = ref('')

const installableSites = computed(() => props.sites.filter((s) => !isInstalled(s)))

function isInstalled(site) {
  return Boolean(props.app && site.installed_apps?.includes(props.app.name))
}

function siteVersion(site) {
  const match = /^version-(\d+)/.exec(site.site_config?.frappe_branch || '')
  return match ? `Version ${match[1]}` : ''
}

async function waitForTask(taskId) {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1500))
    const { task } = await tasksApi.detail(taskId)
    if (task.status === 'running') continue
    if (task.status !== 'success') throw new Error('Install failed. Check the app logs for details.')
    return
  }
}

async function runInstall(site) {
  try {
    const result = await sitesApi.apps.getAndInstall(site.name, {
      app: props.app.name,
      repo: props.app.repo,
      branch: props.app.branch || '',
    })
    if (!result.ok) throw new Error(result.error || `Could not install on ${site.name}.`)
    await waitForTask(result.task_id)
    if (!site.installed_apps.includes(props.app.name)) site.installed_apps.push(props.app.name)
  } catch (caught) {
    error.value = caught.message || `Could not install on ${site.name}.`
  } finally {
    installingNames.value.delete(site.name)
  }
}

function installOnSite(site) {
  if (isInstalled(site) || installingNames.value.has(site.name)) return
  error.value = ''
  installingNames.value.add(site.name)
  runInstall(site)
}

async function installOnAllSites() {
  const targets = installableSites.value.filter((s) => !installingNames.value.has(s.name))
  if (!targets.length) return
  error.value = ''
  targets.forEach((s) => installingNames.value.add(s.name))
  for (const site of targets) {
    await runInstall(site)
  }
}
</script>
