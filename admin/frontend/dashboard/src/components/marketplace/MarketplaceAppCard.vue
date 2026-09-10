<script setup lang="ts">
import { computed, ref } from 'vue'
import { Badge, Button, Dialog, Tooltip } from 'frappe-ui'

import AppIcon from '@/components/apps/AppIcon.vue'

interface Props {
  app: Record<string, any>
}

const props = defineProps<Props>()
defineEmits(['install'])

const showIncompatible = ref(false)

const requirementLabel = computed(() =>
  props.app.needs ? `Needs Frappe ${props.app.needs}` : 'Needs a newer Frappe version',
)

const incompatibleReason = computed(
  () =>
    `${props.app.title} requires ${props.app.needs ? `Frappe ${props.app.needs}` : 'a newer Frappe version'} to install.`,
)
</script>

<template>
  <div class="flex items-center gap-3">
    <AppIcon :name="app.name" :label="app.title" :logo="app.logo_url || ''" size="xl" />

    <div class="flex flex-1 justify-between items-center gap-2 py-2 min-w-0">
      <div class="min-w-0">
        <div class="flex items-center gap-1.5">
          <span class="font-medium text-ink-gray-8 truncate">{{ app.title }}</span>
          <span v-if="app.label" class="text-ink-gray-5 text-xs shrink-0">{{ app.label }}</span>
          <Badge v-if="app.nightly" label="Nightly" size="sm" />
        </div>

        <div class="mt-0.5 text-ink-gray-5 text-p-sm truncate">
          {{ app.description }}
        </div>
      </div>

      <slot name="actions">
        <Tooltip v-if="app.installed" text="Installed">
          <span class="place-items-center grid size-7 shrink-0" role="img" aria-label="Installed">
            <span class="size-4 lucide-check" />
          </span>
        </Tooltip>

        <Tooltip v-else-if="!app.compatible" :text="requirementLabel">
          <Button
            variant="ghost"
            label="Install"
            class="!text-ink-gray-4"
            @click="showIncompatible = true"
          >
            <template #icon><span class="size-4 lucide-download" /></template>
          </Button>
        </Tooltip>

        <Tooltip v-else :text="`Install ${app.title}`">
          <Button variant="ghost" label="Install" class="group" @click="$emit('install', app)">
            <template #icon>
              <span class="size-4 transition-transform duration-150 ease-[var(--ease-out)] group-active:scale-95 group-active:duration-100 lucide-download" />
            </template>
          </Button>
        </Tooltip>
      </slot>
    </div>

    <Dialog v-model="showIncompatible" title="Incompatible app" size="sm">
      <p class="text-ink-gray-7 text-p-sm">{{ incompatibleReason }}</p>
      <div class="flex flex-col gap-1.5 mt-3 text-sm">
        <div class="flex justify-between">
          <span class="text-ink-gray-5">Current version</span>
          <span class="font-medium text-ink-gray-8">{{ app.frappe_version || 'Unknown' }}</span>
        </div>

        <div class="flex justify-between">
          <span class="text-ink-gray-5">Required version</span>
          <span class="font-medium text-ink-gray-8">{{ app.needs || 'Not specified' }}</span>
        </div>
      </div>
    </Dialog>
  </div>
</template>
