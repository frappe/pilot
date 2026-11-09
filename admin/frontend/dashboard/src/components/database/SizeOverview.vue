<script setup lang="ts">
import { Badge, Button, Select, Skeleton, TextInput } from 'frappe-ui'
import { computed, onUnmounted, ref, watch } from 'vue'
import Table from '@/components/common/Table.vue'
import type { DatabaseDiagnostics, DatabaseSize, TableSize } from '@/types/database'
import { formatBytes } from '@/utils/format'
import { relativeTime } from '@/utils/time'

interface Props {
  size: DatabaseSize | null
  loading: boolean
  error: string
  diagnostics: DatabaseDiagnostics
  siteByDatabase: Record<string, string>
  site: string
}

interface Row {
  key: string
  label: string
  detail: string
  site: string
  data: number
  index: number
  claimable: number | null
  total: number
  other: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{ refresh: [] }>()

const PAGE_SIZE = 15

const placeholders: Row[] = Array.from({ length: 7 }, (_, index) => ({
  key: `placeholder-${index}`,
  label: '',
  detail: '',
  site: '',
  data: 0,
  index: 0,
  claimable: 0,
  total: 0,
  other: false,
}))

const tones = {
  data: 'bg-surface-gray-7',
  index: 'bg-surface-gray-5',
  claimable: 'bg-[var(--ink-amber-6)]',
}

const partColumns = [
  { key: 'data', label: 'Data', tone: tones.data },
  { key: 'index', label: 'Index', tone: tones.index },
  { key: 'claimable', label: 'Claimable', tone: tones.claimable },
] as const

const columns = computed(() => [
  { key: 'name', label: props.site ? 'Table' : 'Database', class: 'w-[34%] max-w-0' },
  { key: 'size', label: 'Size', class: 'w-[13%]' },
  { key: 'share', label: 'Share', class: 'w-[13%]' },
  ...partColumns.map((part) => ({ key: part.key, label: part.label, class: 'w-[13%]' })),
])

const visible = ref(PAGE_SIZE)
const query = ref('')
const sortKey = ref<'total' | 'data' | 'index' | 'claimable'>('total')
const updatedAt = ref<number | null>(null)
const now = ref(Date.now())
const ticker = setInterval(() => (now.value = Date.now()), 30000)

watch(
  () => props.size,
  (size) => {
    if (size) updatedAt.value = Date.now()
  },
)

watch(
  () => props.site,
  () => {
    visible.value = PAGE_SIZE
    query.value = ''
  },
)

onUnmounted(() => clearInterval(ticker))

const ready = computed(() => Boolean(props.size) && !props.loading && !props.error)

const isSplit = computed(() => props.size?.data_bytes != null)

const total = computed(() => {
  const size = props.size
  if (!size) return 0

  return (
    size.total_bytes ??
    (size.data_bytes ?? 0) + (size.index_bytes ?? 0) + (size.claimable_bytes ?? 0)
  )
})

const percent = (bytes: number) => (total.value ? (bytes / total.value) * 100 : 0)

const formatPercent = (value: number) =>
  value < 10 ? `${value.toFixed(1)}%` : `${Math.round(value)}%`

const measured = (value: string) => (ready.value ? value : '-')

const parts = computed(() => {
  const size = props.size

  if (size && !isSplit.value) {
    return [{ key: 'data', label: 'Database', tone: tones.data, bytes: total.value }]
  }

  return [
    { key: 'data', label: 'Data', tone: tones.data, bytes: size?.data_bytes ?? 0 },
    { key: 'index', label: 'Index', tone: tones.index, bytes: size?.index_bytes ?? 0 },
    {
      key: 'claimable',
      label: 'Claimable',
      tone: tones.claimable,
      bytes: size?.claimable_bytes ?? 0,
    },
  ]
})

const groups = computed(() => {
  const space = props.size?.free_bytes ?? null
  const waits = props.diagnostics.lock_waits
  const binlog = props.diagnostics.binlog
  const timeout = waits.timeout_seconds
  const schema = props.diagnostics.performance_schema_enabled

  return [
    {
      label: 'Disk',
      rows: [
        { label: 'Free on disk', value: space == null ? '-' : formatBytes(space), dot: '' },
        {
          label: 'Database share',
          value: space == null ? '-' : formatPercent((total.value / (total.value + space)) * 100),
          dot: '',
        },
      ],
    },
    {
      label: 'Server',
      rows: [
        {
          label: 'Engine',
          value: props.diagnostics.engine === 'postgres' ? 'PostgreSQL' : 'MariaDB',
          dot: '',
        },
        { label: 'Connections', value: String(props.diagnostics.active_connections), dot: '' },
        {
          label: 'Lock waits',
          value: String(waits.current_waits),
          dot: waits.current_waits ? tones.claimable : '',
        },
        { label: 'Lock timeout', value: timeout == null ? '-' : `${timeout}s`, dot: '' },
      ],
    },
    {
      label: 'Logging',
      rows: [
        {
          label: 'Performance Schema',
          value: schema ? 'On' : 'Off',
          dot: schema ? 'bg-surface-green-7' : 'bg-surface-gray-4',
        },
        {
          label: 'Binary logs',
          value: binlog
            ? `${binlog.file_count} ${binlog.file_count === 1 ? 'file' : 'files'} · ${formatBytes(binlog.size_bytes)}`
            : 'Off',
          dot: binlog ? 'bg-surface-green-7' : 'bg-surface-gray-4',
        },
      ],
    },
  ]
})

const bytesOf = (part: TableSize) =>
  part.data_bytes + part.index_bytes + (part.claimable_bytes ?? 0)

const toRow = (part: TableSize, site = ''): Row => ({
  key: part.name,
  label: site || part.name,
  detail: site ? part.name : '',
  site,
  data: part.data_bytes,
  index: part.index_bytes,
  claimable: part.claimable_bytes,
  total: bytesOf(part),
  other: false,
})

const lump = (parts: TableSize[], label: string, detail: string): Row[] => {
  if (!parts.length) return []

  const sum = (pick: (part: TableSize) => number) =>
    parts.reduce((all, part) => all + pick(part), 0)
  const claimable = sum((part) => part.claimable_bytes ?? 0)
  const data = sum((part) => part.data_bytes)
  const index = sum((part) => part.index_bytes)

  return [
    {
      key: 'other',
      label,
      detail,
      site: '',
      data,
      index,
      claimable,
      total: data + index + claimable,
      other: true,
    },
  ]
}

const sortOptions = [
  { label: 'Largest', value: 'total', icon: 'lucide-hard-drive' },
  { label: 'Most data', value: 'data', icon: 'lucide-rows-3' },
  { label: 'Most index', value: 'index', icon: 'lucide-list-tree' },
  { label: 'Most claimable', value: 'claimable', icon: 'lucide-recycle' },
]

const sortValue = (part: TableSize) =>
  ({
    total: bytesOf(part),
    data: part.data_bytes,
    index: part.index_bytes,
    claimable: part.claimable_bytes ?? 0,
  })[sortKey.value]

const sorted = computed(() =>
  [...(props.size?.breakdown ?? [])].sort((a, b) => sortValue(b) - sortValue(a)),
)

const matched = computed(() => {
  const needle = query.value.trim().toLowerCase()

  if (!needle) return sorted.value

  return sorted.value.filter((part) =>
    [part.name, props.siteByDatabase[part.name] ?? ''].some((name) =>
      name.toLowerCase().includes(needle),
    ),
  )
})

const shownLabel = computed(
  () => `${Math.min(visible.value, matched.value.length)} of ${matched.value.length}`,
)

const rows = computed(() => {
  const breakdown = matched.value

  if (props.site) {
    return breakdown.slice(0, visible.value).map((part) => toRow(part))
  }

  const owned = breakdown.filter((part) => props.siteByDatabase[part.name])
  const unowned = breakdown.filter((part) => !props.siteByDatabase[part.name])

  return [
    ...owned.map((part) => toRow(part, props.siteByDatabase[part.name])),
    ...lump(unowned, 'Other schemas', 'Databases without a site'),
  ]
})

const tally = computed(() => {
  const breakdown = props.size?.breakdown ?? []
  const count = props.site
    ? breakdown.length
    : breakdown.filter((part) => props.siteByDatabase[part.name]).length

  return ready.value && count ? String(count) : ''
})

const isHeavy = (row: Row) =>
  Boolean(row.claimable) && (row.claimable ?? 0) / (row.total || 1) >= 0.15

const updatedLabel = computed(() => {
  if (props.loading) return 'Updating'
  if (!updatedAt.value) return ''

  return now.value - updatedAt.value < 60000
    ? 'Updated just now'
    : `Updated ${relativeTime(updatedAt.value)}`
})
</script>

<template>
  <section class="flex xl:flex-row flex-col xl:h-[calc(100dvh-93px)] text-sm tabular-nums fade-in">
    <aside
      class="flex flex-col order-last p-5 xl:overflow-y-auto border-t xl:border-t-0 xl:border-l border-outline-gray-1 w-full xl:w-[19rem] shrink-0"
    >
      <Badge :label="site || 'Server-wide'" size="sm" class="self-start" />

