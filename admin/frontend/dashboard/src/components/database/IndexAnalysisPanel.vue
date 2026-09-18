<script setup lang="ts">
import { Button, ErrorMessage, TabButtons } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

import Table from '@/components/common/Table.vue'
import DatabasePanel from '@/components/database/DatabasePanel.vue'
import PerformanceSchemaNotice from '@/components/database/PerformanceSchemaNotice.vue'

import { apiErrorMessage } from '@/api/client'
import { databaseApi } from '@/api/database'

const props = defineProps({
  site: { type: String, default: '' },
  badge: { type: String, default: '' },
  enabled: { type: Boolean, default: false },
  showSite: { type: Boolean, default: false },
  siteByDatabase: { type: Object, default: () => ({}) },
})

const pageSize = 20

const tab = ref('unused_indexes')

const rows = ref([])
const hasNextPage = ref(false)
const loading = ref(false)
const error = ref('')
const loaded = ref(false)

const siteLabel = (database) => props.siteByDatabase[database] || database || '—'

const withSite = (columns) =>
  props.showSite ? [{ key: 'database', label: 'Site' }, ...columns] : columns

const tabOptions = [
  { label: 'Unused', value: 'unused_indexes' },
  { label: 'Redundant', value: 'redundant_indexes' },
]

const columnsByTab = {
  unused_indexes: [
    { key: 'table', label: 'Table Name' },
    { key: 'index', label: 'Index Name' },
  ],
  redundant_indexes: [
    { key: 'table', label: 'Table Name' },
    { key: 'dominant_index', label: 'Dominant Index' },
    { key: 'dominant_index_columns', label: 'Dominant Index Columns' },
    { key: 'redundant_index', label: 'Redundant Index' },
    { key: 'redundant_index_columns', label: 'Redundant Index Columns' },
  ],
}

const columns = computed(() => withSite(columnsByTab[tab.value]))

// Redundant indexes come from information_schema, so they read fine with
// Performance Schema off; unused indexes do not.
const needsPerformanceSchema = computed(() => tab.value === 'unused_indexes')

const load = async (offset = 0) => {
  loading.value = true
  error.value = ''
  try {
    const result = await databaseApi.performanceReport(tab.value, props.site, pageSize, offset)
    if (result?.error) throw new Error(apiErrorMessage(result, 'Could not load the index report.'))
    rows.value = offset ? [...rows.value, ...result.data] : result.data
    hasNextPage.value = result.has_next_page
    loaded.value = true
  } catch (caught) {
    error.value = caught.message || 'Could not load the index report.'
  } finally {
    loading.value = false
  }
}

watch(tab, () => load())

watch(
  () => props.site,
  () => loaded.value && load(),
)
</script>

<template>
  <DatabasePanel
    title="Database Index Analysis"
    subtitle="Analyze the indexes of the database"
    :badge="badge"
    :loading="loading"
    @open="load()"
    @refresh="load()"
  >
    <ErrorMessage v-if="error" :message="error" class="m-4" />

    <template v-else>
      <div class="px-4 pb-3">
        <TabButtons v-model="tab" :options="tabOptions" size="sm" />
      </div>

      <PerformanceSchemaNotice v-if="needsPerformanceSchema && !enabled" />

      <p v-else-if="!rows.length" class="py-10 border-t border-outline-gray-2 text-ink-gray-5 text-sm text-center">
        No results to display
      </p>

      <template v-else>
        <Table class="px-4" height="max-h-96" :columns="columns" :rows="rows">
          <template #database="{ row }">{{ siteLabel(row.database) }}</template>
        </Table>

        <div class="flex justify-end px-4 pb-4">
          <Button v-if="hasNextPage" :loading="loading" @click="load(rows.length)">
            Load more
          </Button>
        </div>
      </template>
    </template>
  </DatabasePanel>
</template>
