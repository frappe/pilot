<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Badge, Button, Dialog, ErrorMessage, LoadingText, Spinner } from 'frappe-ui'

import Table from '@/components/common/Table.vue'

import LucidePlay from '~icons/lucide/play'
import LucideSquare from '~icons/lucide/square'
import LucideTrash2 from '~icons/lucide/trash-2'
import LucideRotateCw from '~icons/lucide/rotate-cw'
import LucideExternalLink from '~icons/lucide/external-link'

import ActionMenu from '@/components/common/ActionMenu.vue'

import { useBenches } from '@/composables/benches/useBenches'

interface Props {
  modelValue?: boolean
}

const props = defineProps<Props>()
const emit = defineEmits(['update:modelValue', 'new-bench'])

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const {
  benches,
  loading,
  controlLoading,
  error: controlError,
  load: loadBenches,
  control: controlBench,
  drop: dropBenchByName,
} = useBenches()
const currentPort = window.location.port
const currentHost = window.location.hostname

const benchToDrop = ref(null)
const dropping = ref(false)

const showDropConfirm = computed({
  get: () => !!benchToDrop.value,
  set: (v) => {
    if (!v) benchToDrop.value = null
  },
})

const columns = [
  { label: 'Bench', key: 'name', class: 'w-1/3' },
  { label: 'Mode', key: 'mode', class: 'text-ink-gray-6 text-sm' },
  { label: 'Manager', key: 'manager', class: 'text-ink-gray-6 text-sm' },
  { label: 'Sites', key: 'sites', class: 'text-ink-gray-6 text-sm' },
  { label: 'Status', key: 'status', class: 'text-center' },
  { label: '', key: 'actions', class: 'w-12' },
]

const rows = computed(() =>
  benches.value.map((b) => ({
    name: b.name,
    mode: benchMode(b),
    manager: benchManager(b),
    sites: b.site_count ?? 0,
    status: statusLabel(b),
    bench: b,
  })),
)

const isCurrentBench = (bench) => {
  if (bench.domain) return bench.domain === currentHost
  return String(bench.port) === String(currentPort)
}

const benchUrl = (bench) => {
  // Production benches carry a backend-computed admin_url on the scheme nginx
  // actually serves (http until the cert is in place, so a not-yet-set-up bench
  // opens over http even from this https page); dev benches use their admin port.
  if (bench.admin_url) return bench.admin_url
  return `${window.location.protocol}//${currentHost}:${bench.port}`
}

const benchMode = (bench) => {
  return bench.production ? 'Production' : 'Development'
}

const benchManager = (bench) => {
  const mgr = bench.process_manager || 'foreground'
  return mgr.charAt(0).toUpperCase() + mgr.slice(1)
}

// Three states. Dev: running iff its admin port is up. Production: the workload
// being up is "Running"; if it's down but the admin control plane is still up
// (socket-activated) the bench is "Admin active" rather than fully "Stopped" —
// e.g. provisioned but setup not finished. null means we couldn't tell (up).
const benchState = (bench) => {
  if (!bench.production) return bench.reachable ? 'running' : 'stopped'
  if (bench.workload_running !== false) return 'running'
  if (bench.admin_running !== false) return 'admin'
  return 'stopped'
}

const STATUS = {
  running: { label: 'Running', theme: 'green' },
  admin: { label: 'Admin active', theme: 'blue' },
  stopped: { label: 'Stopped', theme: 'gray' },
}

const statusLabel = (bench) => {
  return STATUS[benchState(bench)].label
}

const statusTheme = (bench) => {
  return STATUS[benchState(bench)].theme
}

// Production benches route through nginx, which socket-activates the admin on
// demand, so they can always be opened. A dev bench is only reachable while up.
const canOpen = (bench) => {
  if (isCurrentBench(bench)) return false
  return bench.production || bench.reachable
}

const openBench = (bench) => {
  // Open the bench's admin URL in a new tab so the manage view stays put.
  window.open(benchUrl(bench), '_blank', 'noopener')
}

