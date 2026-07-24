<template>
  <div
    v-if="state.visible"
    class="fixed inset-0 z-40"
    @click="close"
    @contextmenu.prevent="close"
  >
    <!-- desktop floating menu -->
    <div
      class="absolute z-50 hidden min-w-40 rounded-lg border border-outline-gray-2 bg-surface-white py-1 shadow-xl sm:block"
      :style="{ left: state.x + 'px', top: state.y + 'px' }"
    >
      <button
        v-for="(it, i) in state.items"
        :key="i"
        class="flex h-8 w-full items-center gap-2 px-3 text-left text-sm text-ink-gray-7 hover:bg-surface-gray-2"
        :class="{ 'text-ink-red-4 hover:bg-surface-red-1': it.danger }"
        @click="run(it)"
      >
        <span v-if="it.icon" :class="it.icon" class="ed-lane text-current"></span>
        <span>{{ it.label }}</span>
      </button>
    </div>

    <!-- mobile bottom sheet -->
    <div
      class="absolute inset-x-0 bottom-0 z-50 rounded-t-xl border-t border-outline-gray-2 bg-surface-white p-2 pb-safe shadow-[0_-4px_20px_rgba(0,0,0,0.15)] sm:hidden"
      @click.stop
    >
      <div class="mx-auto mb-2 h-1 w-10 rounded-full bg-outline-gray-2"></div>
      <button
        v-for="(it, i) in state.items"
        :key="i"
        class="flex h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm text-ink-gray-7 active:bg-surface-gray-2"
        :class="{ 'text-ink-red-4': it.danger }"
        @click="run(it)"
      >
        <span v-if="it.icon" :class="it.icon" class="ed-lane text-current"></span>
        <span>{{ it.label }}</span>
      </button>
      <button
        class="mt-2 flex h-11 w-full items-center justify-center rounded-lg bg-surface-gray-2 px-3 text-sm text-ink-gray-7"
        @click="close"
      >
        Cancel
      </button>
    </div>
  </div>
</template>

<script setup>
import { useContextMenu } from '@/composables/useContextMenu'

const { state, close } = useContextMenu()
function run(it) {
  close()
  it.action()
}
</script>
