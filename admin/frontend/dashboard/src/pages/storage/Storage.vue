<script setup lang="ts">
import { Badge, Button, ErrorMessage, Progress, Skeleton } from 'frappe-ui'
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import AppIcon from '@/components/apps/AppIcon.vue'
import Collapsable from '@/components/common/Collapsable.vue'
import Scrollbar from '@/components/common/Scrollbar.vue'
import FCLogo from '@/components/icons/FC.vue'
import BinlogPurgeAlert from '@/components/storage/BinlogPurgeAlert.vue'
import DiskRing from '@/components/storage/DiskRing.vue'

import { apiErrorMessage } from '@/api/client'
import { monitorApi } from '@/api/monitor'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useSession } from '@/composables/auth/useSession'
import type { StorageBreakdown } from '@/types/storage'
import { formatBytes } from '@/utils/format'

const { session, centralServerUrl } = useSession()
const { titleMap, logoMap, load: loadRegistry } = useAppRegistry()

const storageData = ref<StorageBreakdown | null>(null)
const loading = ref(false)
const error = ref('')
const hovered = ref('')
const hero = ref<HTMLElement | null>(null)
const ring = ref<{ $el: HTMLElement } | null>(null)
const tileElements = new Map<string, HTMLElement>()
const lines = ref<{ key: string; path: string }[]>([])
const showAllDatabases = ref(false)
const purger = ref<InstanceType<typeof BinlogPurgeAlert> | null>(null)
const openSite = ref('')
const openApp = ref('')

const colors = {
  node_modules: 'var(--ink-purple-6)',
  git: 'var(--ink-orange-6)',
  env: 'var(--ink-teal-6)',
  code: 'var(--ink-green-6)',
  logs: 'var(--ink-red-4)',
  sites: 'var(--ink-cyan-6)',
  database: 'var(--ink-pink-6)',
}

const sum = (values: number[]) => values.reduce((total, value) => total + value, 0)

const load = async () => {
  loading.value = true
  error.value = ''

  try {
    storageData.value = await monitorApi.storage()
  } catch (e) {
    error.value = apiErrorMessage(e, 'Could not load storage breakdown.')
  } finally {
    loading.value = false
  }
}

const sites = computed(() => {
  const data = storageData.value

  if (!data) return []

  const databaseOf = Object.fromEntries(
    data.database.databases.filter((row) => row.site).map((row) => [row.site, row.bytes]),
  )

  return data.bench.sites
    .map((site) => ({
      name: site.name,
      files: site.bytes,
      database: databaseOf[site.name] ?? 0,
      bytes: site.bytes + (databaseOf[site.name] ?? 0),
      parts: [
        {
          key: 'database',
          label: 'Database',
          icon: 'lucide-database',
          bytes: databaseOf[site.name] ?? 0,
        },
        {
          key: 'backups',
          label: `Backups · ${site.backup_files.length}`,
          icon: 'lucide-archive',
          bytes: site.backups_bytes,
        },
        {
          key: 'private',
          label: 'Private files',
          icon: 'lucide-lock',
          bytes: site.private_files_bytes,
        },
        {
          key: 'public',
          label: 'Public files',
          icon: 'lucide-folder-open',
          bytes: site.public_files_bytes,
        },
        { key: 'other', label: 'Other', icon: 'lucide-folder', bytes: site.other_bytes },
      ].filter((part) => part.bytes > 0),
    }))
    .sort((left, right) => right.bytes - left.bytes)
})

const apps = computed(() =>
  [...(storageData.value?.bench.apps ?? [])]
    .map((app) => ({
      ...app,
      code: Math.max(app.bytes - app.git_bytes - app.node_modules_bytes, 0),
    }))
    .sort((left, right) => right.bytes - left.bytes),
)

