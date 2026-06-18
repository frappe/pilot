<script setup>
import { ref, computed, watch } from 'vue'
import { Dialog } from 'frappe-ui'
import LucideCheck from '~icons/lucide/check'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue'])

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const benches = ref([])
const currentPort = window.location.port
const currentHost = window.location.hostname

function isCurrentBench(bench) {
  if (bench.domain) return bench.domain === currentHost
  return String(bench.port) === String(currentPort)
}

function benchUrl(bench) {
  // Domain-routed (production) benches live behind nginx on the same scheme as
  // the current page; dev benches are reachable directly on their admin port.
  if (bench.domain) return `${window.location.protocol}//${bench.domain}`
  return `${window.location.protocol}//${currentHost}:${bench.port}`
}

function benchMode(bench) {
  return bench.production ? 'Live' : 'Development'
}

function switchBench(bench) {
  if (isCurrentBench(bench)) return
  window.location.href = benchUrl(bench)
}

async function loadBenches() {
  try {
    const response = await fetch('/api/benches/')
    if (response.ok) benches.value = await response.json()
  } catch { }
}

watch(show, (open) => {
  if (open) loadBenches()
})
</script>

<template>
  <Dialog v-model="show" title="Change Bench" size="lg" :showCloseButton="true">
    <template #default>
      <div @pointerdown.stop>
        <div v-if="benches.length === 0" class="rounded-lg bg-surface-gray-1 px-3 py-6 text-center text-sm text-ink-gray-5">
          No other benches running.
        </div>
        <div v-else class="flex flex-col gap-1">
        <button
          v-for="bench in benches"
          :key="bench.port"
          class="flex items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors w-full"
          :class="isCurrentBench(bench)
            ? 'bg-surface-gray-2 cursor-default'
            : 'hover:bg-surface-gray-2 cursor-pointer'"
          @click="switchBench(bench)"
        >
          <span class="min-w-0 flex-1">
            <span class="block truncate text-sm font-medium text-ink-gray-9">{{ bench.name }}</span>
            <span class="block truncate text-xs text-ink-gray-5">{{ benchMode(bench) }}</span>
          </span>
          <span v-if="isCurrentBench(bench)" class="flex-shrink-0 text-xs font-medium text-ink-gray-5">Current</span>
          <LucideCheck v-if="isCurrentBench(bench)" class="h-4 w-4 flex-shrink-0 text-ink-green-3" />
        </button>
        </div>
      </div>
    </template>
  </Dialog>
</template>
