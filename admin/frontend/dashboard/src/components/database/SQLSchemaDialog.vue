<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Button, Dialog, TextInput } from 'frappe-ui'

import Table from '@/components/common/Table.vue'

interface Props {
  schema?: any[]
}

const props = withDefaults(defineProps<Props>(), {
  schema: () => [],
})
const emit = defineEmits(['preview'])

const show = defineModel({ default: false })

const schemaColumns = [
  { key: 'name', label: 'Column', class: 'font-mono' },
  { key: 'type', label: 'Type', class: 'font-mono' },
]

const search = ref('')
const selected = ref(null)

const filteredTables = computed(() => {
  const query = search.value.toLowerCase().trim()
  return props.schema.filter((t) => !query || t.name.toLowerCase().includes(query))
})

watch(show, (open) => {
  if (open) {
    search.value = ''
    selected.value = null
  }
})

const preview = (table) => {
  emit('preview', table.name)
  show.value = false
}
</script>

<template>
  <Dialog v-model="show" title="Tables" size="3xl">
    <TextInput v-model="search" placeholder="Search tables" autocomplete="off">
      <template #prefix>
        <span class="size-4 text-ink-gray-5 lucide-search" />
      </template>
    </TextInput>

    <div class="flex flex-col sm:flex-row gap-4 mt-3 sm:h-[380px]">
      <!-- Table list -->
      <aside
        class="border-b sm:border-b-0 sm:border-r border-outline-gray-2 sm:w-52 shrink-0 pb-2 sm:pb-0 max-h-40 sm:max-h-none overflow-y-auto"
      >
        <button
          v-for="table in filteredTables"
          :key="table.name"
          class="block px-2 py-1.5 rounded-5 w-full text-sm text-left truncate transition-colors"
          :class="selected?.name === table.name
            ? 'bg-surface-gray-2 text-ink-gray-9 font-medium'
            : 'text-ink-gray-7 hover:bg-surface-alpha-gray-1'"
          @click="selected = table"
        >
          {{ table.name }}
        </button>

        <p v-if="!filteredTables.length" class="px-2 py-1.5 text-ink-gray-4 text-sm">
          No tables found.
        </p>
      </aside>

      <!-- Column details -->
      <div class="flex-1 min-w-0 overflow-y-auto">
        <template v-if="selected">
          <div class="flex items-center justify-between mb-2">
            <h3 class="font-medium text-ink-gray-8 text-sm truncate">
              {{ selected.name }}
              <span class="font-normal text-ink-gray-5"
                >({{ selected.columns.length }}
                columns)</span
              >
            </h3>

            <Button variant="outline" @click="preview(selected)">
              <template #prefix>
                <span class="size-3.5 lucide-eye" />
              </template>
              Preview data
            </Button>
          </div>

          <Table
            class="border rounded-6 border-outline-gray-2"
            height="h-auto"
            :columns="schemaColumns"
            :rows="selected.columns"
          />
        </template>

        <p
          v-else
          class="flex justify-center items-center min-h-[120px] sm:h-full text-ink-gray-4 text-sm"
        >
          Select a table to view its columns.
        </p>
      </div>
    </div>
  </Dialog>
</template>
