<script setup lang="ts">
import { Progress } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'

import { hasApiError } from '@/api/client'
import { monitorApi } from '@/api/monitor'
import { formatBytes } from '@/utils/format'

type Point = Record<string, number | null | undefined>

interface Props {
  points: Point[]
  cpuCount: number | null
  memoryTotal: number | null
  disk: { used_bytes: number | null; total_bytes: number | null } | null
  diskLimit: number
}

const props = defineProps<Props>()

const cpuFields = ['Busy User', 'Busy System', 'Busy IOWait', 'Busy IRQ', 'Busy Other']
const day = 86400 * 1000

const weekDisk = ref<Point[]>([])

const cpuOf = (point: Point) => cpuFields.reduce((sum, field) => sum + (point[field] ?? 0), 0)

const average = (values: number[]) =>
  values.reduce((sum, value) => sum + value, 0) / (values.length || 1)

const forecast = computed(() => {
  const rows = weekDisk.value.filter((point) => point.time && point['Root Disk'] != null)

  if (rows.length < 2) return null

  const xs = rows.map((point) => (point.time as number) / day)
  const ys = rows.map((point) => point['Root Disk'] as number)
  const meanX = average(xs)
  const meanY = average(ys)
  const slope =
    xs.reduce((sum, x, index) => sum + (x - meanX) * (ys[index] - meanY), 0) /
    xs.reduce((sum, x) => sum + (x - meanX) ** 2, 0)
  const target = props.diskLimit || 100
  const current = ys[ys.length - 1]

  if (!(slope > 0.01) || current >= target)
    return { label: `Hits ${target}%`, value: current >= target ? 'Now' : 'Not growing' }

  const date = new Date(Date.now() + ((target - current) / slope) * day)

  return {
    label: `Hits ${target}%`,
    value: new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date),
  }
})

const cards = computed(() => {
  const latest = props.points[props.points.length - 1] ?? {}
  const cpu = cpuOf(latest)
  const memory = latest.Used ?? 0
  const memoryTotal = props.memoryTotal || 0
  const diskUsed = props.disk?.used_bytes ?? 0
  const diskTotal = props.disk?.total_bytes ?? 0

  return [
    {
      label: 'CPU',
      icon: 'lucide-cpu',
      value: props.cpuCount
        ? `${((cpu / 100) * props.cpuCount).toFixed(1)} vCPUs`
        : `${cpu.toFixed(1)}%`,
      rest: props.cpuCount ? `of ${props.cpuCount}` : '',
      percent: cpu,
      facts: [
        { label: 'Avg', value: `${average(props.points.map(cpuOf)).toFixed(1)}%` },
        { label: 'Load', value: (latest.Load1 ?? 0).toFixed(2) },
      ],
    },
    {
      label: 'Memory',
      icon: 'lucide-memory-stick',
      value: formatBytes(memory),
      rest: memoryTotal ? `of ${formatBytes(memoryTotal)}` : '',
      percent: memoryTotal ? (memory / memoryTotal) * 100 : 0,
      facts: [
        { label: 'Avg', value: formatBytes(average(props.points.map((point) => point.Used ?? 0))) },
        { label: 'Swap', value: formatBytes(latest['Swap Used'] ?? 0) },
      ],
    },
    {
      label: 'Storage',
      icon: 'lucide-hard-drive',
      value: formatBytes(diskUsed),
      rest: diskTotal ? `of ${formatBytes(diskTotal)}` : '',
      percent: diskTotal ? (diskUsed / diskTotal) * 100 : 0,
      facts: [
        { label: 'Free', value: formatBytes(Math.max(0, diskTotal - diskUsed)) },
        forecast.value ?? {
          label: 'I/O',
          value: `${formatBytes((latest.Read ?? 0) + (latest.Write ?? 0))}/s`,
        },
      ],
    },
  ]
})

onMounted(async () => {
  try {
    const history = await monitorApi.history('1w')

    if (!hasApiError(history)) weekDisk.value = history.system.points
  } catch {}
})
</script>

<template>
  <section class="gap-4 grid lg:grid-cols-3 mb-4">
    <div
      v-for="card in cards"
      :key="card.label"
      class="flex flex-col gap-3 bg-surface-gray-0 p-4 border border-outline-gray-1.5 rounded-5"
    >
      <span class="flex items-center gap-2 text-ink-gray-8 text-base-medium">
        <span class="size-4 text-ink-gray-5" :class="card.icon" />
        {{ card.label }}
        <span class="ml-auto tabular-nums text-ink-gray-5 text-base">{{ Math.round(card.percent) }}%</span>
      </span>

      <Progress :value="Math.min(100, Math.round(card.percent))" size="md" />

      <span
        class="flex items-baseline gap-4 [&>span:nth-child(2)]:ml-auto text-sm whitespace-nowrap"
      >
        <span class="tabular-nums text-ink-gray-8">
          {{ card.value }}
          <span class="text-ink-gray-5">{{ card.rest }}</span>
        </span>

        <span v-for="fact in card.facts" :key="fact.label" class="tabular-nums text-ink-gray-8">
          <span class="text-ink-gray-5">{{ fact.label }}</span>
          {{ fact.value }}
        </span>
      </span>
    </div>
  </section>
</template>
