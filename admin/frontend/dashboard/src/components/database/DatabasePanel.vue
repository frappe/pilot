<script setup lang="ts">
import { Badge, Button } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

interface Props {
  title?: string
  active?: boolean
  badge?: string | string[]
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  title: '',
  active: false,
  badge: '',
  loading: false,
})

const emit = defineEmits(['refresh', 'open'])

const badges = computed(() => [props.badge].flat().filter(Boolean))

// Callers load on first open, so coming back must not refetch.
const wasOpened = ref(false)

watch(
  () => props.active,
  (isActive) => {
    if (!isActive || wasOpened.value) return

    wasOpened.value = true
    emit('open')
  },
  { immediate: true },
)
</script>

<template>
  <section v-show="active" class="flex flex-col gap-5 h-full min-h-0 fade-in">
    <header v-if="$slots.start || title" class="flex flex-wrap items-center gap-x-2 min-h-7">
      <slot name="start" />

      <h3 v-if="title" class="text-ink-gray-5 text-sm">{{ title }}</h3>
    </header>

    <Teleport v-if="active" defer to="#analyzer-actions">
      <Badge v-for="label in badges" :key="label" :label="label" size="sm" />

      <slot name="actions" />

      <Button
        variant="ghost"
        icon="lucide-refresh-cw"
        label="Refresh"
        tooltip="Refresh"
        :loading="loading"
        @click="$emit('refresh')"
      />
    </Teleport>

    <slot />
  </section>
</template>
