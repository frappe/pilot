<script setup lang="ts">
import { ref } from 'vue'

interface Props {
  text: string
}

const props = defineProps<Props>()

const copied = ref(false)

// navigator.clipboard exists only in secure contexts; an admin reached over plain HTTP
// on an IP address falls back to a hidden textarea. It sits beside the button so a
// dialog's focus trap does not pull focus away before the copy.
const copyWithTextarea = (text: string, anchor: Element): boolean => {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  anchor.parentElement?.appendChild(textarea)
  textarea.select()
  const isCopied = document.execCommand('copy')
  textarea.remove()
  return isCopied
}

const copy = async (event: MouseEvent) => {
  let isCopied = false
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(props.text)
      isCopied = true
    } else if (event.currentTarget instanceof Element) {
      isCopied = copyWithTextarea(props.text, event.currentTarget)
    }
  } catch {
    isCopied = false
  }
  if (!isCopied) return
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
