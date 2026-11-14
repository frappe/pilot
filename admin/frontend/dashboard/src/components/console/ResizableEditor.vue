<script setup lang="ts">
import { ref } from 'vue'

/** Editor card with a footer and a drag handle on its bottom border. The chosen height is kept per `storageKey` in this browser. */
const props = defineProps<{ storageKey: string; quiet?: boolean }>()

const MIN_HEIGHT = 120
const STEP = 40

const readHeight = () => {
  try {
    return Number(localStorage.getItem(props.storageKey)) || null
  } catch {
    return null
  }
}

const height = ref<number | null>(readHeight())
const area = ref<HTMLElement | null>(null)

const setHeight = (value: number | null) => {
  // Leave room below for the toolbar and the output panel.
  height.value = value === null ? null : Math.round(Math.min(Math.max(value, MIN_HEIGHT), window.innerHeight * 0.7))
  try {
    if (height.value === null) localStorage.removeItem(props.storageKey)
    else localStorage.setItem(props.storageKey, String(height.value))
  } catch {}
}

const startDrag = (event: PointerEvent) => {
  const handle = event.currentTarget as HTMLElement
  const startY = event.clientY
  const startHeight = area.value?.offsetHeight ?? MIN_HEIGHT
  const move = (moved: PointerEvent) => setHeight(startHeight + moved.clientY - startY)
  const stop = () => {
    handle.removeEventListener('pointermove', move)
    handle.removeEventListener('pointerup', stop)
    handle.removeEventListener('pointercancel', stop)
  }
  handle.setPointerCapture(event.pointerId)
  handle.addEventListener('pointermove', move)
  handle.addEventListener('pointerup', stop)
  handle.addEventListener('pointercancel', stop)
}

const nudge = (direction: number) => setHeight((area.value?.offsetHeight ?? MIN_HEIGHT) + direction * STEP)
</script>

<template>
  <div class="relative">
    <div
      class="overflow-hidden"
      :class="quiet ? 'border-b border-outline-gray-1' : 'border rounded-4 border-outline-gray-2'"
    >
      <div ref="area" class="h-64 sm:h-[300px] lg:h-[360px]" :style="height ? { height: `${height}px` } : undefined">
        <slot />
      </div>
      <slot name="footer" />
    </div>
    <!-- Sits on the card's bottom border, so it adds no height. -->
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label="Resize editor"
      tabindex="0"
      title="Drag to resize. Double-click to reset."
      class="group -bottom-1.5 z-10 absolute inset-x-0 flex justify-center items-center h-3 cursor-row-resize touch-none select-none focus-visible:outline-none"
      @pointerdown.prevent="startDrag"
      @dblclick="setHeight(null)"
      @keydown.up.prevent="nudge(-1)"
      @keydown.down.prevent="nudge(1)"
    >
      <span
        class="bg-surface-gray-4 group-hover:bg-surface-gray-6 group-focus-visible:bg-surface-gray-6 group-active:bg-surface-gray-8 rounded-full w-8 group-active:w-14 h-1 group-active:h-1.5 scale-x-75 group-hover:scale-x-100 group-active:scale-x-100 transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]"
      />
    </div>
  </div>
</template>
