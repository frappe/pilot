<script setup lang="ts">
import { computed, ref, watch } from 'vue'

interface Props {
  title: string
  url?: string
}

const props = withDefaults(defineProps<Props>(), { url: '' })

const hasFailed = ref(false)
watch(() => props.url, () => (hasFailed.value = false))

// Marketplace data is remote, so only an absolute web URL is shown as an image.
const safeUrl = computed(() => {
  const url = props.url.trim()

  return /^https?:\/\//i.test(url) && !hasFailed.value ? url : ''
})
</script>

<template>
  <img
    v-if="safeUrl"
    class="size-6 rounded-2 object-cover shrink-0"
    :src="safeUrl"
    :alt="title"
    loading="lazy"
    decoding="async"
    @error="hasFailed = true"
  />

  <div
    v-else
    class="grid place-items-center size-6 rounded-2 bg-surface-gray-2 text-xs-medium uppercase text-ink-gray-6 shrink-0"
    aria-hidden="true"
  >
    {{ (title || "?").charAt(0) }}
  </div>
</template>
