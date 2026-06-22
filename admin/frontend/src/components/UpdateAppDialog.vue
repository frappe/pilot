<script setup>
import { ref, watch } from 'vue'
import { Button, Dialog } from 'frappe-ui'
import LucideCheck from '~icons/lucide/check'
import { useTaskProgress } from '../composables/useTaskProgress.js'
import { useAppRegistry, hashColor } from '../composables/useAppRegistry.js'

const props = defineProps({
  modelValue: Boolean,
  apps: { type: Array, default: () => [] }, // [{name}] — apps with updates
})
const emit = defineEmits(['update:modelValue'])

const show = ref(props.modelValue)
watch(() => props.modelValue, v => { show.value = v })
watch(show, v => emit('update:modelValue', v))

const { watchTask } = useTaskProgress()
const { logoMap, loadRegistry } = useAppRegistry()

const selectedApps = ref(new Set())

watch(() => props.modelValue, async (open) => {
  if (!open) return
  selectedApps.value = new Set(props.apps.map(a => a.name))
  loadRegistry()
})

function toggle(name) {
  const next = new Set(selectedApps.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  selectedApps.value = next
}

const updating = ref(false)

async function runUpdate() {
  if (!selectedApps.value.size) return
  updating.value = true
  try {
    const res = await fetch('/api/tasks/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        command: 'update',
        apps: [...selectedApps.value],
      }),
    })
    const d = await res.json()
    if (d.ok) {
      watchTask(d.task_id)
      show.value = false
    }
  } catch { /* best-effort */ }
  finally { updating.value = false }
}
</script>

<template>
  <Dialog v-model="show" :options="{ title: 'Bench Update', size: 'md' }">
    <template #body-content>
      <div class="flex flex-col gap-5">

        <!-- Apps -->
        <div>
          <p class="mb-2 text-sm font-medium text-ink-gray-6">
            Select apps to update
          </p>
          <div class="divide-y divide-outline-gray-1">
            <div
              v-for="app in apps" :key="app.name"
              class="flex cursor-pointer items-center justify-between py-2.5 transition-opacity"
              :class="!selectedApps.has(app.name) && 'opacity-40'"
              @click="toggle(app.name)"
            >
              <div class="flex items-center gap-2.5">
                <div class="flex h-6 w-6 shrink-0 items-center justify-center overflow-hidden rounded"
                  :style="logoMap[app.name] ? {} : { background: hashColor(app.name) }">
                  <img v-if="logoMap[app.name]" :src="logoMap[app.name]" :alt="app.name" class="h-full w-full object-contain" />
                  <span v-else class="text-[10px] font-bold leading-none text-white">{{ app.name[0].toUpperCase() }}</span>
                </div>
                <span class="text-sm text-ink-gray-8">{{ app.name }}</span>
              </div>
              <LucideCheck v-if="selectedApps.has(app.name)" class="h-3.5 w-3.5 shrink-0 text-ink-blue-3" />
            </div>
          </div>
        </div>

        <!-- Info -->
        <p class="text-xs leading-relaxed text-ink-gray-4">
          Code updates download globally. All sites will be migrated after the update.
        </p>

        <!-- Actions -->
        <div class="flex justify-end gap-2 border-t border-outline-gray-1 pt-4">
          <Button variant="ghost" @click="show = false">Not Now</Button>
          <Button variant="solid" :loading="updating" :disabled="!selectedApps.size" @click="runUpdate">
            Update Now
          </Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>
