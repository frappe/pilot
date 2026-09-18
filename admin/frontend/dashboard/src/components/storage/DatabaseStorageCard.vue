<script setup lang="ts">
import { Badge } from 'frappe-ui'
import { computed, ref } from 'vue'

import BinlogPurgeAlert from '@/components/storage/BinlogPurgeAlert.vue'
import UsageMeter from '@/components/common/UsageMeter.vue'

import type { DatabaseBreakdown } from '@/types/storage'
import { formatBytes } from '@/utils/format'

interface Props {
  data: DatabaseBreakdown
  diskTotal: number
}

const props = defineProps<Props>()
const emit = defineEmits<{ purged: [] }>()

const COLORS: Record<string, string> = {
  binlog: 'amber-7',
  wal: 'amber-7',
  databases: 'violet-7',
  core: 'cyan-7',
  error_log: 'orange-7',
  server_log: 'orange-7',
  slow_log: 'pink-7',
  binlog_index: 'blue-7',
}

const GROUP_SHOWN_COUNT = 3

const groupParts = computed(() =>
  [
    {
      label: `${props.data.databases.length} databases`,
      bytes: props.data.databases.reduce((sum, row) => sum + row.bytes, 0),
      color: COLORS.databases,
    },
    ...props.data.components.map((component) => ({
      label: `${props.data.engine} ${component.label}`,
      bytes: component.bytes,
      color: COLORS[component.key] ?? 'gray-7',
    })),
    { label: `${props.data.engine} core files`, bytes: props.data.core_bytes, color: COLORS.core },
  ].sort((a, b) => (b.bytes ?? -1) - (a.bytes ?? -1)),
)

const sortedDatabases = computed(() => [...props.data.databases].sort((a, b) => b.bytes - a.bytes))

const showAllDatabases = ref(false)

const VISIBLE_DATABASE_COUNT = 5

const visibleDatabases = computed(() =>
  showAllDatabases.value
    ? sortedDatabases.value
    : sortedDatabases.value.slice(0, VISIBLE_DATABASE_COUNT),
)

const hiddenCount = computed(() =>
  showAllDatabases.value ? 0 : Math.max(sortedDatabases.value.length - VISIBLE_DATABASE_COUNT, 0),
)

// Not hiddenCount > 0: that goes to 0 once expanded, taking "Show less" with it.
const isDatabaseListExpandable = computed(
  () => sortedDatabases.value.length > VISIBLE_DATABASE_COUNT,
)
</script>

<template>
  <section class="p-5 min-w-0">
    <div class="flex justify-between items-center gap-3 mb-4">
      <h3 class="flex items-center gap-2 font-medium text-ink-gray-8">
        <span class="size-4 lucide-database" />
        Database storage
      </h3>

      <div v-if="data.supported" class="text-ink-gray-6 text-sm">
        <span class="font-medium text-ink-gray-8">{{ formatBytes(data.used_bytes) }}</span>
        of {{ formatBytes(diskTotal) }} used
      </div>
    </div>

    <p v-if="!data.supported" class="text-ink-gray-5">
      Storage breakdown is not available for the {{ data.engine }} engine.
    </p>

    <template v-else>
      <UsageMeter
        :parts="groupParts"
        :total="diskTotal"
        :visible-count="GROUP_SHOWN_COUNT"
        bar-height="h-5"
      />

      <div class="flex items-center gap-2 mt-4 py-3 border-t border-outline-alpha-gray-1">
        <span class="font-medium text-ink-gray-8 text-sm">Usage per database</span>
        <Badge :label="String(data.databases.length)" />
      </div>

      <div class="-mx-2 max-h-40 overflow-y-auto hover-merges-dividers">
        <component
          :is="row.site ? 'router-link' : 'div'"
          v-for="row in visibleDatabases"
          :key="row.schema"
          :to="row.site ? { path: '/database/analyzer', query: { site: row.site } } : undefined"
          class="group block px-2 rounded-4 no-underline transition-colors"
          :class="{ 'hover:bg-surface-gray-1': row.site }"
        >
          <div
            class="flex justify-between items-center gap-4 py-2 border-b border-outline-alpha-gray-1 transition-colors group-last:border-b-0"
          >
            <div class="flex items-center gap-2">
              <span class="text-ink-gray-5" :class="row.site ? 'lucide-globe' : 'lucide-database'" />
              <span
                class="text-sm truncate"
                :class="row.site ? 'text-ink-gray-8' : 'text-ink-gray-7'"
              >
                {{ row.site || row.schema }}
              </span>

              <Badge v-if="row.system" label="system" size="sm" />
              <span v-if="row.site" class="size-3.5 text-ink-gray-5 lucide-chevron-right" />
            </div>

            <div class="text-ink-gray-8 text-sm tabular-nums shrink-0">
              {{ formatBytes(row.bytes) }}
            </div>
          </div>
        </component>
      </div>

      <button
        v-if="isDatabaseListExpandable"
        type="button"
        @click="showAllDatabases = !showAllDatabases"
        class="flex items-center gap-2 text-sm text-ink-gray-6 hover:text-ink-gray-8 mt-2"
      >
        <span class="size-3.5 lucide-chevron-up" :class="{ 'rotate-180': !showAllDatabases }" />

        <span>
          {{ showAllDatabases ? 'Show less' : `Show ${hiddenCount} more` }}
        </span>
      </button>

      <BinlogPurgeAlert
        v-if="data.engine === 'mariadb'"
        :bytes="data.binlog_bytes"
        @purged="emit('purged')"
      />
    </template>
  </section>
</template>
