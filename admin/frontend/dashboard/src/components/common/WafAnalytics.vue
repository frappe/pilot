<template>
  <div v-if="data && data.log_present" class="mb-6">
    <div class="flex items-center gap-2 mb-3">
      <span class="size-4 text-ink-gray-6 lucide-shield-alert" />
      <h2 class="font-semibold text-ink-gray-9 text-base">Web Application Firewall</h2>
      <span v-if="data.mode === 'DetectionOnly'"
        class="bg-surface-amber-2 px-2 py-0.5 rounded-full text-ink-amber-3 text-xs">Detection only</span>
    </div>

    <div class="gap-4 grid grid-cols-2 sm:grid-cols-3 mb-4">
      <div class="bg-surface-white px-4 py-3 border rounded-lg border-outline-gray-2">
        <div class="text-ink-gray-6 text-sm">Flagged requests</div>
        <div class="mt-1 font-semibold text-ink-gray-9 text-xl">{{ totals.flagged }}</div>
      </div>
      <div class="bg-surface-white px-4 py-3 border rounded-lg border-outline-gray-2">
        <div class="text-ink-gray-6 text-sm">{{ data.mode === 'On' ? 'Blocked' : 'Would block' }}</div>
        <div class="mt-1 font-semibold text-ink-red-4 text-xl">
          {{ data.mode === 'On' ? totals.blocked : totals.would_block }}
        </div>
      </div>
      <div class="bg-surface-white px-4 py-3 border rounded-lg border-outline-gray-2">
        <div class="text-ink-gray-6 text-sm">Detected only</div>
        <div class="mt-1 font-semibold text-ink-gray-9 text-xl">{{ totals.flagged - totals.would_block }}</div>
      </div>
    </div>

    <div class="gap-4 grid grid-cols-1 sm:grid-cols-2">
      <div class="bg-surface-white p-4 border rounded-lg border-outline-gray-2">
        <div class="mb-2 text-ink-gray-6 text-sm">Top rules</div>
        <div v-if="!data.top_rules.length" class="text-ink-gray-5 text-xs">No rule matches in this window.</div>
        <div v-for="rule in data.top_rules" :key="rule.id" class="flex justify-between items-center gap-2 py-1">
          <span class="min-w-0 text-ink-gray-8 text-sm truncate" :title="rule.message">
            <span class="font-mono text-ink-gray-5 text-xs">{{ rule.id }}</span> {{ rule.message }}
          </span>
          <span class="font-medium text-ink-gray-7 text-sm shrink-0">{{ rule.count }}</span>
        </div>
      </div>
      <div class="bg-surface-white p-4 border rounded-lg border-outline-gray-2">
        <div class="mb-2 text-ink-gray-6 text-sm">Top source IPs</div>
        <div v-if="!data.top_ips.length" class="text-ink-gray-5 text-xs">No sources in this window.</div>
        <div v-for="row in data.top_ips" :key="row.ip" class="flex justify-between items-center gap-2 py-1">
          <span class="font-mono text-ink-gray-8 text-sm truncate">{{ row.ip }}</span>
          <span class="font-medium text-ink-gray-7 text-sm shrink-0">{{ row.count }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { monitorApi } from '@/api/monitor'

const props = defineProps({ window: { type: String, default: '24h' } })

const data = ref(null)

// The WAF log has no per-second "live" feed; fall back to the shortest window.
function resolveWindow(w) {
  return w === 'live' ? '30m' : w
}

async function load() {
  try {
    data.value = await monitorApi.waf(resolveWindow(props.window))
  } catch {
    data.value = null
  }
}

watch(() => props.window, load, { immediate: true })
</script>
