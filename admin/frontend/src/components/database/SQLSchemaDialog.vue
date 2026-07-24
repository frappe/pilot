<template>
  <Dialog v-model="show" :options="{ title: 'Tables', size: '3xl' }">
    <template #body-content>
      <FormControl v-model="search" type="text" placeholder="Search tables" autocomplete="off">
        <template #prefix>
          <span class="size-4 text-ink-gray-5 lucide-search" />
        </template>
      </FormControl>

      <div class="flex flex-col sm:flex-row gap-4 mt-3 sm:h-[380px]">
        <!-- Table list -->
        <div
          class="border-b sm:border-b-0 sm:border-r border-outline-gray-2 sm:w-52 shrink-0 pb-2 sm:pb-0 max-h-40 sm:max-h-none overflow-y-auto"
        >
          <button
            v-for="table in filteredTables"
            :key="table.name"
            class="block px-2 py-1.5 rounded-md w-full text-sm text-left truncate transition-colors"
            :class="selected?.name === table.name
              ? 'bg-surface-gray-2 text-ink-gray-9 font-medium'
              : 'text-ink-gray-7 hover:bg-surface-gray-1'"
            @click="selected = table"
          >
            {{ table.name }}
          </button>
          <p v-if="!filteredTables.length" class="px-2 py-1.5 text-ink-gray-4 text-sm">
            No tables found.
          </p>
        </div>

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
              <Button variant="outline" size="sm" @click="preview(selected)">
                <template #prefix>
                  <span class="size-3.5 lucide-eye" />
                </template>
                Preview data
              </Button>
            </div>
            <SimpleTable
              :columns="[{ key: 'name', label: 'Column' }, { key: 'type', label: 'Type' }]"
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
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Button, Dialog, FormControl } from 'frappe-ui'
import SimpleTable from '@/components/common/SimpleTable.vue'

const props = defineProps({
  schema: { type: Array, default: () => [] },
})
const emit = defineEmits(['preview'])

const show = defineModel({ default: false })

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

function preview(table) {
  emit('preview', table.name)
  show.value = false
}
</script>