const menuOptions = (bench) => {
  const opts = []
  if (canOpen(bench))
    opts.push({ label: 'Open', icon: LucideExternalLink, onClick: () => openBench(bench) })
  if (bench.production) {
    const running = bench.workload_running
    const current = isCurrentBench(bench)
    if (running !== true && !current)
      opts.push({
        label: 'Start',
        icon: LucidePlay,
        onClick: () => controlBench(bench.name, 'start'),
      })
    if (running !== false)
      opts.push({
        label: 'Restart',
        icon: LucideRotateCw,
        onClick: () => controlBench(bench.name, 'restart'),
      })
    // Stopping the bench you're currently using would kill this very session.
    if (running !== false && !current)
      opts.push({
        label: 'Stop',
        icon: LucideSquare,
        theme: 'red',
        onClick: () => controlBench(bench.name, 'stop'),
      })
  }
  // Only an empty bench can be dropped, and never the one you're using.
  if (!isCurrentBench(bench) && (bench.site_count ?? 0) === 0)
    opts.push({
      label: 'Drop bench',
      icon: LucideTrash2,
      theme: 'red',
      onClick: () => confirmDrop(bench),
    })
  return opts
}

const confirmDrop = (bench) => {
  controlError.value = ''
  benchToDrop.value = bench
}

const dropBench = async () => {
  const bench = benchToDrop.value
  if (!bench) return
  dropping.value = true
  try {
    if (await dropBenchByName(bench.name)) benchToDrop.value = null
  } finally {
    dropping.value = false
  }
}

const newBench = () => {
  show.value = false
  emit('new-bench')
}

watch(show, (open) => {
  if (open) loadBenches()
})
</script>

<template>
  <Dialog v-model="show" title="Manage Benches" size="3xl" :showCloseButton="true">
    <div class="flex flex-col" @pointerdown.stop>
      <div class="flex justify-end items-center gap-1 mb-4">
        <Button variant="ghost" :loading="loading" @click="loadBenches" title="Refresh">
          <template #icon>
            <span class="w-4 h-4 lucide-refresh-cw" />
          </template>
        </Button>

        <Button @click="newBench">
          <template #prefix>
            <span class="w-4 h-4 lucide-plus" />
          </template>
          New Bench
        </Button>
      </div>

      <ErrorMessage v-if="controlError" :message="controlError" class="mb-2" />

      <LoadingText v-if="loading && !benches.length" class="justify-center py-10" />

      <p v-else-if="!benches.length" class="py-10 text-ink-gray-4 text-sm text-center">
        No benches found.
      </p>

      <Table v-else :columns="columns" :rows="rows" height="max-h-96">
        <template #name="{ row }">
          <div class="flex items-center gap-2 min-w-0">
            <span class="font-medium text-sm truncate">{{ row.name }}</span>
            <Badge v-if="isCurrentBench(row.bench)" theme="green" size="sm" label="Current" />
          </div>
        </template>

        <template #status="{ row }">
          <Badge :theme="statusTheme(row.bench)" :label="row.status" />
        </template>

        <template #actions="{ row }">
          <Spinner v-if="controlLoading === row.name" size="md" class="text-ink-gray-5" />

          <ActionMenu
            v-else-if="menuOptions(row.bench).length"
            :options="menuOptions(row.bench)"
          />
        </template>
      </Table>
    </div>
  </Dialog>

  <Dialog v-model="showDropConfirm" title="Drop Bench" size="sm">
    <div class="flex flex-col gap-4" @pointerdown.stop>
      <div class="flex flex-col gap-2 text-ink-gray-7 text-sm leading-relaxed">
        <p>
          Permanently delete <strong class="text-ink-gray-9">{{ benchToDrop?.name }}</strong>?
        </p>

        <p>
          This tears down its production services, nginx config and MariaDB instance, then removes
          the bench directory. This action cannot be undone.
        </p>
      </div>

      <ErrorMessage v-if="controlError" :message="controlError" />
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showDropConfirm = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="dropping" @click="dropBench"
          >Drop Bench</Button
        >
      </div>
    </template>
  </Dialog>
</template>
