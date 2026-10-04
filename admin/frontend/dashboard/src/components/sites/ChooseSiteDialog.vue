<script setup lang="ts">
import { Dialog } from 'frappe-ui'

import SiteRow from '@/components/sites/SiteRow.vue'

import type { SiteResource } from '@/types/sites'

interface Props {
  sites?: SiteResource[]
}

withDefaults(defineProps<Props>(), {
  sites: () => [],
})
const open = defineModel<boolean>('open')
const site = defineModel<string>('site')

const siteMeta = (s: SiteResource) => {
  const count = s.active_apps?.length || 0
  return `${count} app${count === 1 ? '' : 's'}`
}

const choose = (name: string) => {
  site.value = name
  open.value = false
}
</script>

<template>
  <Dialog v-model="open" title="Choose site" size="md">
    <p v-if="!sites.length" class="py-6 text-ink-gray-5 text-p-sm text-center">
      No sites on this bench yet. Create a site to install apps.
    </p>

    <div v-else class="gap-1.5 grid max-h-96 overflow-y-auto">
      <SiteRow label="All sites" icon="lucide-layout-grid" :selected="!site" @click="choose('')">
        <template #suffix>
          <span v-if="!site" class="size-4 shrink-0 lucide-check" />
        </template>
      </SiteRow>

      <SiteRow
        v-for="s in sites"
        :key="s.name"
        :label="s.name"
        :selected="s.name === site"
        @click="choose(s.name)"
      >
        <template #suffix>
          <span class="w-20 text-ink-gray-5 text-sm text-right shrink-0">
            {{ siteMeta(s) }}
          </span>

          <span v-if="s.name === site" class="size-4 shrink-0 lucide-check" />
        </template>
      </SiteRow>
    </div>
  </Dialog>
</template>
