<template>
  <!-- Colorized markup comes from Monaco's tokenizer, which escapes the source. -->
  <span v-if="colored" ref="host" class="ed-code" v-html="colored.html"></span>
  <span v-else class="ed-code">
    {{ plain.pre }}<mark class="ed-mark">{{ plain.hit }}</mark>{{ plain.post }}
  </span>
</template>

<script setup>
import { ref, computed, watchEffect, nextTick } from 'vue'
import { colorizeLine, markMatch } from '@/colorize'
import { languageFor } from '@/lang'
import { useTheme } from '@/composables/useTheme'

const props = defineProps({
  text: { type: String, default: '' },
  path: { type: String, default: '' },
  start: { type: Number, default: 0 },
  end: { type: Number, default: 0 },
})

const { isDark } = useTheme()
const host = ref(null)
const colored = ref(null)

// Shown until the tokenizer resolves, and whenever colorizing fails.
const plain = computed(() => {
  const line = props.text.replace(/\r?\n$/, '').trimStart()
  const shift = props.text.replace(/\r?\n$/, '').length - line.length
  const start = Math.max(0, props.start - shift)
  const end = Math.max(0, props.end - shift)
  return { pre: line.slice(0, start), hit: line.slice(start, end), post: line.slice(end) }
})

watchEffect(async () => {
  // Re-colorize on theme change: Monaco bakes the palette into the markup.
  void isDark.value
  const { text, path, start, end } = props
  try {
    colored.value = await colorizeLine(text, languageFor(path), { start, end })
    await nextTick()
    markMatch(host.value, colored.value.start, colored.value.end)
  } catch {
    colored.value = null
  }
})
</script>