const databaseOther = computed(() => {
  const database = storageData.value?.database

  if (!database?.supported) return []

  const leftover = database.databases.filter((row) => !row.site && !row.system)
  const system = database.databases.filter((row) => row.system)

  return [
    {
      key: 'leftover',
      label: 'Databases without a site',
      detail: `${leftover.length}`,
      bytes: sum(leftover.map((row) => row.bytes)),
    },
    {
      key: 'system',
      label: 'System schemas',
      detail: '',
      bytes: sum(system.map((row) => row.bytes)),
    },
    ...database.components.map((component) => ({
      key: component.key,
      label: component.label.replace(/^\w/, (letter) => letter.toUpperCase()),
      detail: '',
      bytes: component.bytes,
    })),
    { key: 'core', label: 'Engine files', detail: '', bytes: database.core_bytes ?? 0 },
  ]
    .filter((row) => row.bytes > 0)
    .sort((left, right) => right.bytes - left.bytes)
})

const segments = computed(() => {
  const data = storageData.value

  if (!data) return []

  const git = sum(apps.value.map((app) => app.git_bytes))
  const nodeModules = sum(apps.value.map((app) => app.node_modules_bytes))
  const sitesBytes = sum(sites.value.map((site) => site.bytes))
  const databaseBytes = sum(databaseOther.value.map((row) => row.bytes))

  return [
    {
      key: 'node_modules',
      icon: 'lucide-package-open',
      label: 'node_modules',
      note: 'Build packages',
      bytes: nodeModules,
    },
    {
      key: 'git',
      icon: 'lucide-git-branch',
      label: 'Git history',
      note: 'Past versions',
      bytes: git,
    },
    {
      key: 'env',
      icon: 'lucide-box',
      label: 'Python env',
      note: 'Python packages',
      bytes: data.bench.env_bytes,
    },
    {
      key: 'code',
      icon: 'lucide-code',
      label: 'App code',
      note: 'Source and assets',
      bytes: data.bench.apps_bytes - git - nodeModules,
    },
    {
      key: 'logs',
      icon: 'lucide-scroll-text',
      label: 'Logs',
      note: 'Monitor and uptime',
      bytes: data.bench.logs_bytes,
    },
    {
      key: 'sites',
      icon: 'lucide-pilot-site',
      label: 'Sites',
      note: 'Databases and files',
      bytes: sitesBytes,
    },
    {
      key: 'database',
      icon: 'lucide-database',
      label: 'Database other',
      note: 'Not tied to a site',
      bytes: databaseBytes,
    },
  ]
    .map((segment) => ({ ...segment, color: colors[segment.key as keyof typeof colors] }))
    .filter((segment) => segment.bytes > 0)
    .sort((left, right) => right.bytes - left.bytes)
})

const usedPercent = computed(() => {
  const data = storageData.value

  return data?.disk_total ? Math.round((data.disk_used / data.disk_total) * 100) : 0
})

const appParts = (app: (typeof apps.value)[number]) =>
  [
    {
      key: 'node_modules',
      label: 'node_modules',
      icon: 'lucide-package-open',
      bytes: app.node_modules_bytes,
    },
    { key: 'git', label: 'Git history', icon: 'lucide-git-branch', bytes: app.git_bytes },
    { key: 'code', label: 'Code', icon: 'lucide-code', bytes: app.code },
  ].filter((part) => part.bytes > 0)

const ringSize = 216
const ringAnchor = ringSize / 2 + 2
const columnX = 228
const heroWidth = ref(0)

type Slot = { x: number; y: number; angle: number; shift: string }

const isOrbit = computed(() => heroWidth.value >= 1000)

const makeSlot = (side: number, y: number): Slot => {
  const x = side * columnX

  return {
    x,
    y,
    angle: ((Math.atan2(x, -y) * 180) / Math.PI + 360) % 360,
    shift: side > 0 ? 'translate(0, -50%)' : 'translate(-100%, -50%)',
  }
}

