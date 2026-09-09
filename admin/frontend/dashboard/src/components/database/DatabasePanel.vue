<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Badge, Button } from 'frappe-ui'

import Collapsable from '@/components/common/Collapsable.vue'

interface Props {
  title: string
  subtitle?: string
  badge?: string | any[]
  loading?: boolean
  hideChevron?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  subtitle: '',
  badge: '',
  loading: false,
  hideChevron: false,
})

const emit = defineEmits(['refresh', 'open'])

const badges = computed(() => [props.badge].flat().filter(Boolean))

/** A panel with no chevron has nothing to click, so it stays open. */
const opened = ref(props.hideChevron)

// Callers load on first expand, so re-opening must not refetch.
const wasOpened = ref(props.hideChevron)

watch(opened, (isOpen) => {
  if (!isOpen || wasOpened.value) return
  wasOpened.value = true
  emit('open')
})
</script>

<template>
  <div class="bg-surface-white border rounded-6 border-outline-gray-2 fade-in">
    <Collapsable v-model:opened="opened">
      <template #header="{ toggle }">
        <div
          class="flex flex-wrap items-center gap-x-2 p-4"
          :class="hideChevron ? '' : 'cursor-pointer'"
          @click="hideChevron || toggle()"
        >
          <h3 class="font-semibold leading-7">{{ title }}</h3>

          <Badge v-for="label in badges" :key="label" :label="label" size="sm" />

          <div v-if="opened" class="flex items-center gap-3 ml-auto shrink-0" @click.stop>
            <slot name="actions" />

            <Button
              icon="lucide-refresh-cw"
              label="Refresh"
              tooltip="Refresh"
              :loading="loading"
              @click="$emit('refresh')"
            />
          </div>

          <span
            v-if="!hideChevron"
            class="size-4 text-ink-gray-5 transition-transform shrink-0 lucide-chevron-up"
            :class="[opened ? '' : 'ml-auto rotate-180']"
          />

          <p v-if="subtitle" class="mt-1 w-full text-ink-gray-5 text-sm">{{ subtitle }}</p>
        </div>
      </template>

      <slot />
    </Collapsable>
  </div>
</template>
