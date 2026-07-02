<template>
  <Dialog v-model="open" :options="{ title: 'Choose site', size: 'md' }">
    <template #body-content>
      <div class="gap-2 grid px-2 max-h-96 overflow-y-auto">
        <button v-for="s in sites" :key="s.name" type="button"
          class="flex items-center gap-3 p-3 border rounded-lg text-left transition-colors" :class="s.name === site
            ? 'border-outline-gray-4 bg-surface-gray-1 ring-1 ring-outline-gray-4'
            : 'border-outline-gray-2 hover:bg-surface-gray-1'" @click="choose(s.name)">
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

        <p v-if="!sites.length" class="py-6 text-ink-gray-5 text-sm text-center">
          No sites available on this bench.
        </p>
      </div>
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

function siteVersion(s) {
  const match = /^version-(\d+)/.exec(s.site_config?.frappe_branch || '')
  return match ? `Version ${match[1]}` : ''
}

function choose(name) {
  site.value = name
  open.value = false
}
</script>