const rightCount = computed(() => Math.floor(segments.value.length / 2))
const leftCount = computed(() => segments.value.length - rightCount.value)
const orbitReach = computed(() => ((leftCount.value - 1) / 2) * 72 + 28)
const diskY = computed(() => -Math.max(orbitReach.value + 28, ringSize / 2 + 52))
const orbitCenter = computed(() => 32 - diskY.value + 28)
const orbitHeight = computed(() => orbitCenter.value + orbitReach.value + 24)

const columnRows = (count: number) =>
  Array.from({ length: count }, (_, index) => (index - (count - 1) / 2) * 72)

const slots = computed<Slot[]>(() => [
  ...columnRows(rightCount.value).map((y) => makeSlot(1, y)),
  ...columnRows(leftCount.value)
    .reverse()
    .map((y) => makeSlot(-1, y)),
])

const angleGap = (left: number, right: number) => {
  const gap = Math.abs(left - right) % 360

  return Math.min(gap, 360 - gap)
}

const sliceAngles = (start: number) => {
  let offset = 0

  return segments.value.map((segment) => {
    const share = segment.bytes / (pilotBytes.value || 1)
    const from = start + offset * 360
    const angle = (from + (share / 2) * 360) % 360

    offset += share

    return { key: segment.key, angle, from, to: from + share * 360 }
  })
}

const anchorAngle = (slice: { from: number; to: number }, slot: Slot | undefined) => {
  const margin = Math.min(4, (slice.to - slice.from) / 2)
  const low = slice.from + margin
  const high = slice.to - margin
  let target = slot?.angle ?? low

  while (target < slice.from) target += 360

  if (target <= high) return target

  return target - high < slice.from + 360 - target ? high : low
}

const ringLayout = computed(() => {
  const placement: Record<string, Slot> = Object.fromEntries(
    segments.value.flatMap((segment, index) =>
      slots.value[index] ? [[segment.key, slots.value[index]]] : [],
    ),
  )
  const cost = (start: number) =>
    sum(
      sliceAngles(start)
        .filter((item) => placement[item.key])
        .map(
          (item) =>
            angleGap(anchorAngle(item, placement[item.key]), placement[item.key].angle) ** 2,
        ),
    )
  const start = Array.from({ length: 120 }, (_, index) => index * 3).reduce((best, candidate) =>
    cost(candidate) < cost(best) ? candidate : best,
  )

  return {
    start,
    placement,
    angles: Object.fromEntries(
      sliceAngles(start).map((item) => [item.key, anchorAngle(item, placement[item.key])]),
    ),
  }
})

const place = (x: number, y: number, shift = 'translate(-50%, -50%)') =>
  isOrbit.value
    ? { left: `calc(50% + ${x}px)`, top: `${orbitCenter.value + y}px`, transform: shift }
    : undefined

const tilePlace = (key: string) => {
  const slot = ringLayout.value.placement[key]

  return slot ? place(slot.x, slot.y, slot.shift) : undefined
}

const setTile = (key: string, element: unknown) => {
  if (element instanceof HTMLElement) tileElements.set(key, element)
  else tileElements.delete(key)
}

const tileTarget = (slot: Slot, tile: DOMRect, container: DOMRect) => {
  if (slot.x !== 0) {
    const side = Math.sign(slot.x)

    return {
      x: (side > 0 ? tile.left - 4 : tile.right + 4) - container.left,
      y: tile.top + tile.height / 2 - container.top,
      normalX: -side,
      normalY: 0,
    }
  }

  const below = slot.y > 0

  return {
    x: tile.left + tile.width / 2 - container.left,
    y: (below ? tile.top - 4 : tile.bottom + 4) - container.top,
    normalX: 0,
    normalY: below ? -1 : 1,
  }
}

