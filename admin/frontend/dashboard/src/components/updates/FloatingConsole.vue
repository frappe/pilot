<script setup lang="ts">
import { Button, Select } from 'frappe-ui'
import { computed, reactive, ref } from 'vue'

import PythonConsole from '@/components/console/PythonConsole.vue'

const props = defineProps<{ site: string; sites: string[] }>()
const emit = defineEmits<{ close: [] }>()

const consoleSite = ref(props.site)
const isSmall = ref(false)
const offset = reactive({ x: 0, y: 0 })

const siteOptions = computed(() => props.sites.map((name) => ({ label: name, value: name })))

const grab = (event: PointerEvent) => {
  if (event.button !== 0) return

  const origin = { x: event.clientX - offset.x, y: event.clientY - offset.y }

  const move = (next: PointerEvent) => {
    offset.x = next.clientX - origin.x
    offset.y = next.clientY - origin.y
  }

  const stop = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
  }

  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop)
}
</script>

<template>
  <Teleport to="body">
    <Transition
      appear
      enter-active-class="transition duration-200 ease-out origin-bottom-right motion-reduce:transition-none"
      enter-from-class="opacity-0 scale-[0.96] translate-y-2"
      leave-active-class="transition duration-150 ease-in origin-bottom-right motion-reduce:transition-none"
      leave-to-class="opacity-0 scale-[0.96] translate-y-2"
      mode="out-in"
    >
      <section
        v-if="!isSmall"
        class="right-5 bottom-5 z-40 fixed flex flex-col bg-surface-elevation-1 shadow-2xl border border-outline-gray-2 rounded-6 w-[min(580px,calc(100vw-2.5rem))] h-[min(540px,calc(100dvh-5rem))] overflow-hidden"
        :style="{ translate: `${offset.x}px ${offset.y}px` }"
      >
        <header
          class="flex items-center gap-2 pr-2 pl-4 border-b border-outline-gray-1 h-12 cursor-grab active:cursor-grabbing select-none shrink-0"
          @pointerdown="grab"
        >
          <span class="size-4 text-ink-gray-5 lucide-square-terminal shrink-0" aria-hidden="true" />
          <h2 class="text-ink-gray-8 text-base-medium">Console</h2>
          <Select v-model="consoleSite" class="ml-auto w-44" :options="siteOptions" @pointerdown.stop />
          <Button class="-ml-1" variant="ghost" icon="lucide-minus" label="Minimize" @pointerdown.stop @click="isSmall = true" />
          <Button class="-ml-1" variant="ghost" icon="lucide-x" label="Close console" @pointerdown.stop @click="emit('close')" />
        </header>

        <div class="flex-1 min-h-0">
          <PythonConsole v-model:site="consoleSite" compact />
        </div>
      </section>

      <button
        v-else
        type="button"
        class="right-5 bottom-5 z-40 fixed flex items-center gap-2 bg-surface-elevation-1 shadow-2xl pr-3 pl-3 border border-outline-gray-2 rounded-full h-9 active:scale-[0.97] transition-transform"
        @click="isSmall = false"
      >
        <span class="size-4 text-ink-gray-5 lucide-square-terminal" aria-hidden="true" />
        <span class="text-ink-gray-8 text-sm-medium">Console</span>
        <span class="text-ink-gray-5 text-sm">{{ consoleSite }}</span>
      </button>
    </Transition>
  </Teleport>
</template>
