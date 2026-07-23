<template>
  <!-- Colorized markup is produced by Monaco's tokenizer, which escapes the source. -->
  <span v-if="html" class="ed-code" v-html="html"></span>
  <span v-else class="ed-code">
    {{ plain.pre }}<mark class="rounded-sm bg-surface-amber-2 text-ink-gray-9">{{ plain.hit }}</mark>{{ plain.post }}
  </span>
</template>

<script setup>
import { ref, computed, watchEffect } from 'vue'
import { colorizeLine } from '@/colorize'
import { languageFor } from '@/monaco'
import { useTheme } from '@/composables/useTheme'

const props = defineProps({
  text: { type: String, default: '' },
  path: { type: String, default: '' },
  start: { type: Number, default: 0 },
  end: { type: Number, default: 0 },
})

const { isDark } = useTheme()
const html = ref('')

// Shown until the tokenizer resolves, and whenever colorizing fails.
const plain = computed(() => {
  const line = props.text.replace(/\r?\n$/, '')
  return {
    pre: line.slice(0, props.start).replace(/^\s+/, ''),
    hit: line.slice(props.start, props.end),
    post: line.slice(props.end),
  }
})

watchEffect(async () => {
  // Re-colorize on theme change: Monaco bakes the palette into the markup.
  void isDark.value
  const { text, path, start, end } = props
  try {
    html.value = await colorizeLine(text, languageFor(path), { start, end })
  } catch {
    html.value = ''
  }
})
</script>