const drawLines = () => {
  const container = hero.value?.getBoundingClientRect()

  if (!container || !isOrbit.value) {
    lines.value = []

    return
  }

  const centerX = container.width / 2
  const { angles, placement } = ringLayout.value

  lines.value = segments.value.flatMap((segment) => {
    const tile = tileElements.get(segment.key)?.getBoundingClientRect()
    const slot = placement[segment.key]

    if (!tile || !slot) return []

    const radians = (angles[segment.key] * Math.PI) / 180
    const fromX = centerX + Math.sin(radians) * ringAnchor
    const fromY = orbitCenter.value - Math.cos(radians) * ringAnchor
    const target = tileTarget(slot, tile, container)
    const distance = Math.hypot(target.x - fromX, target.y - fromY)
    const pull = Math.min(distance * 0.35, 90)
    const leaveX = Math.sin(radians) + (target.x - fromX) / distance
    const leaveY = -Math.cos(radians) + (target.y - fromY) / distance
    const leaveLength = Math.hypot(leaveX, leaveY) || 1

    return [
      {
        key: segment.key,
        path: `M ${fromX} ${fromY} C ${fromX + (leaveX / leaveLength) * pull} ${fromY + (leaveY / leaveLength) * pull}, ${target.x + target.normalX * pull} ${target.y + target.normalY * pull}, ${target.x} ${target.y}`,
      },
    ]
  })
}

const resizeObserver = new ResizeObserver(([entry]) => {
  heroWidth.value = entry.contentRect.width
  nextTick(drawLines)
})

watch(hero, (element) => {
  resizeObserver.disconnect()

  if (element) resizeObserver.observe(element)
})

watch([segments, isOrbit, ringLayout], () => nextTick(drawLines))

onBeforeUnmount(() => resizeObserver.disconnect())

const sitesBytes = computed(() => sum(sites.value.map((site) => site.bytes)))

const pilotBytes = computed(() => sum(segments.value.map((segment) => segment.bytes)))
const databases = computed(() =>
  [...(storageData.value?.database.databases ?? [])].sort(
    (left, right) => right.bytes - left.bytes,
  ),
)

const visibleDatabases = computed(() =>
  showAllDatabases.value ? databases.value : databases.value.slice(0, 8),
)

const engineFiles = computed(() => {
  const database = storageData.value?.database
  const rows = databaseOther.value.filter(
    (row) => row.key !== 'leftover' && row.key !== 'system' && row.key !== 'binlog',
  )

  if (!database) return rows

  return [
    { key: 'binlog', label: 'Binary logs', detail: '', bytes: database.binlog_bytes },
    ...rows,
  ]
})

onMounted(() => {
  loadRegistry()
  load()
})
</script>

