<script setup lang="ts">
import { ref } from 'vue'

import { copyText } from '@/utils/clipboard'

interface Props {
  text: string
}

const props = defineProps<Props>()

const copied = ref(false)

const copy = async (event: MouseEvent) => {
  if (!(await copyText(props.text, event.currentTarget))) return
  copied.value = true
  setTimeout(() => (copied.value = false), 1000)
}
</script>

<template>
  <button type="button" :aria-label="copied ? 'Copied' : 'Copy'" @click.stop="copy">
    <span v-if="copied" class="size-3.5 fade-in lucide-check" />
    <span v-else class="size-3.5 fade-in lucide-clipboard" />
  </button>
</template>
