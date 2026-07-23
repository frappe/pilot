<template>
  <Dialog v-model="open" :options="{ title: 'Which site are you browsing for?', size: 'md' }">
    <template #body-content>
      <p v-if="!sites.length" class="py-6 text-ink-gray-5 text-sm text-center">
        No sites on this bench yet. Create a site to install apps.
      </p>

      <template v-else>
        <p class="mb-4 text-ink-gray-6 text-p-sm">
          Installed apps are marked, and installs target this site.
        </p>

        <div class="gap-2 grid max-h-96 overflow-y-auto">
          <button type="button"
            class="flex items-center gap-3 p-3 border rounded-lg text-left transition duration-150 ease-[var(--ease-out)] active:scale-[0.98]"
            :class="rowClass('')"
            @click="choose('')">
            <span class="place-items-center grid bg-surface-gray-2 rounded-md size-8 shrink-0">
              <span class="size-4 text-ink-gray-6 lucide-layout-grid" />
            </span>
            <div class="flex-1 min-w-0">
              <p class="font-medium text-ink-gray-8 text-sm truncate">All sites</p>
              <p class="text-ink-gray-5 text-p-sm truncate">Browse every available app</p>
            </div>
            <span v-if="!site" class="size-4 text-ink-gray-8 shrink-0 lucide-check" />
          </button>

          <button v-for="s in sites" :key="s.name" type="button"
            class="flex items-center gap-3 p-3 border rounded-lg text-left transition duration-150 ease-[var(--ease-out)] active:scale-[0.98]"
            :class="rowClass(s.name)"
            @click="choose(s.name)">
            <span class="place-items-center grid bg-surface-gray-2 rounded-md size-8 shrink-0">
              <span class="size-4 text-ink-gray-6 lucide-globe" />
            </span>
            <div class="flex-1 min-w-0">
              <p class="font-medium text-ink-gray-8 text-sm truncate">{{ s.name }}</p>
              <p class="text-ink-gray-5 text-p-sm truncate">
                {{ s.installed_apps?.length || 0 }} app{{ s.installed_apps?.length === 1 ? '' : 's' }}
                <template v-if="siteVersion(s)"> · {{ siteVersion(s) }}</template>
              </p>
            </div>
            <span v-if="s.name === site" class="size-4 text-ink-gray-8 shrink-0 lucide-check" />
          </button>
        </div>
      </template>
    </template>
  </Dialog>
</template>

<script setup>
import { Dialog } from 'frappe-ui'

defineProps({
  sites: { type: Array, default: () => [] },
})
const open = defineModel('open')
const site = defineModel('site')

function rowClass(name) {
  return name === site.value
    ? 'border-outline-gray-4 bg-surface-gray-1'
    : 'border-outline-gray-2 hover:bg-surface-gray-1'
}

function siteVersion(s) {
  const match = /^version-(\d+)/.exec(s.framework_branch || '')
  return match ? `Version ${match[1]}` : ''
}

function choose(name) {
  site.value = name
  open.value = false
}
</script>
