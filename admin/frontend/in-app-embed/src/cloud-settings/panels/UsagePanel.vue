<script setup lang="ts">
import { Button, ErrorMessage } from 'frappe-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Panel from '../components/Panel.vue'
import Scrollbar from '../components/Scrollbar.vue'
import { settleTask, type Store } from '../store'

interface Props {
  store: Store
  active?: boolean
}

const props = defineProps<Props>()
const store = props.store

const colors = {
  database: { bar: 'var(--surface-blue-6)', icon: 'var(--ink-blue-6)' },
  files: { bar: 'var(--surface-teal-6)', icon: 'var(--ink-teal-6)' },
  backups: { bar: 'var(--surface-amber-6)', icon: 'var(--ink-amber-6)' },
  other: { bar: 'var(--surface-gray-4)', icon: 'var(--ink-gray-5)' },
}

const units = { MB: 1024 ** 2, GB: 1024 ** 3, TB: 1024 ** 4 }

const fileIcons = {
  json: 'lucide-file-json',
  lock: 'lucide-file-lock',
  db: 'lucide-database',
  gz: 'lucide-file-archive',
  tar: 'lucide-file-archive',
  zip: 'lucide-file-archive',
  sql: 'lucide-file-code',
  log: 'lucide-file-text',
}

const iconFor = (name) => fileIcons[name.split('.').pop()] || 'lucide-file'

const usage = ref(null)
const error = ref('')
const refreshing = ref(false)

let gone = false

onBeforeUnmount(() => (gone = true))

const load = async () => {
  error.value = ''

  try {
    const [storage] = await Promise.all([store.api.getStorage(), store.loadBilling()])

    usage.value = storage
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  }
}

watch(
  () => props.active,
  (active) => {
    if (active) load()
  },
  { immediate: true },
)

const formatBytes = (bytes) => {
  const names = ['B', 'KB', 'MB', 'GB', 'TB']
  const power = bytes > 0 ? Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 4) : 0

  return `${Number((bytes / 1024 ** power).toFixed(1))} ${names[power]}`
}

const quota = computed(() => {
  const match = String(store.state.billing?.plan?.specs?.storage || '').match(
    /([\d.]+)\s*(MB|GB|TB)/i,
  )

  return match ? Number(match[1]) * units[match[2].toUpperCase()] : 0
})

const total = computed(() => (usage.value?.database_bytes || 0) + (usage.value?.bytes || 0))

const hardwareIcons = {
  cpu: { icon: 'lucide-cpu', tint: 'bg-surface-gray-2 text-ink-gray-5' },
  memory: { icon: 'lucide-memory-stick', tint: 'bg-surface-gray-2 text-ink-gray-5' },
}

const fallbackIcon = { icon: 'lucide-gauge', tint: 'bg-surface-gray-2 text-ink-gray-5' }

const hardware = computed(() =>
  (store.state.billing?.usage || [])
    .filter((meter) => meter.name.toLowerCase() !== 'storage')
    .map((meter) => ({
      ...meter,
      percent: Math.max(0, Math.min(100, Math.round(Number(meter.percent) || 0))),
    })),
)

const bySize = (items) => [...items].sort((a, b) => b.bytes - a.bytes)

const entries = (items, prefix) =>
  bySize(items || []).map((item) => ({
    key: `${prefix}:${item.name}`,
    label: item.name,
    bytes: item.bytes,
  }))

const nodes = computed(() => {
  const site = usage.value || {}

  return bySize([
    {
      key: 'database',
      color: colors.database,
      label: __('Database'),
      bytes: site.database_bytes || 0,
      icon: 'lucide-database',
    },
    {
      key: 'files',
      color: colors.files,
      label: __('Files'),
      bytes: (site.public_files_bytes || 0) + (site.private_files_bytes || 0),
      icon: 'lucide-folder',
      children: bySize([
        {
          key: 'public',
          label: __('Public files'),
          bytes: site.public_files_bytes || 0,
        },
        {
          key: 'private',
          label: __('Private files'),
          bytes: site.private_files_bytes || 0,
        },
      ]),
    },
    {
      key: 'backups',
      color: colors.backups,
      label: __('Backups'),
      bytes: site.backups_bytes || 0,
      icon: 'lucide-archive',
      children: entries(site.backup_files, 'backup'),
    },
    {
      key: 'other',
      color: colors.other,
      label: __('Other'),
      bytes: site.other_bytes || 0,
      icon: 'lucide-box',
      children: entries(site.other_entries, 'other'),
    },
  ])
})

const blocks = computed(() => {
  const scale = quota.value || total.value || 1

  const used = nodes.value
    .filter((node) => node.bytes)
    .map((node) => ({ ...node, share: node.bytes / scale }))

  if (quota.value <= total.value) return used

  const free = quota.value - total.value

  return [...used, { key: 'free', label: __('Free'), bytes: free, share: free / scale }]
})

const measuredAt = computed(() =>
  new Date(usage.value?.collected_at).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }),
)

