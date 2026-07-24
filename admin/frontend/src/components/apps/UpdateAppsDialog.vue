<template>
  <Dialog v-model="open" :options="{ title: 'Updates', size: 'md' }">
    <template #body-content>
      <div class="flex flex-col gap-4">
        <div v-if="checking" class="flex justify-center py-8">
          <LoadingText />
        </div>
        <p v-else-if="!appNames.length" class="py-6 text-ink-gray-5 text-sm text-center">
          Your bench is up to date.
        </p>
        <template v-else>
          <div class="flex flex-col gap-1 max-h-80 overflow-y-auto">
            <button
              v-for="name in appNames"
              :key="name"
              type="button"
              class="flex items-center gap-3 hover:bg-surface-gray-1 p-2 rounded-lg text-left transition-colors"
              @click="toggle(name)"
            >
              <AppIcon :name="name" class="rounded-lg size-8 shrink-0" />
              <span class="flex-1 min-w-0">
                <p class="font-medium text-ink-gray-8 text-sm truncate">
                  {{ titleMap[name] || name }}
                </p>
                <p
                  v-if="updates[name]"
                  class="mt-1 flex items-center gap-1 font-mono text-ink-gray-5 text-xs truncate"
                >
                  {{ updates[name].current }}
                  <span class="lucide-arrow-right size-3 shrink-0 text-ink-gray-4" />
                  <span class="text-ink-green-7">{{ updates[name].target }}</span>
                </p>
              </span>
              <Checkbox :model-value="selected.has(name)" class="pointer-events-none shrink-0" />
            </button>
          </div>

          <div class="flex flex-col gap-2 pt-2">
            <label class="flex items-center gap-2 cursor-pointer">
              <Checkbox v-model="safeguard" />
              <span class="text-ink-gray-7 text-sm">Take backup of sites</span>
            </label>
          </div>
        </template>

        <ErrorMessage v-if="error" :message="error" />

        <div class="flex justify-end gap-2 pt-4 border-t border-outline-gray-1">
          <Button variant="ghost" @click="open = false">Cancel</Button>
          <Button
            v-if="appNames.length"
            variant="solid"
            :loading="updating"
            :disabled="!selected.size"
            @click="runUpdate"
          >
            {{ selected.size == 0 ? 'Update' : (
                appNames.length == selected.size ? 'Update all' :
                  (
                    selected.size == 1 ? 'Update 1 app' : `Update ${selected.size} apps`
                  )
              ) }}
          </Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Checkbox, Dialog, ErrorMessage, LoadingText } from 'frappe-ui'
import { migrationsApi } from '@/api/migrations'
import AppIcon from '@/components/apps/AppIcon.vue'
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

function toggle(name) {
  const next = new Set(selected.value)
  next.has(name) ? next.delete(name) : next.add(name)
  selected.value = next
}

async function runUpdate() {
  if (!selected.value.size) return
  updating.value = true
  error.value = ''
  try {
    const res = await migrationsApi.createUpdate({
      apps: [...selected.value],
      disable_safeguards: !safeguard.value,
    })
    open.value = false
    router.push({ name: 'MigrationDetail', params: { operationId: res.operation.id } })
  } catch (e) {
    error.value = e.message || 'Failed to start update.'
  } finally {
    updating.value = false
  }
}
</script>
