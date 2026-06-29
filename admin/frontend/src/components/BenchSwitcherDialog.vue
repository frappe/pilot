<script setup>
import { ref, computed, watch } from 'vue'
import { Dialog, Button, Badge, Dropdown, ListView, ErrorMessage } from 'frappe-ui'
import LucidePlus from '~icons/lucide/plus'
import LucideRefreshCw from '~icons/lucide/refresh-cw'
import LucideMoreVertical from '~icons/lucide/more-vertical'
import LucideExternalLink from '~icons/lucide/external-link'
import LucidePlay from '~icons/lucide/play'
import LucideSquare from '~icons/lucide/square'
import LucideRotateCw from '~icons/lucide/rotate-cw'
import LucideLoader2 from '~icons/lucide/loader-2'
import LucideTrash2 from '~icons/lucide/trash-2'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'new-bench'])

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const benches = ref([])
const loading = ref(false)
const currentPort = window.location.port
const currentHost = window.location.hostname
const controlLoading = ref('')
const controlError = ref('')

const benchToDrop = ref(null)
const dropping = ref(false)

const showDropConfirm = computed({
  get: () => !!benchToDrop.value,
  set: (v) => { if (!v) benchToDrop.value = null },
})

const columns = [
  { label: 'Bench', key: 'name', align: 'left', width: 2 },
  { label: 'Mode', key: 'mode', align: 'left', width: 1 },
  { label: 'Manager', key: 'manager', align: 'left', width: 1 },
  { label: 'Sites', key: 'sites', align: 'left', width: 1 },
  { label: 'Status', key: 'status', align: 'center', width: 1 },
  { label: '', key: 'actions', align: 'right', width: '3rem' },
]

const rows = computed(() => benches.value.map((b) => ({
  name: b.name,
  mode: benchMode(b),
  manager: benchManager(b),
  sites: b.site_count ?? 0,
  status: statusLabel(b),
  bench: b,
})))

function isCurrentBench(bench) {
  if (bench.domain) return bench.domain === currentHost
  return String(bench.port) === String(currentPort)
}

function benchUrl(bench) {
  // Production benches carry a backend-computed admin_url on the scheme nginx
  // actually serves (http until the cert is in place, so a not-yet-set-up bench
  // opens over http even from this https page); dev benches use their admin port.
  if (bench.admin_url) return bench.admin_url
  return `${window.location.protocol}//${currentHost}:${bench.port}`
}

function benchMode(bench) {
  return bench.production ? 'Production' : 'Development'
}

function benchManager(bench) {
  // Mirrors `bench ls`: dev benches run their processes in the foreground.
  const mgr = bench.production ? (bench.process_manager || 'supervisor') : 'foreground'
  return mgr.charAt(0).toUpperCase() + mgr.slice(1)
}

