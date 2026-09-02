<script setup lang="ts">
import { computed } from 'vue'
import { Badge, Button, Dropdown, Tooltip } from 'frappe-ui'

import AppIcon from '@/components/apps/AppIcon.vue'
import ProcessStateBadge from '@/components/processes/ProcessStateBadge.vue'
import Table from '@/components/common/Table.vue'

import { formatCpu, formatMemory, isProcessBusy, processLabel } from '@/utils/processFormat'
import { toSentenceCase } from '@/utils/format'

interface Props {
  source: string
  processes: any[]
}

const props = defineProps<Props>()
const emit = defineEmits(['restart', 'inspect'])

const isBench = computed(() => props.source === 'bench')

const columns = [
  { label: 'Process', key: 'name' },
  { label: 'State', key: 'state' },
  { label: 'CPU', key: 'cpu', class: 'text-right' },
  { label: 'Memory', key: 'memory', class: 'text-right' },
  { label: 'Uptime', key: 'uptime', class: 'text-right' },
  { label: '', key: 'actions', class: 'w-10' },
]

const rows = computed(() =>
  props.processes.map((process) => ({
    id: process.name,
    name: processLabel(process.name, props.source),
    fullName: process.name,
    state: process.state,
    busy: isProcessBusy(process.state),
    cpu: formatCpu(process.cpu_percent),
    memory: formatMemory(process.memory_mb),
    uptime: process.uptime || '—',
    logFilename: process.log_filename,
    declaration: process.declaration || null,
    process,
  })),
)

const menuOptions = (row) => {
  const options = [
    { label: 'Restart', icon: 'refresh-cw', onClick: () => emit('restart', row.process) },
  ]
  if (row.declaration)
    options.push({ label: 'Declaration', icon: 'file-code', onClick: () => emit('inspect', row.process) })
  return options
}
</script>

<template>
  <section>
    <header class="flex items-center gap-2 px-1 pb-2">
      <span
        v-if="isBench"
        class="place-items-center grid bg-surface-gray-2 rounded-4 size-5 text-ink-gray-5 shrink-0"
      >
        <span class="size-3 lucide-server" />
      </span>
      <AppIcon v-else :name="source" size="sm" />

      <h2 class="font-medium text-ink-gray-8 text-base">
        {{ isBench ? 'Bench' : toSentenceCase(source) }}
      </h2>
      <Badge :label="processes.length" size="sm" />
    </header>

    <Table :columns="columns" :rows="rows" height="h-auto">
      <template #name="{ row }">
        <Tooltip :text="row.fullName">
          <span class="font-medium text-ink-gray-8">{{ row.name }}</span>
        </Tooltip>
      </template>

      <template #state="{ row }">
        <ProcessStateBadge :state="row.state" />
      </template>

      <template #cpu="{ row }">
        <span class="text-ink-gray-7 tabular-nums">{{ row.cpu }}</span>
      </template>

      <template #memory="{ row }">
        <span class="text-ink-gray-7 tabular-nums">{{ row.memory }}</span>
      </template>

      <template #uptime="{ row }">
        <span class="text-ink-gray-5 tabular-nums">{{ row.uptime }}</span>
      </template>

      <template #actions="{ row }">
        <div class="flex justify-end items-center gap-1">
          <Tooltip text="View log">
            <router-link
              :to="{ name: 'Logs', query: { file: row.logFilename } }"
              class="inline-grid place-items-center rounded-4 size-6 text-ink-gray-4 hover:text-ink-gray-8 hover:bg-surface-gray-2 transition-colors"
            >
              <span class="size-4 lucide-scroll-text" />
            </router-link>
          </Tooltip>

          <Dropdown :options="menuOptions(row)">
            <template #default="{ open }">
              <Button
                variant="ghost"
                :active="open"
                :loading="row.busy"
                icon="lucide-ellipsis"
                label="Process actions"
                tooltip="Actions"
              />
            </template>
          </Dropdown>
        </div>
      </template>
    </Table>
  </section>
</template>
