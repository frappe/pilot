<template>
  <div ref="root" class="inline-flex relative">
    <Button variant="ghost" size="sm" :active="open" @click="toggle">
      <template #icon>
        <span class="size-4 lucide-ellipsis-vertical" />
      </template>
    </Button>
    <Teleport to="body">
      <div
        v-if="open"
        ref="panel"
        data-dismissable-layer
        class="z-[60] fixed bg-surface-elevation-1 shadow-2xl p-1 border rounded-lg border-outline-gray-2 w-40 pointer-events-auto"
        :style="panelStyle"
      >
        <Button
          v-for="option in options"
          :key="option.label"
          variant="ghost"
          :theme="option.theme"
          class="!justify-start w-full"
          @click="select(option)"
        >
          <template #prefix>
            <component :is="option.icon" class="size-4 shrink-0" />
          </template>
          {{ option.label }}
        </Button>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { Button } from 'frappe-ui'

const props = defineProps({ options: { type: Array, default: () => [] } })

const root = ref(null)
const panel = ref(null)
const open = ref(false)
const panelStyle = ref({})

function onOutside(event) {
  if (root.value?.contains(event.target)) return
  if (panel.value?.contains(event.target)) return
  close()
}

function toggle() {
  if (open.value) return close()
  const rect = root.value.getBoundingClientRect()
  const opensUp = rect.bottom + props.options.length * 36 + 12 > window.innerHeight
  panelStyle.value = opensUp
    ? {
        right: `${window.innerWidth - rect.right}px`,
        bottom: `${window.innerHeight - rect.top + 4}px`,
      }
    : { right: `${window.innerWidth - rect.right}px`, top: `${rect.bottom + 4}px` }
  open.value = true
  document.addEventListener('pointerdown', onOutside, true)
  document.addEventListener('scroll', close, true)
}

function close() {
  open.value = false
  document.removeEventListener('pointerdown', onOutside, true)
  document.removeEventListener('scroll', close, true)
}

function select(option) {
  close()
  option.onClick?.()
}

onBeforeUnmount(close)
</script>