<template>
  <Teleport defer to="#header-actions">
    <Button
      variant="ghost"
      icon="lucide-refresh-cw"
      tooltip="Refresh"
      :loading="loading"
      @click="load"
    />

    <Button
      v-if="session.centralUrl"
      :icon-left="h(FCLogo, { class: 'size-4' })"
      label="Manage storage"
      :link="centralServerUrl('resize')"
    />
  </Teleport>

  <div v-if="loading && !storageData" class="flex flex-col gap-4 p-3 md:p-4">
    <Skeleton class="rounded-7 h-[17rem]" />

    <div class="gap-4 grid lg:grid-cols-3">
      <Skeleton v-for="card in 3" :key="card" class="rounded-6 h-96" />
    </div>
  </div>

  <ErrorMessage v-else-if="error" :message="error" class="m-3 md:m-4" />

  <div v-else-if="storageData" class="flex flex-col gap-4 p-3 md:p-4 fade-in">
    <section
      ref="hero"
      class="relative md:-mx-4 md:-mt-4"
      :class="{ 'flex flex-col items-center gap-6 px-4 py-6': !isOrbit }"
      :style="isOrbit ? { height: `${orbitHeight}px` } : undefined"
    >
      <svg v-if="isOrbit" class="absolute inset-0 size-full pointer-events-none" aria-hidden="true">
        <path
          v-for="line in lines"
          :key="line.key"
          :d="line.path"
          fill="none"
          :class="{ 'connector-active': hovered === line.key }"
          class="connector"
          stroke-width="1"
          stroke-dasharray="4 4"
        />
      </svg>

      <div
        class="flex items-center gap-3 bg-surface-gray-1 px-3 py-2.5 rounded-5 w-72"
        :class="{ 'orbit-item': isOrbit }"
        :style="place(0, diskY)"
      >
        <span
          class="place-items-center grid bg-surface-gray-3 dark:bg-surface-gray-2 rounded-5 size-10 shrink-0"
        >
          <span class="size-5 text-ink-gray-6 lucide-hard-drive" />
        </span>

        <span class="flex flex-col flex-1 gap-2">
          <span class="flex items-baseline gap-6 whitespace-nowrap">
            <span class="flex-1 text-ink-gray-8 text-base">Disk</span>
            <span class="tabular-nums text-ink-gray-7 text-sm">
              {{ formatBytes(storageData.disk_used) }}
              of {{ formatBytes(storageData.disk_total) }}
            </span>
          </span>

          <Progress :value="usedPercent" size="sm" />
        </span>
      </div>

      <DiskRing
        ref="ring"
        :segments="segments"
        :total="pilotBytes"
        :size="ringSize"
        :thickness="15"
        :start="ringLayout.start"
        :active="hovered"
        @hover="hovered = $event"
        :class="{ 'orbit-item': isOrbit }"
        :style="place(0, 0)"
      >
        <span class="tabular-nums text-ink-gray-9 text-xl-semibold">
          {{ formatBytes(pilotBytes) }}
        </span>
        <span class="mt-1 text-ink-gray-5 text-sm">used by Pilot</span>
      </DiskRing>

      <ul :class="isOrbit ? 'contents' : 'grid sm:grid-cols-2 gap-3 w-full'">
        <li
          v-for="segment in segments"
          :key="segment.key"
          :style="tilePlace(segment.key)"
          :ref="(element) => setTile(segment.key, element)"
          class="flex items-center gap-3 px-3 cursor-pointer py-2.5 rounded-5 transition-colors duration-150"
          :class="[hovered === segment.key ? 'bg-surface-gray-2' : 'bg-surface-gray-1', { 'orbit-item w-72': isOrbit }]"
          @mouseenter="hovered = segment.key"
          @mouseleave="hovered = ''"
        >
          <span
            class="place-items-center grid rounded-5 size-10 transition-colors duration-150 shrink-0"
            :class="hovered === segment.key ? 'bg-surface-gray-4 dark:bg-surface-gray-3' : 'bg-surface-gray-3 dark:bg-surface-gray-2'"
          >
            <span class="size-5 text-ink-gray-6" :class="segment.icon" />
          </span>

          <span class="flex flex-col flex-1 gap-0.5 min-w-0">
            <span class="flex items-baseline gap-6 whitespace-nowrap">
              <span
                class="flex flex-1 items-center gap-2 text-ink-gray-8 text-base legend-dot"
                :style="{ '--dot-color': segment.color }"
              >
                {{ segment.label }}
              </span>
              <span class="tabular-nums text-ink-gray-7 text-sm">
                {{ formatBytes(segment.bytes) }}
              </span>
            </span>
            <span class="text-ink-gray-5 text-xs">{{ segment.note }}</span>
          </span>
        </li>
      </ul>
    </section>

    <div class="flex-1 grid md:grid-cols-2 xl:grid-cols-3 border border-outline-gray-1.5 rounded-6">
      <section class="flex flex-col gap-2 md:border-r border-outline-gray-1.5 p-5">
        <span class="flex items-center gap-2">
          <span class="size-4 text-ink-gray-5 lucide-pilot-site" />
          <span class="text-ink-gray-8 text-base-medium">Sites</span>
          <span class="text-ink-gray-5 text-sm">{{ sites.length }}</span>
          <Badge :label="formatBytes(sitesBytes)" class="-my-0.5 ml-auto tabular-nums" />
        </span>

        <div class="flex flex-col">
          <Collapsable
            v-for="site in sites"
            :key="site.name"
            :opened="openSite === site.name"
            @update:opened="openSite = $event ? site.name : ''"
          >
            <template #header="{ opened, toggle }">
              <button
                type="button"
                :aria-expanded="opened"
                class="group flex items-center gap-2 hover:bg-surface-gray-1 -mx-2 px-2 rounded-4 w-[calc(100%+1rem)] h-9 text-base text-left transition-colors duration-150"
                @click="toggle"
              >
                <span
                  class="size-3.5 text-ink-gray-4 transition-transform duration-150 lucide-chevron-right shrink-0"
                  :class="{ 'rotate-90': opened }"
                />
                <span
                  class="text-ink-gray-8 group-hover:text-ink-gray-9 transition-colors duration-150"
                >
                  {{ site.name }}
                </span>
                <span class="ml-auto tabular-nums text-ink-gray-8 whitespace-nowrap">
                  {{ formatBytes(site.bytes) }}
                </span>
              </button>
            </template>

            <div class="flex flex-col ml-[7px] pl-4 border-outline-gray-2 border-l">
              <span
                v-for="part in site.parts"
                :key="part.key"
                class="flex items-center gap-2 h-7 text-sm"
              >
                <span class="size-3.5 text-ink-gray-4 shrink-0" :class="part.icon" />
                <span class="text-ink-gray-5">{{ part.label }}</span>
                <span class="ml-auto tabular-nums text-ink-gray-6 whitespace-nowrap">
                  {{ formatBytes(part.bytes) }}
                </span>
              </span>
            </div>
          </Collapsable>
        </div>
      </section>

      <section
        class="flex flex-col gap-2 border-t md:border-t-0 xl:border-r border-outline-gray-1.5 p-5"
      >
        <span class="flex items-center gap-2">
          <span class="size-4 text-ink-gray-5 lucide-package" />
          <span class="text-ink-gray-8 text-base-medium">Apps</span>
          <span class="text-ink-gray-5 text-sm">{{ apps.length }}</span>
          <Badge
            :label="formatBytes(storageData.bench.apps_bytes)"
            class="-my-0.5 ml-auto tabular-nums"
          />
        </span>

        <div class="flex flex-col">
          <Collapsable
            v-for="app in apps.slice(0, 8)"
            :key="app.name"
            :opened="openApp === app.name"
            @update:opened="openApp = $event ? app.name : ''"
          >
            <template #header="{ opened, toggle }">
              <button
                type="button"
                :aria-expanded="opened"
                class="group flex items-center gap-2 hover:bg-surface-gray-1 -mx-2 px-2 rounded-4 w-[calc(100%+1rem)] h-9 text-base text-left transition-colors duration-150"
                @click="toggle"
              >
                <span
                  class="size-3.5 text-ink-gray-4 transition-transform duration-150 lucide-chevron-right shrink-0"
                  :class="{ 'rotate-90': opened }"
                />
                <AppIcon
                  :name="app.name"
                  :label="titleMap[app.name] || app.name"
                  :logo="logoMap[app.name]"
                  size="xs"
                />
                <span
                  class="text-ink-gray-8 group-hover:text-ink-gray-9 transition-colors duration-150"
                >
                  {{ titleMap[app.name] || app.name }}
                </span>
                <span class="ml-auto tabular-nums text-ink-gray-8 whitespace-nowrap">
                  {{ formatBytes(app.bytes) }}
                </span>
              </button>
            </template>

            <div class="flex flex-col ml-[7px] pl-4 border-outline-gray-2 border-l">
              <span
                v-for="part in appParts(app)"
                :key="part.key"
                class="flex items-center gap-2 h-7 text-sm"
              >
                <span class="size-3.5 text-ink-gray-4 shrink-0" :class="part.icon" />
                <span class="text-ink-gray-5">{{ part.label }}</span>
                <span class="ml-auto tabular-nums text-ink-gray-6 whitespace-nowrap">
                  {{ formatBytes(part.bytes) }}
                </span>
              </span>
            </div>
          </Collapsable>
        </div>
      </section>

      <section
        v-if="storageData.database.supported"
        class="flex flex-col gap-2 md:col-span-2 xl:col-span-1 border-t xl:border-t-0 border-outline-gray-1.5 p-5"
      >
        <span class="flex items-center gap-2">
          <span class="size-4 text-ink-gray-5 lucide-database" />
          <span class="text-ink-gray-8 text-base-medium">Databases</span>
          <span class="text-ink-gray-5 text-sm">{{ databases.length }}</span>
          <Badge
            :label="formatBytes(storageData.database.used_bytes)"
            class="-my-0.5 ml-auto tabular-nums"
          />
        </span>

        <Scrollbar class="-ml-2 -mr-4" :class="{ 'h-80': showAllDatabases }">
          <div class="flex flex-col pr-4 pl-2">
            <component
              :is="row.site ? 'router-link' : 'div'"
              v-for="row in visibleDatabases"
              :key="row.schema"
              :to="row.site ? { name: 'DB analyzer', query: { site: row.site } } : undefined"
              class="group flex items-center gap-2 hover:bg-surface-gray-1 -mx-2 px-2 rounded-4 h-9 text-base transition-colors duration-150"
            >
              <span
                class="size-4 text-ink-gray-4 shrink-0"
                :class="row.site ? 'lucide-pilot-site' : 'lucide-database'"
              />
              <span
                class="transition-colors duration-150"
                :class="row.site ? 'text-ink-gray-7 group-hover:text-ink-gray-9' : 'text-ink-gray-6'"
              >
                {{ row.site || row.schema }}
              </span>
              <Badge v-if="row.system" label="system" size="sm" />
              <span
                v-if="row.site"
                class="size-3.5 text-ink-gray-4 lucide-chevron-right shrink-0"
              />
              <span class="ml-auto tabular-nums text-ink-gray-8 whitespace-nowrap">
                {{ formatBytes(row.bytes) }}
              </span>
            </component>
          </div>
        </Scrollbar>

        <Button
          v-if="databases.length > 8"
          variant="ghost"
          class="self-start"
          :icon-left="showAllDatabases ? 'lucide-chevron-up' : 'lucide-chevron-down'"
          :label="showAllDatabases ? 'Show less' : `Show ${databases.length - 8} more`"
          @click="showAllDatabases = !showAllDatabases"
        />

        <div class="flex flex-col mt-auto pt-3 border-outline-gray-1 border-t">
          <span
            v-for="row in engineFiles"
            :key="row.key"
            class="flex items-center gap-2 last:-mb-2 h-9 text-base"
          >
            <span class="flex-1 text-ink-gray-6">{{ row.label }}</span>
            <Button
              v-if="row.key === 'binlog' && storageData.database.engine === 'mariadb'"
              size="xs"
              class="-my-1"
              label="Purge"
              icon-left="lucide-brush-cleaning"
              :disabled="!row.bytes"
              @click="purger?.openDialog()"
            />
            <span class="tabular-nums text-ink-gray-8">{{ formatBytes(row.bytes) }}</span>
          </span>
        </div>
      </section>
    </div>

    <BinlogPurgeAlert
      ref="purger"
      v-if="storageData.database.engine === 'mariadb'"
      :bytes="storageData.database.binlog_bytes"
      @purged="load"
    />
  </div>
</template>

<style scoped>
.orbit-item {
  position: absolute;
}

.connector {
  stroke: var(--outline-gray-4);
  transition: stroke 200ms ease-out;
}

.connector-active {
  stroke: var(--ink-gray-8);
  animation: connector-flow 600ms linear infinite;
}

@keyframes connector-flow {
  to {
    stroke-dashoffset: -8;
  }
}

@media (prefers-reduced-motion: reduce) {
  .connector,
  .connector-active {
    transition: none;
    animation: none;
  }
}
</style>