      <span class="flex flex-col gap-1.5 mt-4">
        <span class="flex items-center h-4 text-ink-gray-5">Total size</span>
        <Skeleton v-if="loading" class="rounded-2 w-24 h-7" />
        <span v-else class="flex items-center h-7 text-4xl-semibold text-ink-gray-9">
          {{ measured(formatBytes(total)) }}
        </span>
      </span>

      <Skeleton v-if="loading" class="mt-4 rounded-full w-full h-2 shrink-0" />

      <span
        v-else
        class="flex gap-px bg-surface-gray-2 mt-4 rounded-full w-full h-2 overflow-hidden shrink-0"
      >
        <template v-if="ready">
          <span
            v-for="part in parts"
            :key="part.key"
            class="h-full transition-[flex-grow] duration-300"
            :class="part.tone"
            :style="{ flexGrow: part.bytes }"
          />
        </template>
      </span>

      <dl class="flex flex-col mt-3">
        <div v-for="part in parts" :key="part.key" class="flex items-center gap-2 h-7">
          <span class="rounded-full size-2 shrink-0" :class="part.tone" />
          <dt class="w-[7.5rem] text-ink-gray-5 shrink-0">{{ part.label }}</dt>
          <Skeleton v-if="loading" class="rounded-2 w-24 h-3.5" />
          <dd v-else-if="ready" class="flex items-center gap-1.5 text-ink-gray-8">
            {{ formatBytes(part.bytes) }}
            <span class="text-ink-gray-5">{{ formatPercent(percent(part.bytes)) }}</span>
          </dd>
          <dd v-else class="text-ink-gray-8">-</dd>
        </div>
      </dl>

