<script setup lang="ts">
import type { Language } from '@codemirror/language'
import { computed, shallowRef, watchEffect } from 'vue'

import { colorizeLine } from '@/codemirror/colorize'

const props = defineProps<{ text: string; path: string; start: number; end: number }>()

const language = shallowRef<Language | null>(null)

const html = computed(() => colorizeLine(props.text, props.start, props.end, language.value))

watchEffect(async () => {
  const path = props.path
  const { loadLanguage } = await import('@/codemirror/languages')
  const loaded = await loadLanguage(path).catch(() => null)

  if (path === props.path) language.value = loaded?.language ?? null
})
</script>

<template>
  <span class="ed-code" v-html="html" />
</template>
