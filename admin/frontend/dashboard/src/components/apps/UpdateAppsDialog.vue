<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Checkbox, Dialog, ErrorMessage, LoadingText } from 'frappe-ui'

import AppIcon from '@/components/apps/AppIcon.vue'

import { updatesApi } from '@/api/updates'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useAppUpdates } from '@/composables/apps/useAppUpdates'

const open = defineModel()
const router = useRouter()

const { updates, appsWithUpdates, checking } = useAppUpdates()
const { titleMap, load: loadRegistry } = useAppRegistry()

const appNames = computed(() => {
  const names = [...appsWithUpdates.value]
  const frappeIndex = names.indexOf('frappe')
  if (frappeIndex > 0) {
    names.splice(frappeIndex, 1)
    names.unshift('frappe')
  }
  return names
})

const selected = ref(new Set())

const updateLabel = computed(() => {
  const count = selected.value.size
  if (!count) return 'Update'
  if (count === appNames.value.length) return 'Update all'
  return count === 1 ? 'Update 1 app' : `Update ${count} apps`
})
const safeguard = ref(true)
const updating = ref(false)
const error = ref('')

watch(open, (isOpen) => {
  if (isOpen) loadRegistry()
})
watch(
  appNames,
  (names) => {
    selected.value = new Set(names)
  },
  { immediate: true },
)

const toggle = (name) => {
  const next = new Set(selected.value)
  next.has(name) ? next.delete(name) : next.add(name)
  selected.value = next
}

const toggleAll = () => {
  selected.value = selected.value.size === appNames.value.length ? new Set() : new Set(appNames.value)
}

const runUpdate = async () => {
  if (!selected.value.size) return
  updating.value = true
  error.value = ''
  try {
    const res = await updatesApi.createUpdate({
      apps: [...selected.value],
      disable_safeguards: !safeguard.value,
    })
    open.value = false
    router.push({ name: 'UpdateDetail', params: { operationId: res.operation.id } })
  } catch (e) {
    error.value = e.message || 'Failed to start update.'
  } finally {
    updating.value = false
  }
}
</script>

<template>
  <Dialog v-model="open" title="Updates" size="md">
    <div class="flex flex-col gap-4">
      <LoadingText v-if="checking" class="justify-center py-8" />

      <p v-else-if="!appNames.length" class="py-6 text-ink-gray-5 text-sm text-center">
        Your bench is up to date.
      </p>

      <template v-else>
        <div class="flex flex-col gap-2">
          <div class="flex items-center justify-between">
            <span class="text-ink-gray-5 text-sm">
              {{ selected.size }} of {{ appNames.length }} selected
            </span>

            <Button variant="ghost" @click="toggleAll">
              {{ selected.size === appNames.length ? 'Unselect all' : 'Select all' }}
            </Button>
          </div>

          <div class="flex flex-col gap-3 max-h-80 overflow-y-auto">
            <button
              v-for="name in appNames"
              :key="name"
              type="button"
              role="checkbox"
              :aria-checked="selected.has(name)"
              class="flex items-center gap-2.5 pr-2 text-left"
              @click="toggle(name)"
            >
              <AppIcon :name="name" size="xl" />
              <span class="flex-1 min-w-0">
                <p class="font-medium text-ink-gray-8 truncate">
                  {{ titleMap[name] || name }}
                </p>

                <p
                  v-if="updates[name]"
                  class="flex items-center gap-1 mt-0.5 font-mono text-ink-gray-5 text-p-xs truncate"
                >
                  {{ updates[name].current }}
                  <span class="lucide-arrow-right size-3 shrink-0 text-ink-gray-4" />
                  <span class="text-ink-green-6">{{ updates[name].target }}</span>
                </p>
              </span>

              <Checkbox
                :model-value="selected.has(name)"
                class="pointer-events-none shrink-0"
                tabindex="-1"
                aria-hidden="true"
              />
            </button>
          </div>
        </div>

        <label class="flex items-center gap-2 pt-2 cursor-pointer">
          <Checkbox v-model="safeguard" />
          <span class="text-ink-gray-7 text-sm">Take backup of sites</span>
        </label>
      </template>

      <ErrorMessage v-if="error" :message="error" />

    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="open = false">Cancel</Button>
        <Button
          v-if="appNames.length"
          variant="solid"
          :loading="updating"
          :disabled="!selected.size"
          @click="runUpdate"
        >
          {{ updateLabel }}
        </Button>
      </div>
    </template>
  </Dialog>
</template>