      <dl
        v-for="group in groups"
        :key="group.label"
        class="flex flex-col mt-3 pt-3 border-t border-outline-gray-2"
      >
        <dt class="flex items-center h-7 text-sm-medium text-ink-gray-8">{{ group.label }}</dt>

        <div v-for="fact in group.rows" :key="fact.label" class="flex items-center gap-2 h-7">
          <dt class="w-[8.5rem] text-ink-gray-5 shrink-0">{{ fact.label }}</dt>
          <Skeleton v-if="loading && group.label === 'Disk'" class="rounded-2 w-16 h-3.5" />
          <dd v-else class="flex items-center gap-2 text-ink-gray-8">
            <span v-if="fact.dot" class="rounded-full size-1.5" :class="fact.dot" />
            {{ fact.value }}
          </dd>
        </div>
      </dl>

      <div class="flex items-center gap-3 mt-auto pt-4">
        <span class="text-ink-gray-5 text-xs">{{ updatedLabel }}</span>

        <Button
          variant="ghost"
          size="sm"
          icon="lucide-refresh-cw"
          label="Refresh"
          tooltip="Refresh"
          :loading="loading"
          @click="emit('refresh')"
        />
      </div>
    </aside>

    <div class="flex flex-col flex-1 min-w-0">
      <header class="flex items-center gap-2 px-3 md:px-4 h-12 shrink-0">
        <nav class="flex items-center gap-1.5 min-w-0 text-base-medium">
          <RouterLink
            v-if="site"
            :to="{ query: { ...$route.query, site: undefined } }"
            class="text-ink-gray-5 hover:text-ink-gray-8 transition-colors duration-150"
          >
            Databases
          </RouterLink>
          <span v-if="site" class="text-ink-gray-4">/</span>
          <h4 class="text-ink-gray-8 truncate">{{ site || 'Databases' }}</h4>
        </nav>
        <span class="text-ink-gray-5">{{ tally }}</span>