const refresh = async () => {
  refreshing.value = true
  error.value = ''

  try {
    const { task_id } = await store.api.refreshStorage()

    if (!(await settleTask(task_id, () => gone, __("Couldn't measure usage.")))) return

    await load()
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  } finally {
    refreshing.value = false
  }
}
</script>

<template>
  <Panel
    :title="__('Usage')"
    :description="__('How much of your server and storage your site uses.')"
    :loading="!usage && !error"
    :error="usage ? '' : error"
    :error-title="__(`Couldn't load usage`)"
    @retry="load"
  >
    <template #actions>
      <Button
        class="col-start-2 row-span-2 row-start-1"
        icon-left="lucide-refresh-cw"
        :loading="refreshing"
        :label="__('Refresh')"
        @click="refresh"
      />
    </template>

    <ErrorMessage :message="error" class="mb-4" />

    <div v-if="hardware.length" class="mb-4 grid gap-3 sm:grid-cols-2">
      <section
        v-for="meter in hardware"
        :key="meter.name"
        class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 rounded-6 border border-outline-gray-2 p-4"
      >
        <span
          :class="[
            'row-span-3 grid size-10 place-items-center rounded-6',
            (hardwareIcons[meter.name.toLowerCase()] || fallbackIcon).tint,
          ]"
        >
          <span
            :class="[(hardwareIcons[meter.name.toLowerCase()] || fallbackIcon).icon, 'size-5']"
            aria-hidden="true"
          />
        </span>

        <p class="text-sm text-ink-gray-6">{{ meter.name }}</p>

        <p class="text-sm-medium tabular-nums text-ink-gray-9">{{ meter.percent }}%</p>

        <div
          class="col-span-2 my-2 h-1 overflow-hidden rounded-full bg-surface-gray-2"
          role="progressbar"
          :aria-label="meter.name"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="meter.percent"
        >
          <div
            class="h-full rounded-full bg-surface-gray-10 transition-[width] duration-300"
            :style="{ width: `${meter.percent}%` }"
          />
        </div>

        <p class="col-span-2 text-p-xs text-ink-gray-5">{{ meter.detail }}</p>
      </section>
    </div>

    <section class="rounded-6 border border-outline-gray-2 p-5">
      <p class="flex items-center justify-between gap-3">
        <span class="flex items-center gap-2 text-base-medium text-ink-gray-8">
          <span class="lucide-hard-drive size-4" aria-hidden="true" />
          {{ __("Site storage") }}
        </span>

        <span class="text-sm text-ink-gray-6">
          <span class="text-sm-medium tabular-nums text-ink-gray-8">{{ formatBytes(total) }}</span>
          {{ quota ? __("of {0} used", [formatBytes(quota)]) : __("used") }}
        </span>
      </p>

      <div class="mt-4 flex h-5 gap-0.5 overflow-hidden rounded-full">
        <span
          v-for="block in blocks"
          :key="block.key"
          :class="!block.color && 'bg-surface-gray-2'"
          :style="{ flex: `${block.share} 1 0`, background: block.color?.bar }"
        />
      </div>

      <div class="-mx-1.5 mt-3 text-base">
        <template v-for="node in nodes" :key="node.key">
          <details v-if="node.children?.length" name="usage" class="group">
            <summary
              class="flex h-8 cursor-pointer list-none items-center gap-2 rounded-4 pl-0.5 pr-1.5 hover:bg-surface-gray-2 [&::-webkit-details-marker]:hidden"
            >
              <span
                class="lucide-chevron-right size-3.5 shrink-0 text-ink-gray-5 transition-transform group-open:rotate-90"
              />
              <span :class="[node.icon, 'size-4 shrink-0']" :style="{ color: node.color.icon }" />
              <span class="truncate text-ink-gray-8">{{ node.label }}</span>
              <span class="ml-auto shrink-0 tabular-nums text-ink-gray-7">
                {{ formatBytes(node.bytes) }}
              </span>
            </summary>

            <Scrollbar
              class="-mr-2 ml-2 border-l border-outline-gray-2"
              viewport-class="max-h-56 pl-2.5 pr-2"
            >
              <p
                v-for="child in node.children"
                :key="child.key"
                class="flex h-8 items-center gap-2 px-1.5 text-ink-gray-5"
              >
                <span :class="[iconFor(child.label), 'size-4 shrink-0 text-ink-gray-4']" />
                <span class="truncate">{{ child.label }}</span>
                <span class="ml-auto shrink-0 tabular-nums">{{ formatBytes(child.bytes) }}</span>
              </p>
            </Scrollbar>
          </details>

          <p v-else class="flex h-8 items-center gap-2 pl-6 pr-1.5">
            <span :class="[node.icon, 'size-4 shrink-0']" :style="{ color: node.color.icon }" />
            <span class="truncate text-ink-gray-8">{{ node.label }}</span>
            <span class="ml-auto shrink-0 tabular-nums text-ink-gray-7">
              {{ formatBytes(node.bytes) }}
            </span>
          </p>
        </template>
      </div>

      <p class="mt-3 text-p-sm text-ink-gray-5">{{ __("Measured {0}", [measuredAt]) }}</p>
    </section>
  </Panel>
</template>
