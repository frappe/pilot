<script setup lang="ts">
import { ref, onBeforeUnmount } from 'vue'
import { Button } from 'frappe-ui'

interface Props {
  options?: any[]
}

const props = withDefaults(defineProps<Props>(), {
  options: () => [],
})

const root = ref(null)
const panel = ref(null)
const open = ref(false)
const panelStyle = ref({})

const onOutside = (event) => {
  if (root.value?.contains(event.target)) return
  if (panel.value?.contains(event.target)) return
  close()
}

const toggle = () => {
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

const close = () => {
  open.value = false
  document.removeEventListener('pointerdown', onOutside, true)
  document.removeEventListener('scroll', close, true)
}

const select = (option) => {
  close()
  option.onClick?.()
}

onBeforeUnmount(close)
</script>

<template>
  <div ref="root" class="inline-flex relative">
    <Button variant="ghost" :active="open" @click="toggle">
      <template #icon>
        <span class="size-4 lucide-ellipsis-vertical" />
      </template>
    </Button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="panel"
        data-dismissable-layer
        class="z-[60] fixed bg-surface-elevation-1 shadow-2xl p-1 border rounded-6 border-outline-gray-2 w-40 pointer-events-auto"
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
