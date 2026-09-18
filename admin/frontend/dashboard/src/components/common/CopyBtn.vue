<script setup lang="ts">
import { ref } from 'vue'

interface Props {
  text: string
}

const props = defineProps<Props>()

const copied = ref(false)

const copy = async () => {
  await navigator.clipboard.writeText(props.text)
  copied.value = true
  setTimeout(() => (copied.value = false), 1000)
}
</script>

<template>
  <button type="button" :aria-label="copied ? 'Copied' : 'Copy'" @click="copy">
    <span v-if="copied" class="size-3.5 fade-in lucide-check" />
    <span v-else class="size-3.5 fade-in lucide-clipboard" />
  </button>
</template>