// Three states. Dev: running iff its admin port is up. Production: the workload
// being up is "Running"; if it's down but the admin control plane is still up
// (socket-activated) the bench is "Admin active" rather than fully "Stopped" —
// e.g. provisioned but setup not finished. null means we couldn't tell (up).
function benchState(bench) {
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

function statusLabel(bench) {
  return STATUS[benchState(bench)].label
}

function statusTheme(bench) {
  return STATUS[benchState(bench)].theme
}

// Production benches route through nginx, which socket-activates the admin on
// demand, so they can always be opened. A dev bench is only reachable while up.
function canOpen(bench) {
  if (isCurrentBench(bench)) return false
  return bench.production || bench.reachable
}

function openBench(bench) {
  // Open the bench's admin URL in a new tab so the manage view stays put.
  window.open(benchUrl(bench), '_blank', 'noopener')
}

function menuOptions(bench) {
  const opts = []
  if (canOpen(bench))
    opts.push({ label: 'Open', icon: LucideExternalLink, onClick: () => openBench(bench) })
  if (bench.production) {
    const running = bench.workload_running
    const current = isCurrentBench(bench)
    if (running !== true && !current)
      opts.push({ label: 'Start', icon: LucidePlay, onClick: () => control(bench, 'start') })
    if (running !== false)
      opts.push({ label: 'Restart', icon: LucideRotateCw, onClick: () => control(bench, 'restart') })
    // Stopping the bench you're currently using would kill this very session.
    if (running !== false && !current)
      opts.push({ label: 'Stop', icon: LucideSquare, theme: 'red', onClick: () => control(bench, 'stop') })
  }
  // Only an empty bench can be dropped, and never the one you're using.
  if (!isCurrentBench(bench) && (bench.site_count ?? 0) === 0)
    opts.push({ label: 'Drop bench', icon: LucideTrash2, theme: 'red', onClick: () => confirmDrop(bench) })
  return opts
}

async function loadBenches() {
  loading.value = true
  try {
    const response = await fetch('/api/benches/')
    if (response.ok) benches.value = await response.json()
  } catch { } finally {
    loading.value = false
  }
}

async function control(bench, action) {
  controlLoading.value = bench.name
  controlError.value = ''
  try {
    const res = await fetch(`/api/benches/${bench.name}/actions/${action}`, { method: 'POST' })
    const d = await res.json()
    if (!d.ok) { controlError.value = d.error; return }
    await loadBenches()
  } catch (e) {
    controlError.value = e.message
  } finally {
    if (controlLoading.value === bench.name) controlLoading.value = ''
  }
}

function confirmDrop(bench) {
  controlError.value = ''
  benchToDrop.value = bench
}

async function dropBench() {
  const bench = benchToDrop.value
  if (!bench) return
  dropping.value = true
  controlError.value = ''
  try {
    const res = await fetch(`/api/benches/${bench.name}`, { method: 'DELETE' })
    const d = await res.json()
    if (!d.ok) { controlError.value = d.error; return }
    benchToDrop.value = null
    await loadBenches()
  } catch (e) {
    controlError.value = e.message
  } finally {
    dropping.value = false
  }
}

function newBench() {
  show.value = false
  emit('new-bench')
}

watch(show, (open) => {
  if (open) loadBenches()
})
</script>

<template>
  <Dialog v-model="show" title="Manage Benches" size="3xl" :showCloseButton="true">
    <template #default>
      <div class="flex flex-col" @pointerdown.stop>
        <div class="mb-4 flex items-center justify-end gap-1">
          <Button variant="ghost" size="sm" :loading="loading" @click="loadBenches" title="Refresh">
            <template #icon>
              <LucideRefreshCw class="h-4 w-4" />
            </template>
          </Button>
          <Button variant="outline" size="sm" @click="newBench">
            <template #prefix>
              <LucidePlus class="h-4 w-4" />
            </template>
            New Bench
          </Button>
        </div>

        <ErrorMessage v-if="controlError" :message="controlError" class="mb-2" />

        <div v-if="loading && !benches.length" class="py-10 text-center text-sm text-ink-gray-5">Loading…</div>
        <div v-else-if="!benches.length" class="py-10 text-center text-sm text-ink-gray-4">
          No benches found.
        </div>
        <ListView v-else :columns="columns" :rows="rows" row-key="name"
          :options="{ selectable: false, showTooltip: false, rowHeight: 48 }">
          <template #cell="{ column, row, item }">
            <!-- Bench name + Current marker. Running state lives in the Status
                 column, so the name stays flush-left under its header. -->
            <div v-if="column.key === 'name'" class="flex w-full min-w-0 items-center gap-2 text-left">
              <span class="truncate text-sm font-medium text-ink-gray-9">{{ row.name }}</span>
              <Badge v-if="isCurrentBench(row.bench)" theme="green" size="sm" label="Current" />
            </div>

            <!-- Status badge -->
            <div v-else-if="column.key === 'status'" class="flex w-full justify-center">
              <Badge :theme="statusTheme(row.bench)" :label="row.status" />
            </div>

            <!-- Per-bench actions -->
            <div v-else-if="column.key === 'actions'" class="flex w-full justify-end">
              <span v-if="controlLoading === row.name" class="flex h-7 w-7 items-center justify-center">
                <LucideLoader2 class="h-4 w-4 animate-spin text-ink-gray-5" />
              </span>
              <Dropdown v-else-if="menuOptions(row.bench).length" :options="menuOptions(row.bench)" placement="left">
                <template #default="{ open }">
                  <Button variant="ghost" size="sm" :active="open">
                    <template #icon>
                      <LucideMoreVertical class="h-4 w-4" />
                    </template>
                  </Button>
                </template>
              </Dropdown>
            </div>

            <!-- Mode / Manager -->
            <div v-else class="w-full truncate text-sm text-ink-gray-6">{{ item }}</div>
          </template>
        </ListView>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showDropConfirm" :options="{ title: 'Drop Bench', size: 'sm' }">
    <template #default>
      <div class="flex flex-col gap-4" @pointerdown.stop>
        <div class="flex flex-col gap-2 text-sm leading-relaxed text-ink-gray-7">
          <p>Permanently delete <strong class="text-ink-gray-9">{{ benchToDrop?.name }}</strong>?</p>
          <p>
            This tears down its production services, nginx config and MariaDB instance, then
            removes the bench directory. This action cannot be undone.
          </p>
        </div>
        <ErrorMessage v-if="controlError" :message="controlError" />
        <div class="flex justify-end gap-2">
          <Button variant="ghost" @click="showDropConfirm = false">Cancel</Button>
          <Button variant="solid" theme="red" :loading="dropping" @click="dropBench">Drop Bench</Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>