        <TextInput
          v-model="query"
          size="sm"
          :placeholder="site ? 'Search tables' : 'Search databases'"
          autocomplete="off"
          class="ml-auto w-56"
        >
          <template #prefix>
            <span class="size-4 text-ink-gray-5 lucide-search" />
          </template>
        </TextInput>

        <Select v-model="sortKey" :options="sortOptions" side="bottom" size="sm" class="max-w-40" />
      </header>

      <div
        v-if="!loading && (error || !rows.length)"
        class="flex flex-col flex-1 justify-center items-center gap-1 py-16 text-center"
      >
        <span
          class="mb-2 size-6 text-ink-gray-4"
          :class="error ? 'lucide-circle-alert' : 'lucide-database'"
        />
        <p class="text-base-medium text-ink-gray-8">
          {{ error ? 'Could not read the size' : query ? 'No matches' : 'No breakdown' }}
        </p>
        <p class="max-w-sm text-ink-gray-6 text-p-sm">
          {{ error || (query ? `Nothing matches "${query}"` : 'This server reports one total for every database.') }}
        </p>
        <Button v-if="query && !error" size="sm" label="Clear search" class="mt-3" @click="query = ''" />
      </div>

      <Table
        v-else
        :columns="columns"
        :rows="loading ? placeholders : rows"
        height="flex-1"
        class="mx-3 md:mx-4 text-base"
      >
        <template #name="{ row }">
          <Skeleton v-if="loading" class="rounded-2 w-48 h-3.5" />
          <span v-else class="flex items-baseline gap-2 min-w-0">
            <RouterLink
              v-if="row.site"
              :to="{ query: { ...$route.query, site: row.site } }"
              class="after:absolute after:inset-0 text-ink-gray-8 shrink-0"
            >
              {{ row.label }}
            </RouterLink>
            <span v-else class="text-ink-gray-6 shrink-0">{{ row.label }}</span>
            <span v-if="row.site" class="min-w-0 font-mono text-ink-gray-4 text-xs truncate">
              {{ row.detail }}
            </span>
          </span>
        </template>

        <template #size="{ row }">
          <Skeleton v-if="loading" class="rounded-2 w-14 h-3.5" />
          <span v-else class="text-base-medium text-ink-gray-8">{{ formatBytes(row.total) }}</span>
        </template>

        <template #share="{ row }">
          <Skeleton v-if="loading" class="rounded-2 w-14 h-3.5" />
          <span v-else class="text-ink-gray-5">{{ formatPercent(percent(row.total)) }}</span>
        </template>

        <template v-for="part in partColumns" :key="part.key" #[part.key]="{ row }">
          <Skeleton v-if="loading" class="rounded-2 w-14 h-3.5" />
          <span
            v-else
            :class="part.key === 'claimable' && isHeavy(row) ? 'text-ink-gray-8' : 'text-ink-gray-6'"
          >
            {{ row[part.key] ? formatBytes(row[part.key] ?? 0) : '-' }}
          </span>
        </template>
      </Table>

      <footer
        v-if="ready && site && matched.length > PAGE_SIZE"
        class="flex items-center px-5 md:px-6 border-t border-outline-gray-1 h-12 shrink-0"
      >
        <span class="mr-auto text-ink-gray-5">{{ shownLabel }}</span>

        <Button
          v-if="visible < matched.length"
          size="sm"
          label="Load more"
          @click="visible += PAGE_SIZE"
        />
      </footer>
    </div>
  </section>
</template>
